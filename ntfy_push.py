"""One-off ntfy.sh push from the unattended run scripts.

Used by tools/phase1_prompt.ps1 when a scheduled job has to GIVE UP waiting for a Claude
usage-cap reset (the reset lands past the job's deadline, or the cap message could not be
parsed). It is the "a human may need to look" counterpart of the "briefings published" push
in publish_feed.py, and reuses that push's config (config.NTFY_SERVER / NTFY_TOPIC; env
BRIEFING_NTFY_TOPIC="" disables both). Best-effort: never exits non-zero, so a notification
problem can never sink the run that called it.

    python ntfy_push.py --title "Briefing run paused" --tags warning "body text"
"""
import argparse
import sys
import urllib.request

import config


def push(title: str, body: str, tags: str = "warning") -> bool:
    topic = (config.NTFY_TOPIC or "").strip()
    if not topic:
        print("ntfy_push: no topic configured - skipping", file=sys.stderr)
        return False
    url = f"{config.NTFY_SERVER.rstrip('/')}/{topic}"
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"Title": title, "Tags": tags},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        print(f"ntfy_push: pushed to {topic}", file=sys.stderr)
        return True
    except Exception as e:  # best-effort by design
        print(f"ntfy_push: failed ({e})", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title", default="Briefing run")
    ap.add_argument("--tags", default="warning")
    ap.add_argument("body")
    a = ap.parse_args()
    push(a.title, a.body, a.tags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
