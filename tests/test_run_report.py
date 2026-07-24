"""Unit tests for run_report.py — deterministic per-run metrics."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import orchestrator  # noqa: E402
import run_report  # noqa: E402

DATE = "2026-07-16"


class StatedFloorTest(unittest.TestCase):
    def test_range_form(self):
        self.assertEqual(run_report.stated_floor("Make a 1200 to 1500 word briefing on X."), 1200)

    def test_synthesis_form(self):
        self.assertEqual(run_report.stated_floor("...aim 800 to 1000 words. Do not..."), 800)

    def test_default_when_absent(self):
        self.assertEqual(run_report.stated_floor("no length stated here"), run_report.DEFAULT_FLOOR)


def _review(pid, issues):
    return {"prompt_id": pid, "run_date": DATE, "decision": "approve",
            "decision_reason": "solid", "issues_found": issues, "changes_made": [],
            "scores": {"novelty": 8, "factual_support": 8, "analytical_depth": 8,
                       "editorial_quality": 8, "audio_flow": 8, "prompt_compliance": 8,
                       "overall": 8}}


def _deep(pid, n_facts, n_contra):
    return {"prompt_id": pid, "run_date": DATE, "status": "complete",
            "lead_candidates": [{
                "title": "Deepened item", "summary": "s", "sources": [],
                "important_facts": [{"fact": f"f{i}", "quote": f"q{i}", "source_url": "u"}
                                    for i in range(n_facts)]}],
            "secondary_items": [], "items_to_ignore": [], "research_gaps": [],
            "contradictions": [{"plan_claim": "c", "evidence": "e"} for _ in range(n_contra)]}


class RunReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_id": "x", "orphans": "", "prompts": [
                {"id": "a", "name": "A", "prompt": "Make a 1200 to 1500 word briefing.",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
                {"id": "syn", "name": "Syn", "prompt": "aim 800 to 1000 words", "kind": "synthesis",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
            ]}, f)
        orchestrator.init_run(DATE, "strict")

    def tearDown(self):
        config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _artifact(self, pid, name, doc):
        path = os.path.join(orchestrator.prompt_dir(DATE, pid), name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f) if name.endswith(".json") else f.write(doc)

    def _approve(self, pid, words):
        self._artifact(pid, "review.json", _review(pid, [
            "Figure audit: the value appears only in the summary prose, not a verbatim quote.",
            "Minor: rounded 4.707 to 4.71 for the ear."]))
        self._artifact(pid, "final.txt", " ".join(["word"] * words))
        orchestrator.approve(pid, DATE)

    def test_metrics_with_and_without_deep_dive(self):
        # "a": deep dive fired (3 facts, 2 contradictions), 5-word final -> under the 1200 floor,
        # one soft-support issue of two.
        self._artifact("a", orchestrator.DEEP_FILE, _deep("a", n_facts=3, n_contra=2))
        self._approve("a", words=5)
        # "syn": no deep dive, 900-word final -> at/above its 800 floor.
        self._approve("syn", words=900)

        report = run_report.build_report(DATE)
        rows = {r["id"]: r for r in report["prompts"]}

        a = rows["a"]
        self.assertTrue(a["deep_dive"])
        self.assertEqual((a["deep_facts"], a["contradictions"]), (3, 2))
        self.assertEqual((a["words"], a["floor"]), (5, 1200))
        self.assertTrue(a["under_floor"])
        self.assertEqual(a["soft_support_flags"], 1)
        self.assertEqual(a["review_overall"], 8)

        syn = rows["syn"]
        self.assertFalse(syn["deep_dive"])
        self.assertEqual(syn["floor"], 800)
        self.assertFalse(syn["under_floor"])

        t = report["totals"]
        self.assertEqual((t["approved"], t["deep_dives_fired"], t["contradictions_found"]), (2, 1, 2))
        # both reviews carry the soft-support phrase (shared _approve), so the batch sum is 2.
        self.assertEqual((t["under_floor"], t["written"], t["soft_support_flags"]), (1, 2, 2))

    def test_report_survives_missing_artifacts(self):
        # No artifacts written at all: metrics degrade gracefully (pending, zeros), no crash.
        report = run_report.build_report(DATE)
        self.assertEqual(report["totals"]["prompts"], 2)
        self.assertTrue(all(r["words"] == 0 and not r["deep_dive"] for r in report["prompts"]))
        # format_report must not raise on Nones (review_overall absent).
        self.assertIn("run 2026-07-16", run_report.format_report(report))

    def test_json_mode_shape(self):
        self._approve("a", words=1300)
        self._approve("syn", words=900)
        rc = run_report.main(["--date", DATE, "--json"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
