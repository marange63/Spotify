"""Deterministic per-run metrics for the agent-performance analysis.

Read-only over ``runs/<date>/`` and ``prompts.json``; computes exactly the numbers the analysis
author would otherwise eyeball from the artifacts (deep-dive firing, word count vs. the prompt's
stated floor, contradictions, reviewer scores, and the "figure has no verbatim quote" flag count).
Keeping these in code — not in the model's head — is what makes a recurring daily report trustworthy
and cheap.

    python run_report.py --date 2026-07-24          # human table
    python run_report.py --date 2026-07-24 --json    # same data as JSON

The ``daily-briefing`` skill runs this at the end of each run and cites the block in
``analyses/<date>.md``; it is also independently runnable for spot checks.
"""
import argparse
import datetime
import json
import os
import re
import sys

import config
import library
import orchestrator

# Reviewer issues_found entries in this family mean a figure reached the draft without a verbatim
# supporting quote (supported only by dossier-summary prose, then hedged or cut). Tracked by hand on
# 2026-07-23/24; this is the signal the deep-dive stage is meant to drive to zero on its item.
_SOFT_SUPPORT_RE = re.compile(
    r"verbatim quote|no .{0,12}quote|soft support|summary (?:prose|narrative|text)|"
    r"dossier summary|not (?:in|from) .{0,20}quote|hedged?",
    re.I,
)
_FLOOR_RE = re.compile(r"(\d{3,4})\s*(?:to|-|–|—)\s*\d{3,4}\s*word", re.I)
_FLOOR_FALLBACK_RE = re.compile(r"(\d{3,4})\s*word", re.I)
DEFAULT_FLOOR = 700


def stated_floor(prompt_text: str) -> int:
    """Lower word bound the prompt asks for (e.g. '1200 to 1500 word' -> 1200); default 700."""
    m = _FLOOR_RE.search(prompt_text) or _FLOOR_FALLBACK_RE.search(prompt_text)
    return int(m.group(1)) if m else DEFAULT_FLOOR


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _word_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return len(f.read().split())
    except FileNotFoundError:
        return 0


def _soft_support_flags(review: dict) -> int:
    if not review:
        return 0
    n = 0
    for issue in review.get("issues_found") or []:
        text = issue if isinstance(issue, str) else json.dumps(issue, ensure_ascii=False)
        if _SOFT_SUPPORT_RE.search(text):
            n += 1
    return n


def prompt_metrics(date: str, entry: dict, floors: dict) -> dict:
    """Metrics for one prompt in the run, from its runs/<date>/<id>/ artifacts."""
    pid = entry["id"]
    pdir = orchestrator.prompt_dir(date, pid)
    deep = _load_json(os.path.join(pdir, orchestrator.DEEP_FILE))
    review = _load_json(os.path.join(pdir, orchestrator.REVIEW_FILE))
    floor = floors.get(pid, DEFAULT_FLOOR)
    words = _word_count(os.path.join(pdir, orchestrator.FINAL_FILE))
    scores = (review or {}).get("scores") or {}
    return {
        "id": pid,
        "status": entry.get("status"),
        "deep_dive": deep is not None,
        "deep_status": (deep or {}).get("status"),
        "deep_facts": sum(len(c.get("important_facts") or [])
                          for c in (deep or {}).get("lead_candidates") or []),
        "contradictions": len((deep or {}).get("contradictions") or []),
        "words": words,
        "floor": floor,
        "under_floor": bool(words) and words < floor,
        "soft_support_flags": _soft_support_flags(review),
        "review_overall": scores.get("overall"),
    }


def build_report(date: str) -> dict:
    """Full run report: per-prompt metrics + batch totals. Reuses orchestrator run state."""
    status = orchestrator.run_status(date)  # raises FileNotFoundError if init never ran
    floors = {p["id"]: stated_floor(p.get("prompt", "")) for p in library.load()["prompts"]}
    rows = [prompt_metrics(date, e, floors) for e in status["prompts"]]
    written = [r for r in rows if r["words"]]
    totals = {
        "prompts": len(rows),
        "approved": sum(r["status"] == "approved" for r in rows),
        "skipped": sum(r["status"] == "skipped" for r in rows),
        "failed": sum(r["status"] == "failed" for r in rows),
        "deep_dives_fired": sum(r["deep_dive"] for r in rows),
        "contradictions_found": sum(r["contradictions"] for r in rows),
        "under_floor": sum(r["under_floor"] for r in rows),
        "written": len(written),
        "soft_support_flags": sum(r["soft_support_flags"] for r in rows),
    }
    return {"date": date, "novelty": status.get("novelty"), "prompts": rows, "totals": totals}


def format_report(report: dict) -> str:
    """Human-readable table for the terminal and for pasting into the analysis."""
    lines = [f"run {report['date']} — novelty: {report['novelty']}", ""]
    hdr = f"{'prompt':24} {'status':9} {'dive':5} {'facts':5} {'contra':6} {'words':6} {'<flr':5} {'soft':4} {'ovr':3}"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in report["prompts"]:
        lines.append(
            f"{r['id']:24} {str(r['status']):9} "
            f"{('yes' if r['deep_dive'] else '-'):5} "
            f"{r['deep_facts']:5} {r['contradictions']:6} "
            f"{r['words']:6} {('UNDER' if r['under_floor'] else '-'):5} "
            f"{r['soft_support_flags']:4} {str(r['review_overall'] if r['review_overall'] is not None else '-'):3}"
        )
    t = report["totals"]
    lines += ["", (f"{t['prompts']} prompts: {t['approved']} approved, {t['skipped']} skipped, "
                   f"{t['failed']} failed  |  deep dives fired: {t['deep_dives_fired']}, "
                   f"contradictions: {t['contradictions_found']}  |  under floor: "
                   f"{t['under_floor']}/{t['written']}  |  soft-support flags: {t['soft_support_flags']}")]
    return "\n".join(lines)


def main(argv=None) -> int:
    config.configure_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    args = ap.parse_args(argv)
    try:
        report = build_report(args.date)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
