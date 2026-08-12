"""Ad-hoc verification of the freshness/embargo gate (#2) and source-verify (#1).
Run: conda run -n Spotify --no-capture-output python mtest/test_freshness_check.py
"""
import datetime, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import freshness, orchestrator

NOW = datetime.datetime.fromisoformat("2026-08-12T05:15:00-04:00")  # before the 08:30 CPI release
cal = freshness.load_calendar()
fails = []

def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        fails.append(name)

# 1. Calendar loads and CPI is pending at 05:15
check("calendar loads >=1 release", len(cal) >= 1)
check("CPI pending at 05:15", any("cpi" in r["keywords"] for r in freshness.pending_releases(NOW, cal)))

# 2. The REAL fabricated deep_research.json from today's incident must be flagged
real = "runs/2026-08-12/capital-markets-radar/deep_research.json"
if os.path.exists(real):
    doc = json.load(open(real, encoding="utf-8"))
    errs = orchestrator.validate_research(doc, run_now=NOW)
    pend = [e for e in errs if "has not occurred" in e]
    print("  -> incident errors:", len(errs), "| pre-release:", len(pend))
    if pend:
        print("     e.g.", pend[0][:140])
    check("incident deep_research.json flagged as pre-release fabrication", bool(pend))
else:
    print("SKIP incident file not present:", real)

# 3. Unhedged realized figure is caught; hedged/consensus figure is allowed
caught = freshness.fact_reports_pending_release("Core CPI came in at 3.1 percent in July", NOW, cal)
allowed = freshness.fact_reports_pending_release("Consensus expects core CPI near 2.5 percent", NOW, cal)
schedule = freshness.fact_reports_pending_release("The July CPI report is due at 8:30 Eastern", NOW, cal)
check("unhedged 'came in at 3.1 percent' flagged", caught is not None)
check("hedged 'consensus expects ... 2.5 percent' allowed", allowed is None)
check("schedule mention (no figure) allowed", schedule is None)

# 4. Nothing pending after the release -> gate silent
after = datetime.datetime.fromisoformat("2026-08-12T09:00:00-04:00")
check("after release: same figure not flagged", freshness.fact_reports_pending_release(
    "Core CPI came in at 3.1 percent in July", after, cal) is None)

# 5. Future-dated URL detection (run 2026-08-12)
ref = datetime.date(2026, 8, 12)
check("future URL slug flagged", freshness.url_date_tokens_after(
    "https://x.com/news/cpi-report-20260813", ref) == datetime.date(2026, 8, 13))
check("same-day URL slug allowed", freshness.url_date_tokens_after(
    "https://x.com/news/cpi-20260812", ref) is None)
check("past URL slug allowed", freshness.url_date_tokens_after(
    "https://x.com/news/cpi-20250812", ref) is None)

# 6. run_context text mentions the pending CPI
ctx = freshness.run_context_text(NOW, cal)
check("run_context lists pending CPI", "CPI" in ctx and "NOT" in ctx)

# 7. verify_sources: deterministic future-dated branch (no network) via a synthetic doc
tmp = "mtest/_synthetic_research.json"
json.dump({"prompt_id": "t", "run_date": "2026-08-12", "status": "complete",
           "lead_candidates": [{"title": "t", "summary": "s", "sources": [],
             "important_facts": [{"fact": "f", "quote": "q",
               "source_url": "https://x.com/a-20260814"}]}],
           "secondary_items": [], "items_to_ignore": [], "research_gaps": []},
          open(tmp, "w", encoding="utf-8"))
probs, checked = orchestrator.verify_sources(tmp, write=False)
check("verify-sources flags future-dated source (no network)",
      any(p["severity"] == "error" and "future" in p["issue"] for p in probs))
os.remove(tmp)

print("\n%d passed, %d failed" % (14 - len(fails), len(fails)))
sys.exit(1 if fails else 0)
