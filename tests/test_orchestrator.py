"""Unit tests for orchestrator.py — the deterministic gates of the three-agent pipeline.

Agent execution is mocked by writing the artifact files directly (the agents are
Claude Code subagents; the gates are what must be airtight)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import orchestrator  # noqa: E402
import publish_feed  # noqa: E402

DATE = "2026-07-16"


def _valid_research(pid="a"):
    return {
        "prompt_id": pid, "run_date": DATE, "status": "complete",
        "lead_candidates": [{
            "title": "Something happened", "event_date": DATE,
            "summary": "It happened.", "why_it_matters": "It matters.",
            "sources": [{"title": "Filing", "url": "https://x", "source_type": "primary"}],
            "important_facts": ["fact"], "uncertainties": [],
            "possible_second_order_effects": [], "importance_score": 8,
        }],
        "secondary_items": [], "items_to_ignore": [], "research_gaps": [],
    }


def _valid_plan(pid="a", decision="write"):
    doc = {"prompt_id": pid, "run_date": DATE, "decision": decision,
           "decision_reason": "reason", "central_thesis": "thesis",
           "lead_story": "Something happened",
           "approved_items": [{"research_item": "Something happened", "treatment": "lead",
                               "reason": "strongest", "skeptical_note": "", "required_caveats": []}],
           "rejected_items": [], "required_arguments": [], "required_second_order_effects": [],
           "recommended_structure": ["lead", "close"],
           "material_repeated_from_prior_briefings": []}
    if decision == "skip":
        doc.update(central_thesis="", lead_story="", approved_items=[], recommended_structure=[])
    return doc


def _valid_review(pid="a", decision="approve"):
    return {"prompt_id": pid, "run_date": DATE, "decision": decision,
            "decision_reason": "solid" if decision == "approve" else "weak",
            "scores": {"novelty": 8, "factual_support": 8, "analytical_depth": 8,
                       "editorial_quality": 8, "audio_flow": 8, "prompt_compliance": 8,
                       "overall": 8},
            "issues_found": [], "changes_made": []}


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        self._write_prompts([
            {"id": "a", "name": "A", "prompt": "p", "enabled": True,
             "last_episode_uri": None, "last_published": None},
            {"id": "b", "name": "B", "prompt": "p", "enabled": True,
             "last_episode_uri": None, "last_published": None},
            {"id": "syn", "name": "Synth", "prompt": "p", "enabled": True, "kind": "synthesis",
             "last_episode_uri": None, "last_published": None},
            {"id": "off", "name": "Off", "prompt": "p", "enabled": False,
             "last_episode_uri": None, "last_published": None},
        ])

    def tearDown(self):
        config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_prompts(self, prompts):
        data = {"version": 1, "show_id": "spotify:show:x", "prompts": prompts, "orphans": []}
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _write_artifact(self, pid, name, doc):
        path = os.path.join(orchestrator.prompt_dir(DATE, pid), name)
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(doc, str):
                f.write(doc)
            else:
                json.dump(doc, f)
        return path

    # --- init (spec tests 1, 4, 13, 14) --------------------------------------

    def test_init_creates_dirs_synthesis_last_disabled_dropped(self):
        plan = orchestrator.init_run(DATE, "strict")
        ids = [p["id"] for p in plan["prompts"]]
        self.assertEqual(ids, ["a", "b", "syn"])  # off dropped, synthesis last
        for pid in ids:
            self.assertTrue(os.path.isdir(orchestrator.prompt_dir(DATE, pid)))
        state = orchestrator.load_state(DATE)
        self.assertEqual(state["novelty"], "strict")
        self.assertTrue(all(e["status"] == "pending" for e in state["prompts"]))
        self.assertEqual(state["prompts"][2]["kind"], "synthesis")

    def test_init_records_novelty_mode(self):
        orchestrator.init_run(DATE, "relaxed")  # the -RepeatOK path
        self.assertEqual(orchestrator.load_state(DATE)["novelty"], "relaxed")
        with self.assertRaises(ValueError):
            orchestrator.init_run(DATE, "whatever")

    def test_reinit_same_day_preserves_statuses(self):
        orchestrator.init_run(DATE, "strict")
        orchestrator.mark("a", DATE, "failed", "research", "web search down")
        plan = orchestrator.init_run(DATE, "strict")  # idempotent resume
        state = orchestrator.load_state(DATE)
        entry = next(e for e in state["prompts"] if e["id"] == "a")
        self.assertEqual((entry["status"], entry["stage"]), ("failed", "research"))
        self.assertEqual([p["id"] for p in plan["prompts"]], ["a", "b", "syn"])

    # --- validation (spec tests 2, 3, 5, 6) ------------------------------------

    def test_validate_research_good_and_bad(self):
        self.assertEqual(orchestrator.validate_research(_valid_research()), [])
        insufficient = {**_valid_research(), "status": "insufficient", "lead_candidates": []}
        self.assertEqual(orchestrator.validate_research(insufficient), [])  # allowed, no padding
        bad = _valid_research()
        del bad["lead_candidates"]
        bad["status"] = "amazing"
        problems = orchestrator.validate_research(bad)
        self.assertTrue(any("lead_candidates" in p for p in problems))
        self.assertTrue(any("status" in p for p in problems))
        empty_complete = {**_valid_research(), "lead_candidates": []}
        self.assertTrue(orchestrator.validate_research(empty_complete))

    def test_validate_plan_write_and_skip(self):
        self.assertEqual(orchestrator.validate_plan(_valid_plan(decision="write")), [])
        self.assertEqual(orchestrator.validate_plan(_valid_plan(decision="skip")), [])
        bad = _valid_plan()
        bad["approved_items"] = []
        bad["central_thesis"] = ""
        problems = orchestrator.validate_plan(bad)
        self.assertTrue(any("approved item" in p for p in problems))
        self.assertTrue(any("central_thesis" in p for p in problems))

    def test_validate_review_good_and_bad(self):
        self.assertEqual(orchestrator.validate_review(_valid_review()), [])
        bad = _valid_review()
        bad["decision"] = "publish"
        del bad["scores"]["overall"]
        problems = orchestrator.validate_review(bad)
        self.assertTrue(any("decision" in p for p in problems))
        self.assertTrue(any("scores.overall" in p for p in problems))

    def test_validate_file_handles_missing_and_broken_json(self):
        orchestrator.init_run(DATE, "strict")
        self.assertTrue(orchestrator.validate_file("research", "nope.json"))
        path = self._write_artifact("a", "research.json", "{not json")
        problems = orchestrator.validate_file("research", path)
        self.assertTrue(any("invalid JSON" in p for p in problems))

    # --- approve gate (spec tests 7, 8, 9, 15) ---------------------------------

    def _briefing(self, pid):
        return os.path.join(config.BRIEFINGS_DIR, pid + ".txt")

    def test_approve_copies_only_on_approve(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", "Good morning. The script.\n")
        dest = orchestrator.approve("a", DATE)
        self.assertEqual(dest, self._briefing("a"))
        with open(dest, encoding="utf-8") as f:
            self.assertIn("The script", f.read())
        state = orchestrator.load_state(DATE)
        self.assertEqual(next(e for e in state["prompts"] if e["id"] == "a")["status"], "approved")
        # the approved copy is what --require-fresh publishing selects
        self.assertTrue(publish_feed._fresh_today(dest, orchestrator.datetime.date.today().isoformat()))

    def test_approve_refuses_without_review(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "final.txt", "script")
        with self.assertRaises(RuntimeError):
            orchestrator.approve("a", DATE)
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_refuses_on_skip_or_failed_review(self):
        orchestrator.init_run(DATE, "strict")
        for decision in ("skip", "failed"):
            self._write_artifact("a", "review.json", _valid_review(decision=decision))
            self._write_artifact("a", "final.txt", "script")
            with self.assertRaises(RuntimeError):
                orchestrator.approve("a", DATE)
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_refuses_on_missing_or_empty_final(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        with self.assertRaises(RuntimeError):
            orchestrator.approve("a", DATE)
        self._write_artifact("a", "final.txt", "   \n")
        with self.assertRaises(RuntimeError):
            orchestrator.approve("a", DATE)
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_same_day_rerun_overwrites_in_place(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", "version one")
        orchestrator.approve("a", DATE)
        self._write_artifact("a", "final.txt", "version two")
        orchestrator.approve("a", DATE)  # idempotent re-run, no duplicate files
        with open(self._briefing("a"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "version two")

    # --- batch independence + status (spec tests 10, 11) -----------------------

    def test_one_failed_prompt_does_not_affect_others(self):
        orchestrator.init_run(DATE, "strict")
        orchestrator.mark("a", DATE, "failed", "research", "all retries exhausted")
        self._write_artifact("b", "review.json", _valid_review(pid="b"))
        self._write_artifact("b", "final.txt", "b script")
        orchestrator.approve("b", DATE)
        summary = orchestrator.run_status(DATE)
        by_id = {e["id"]: e["status"] for e in summary["prompts"]}
        self.assertEqual(by_id["a"], "failed")
        self.assertEqual(by_id["b"], "approved")
        self.assertEqual(summary["approved"], ["b"])

    def test_skip_is_recorded_and_never_touches_briefings(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "editorial_plan.json", _valid_plan(decision="skip"))
        orchestrator.mark("a", DATE, "skipped", "plan", "nothing materially new")
        self.assertFalse(os.path.exists(self._briefing("a")))
        self.assertEqual(orchestrator.run_status(DATE)["approved"], [])

    def test_orchestrator_writes_only_runs_and_briefings(self):
        """Dry-run guarantee: the gates never touch docs/, feed_state, or git."""
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", "script")
        orchestrator.approve("a", DATE)
        top = set(os.listdir(self.tmp))
        self.assertEqual(top, {"prompts.json", "runs", "briefings"})

    # --- CLI exit codes ---------------------------------------------------------

    def test_cli_validate_and_approve_exit_codes(self):
        orchestrator.init_run(DATE, "strict")
        good = self._write_artifact("a", "research.json", _valid_research())
        self.assertEqual(orchestrator.main(["validate", "research", good]), 0)
        bad = self._write_artifact("a", "bad.json", {"prompt_id": "a"})
        self.assertEqual(orchestrator.main(["validate", "research", bad]), 1)
        self.assertEqual(orchestrator.main(["approve", "a", "--date", DATE]), 1)  # no review yet
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", "script")
        self.assertEqual(orchestrator.main(["approve", "a", "--date", DATE]), 0)
        self.assertEqual(orchestrator.main(["status", "--date", DATE, "--json"]), 0)
        self.assertEqual(orchestrator.main(["status", "--date", "1999-01-01"]), 1)


if __name__ == "__main__":
    unittest.main()
