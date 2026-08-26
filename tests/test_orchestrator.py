"""Unit tests for orchestrator.py — the deterministic gates of the four-stage pipeline.

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
            "important_facts": [{"fact": "X rose 10%",
                                 "quote": "X rose 10 percent in the quarter.",
                                 "source_url": "https://x"}],
            "uncertainties": [],
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
           "material_repeated_from_prior_briefings": [], "deep_dive_requests": []}
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


def _valid_final_check(pid="a", verdict="pass", defects=None, rnd=1):
    if defects is None:
        defects = [] if verdict == "pass" else [{
            "severity": "hard", "kind": "clarity", "quote": "a 62-word sentence",
            "why": "unfollowable on one listen", "fix": "split at 'and because'"}]
    return {"prompt_id": pid, "run_date": DATE, "revision_round": rnd, "verdict": verdict,
            "listener_question": "what changed in semis today?",
            "answer_heard": "a warrant restructured a supply relationship",
            "answered": True,
            "scores": {"clarity": 8, "listenability": 7, "payoff": 8},
            "defects": defects, "verdict_reason": "clears the bar on one hearing"}


def _script(marker: str = "one") -> str:
    """A well-formed ~760-word script for approve() fixtures.

    approve() now runs the deterministic script gate over final.txt, so a 4-word stub no longer
    reaches the copy step. ``marker`` keeps otherwise-identical fixtures distinguishable.
    """
    body = "The desk repriced that trade today. " * 10
    paras = [f"Good morning. The script covers {marker} today."] + [body.strip()] * 14
    return "\n\n".join(paras) + "\n"


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

    def test_init_writes_prompt_txt_for_each_prompt(self):
        # The standing prompt is persisted so agents read it from disk instead of the parent
        # embedding it in every dispatch (the "orchestration" token cost lever).
        self._write_prompts([{"id": "a", "name": "A", "prompt": "Full standing prompt text.",
                              "enabled": True, "last_episode_uri": None, "last_published": None}])
        plan = orchestrator.init_run(DATE, "strict")
        path = os.path.join(orchestrator.prompt_dir(DATE, "a"), orchestrator.PROMPT_FILE)
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "Full standing prompt text.")
        self.assertEqual(plan["prompts"][0]["artifacts"]["prompt"], path)

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

    def test_validate_research_quote_contract(self):
        # object-form facts with fact+quote pass (the fixture)
        self.assertEqual(orchestrator.validate_research(_valid_research()), [])
        # legacy string-form facts are rejected
        legacy = _valid_research()
        legacy["lead_candidates"][0]["important_facts"] = ["a bare string fact"]
        problems = orchestrator.validate_research(legacy)
        self.assertTrue(any("verbatim-quote contract" in p for p in problems))
        # a fact object missing/blank quote is rejected
        unquoted = _valid_research()
        unquoted["lead_candidates"][0]["important_facts"] = [
            {"fact": "X rose 10%", "quote": "  ", "source_url": "https://x"}]
        problems = orchestrator.validate_research(unquoted)
        self.assertTrue(any("quote" in p for p in problems))
        # empty important_facts on a lead is tolerated (facts may be thin, not fabricated)
        thin = _valid_research()
        thin["lead_candidates"][0]["important_facts"] = []
        self.assertEqual(orchestrator.validate_research(thin), [])
        # insufficient dossiers are exempt (no leads to check)
        insufficient = {**_valid_research(), "status": "insufficient", "lead_candidates": []}
        self.assertEqual(orchestrator.validate_research(insufficient), [])

    def test_validate_plan_write_and_skip(self):
        self.assertEqual(orchestrator.validate_plan(_valid_plan(decision="write")), [])
        self.assertEqual(orchestrator.validate_plan(_valid_plan(decision="skip")), [])
        bad = _valid_plan()
        bad["approved_items"] = []
        bad["central_thesis"] = ""
        problems = orchestrator.validate_plan(bad)
        self.assertTrue(any("approved item" in p for p in problems))
        self.assertTrue(any("central_thesis" in p for p in problems))

    def test_validate_plan_deep_dive_requests(self):
        # a well-formed request naming an approved item passes
        ok = _valid_plan()
        ok["deep_dive_requests"] = [{"research_item": "Something happened",
                                     "questions": ["How much supply cleared?", "Primary source?"]}]
        self.assertEqual(orchestrator.validate_plan(ok), [])
        # the key is required — the analyst must consciously decide, even if the answer is "none"
        missing = _valid_plan()
        del missing["deep_dive_requests"]
        self.assertTrue(any("deep_dive_requests" in p
                            for p in orchestrator.validate_plan(missing)))
        # bounds: at most one request, at most three questions
        too_many = _valid_plan()
        too_many["deep_dive_requests"] = [
            {"research_item": "Something happened", "questions": ["q"]},
            {"research_item": "Something happened", "questions": ["q"]}]
        self.assertTrue(any("max is 1" in p for p in orchestrator.validate_plan(too_many)))
        wordy = _valid_plan()
        wordy["deep_dive_requests"] = [{"research_item": "Something happened",
                                        "questions": ["a", "b", "c", "d"]}]
        self.assertTrue(any("max is 3" in p for p in orchestrator.validate_plan(wordy)))
        # cannot deep-dive an item that was never approved
        unapproved = _valid_plan()
        unapproved["deep_dive_requests"] = [{"research_item": "Rejected story",
                                             "questions": ["why?"]}]
        self.assertTrue(any("does not match any approved item" in p
                            for p in orchestrator.validate_plan(unapproved)))
        # an empty questions list is a malformed request, not a no-op
        blank = _valid_plan()
        blank["deep_dive_requests"] = [{"research_item": "Something happened", "questions": []}]
        self.assertTrue(any("omit the request instead" in p
                            for p in orchestrator.validate_plan(blank)))
        # a skipped plan has no script to support, so it must not commission research
        skipped = _valid_plan(decision="skip")
        skipped["deep_dive_requests"] = [{"research_item": "Something happened",
                                          "questions": ["why?"]}]
        self.assertTrue(any("must not request a deep dive" in p
                            for p in orchestrator.validate_plan(skipped)))

    def test_validate_deep_reuses_the_research_quote_contract(self):
        # deep_research.json shares research.json's schema, so "deep" validates identically
        orchestrator.init_run(DATE, "strict")
        path = self._write_artifact("a", "deep_research.json", _valid_research())
        self.assertEqual(orchestrator.validate_file("deep", path), [])
        unquoted = _valid_research()
        unquoted["lead_candidates"][0]["important_facts"] = [
            {"fact": "X rose 10%", "quote": "", "source_url": "https://x"}]
        path = self._write_artifact("b", "deep_research.json", unquoted)
        self.assertTrue(any("quote" in p for p in orchestrator.validate_file("deep", path)))

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

    def _pass_final_check(self, pid="a"):
        """Satisfy stage 5 so tests targeting the earlier gates can reach the copy."""
        return self._write_artifact(pid, "final_check.json", _valid_final_check(pid))

    def test_approve_copies_only_on_approve(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", _script())
        self._pass_final_check("a")
        dest = orchestrator.approve("a", DATE)
        self.assertEqual(dest, self._briefing("a"))
        with open(dest, encoding="utf-8") as f:
            self.assertIn("The script", f.read())
        state = orchestrator.load_state(DATE)
        self.assertEqual(next(e for e in state["prompts"] if e["id"] == "a")["status"], "approved")
        # the approved copy is what --require-fresh publishing selects
        self.assertTrue(publish_feed._fresh_for_run(dest, orchestrator.datetime.date.today().isoformat()))

    def test_approve_refuses_without_review(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "final.txt", _script())
        with self.assertRaises(RuntimeError):
            orchestrator.approve("a", DATE)
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_refuses_on_skip_or_failed_review(self):
        orchestrator.init_run(DATE, "strict")
        for decision in ("skip", "failed"):
            self._write_artifact("a", "review.json", _valid_review(decision=decision))
            self._write_artifact("a", "final.txt", _script())
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
        self._write_artifact("a", "final.txt", _script("version one"))
        self._pass_final_check("a")
        orchestrator.approve("a", DATE)
        self._write_artifact("a", "final.txt", _script("version two"))
        self._pass_final_check("a")  # the rewritten script must be re-read
        orchestrator.approve("a", DATE)  # idempotent re-run, no duplicate files
        with open(self._briefing("a"), encoding="utf-8") as f:
            shipped = f.read()
        self.assertIn("version two", shipped)
        self.assertNotIn("version one", shipped)

    # --- batch independence + status (spec tests 10, 11) -----------------------

    def test_one_failed_prompt_does_not_affect_others(self):
        orchestrator.init_run(DATE, "strict")
        orchestrator.mark("a", DATE, "failed", "research", "all retries exhausted")
        self._write_artifact("b", "review.json", _valid_review(pid="b"))
        self._write_artifact("b", "final.txt", _script("b"))
        self._pass_final_check("b")
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
        self._write_artifact("a", "final.txt", _script())
        self._pass_final_check("a")
        orchestrator.approve("a", DATE)
        top = set(os.listdir(self.tmp))
        self.assertEqual(top, {"prompts.json", "runs", "briefings"})

    # --- script gate (the write stage had no validator at all before) -----------

    def test_stage_chain_validates_the_draft(self):
        """_stage_chain passed None for the write stage, so draft.txt was never checked."""
        for kind in ("normal", "synthesis"):
            chain = dict((stage, vkind) for stage, _paths, vkind in
                         orchestrator._stage_chain(DATE, "a", kind))
            self.assertEqual(chain["write"], "script", kind)

    def test_validate_script_cli_and_dispatch(self):
        orchestrator.init_run(DATE, "strict")
        clean = self._write_artifact("a", "draft.txt", _script())
        self.assertEqual(orchestrator.validate_file("script", clean, write=False), [])
        self.assertEqual(orchestrator.main(["validate", "script", clean]), 0)
        leaky = self._write_artifact("b", "draft.txt",
                                     "Good morning. The dossier puts it near ten billion.")
        self.assertTrue(orchestrator.validate_file("script", leaky, write=False))
        self.assertEqual(orchestrator.main(["validate", "script", leaky]), 1)

    def test_validate_script_writes_metrics_sidecar(self):
        orchestrator.init_run(DATE, "strict")
        path = self._write_artifact("a", "final.txt", _script())
        orchestrator.validate_script_file(path, write=True)
        side = orchestrator.script_check_path(path)
        self.assertTrue(os.path.exists(side))
        with open(side, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertIn("metrics", doc)
        self.assertEqual(doc["stage"], "final")

    def test_resume_does_not_write_sidecars(self):
        """A resume walk is read-only — it must not litter the tree with metrics files."""
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "research.json", _valid_research())
        self._write_artifact("a", "editorial_plan.json", _valid_plan())
        draft = self._write_artifact("a", "draft.txt", _script())
        state = orchestrator.load_state(DATE)
        orchestrator.resume_for_prompt(DATE, orchestrator._find_entry(state, "a"))
        self.assertFalse(os.path.exists(orchestrator.script_check_path(draft)))

    def test_approve_refuses_a_script_that_fails_the_gate(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        # A stage direction the reviewer waved through — the real 2026-08-24 failure mode.
        self._write_artifact("a", "final.txt",
                             _script() + "\nSay clearly that these are letters of intent.")
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("script gate", str(ctx.exception))
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_refuses_an_under_floor_final(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", "Good morning. Far too short.")
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("under_floor", str(ctx.exception))

    # --- source_check filename collision (research.json and deep share a directory) ----

    def test_source_check_paths_do_not_collide(self):
        research = orchestrator.source_check_path("runs/D/id/research.json")
        deep = orchestrator.source_check_path("runs/D/id/deep_research.json")
        self.assertNotEqual(research, deep)
        self.assertTrue(os.path.basename(research).startswith("research"))
        self.assertTrue(os.path.basename(deep).startswith("deep_research"))

    # --- mark --stage deep was rejected because STAGES omitted it ---------------

    def test_mark_accepts_the_deep_stage(self):
        orchestrator.init_run(DATE, "strict")
        orchestrator.mark("a", DATE, "failed", "deep", "dive came back unusable")
        entry = orchestrator._find_entry(orchestrator.load_state(DATE), "a")
        self.assertEqual((entry["status"], entry["stage"]), ("failed", "deep"))

    # --- stage 5: the fresh final reader ----------------------------------------

    def test_final_check_is_in_both_chains(self):
        """Synthesis prompts especially — they have no research or plan gate at all."""
        for kind in ("normal", "synthesis"):
            chain = [stage for stage, _p, _v in orchestrator._stage_chain(DATE, "a", kind)]
            self.assertEqual(chain[-1], "final_check", kind)

    def test_validate_final_check_schema(self):
        v = orchestrator.validate_final_check
        self.assertEqual(v(_valid_final_check()), [])
        self.assertTrue(v(_valid_final_check(verdict="nope")))
        bad = _valid_final_check()
        bad["scores"]["clarity"] = 11
        self.assertTrue(v(bad))
        bad = _valid_final_check()
        bad["revision_round"] = orchestrator.MAX_FINAL_CHECK_ROUNDS + 1
        self.assertTrue(v(bad))
        bad = _valid_final_check()
        bad["answer_heard"] = "   "
        self.assertTrue(v(bad))

    def test_revise_needs_a_named_hard_defect(self):
        """No vetoing a script without saying what is wrong with it."""
        doc = _valid_final_check(verdict="revise", defects=[])
        self.assertTrue(any("hard" in e for e in orchestrator.validate_final_check(doc)))
        soft_only = _valid_final_check(verdict="revise", defects=[{
            "severity": "soft", "kind": "payoff", "quote": "q", "why": "w", "fix": "f"}])
        self.assertTrue(orchestrator.validate_final_check(soft_only))

    def test_pass_may_not_carry_a_hard_defect(self):
        """The mirror rule: it cannot wave through something it just called broken."""
        doc = _valid_final_check(verdict="pass", defects=[{
            "severity": "hard", "kind": "clarity", "quote": "q", "why": "w", "fix": "f"}])
        self.assertTrue(orchestrator.validate_final_check(doc))

    def test_defects_must_be_actionable(self):
        """Someone else does the rewriting, so an unquoted or fixless defect is useless."""
        doc = _valid_final_check(verdict="revise", defects=[{
            "severity": "hard", "kind": "clarity", "quote": "", "why": "w", "fix": ""}])
        problems = orchestrator.validate_final_check(doc)
        self.assertTrue(any("quote" in e for e in problems))
        self.assertTrue(any("fix" in e for e in problems))

    def _ready_to_approve(self, pid="a"):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact(pid, "review.json", _valid_review(pid=pid))
        self._write_artifact(pid, "final.txt", _script())

    def test_approve_refuses_without_a_final_check(self):
        self._ready_to_approve()
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("final_check.json", str(ctx.exception))
        self.assertFalse(os.path.exists(self._briefing("a")))

    def test_approve_refuses_a_revise_verdict(self):
        """The reviewer approved; the fresh reader did not. The fresh reader wins."""
        self._ready_to_approve()
        self._write_artifact("a", "final_check.json", _valid_final_check(verdict="revise"))
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("revise", str(ctx.exception))

    def test_approve_refuses_a_stale_final_check(self):
        """A reviewer revision must be re-read, not inherited from the pre-revision script."""
        self._ready_to_approve()
        fc = self._write_artifact("a", "final_check.json", _valid_final_check())
        final = os.path.join(orchestrator.prompt_dir(DATE, "a"), "final.txt")
        old = os.path.getmtime(final) - 60
        os.utime(fc, (old, old))
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("older than", str(ctx.exception))

    def test_approve_refuses_an_invalid_final_check(self):
        self._ready_to_approve()
        self._write_artifact("a", "final_check.json", {"prompt_id": "a"})
        with self.assertRaises(RuntimeError) as ctx:
            orchestrator.approve("a", DATE)
        self.assertIn("invalid", str(ctx.exception))

    # --- revision budget (must survive --prune and chunked sessions) -------------

    def test_revision_budget_charges_and_exhausts(self):
        orchestrator.init_run(DATE, "strict")
        first = orchestrator.record_revision("a", DATE, budget=2)
        self.assertEqual((first["count"], first["exhausted"]), (1, False))
        second = orchestrator.record_revision("a", DATE, budget=2)
        self.assertEqual((second["count"], second["exhausted"]), (2, False))
        third = orchestrator.record_revision("a", DATE, budget=2)
        self.assertEqual((third["count"], third["exhausted"]), (3, True))

    def test_default_budget_buys_one_revision_before_exhausting(self):
        """Regression (2026-08-26): the FIRST revise verdict used to exhaust a budget of 1, so the
        revision pass never ran and any single defect killed the episode."""
        orchestrator.init_run(DATE, "strict")
        first = orchestrator.record_revision("a", DATE)
        self.assertFalse(first["exhausted"])          # buys final_check round 2
        second = orchestrator.record_revision("a", DATE)
        self.assertTrue(second["exhausted"])          # a second revise does give up

    def test_revision_budget_lives_in_run_json_so_prune_cannot_reset_it(self):
        orchestrator.init_run(DATE, "strict")
        orchestrator.record_revision("a", DATE)
        # Deleting the artifact (what `resume --prune` does) must not forgive the revision.
        fc = os.path.join(orchestrator.prompt_dir(DATE, "a"), "final_check.json")
        if os.path.exists(fc):
            os.remove(fc)
        entry = orchestrator._find_entry(orchestrator.load_state(DATE), "a")
        self.assertEqual(entry["revisions"]["final_check"], 1)

    def test_revision_cli_exit_three_when_exhausted(self):
        orchestrator.init_run(DATE, "strict")
        self.assertEqual(orchestrator.main(["revision", "a", "--date", DATE, "--budget", "1"]), 0)
        self.assertEqual(orchestrator.main(["revision", "a", "--date", DATE, "--budget", "1"]), 3)

    # --- resume must not point at an unreachable stage 5 ------------------------

    def test_resume_finalizes_when_the_review_did_not_approve(self):
        """A skipped review leaves no script to read, so resume must say finalize, not final_check."""
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "research.json", _valid_research())
        self._write_artifact("a", "editorial_plan.json", _valid_plan())
        self._write_artifact("a", "draft.txt", _script())
        self._write_artifact("a", "review.json", _valid_review(decision="skip"))
        self._write_artifact("a", "final.txt", _script())
        state = orchestrator.load_state(DATE)
        out = orchestrator.resume_for_prompt(DATE, orchestrator._find_entry(state, "a"))
        self.assertEqual(out["resume_stage"], "finalize")

    def test_resume_points_at_final_check_when_it_is_missing(self):
        orchestrator.init_run(DATE, "strict")
        self._write_artifact("a", "research.json", _valid_research())
        self._write_artifact("a", "editorial_plan.json", _valid_plan())
        self._write_artifact("a", "draft.txt", _script())
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", _script())
        state = orchestrator.load_state(DATE)
        out = orchestrator.resume_for_prompt(DATE, orchestrator._find_entry(state, "a"))
        self.assertEqual(out["resume_stage"], "final_check")

    # --- CLI exit codes ---------------------------------------------------------

    def test_cli_validate_and_approve_exit_codes(self):
        orchestrator.init_run(DATE, "strict")
        good = self._write_artifact("a", "research.json", _valid_research())
        self.assertEqual(orchestrator.main(["validate", "research", good]), 0)
        bad = self._write_artifact("a", "bad.json", {"prompt_id": "a"})
        self.assertEqual(orchestrator.main(["validate", "research", bad]), 1)
        self.assertEqual(orchestrator.main(["approve", "a", "--date", DATE]), 1)  # no review yet
        self._write_artifact("a", "review.json", _valid_review())
        self._write_artifact("a", "final.txt", _script())
        self._pass_final_check("a")
        self.assertEqual(orchestrator.main(["approve", "a", "--date", DATE]), 0)
        self.assertEqual(orchestrator.main(["status", "--date", DATE, "--json"]), 0)
        self.assertEqual(orchestrator.main(["status", "--date", "1999-01-01"]), 1)


if __name__ == "__main__":
    unittest.main()
