"""Verify that the PUBLIC feed is the feed this machine built — i.e. the day's episodes are live.

Why this exists: a successful ``git push`` is not a successful publish. The Pages site is deployed
by the ``.github/workflows/pages.yml`` workflow, and on 2026-11-21 that deploy failed (an artifact
lookup race in ``actions/deploy-pages``) AFTER the push had succeeded: ``publish_feed.py`` sent its
"briefings published" push, the 08:20 completion pass found nothing to do and reported success, and
the live feed sat on the previous day's build for 10.5 hours. The only signal was GitHub's own
failure email. Nothing in the pipeline looked at what listeners could actually fetch. This does.

It compares the episodes in the local ``docs/feed.xml`` with the live ``<FEED_BASE_URL>/feed.xml``
(same GUIDs, same enclosure URLs — the ``?v=`` token changes whenever an episode is republished, so
a stale build shows up even when no GUID is missing), then checks that each of the day's audio URLs
actually downloads. On a mismatch it pushes an ntfy alert naming the cause it can see: unpushed
commits, or the state of the latest Deploy Pages run.

    python live_check.py                       # check today now; alert on a mismatch
    python live_check.py --wait 600            # completion pass: allow a deploy in flight 10 min
    python live_check.py --date 2026-11-21 --no-notify      # report only

Exit code: 0 = live. 1 = NOT live (alert sent unless --no-notify). 2 = inconclusive — the site
could not be reached at all, or the check itself broke; deliberately not an alert, so a flaky
network or a bug here can never cry wolf.

Read-only: it never commits, pushes or re-triggers a deploy.
"""
import argparse
import datetime
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import config
import ntfy_push

log = logging.getLogger("live_check")

WORKFLOW_FILE = "pages.yml"
_HEADERS = {"User-Agent": "cautious-optimism-briefings", "Cache-Control": "no-cache"}

LIVE, NOT_LIVE, INCONCLUSIVE = "live", "not live", "inconclusive"


class Unreachable(Exception):
    """The site gave no HTTP answer at all (DNS, connection, timeout) — we learned nothing."""


@dataclass
class Diff:
    missing: list = field(default_factory=list)   # in the local feed, absent from the live one
    stale: list = field(default_factory=list)     # in both, but the live enclosure URL is older
    extra: list = field(default_factory=list)     # live only (pruned locally, still served)

    @property
    def in_sync(self) -> bool:
        return not (self.missing or self.stale or self.extra)


@dataclass
class Result:
    status: str
    date: str
    today: dict = field(default_factory=dict)     # guid -> title, the day's episodes (local feed)
    diff: Diff | None = None
    audio: list = field(default_factory=list)     # [(guid, problem)] for audio that won't download
    cause: str = ""


def parse_items(xml_text: str) -> dict:
    """``{guid: (enclosure_url, title)}`` for every ``<item>`` in an RSS document."""
    items = {}
    for it in ET.fromstring(xml_text).iter("item"):
        guid = (it.findtext("guid") or "").strip()
        if not guid:
            continue
        enc = it.find("enclosure")
        url = (enc.get("url") if enc is not None else "") or ""
        items[guid] = (url.strip(), (it.findtext("title") or guid).strip())
    return items


def compare(local: dict, live: dict) -> Diff:
    return Diff(
        missing=sorted(g for g in local if g not in live),
        stale=sorted(g for g in local if g in live and local[g][0] != live[g][0]),
        extra=sorted(g for g in live if g not in local),
    )


def _http(url: str, method: str = "GET", timeout: int = 30, headers: dict | None = None):
    """One request. Returns ``(status, body)``; raises ``urllib.error.HTTPError`` when the server
    answered with an error status and ``Unreachable`` when it did not answer at all."""
    req = urllib.request.Request(url, method=method, headers={**_HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError) as e:      # includes socket timeouts
        raise Unreachable(str(getattr(e, "reason", e))) from None


def fetch_live_feed() -> str:
    # The query string is a cache-buster: Pages sits behind a CDN with a 10-minute max-age.
    _, body = _http(f"{config.FEED_BASE_URL}/feed.xml?cb={int(time.time())}")
    return body.decode("utf-8", errors="replace")


def check_audio(urls: dict) -> list:
    """HEAD each ``{guid: url}``; return ``[(guid, problem)]`` for the ones that do not answer 200.
    One retry each, so a single dropped connection is not reported as missing audio."""
    problems = []
    for guid, url in sorted(urls.items()):
        if not url:
            problems.append((guid, "no enclosure URL"))
            continue
        problem = ""
        for _ in range(2):
            try:
                status, _ = _http(url, method="HEAD")
                problem = "" if status == 200 else f"HTTP {status}"
            except urllib.error.HTTPError as e:
                problem = f"HTTP {e.code}"
            except Unreachable:
                problem = "no response"
            if not problem:
                break
        if problem:
            problems.append((guid, problem))
    return problems


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", config.HERE, *args], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def commits_ahead() -> int:
    """Local ``main`` commits origin/main lacks (tracking ref only, no fetch) — a publish that was
    committed but never pushed. 0 when git cannot say."""
    try:
        return int(_git("rev-list", "--count", "origin/main..main") or 0)
    except ValueError:
        return 0


def origin_head() -> str | None:
    return _git("rev-parse", "origin/main")


def latest_deploy() -> dict | None:
    """The most recent Deploy Pages run on main, from the public API (no token: the publish PAT is
    Contents-only and the repo is public). None when it cannot be read — purely diagnostic."""
    url = (f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/workflows/"
           f"{WORKFLOW_FILE}/runs?branch=main&per_page=1")
    try:
        _, body = _http(url, timeout=20, headers={"Accept": "application/vnd.github+json"})
        runs = json.loads(body).get("workflow_runs") or []
        return runs[0] if runs else None
    except Exception as e:      # diagnostic only; never let it change the verdict
        log.info("could not read the deploy status (%s)", e)
        return None


def _deploy_failed(run: dict | None, head: str | None) -> bool:
    """True when the deploy of the commit we pushed has finished and did not succeed — there is no
    point waiting any longer for the site to change."""
    return bool(run and head and run.get("head_sha") == head
                and run.get("status") == "completed" and run.get("conclusion") != "success")


def _cause(ahead: int, run: dict | None, head: str | None, waited: int) -> str:
    if ahead:
        return (f"{ahead} publish commit(s) exist only on this machine - the git push never "
                "reached GitHub.")
    if not run:
        return "The Deploy Pages status could not be read."
    label = f"Deploy Pages run #{run.get('run_number')}"
    if head and run.get("head_sha") != head:
        return f"No Deploy Pages run has started for the latest push (most recent is {label})."
    if run.get("status") != "completed":
        return f"{label} is still {run.get('status')} after {waited}s."
    if run.get("conclusion") != "success":
        return f"{label} ended in {run.get('conclusion')}: {run.get('html_url')}"
    return f"{label} succeeded, yet the site still serves an older feed."


def verify(date: str, wait: int = 0, interval: int = 30) -> Result:
    """Compare the local feed with the live one, polling up to ``wait`` seconds for a deploy that
    is still in flight. Stops early once the feeds match, once the deploy of the pushed commit is
    known to have failed, or when nothing was pushed at all (waiting cannot help)."""
    with open(config.FEED_FILE, encoding="utf-8") as f:
        local = parse_items(f.read())
    today = {g: title for g, (_, title) in local.items() if g.endswith("-" + date)}
    result = Result(INCONCLUSIVE, date, today)

    ahead = commits_ahead()
    head = origin_head()
    started = time.monotonic()
    deadline = started + (0 if ahead else max(0, wait))
    run = None
    while True:
        problem = ""
        result.diff = None
        try:
            result.diff = compare(local, parse_items(fetch_live_feed()))
        except Unreachable as e:
            problem = f"unreachable ({e})"
        except urllib.error.HTTPError as e:
            problem = f"HTTP {e.code}"
        except ET.ParseError:
            problem = "not valid XML"
        if result.diff is not None and result.diff.in_sync:
            break
        if time.monotonic() >= deadline:
            break
        run = latest_deploy()
        if _deploy_failed(run, head):
            break
        log.info("live feed not current yet (%s) - checking again in %ds",
                 problem or "older build", interval)
        time.sleep(interval)
    waited = int(time.monotonic() - started)

    if result.diff is not None and result.diff.in_sync:
        result.audio = check_audio({g: local[g][0] for g in today})
        result.status = NOT_LIVE if result.audio else LIVE
        return result
    if problem.startswith("unreachable"):
        result.cause = f"the Pages site did not respond: {problem}"
        return result                                   # INCONCLUSIVE
    result.status = NOT_LIVE
    if result.diff is None:
        result.cause = f"The live feed.xml answered {problem}."
    else:
        result.cause = _cause(ahead, run or latest_deploy(), head, waited)
    return result


def _names(guids: list, titles: dict, limit: int = 4) -> str:
    # Feed titles read "<show name> — <long date>"; the date is already in the alert.
    shown = [titles.get(g, g).split(" — ")[0] for g in guids[:limit]]
    more = len(guids) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def describe(result: Result) -> str:
    """One paragraph for the log and the phone alert."""
    r = result
    if r.status == LIVE:
        if not r.today:
            return f"Live feed matches the local build (no episodes dated {r.date} to check)."
        return (f"Live feed matches the local build; all {len(r.today)} episode(s) for {r.date} "
                "are downloadable.")
    if r.status == INCONCLUSIVE:
        return f"Could not check the live feed - {r.cause}"
    if r.audio:
        bad = ", ".join(f"{r.today.get(g, g)} ({why})" for g, why in r.audio[:4])
        return (f"The live feed is current, but {len(r.audio)} of {len(r.today)} audio file(s) "
                f"for {r.date} do not download: {bad}. The deploy workflow did not mirror them "
                "from the audio Release.")
    fix = "Pushing any commit to main re-runs the deploy."
    if r.diff is None:
        return f"{r.cause} The day's episodes cannot be fetched. {fix}"
    behind = [g for g in r.diff.missing + r.diff.stale if g in r.today]
    if behind:
        what = (f"{len(behind)} of {len(r.today)} episode(s) for {r.date} are NOT on the live "
                f"feed ({_names(sorted(behind), r.today)}).")
    else:
        n = len(r.diff.missing) + len(r.diff.stale) + len(r.diff.extra)
        what = (f"The live feed is not the build this machine published ({n} episode entr"
                f"{'y differs' if n == 1 else 'ies differ'}; none dated {r.date}).")
    return f"{what} {r.cause} {fix}"


def main() -> int:
    config.configure_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--wait", type=int, default=0,
                    help="seconds to keep polling for a deploy still in flight (default 0)")
    ap.add_argument("--interval", type=int, default=30, help="seconds between polls")
    ap.add_argument("--no-notify", action="store_true", help="do not send the ntfy alert")
    args = ap.parse_args()

    try:
        result = verify(args.date, wait=args.wait, interval=max(1, args.interval))
    except Exception:
        log.exception("live check could not run")
        return 2
    text = describe(result)
    if result.status == LIVE:
        log.info("LIVE: %s", text)
        return 0
    if result.status == INCONCLUSIVE:
        log.warning("INCONCLUSIVE: %s", text)
        return 2
    log.error("NOT LIVE: %s", text)
    if not args.no_notify:
        ntfy_push.push(f"Briefings NOT live ({args.date})", text, "warning")
    return 1


if __name__ == "__main__":
    sys.exit(main())
