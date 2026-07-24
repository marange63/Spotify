"""Deterministic per-run metrics for the agent-performance analysis.

Read-only over ``runs/<date>/``, ``prompts.json``, and the run's Claude Code transcripts; computes
the numbers the analysis author would otherwise eyeball (deep-dive firing, word count vs. the
prompt's stated floor, contradictions, reviewer scores, the "figure has no verbatim quote" flag
count) plus the run's **grand-total token usage** (tip to tail, including subagents) and a short
N-day trend. Keeping these in code — not the model's head — is what makes a recurring report
trustworthy.

    python run_report.py --date 2026-07-24                 # table + token total + 5-day trend
    python run_report.py --date 2026-07-24 --history 0      # just this run
    python run_report.py --date 2026-07-24 --json           # this run's data as JSON
    python run_report.py --date 2026-07-24 --start          # stamp the token-window start (idempotent)
    python run_report.py --date 2026-07-24 --end            # stamp the token-window end

Token accounting needs a ``runs/<date>/token_window.json`` (``{start,end}`` in UTC ISO). The
scheduled job stamps it via ``--start``/``--end`` around phase 1 (phase 2 spends no model tokens);
without it the token metric reads n/a. Tokens are summed from ``config.CLAUDE_TRANSCRIPTS_DIR``.
"""
import argparse
import datetime
import glob
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
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_FLOOR = 700
TOKEN_WINDOW_FILE = "token_window.json"
_TOKEN_FIELDS = ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens")


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
    """Metrics for one prompt in the run, from its runs/<date>/<id>/ artifacts (no transcripts)."""
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


def _cheap_report(date: str) -> dict:
    """Per-prompt metrics + batch totals + total words. No transcript reads (cheap)."""
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
    return {"date": date, "novelty": status.get("novelty"), "prompts": rows,
            "totals": totals, "words_total": sum(r["words"] for r in rows)}


# --- token accounting (transcripts) ------------------------------------------

def _now_utc_iso() -> str:
    n = datetime.datetime.now(datetime.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def window_path(date: str) -> str:
    return os.path.join(orchestrator.run_dir(date), TOKEN_WINDOW_FILE)


def mark_window(date: str, which: str) -> dict:
    """Stamp the run's token-window ``start`` or ``end`` (UTC now). ``start`` is idempotent —
    an existing start is never overwritten, so the true run beginning survives retries."""
    path = window_path(date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = _load_json(path) or {}
    if which == "start" and data.get("start"):
        return data
    data[which] = _now_utc_iso()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return data


def _scan_windows(windows: dict) -> dict:
    """Sum transcript token usage into each named window in a single pass. ``windows`` maps a key
    to ``(start, end)`` UTC-ISO bounds; returns ``{key: {input,output,cache_creation,cache_read,
    total}}``. Windows are per-run and disjoint, so each usage record lands in at most one."""
    out = {k: dict.fromkeys(("input", "output", "cache_creation", "cache_read"), 0) for k in windows}
    tdir = config.CLAUDE_TRANSCRIPTS_DIR
    if not os.path.isdir(tdir):
        return {}
    items = list(windows.items())
    for path in glob.glob(os.path.join(tdir, "**", "*.jsonl"), recursive=True):
        try:
            f = open(path, encoding="utf-8")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                u = (o.get("message") or {}).get("usage")
                ts = o.get("timestamp") or ""
                if not u or not ts:
                    continue
                for key, (start, end) in items:
                    if start <= ts <= end:
                        acc = out[key]
                        acc["input"] += u.get("input_tokens", 0)
                        acc["output"] += u.get("output_tokens", 0)
                        acc["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                        acc["cache_read"] += u.get("cache_read_input_tokens", 0)
                        break
    for acc in out.values():
        acc["total"] = sum(acc.values())
    return out


def token_usage(date: str) -> dict | None:
    """Grand-total token usage for the run (tip to tail, incl. subagents and cache), or None if
    no token window was recorded or the transcripts are absent on this machine."""
    win = _load_json(window_path(date))
    if not win or not win.get("start"):
        return None
    start, end = win["start"], win.get("end") or _now_utc_iso()
    scanned = _scan_windows({date: (start, end)})
    if not scanned:
        return None
    usage = scanned[date]
    usage["window"] = {"start": start, "end": end}
    return usage


def build_report(date: str) -> dict:
    """Full single-run report: cheap metrics + grand-total token usage + tokens/word."""
    report = _cheap_report(date)
    tokens = token_usage(date)
    report["tokens"] = tokens
    words = report["words_total"]
    report["tokens_per_word"] = (tokens["total"] / words) if (tokens and words) else None
    return report


# --- N-day trend --------------------------------------------------------------

def _recent_dates(date: str, n: int) -> list:
    """Up to ``n`` run dates on or before ``date`` that have a run.json, newest first."""
    try:
        names = os.listdir(config.RUNS_DIR)
    except FileNotFoundError:
        return []
    dates = [d for d in names if _DATE_DIR_RE.match(d) and d <= date
             and os.path.exists(os.path.join(config.RUNS_DIR, d, "run.json"))]
    return sorted(dates, reverse=True)[:n]


def build_history(date: str, n: int) -> list:
    """Compact per-day summaries for the last ``n`` runs (newest first), including tokens/word.
    One transcript pass covers every day's window."""
    dates = _recent_dates(date, n)
    if not dates:
        return []
    summaries = {d: _cheap_report(d) for d in dates}
    windows = {}
    for d in dates:
        win = _load_json(window_path(d))
        if win and win.get("start"):
            windows[d] = (win["start"], win.get("end") or _now_utc_iso())
    tokens = _scan_windows(windows) if windows else {}
    rows = []
    for d in dates:
        s = summaries[d]
        t = s["totals"]
        tok = tokens.get(d, {}).get("total") if d in tokens else None
        words = s["words_total"]
        rows.append({
            "date": d, "approved": t["approved"], "skipped": t["skipped"],
            "deep_dives_fired": t["deep_dives_fired"], "under_floor": t["under_floor"],
            "written": t["written"], "soft_support_flags": t["soft_support_flags"],
            "words": words, "tokens_total": tok,
            "tokens_per_word": (tok / words) if (tok and words) else None,
        })
    return rows


# --- formatting ---------------------------------------------------------------

def _fmt_tokens(tokens: dict, words: int, per_word) -> list:
    m = tokens
    return [
        "grand total token usage — tip to tail (incl. subagents + cache):",
        f"  input {m['input']:,}   output {m['output']:,}   "
        f"cache_creation {m['cache_creation']:,}   cache_read {m['cache_read']:,}",
        f"  TOTAL {m['total']:,} tokens  /  {words:,} words  =  "
        f"{per_word:,.0f} tokens/word" if per_word else f"  TOTAL {m['total']:,} tokens",
    ]


def format_report(report: dict) -> str:
    lines = [f"run {report['date']} — novelty: {report['novelty']}", ""]
    hdr = (f"{'prompt':24} {'status':9} {'dive':5} {'facts':5} {'contra':6} "
           f"{'words':6} {'<flr':5} {'soft':4} {'ovr':3}")
    lines += [hdr, "-" * len(hdr)]
    for r in report["prompts"]:
        lines.append(
            f"{r['id']:24} {str(r['status']):9} "
            f"{('yes' if r['deep_dive'] else '-'):5} "
            f"{r['deep_facts']:5} {r['contradictions']:6} "
            f"{r['words']:6} {('UNDER' if r['under_floor'] else '-'):5} "
            f"{r['soft_support_flags']:4} "
            f"{str(r['review_overall'] if r['review_overall'] is not None else '-'):3}"
        )
    t = report["totals"]
    lines += ["", (f"{t['prompts']} prompts: {t['approved']} approved, {t['skipped']} skipped, "
                   f"{t['failed']} failed  |  deep dives fired: {t['deep_dives_fired']}, "
                   f"contradictions: {t['contradictions_found']}  |  under floor: "
                   f"{t['under_floor']}/{t['written']}  |  soft-support flags: "
                   f"{t['soft_support_flags']}")]
    lines.append("")
    if report.get("tokens"):
        lines += _fmt_tokens(report["tokens"], report["words_total"], report["tokens_per_word"])
    else:
        lines.append("grand total token usage: n/a (no runs/<date>/token_window.json for this run)")
    return "\n".join(lines)


def format_history(rows: list) -> str:
    lines = [f"trend — last {len(rows)} runs (newest first):", ""]
    hdr = (f"{'date':12} {'appr':4} {'skip':4} {'dive':4} {'<flr':4} {'soft':4} "
           f"{'words':6} {'tokens':>14} {'tok/word':>9}")
    lines += [hdr, "-" * len(hdr)]
    for r in rows:
        tok = f"{r['tokens_total']:,}" if r["tokens_total"] is not None else "n/a"
        tpw = f"{r['tokens_per_word']:,.0f}" if r["tokens_per_word"] is not None else "n/a"
        lines.append(
            f"{r['date']:12} {r['approved']:4} {r['skipped']:4} {r['deep_dives_fired']:4} "
            f"{r['under_floor']:4} {r['soft_support_flags']:4} {r['words']:6} {tok:>14} {tpw:>9}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    config.configure_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--json", action="store_true", help="emit this run's data as JSON")
    ap.add_argument("--history", type=int, default=5,
                    help="days of trend to append (0 = none; text mode only)")
    ap.add_argument("--start", action="store_true", help="stamp token-window start (idempotent)")
    ap.add_argument("--end", action="store_true", help="stamp token-window end")
    args = ap.parse_args(argv)

    if args.start or args.end:
        if args.start:
            mark_window(args.date, "start")
        if args.end:
            mark_window(args.date, "end")
        print(f"token window for {args.date}: {_load_json(window_path(args.date))}")
        return 0

    try:
        report = build_report(args.date)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(format_report(report))
    if args.history:
        hist = build_history(args.date, args.history)
        if len(hist) > 1:
            print("\n" + format_history(hist))
    return 0


if __name__ == "__main__":
    sys.exit(main())
