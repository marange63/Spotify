"""Freshness / embargo defenses for the briefing pipeline (stdlib only).

The four-stage pipeline verifies that every figure *traces to a verbatim quote in the
dossier* — but a quote proves the draft matches the dossier, not that the source is real
or that the event has happened. On 2026-08-12 the deep-researcher fabricated a full CPI
report (with future-dated source URLs and invented quotes) three hours before the 8:30 ET
release; it passed every downstream audit because the fabricated quote satisfied the trace.

This module adds two deterministic, network-free defenses used by ``orchestrator.py``:

  1. **Future-dated source detection** — a source URL whose embedded date token is after the
     run date is a hallucinated/misdated source (the incident's tell: ``...-202508121356``).
  2. **Pre-release embargo gate** — a figure-bearing fact that reports a number for a
     scheduled release (from ``release_calendar.json``) whose datetime is after the run
     moment cannot be real: the release has not occurred yet.

It also renders the ``run_context.txt`` block that ``init`` drops into each run so the
web-research agents get the temporal anchor they otherwise lack (they receive a run *date*,
never a run *time*).

Network-based source verification (fetch the URL, confirm the quote is on the page) lives in
``orchestrator.py verify-sources`` — it is best-effort and advisory, kept out of here so this
module stays deterministic and import-cheap.
"""
import datetime
import json
import os
import re

import config  # noqa: F401  (kept for path parity with the rest of the pipeline)

CALENDAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "release_calendar.json")

# A decimal, a percent, or a basis-point figure — the signal that a fact *reports a number*
# rather than merely naming a release. "core CPI came in at 3.1 percent" trips this; "CPI is
# due Thursday" does not.
_FIGURE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|percent|percentage points?|basis points?|bps|bp)\b"
    r"|\d+\.\d+", re.IGNORECASE)

# Hedges that mark a number as an expectation/consensus/schedule rather than a realized print,
# so the embargo gate does not hard-fail a legitimate "consensus calls for 3.4%" line.
_HEDGE_RE = re.compile(
    r"\b(expected|expects?|expectation|consensus|forecast|projected|estimate[ds]?|"
    r"penciled|poll(?:ed)?|scheduled|due|awaited|anticipat\w*|will\s+(?:print|release|report|show)|"
    r"ahead of|before the|preview|priced in)\b", re.IGNORECASE)

# Date tokens commonly embedded in article slugs/paths: YYYYMMDDhhmm, YYYYMMDD, YYYY-MM-DD,
# YYYY/MM/DD, YYYY_MM_DD. Longest-first so the 12-digit form wins over its 8-digit prefix.
_URL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?(0[1-9]|[12]\d|3[01])(?:(\d{2})(\d{2}))?(?!\d)")


# --- release calendar --------------------------------------------------------------------

def load_calendar(path: str = CALENDAR_PATH) -> list:
    """Return the list of scheduled-release records, or [] if the file is missing/unreadable.

    Best-effort by design: a missing calendar disables the embargo gate but never crashes a
    run. Each record: ``{name, datetime (ISO w/ offset), keywords: [lowercase, ...]}``.
    """
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in doc.get("releases", []):
        dt = _parse_dt(r.get("datetime"))
        if dt is None:
            continue
        out.append({
            "name": r.get("name") or "(unnamed release)",
            "datetime": dt,
            "keywords": [str(k).lower() for k in (r.get("keywords") or []) if str(k).strip()],
        })
    return out


def _parse_dt(value):
    """Parse an ISO-8601 datetime (with offset) to an aware datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:  # treat a naive calendar entry as local time
        dt = dt.astimezone()
    return dt


def pending_releases(now: datetime.datetime, calendar: list | None = None) -> list:
    """Releases whose datetime is strictly after ``now`` — i.e. not yet occurred."""
    cal = load_calendar() if calendar is None else calendar
    return [r for r in cal if r["datetime"] > now]


def run_context_text(now: datetime.datetime, calendar: list | None = None) -> str:
    """The RUN CONTEXT block written to runs/<date>/run_context.txt and read by the
    research/review agents — gives them the run's as-of time and the releases that have NOT
    happened yet, which they must never state as fact."""
    pend = pending_releases(now, calendar)
    lines = [
        "RUN CONTEXT (injected by orchestrator.py — not part of any standing prompt)",
        f"As-of time for this run: {now.isoformat(timespec='seconds')}",
        "",
        "Anything scheduled to publish at or after the as-of time above HAS NOT HAPPENED yet.",
        "Never state its outcome as fact, never infer it from consensus, and reject any source",
        "whose timestamp/URL date is after the as-of time as hallucinated or misdated.",
    ]
    if pend:
        lines.append("")
        lines.append("Scheduled releases that have NOT occurred as of this run:")
        for r in sorted(pend, key=lambda r: r["datetime"]):
            lines.append(f"  - {r['name']} — releases {r['datetime'].isoformat(timespec='minutes')}")
    else:
        lines.append("")
        lines.append("No calendared release is pending as of this run.")
    return "\n".join(lines) + "\n"


# --- deterministic checks used by validate_research --------------------------------------

def url_date_tokens_after(url: str, ref_date: datetime.date):
    """If ``url`` embeds a date token later than ``ref_date``, return that date; else None.

    Catches the fabricated-source tell: a URL slug dated after the run (a source that could not
    exist yet). Only flags dates strictly after ref_date, so a same-day article passes.
    """
    if not isinstance(url, str):
        return None
    latest = None
    for m in _URL_DATE_RE.finditer(url):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            cand = datetime.date(y, mo, d)
        except ValueError:
            continue
        if cand > ref_date and (latest is None or cand > latest):
            latest = cand
    return latest


def fact_reports_pending_release(text: str, now, calendar: list | None = None):
    """If ``text`` reports a *figure* for a not-yet-occurred calendared release, return that
    release record; else None.

    A hedged number ("consensus calls for 3.4%") is allowed; an unhedged realized-sounding
    number ("core CPI came in at 3.1 percent") is not. ``now`` may be None (e.g. the run
    timestamp is unavailable) — in that case the gate is skipped and None is returned.
    """
    if now is None or not isinstance(text, str) or not text.strip():
        return None
    low = text.lower()
    if not _FIGURE_RE.search(text) or _HEDGE_RE.search(low):
        return None
    for r in pending_releases(now, calendar):
        if any(kw in low for kw in r["keywords"]):
            return r
    return None
