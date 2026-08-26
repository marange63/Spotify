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
        """Seed an approved prompt with an exactly ``words``-long final.

        Sets run.json status directly instead of calling ``orchestrator.approve``: approve now
        runs the deterministic script gate, which (correctly) rejects the deliberately tiny
        finals these tests use to exercise run_report's ``under_floor`` metric. The approve gate
        has its own coverage in test_orchestrator.
        """
        self._artifact(pid, "review.json", _review(pid, [
            "Figure audit: the value appears only in the summary prose, not a verbatim quote.",
            "Minor: rounded 4.707 to 4.71 for the ear."]))
        self._artifact(pid, "final.txt", " ".join(["word"] * words))
        state = orchestrator.load_state(DATE)
        orchestrator._find_entry(state, pid).update(status="approved", stage=None, reason=None)
        orchestrator._save_state(DATE, state)

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


class TokenAccountingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR,
                       config.CLAUDE_TRANSCRIPTS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        config.CLAUDE_TRANSCRIPTS_DIR = os.path.join(self.tmp, "transcripts")
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_id": "x", "orphans": "", "prompts": [
                {"id": "a", "name": "A", "prompt": "Make a 1200 to 1500 word briefing.",
                 "enabled": True, "last_episode_uri": None, "last_published": None}]}, f)
        orchestrator.init_run(DATE, "strict")

    def tearDown(self):
        (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR,
         config.CLAUDE_TRANSCRIPTS_DIR) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _transcript(self, name, records):
        os.makedirs(os.path.dirname(os.path.join(config.CLAUDE_TRANSCRIPTS_DIR, name)),
                    exist_ok=True)
        with open(os.path.join(config.CLAUDE_TRANSCRIPTS_DIR, name), "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    @staticmethod
    def _usage(ts, i, o, cc, cr):
        return {"timestamp": ts, "message": {"usage": {
            "input_tokens": i, "output_tokens": o,
            "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr}}}

    def test_no_window_reads_na(self):
        self.assertIsNone(run_report.token_usage(DATE))

    def test_window_sums_only_in_range_across_files_including_subagents(self):
        run_report.mark_window(DATE, "start")  # not used for bounds here; set explicit window below
        with open(run_report.window_path(DATE), "w", encoding="utf-8") as f:
            json.dump({"start": "2026-07-16T09:00:00.000Z",
                       "end": "2026-07-16T09:30:00.000Z"}, f)
        # main session: one in-window, one out-of-window (must be excluded)
        self._transcript("main.jsonl", [
            self._usage("2026-07-16T09:05:00.000Z", 10, 20, 30, 40),
            self._usage("2026-07-16T08:00:00.000Z", 999, 999, 999, 999)])
        # a subagent session in a subdir, in-window (must be included)
        self._transcript(os.path.join("main", "subagents", "agent-x.jsonl"),
                         [self._usage("2026-07-16T09:10:00.000Z", 1, 2, 3, 4)])
        u = run_report.token_usage(DATE)
        self.assertEqual((u["input"], u["output"], u["cache_creation"], u["cache_read"]),
                         (11, 22, 33, 44))
        self.assertEqual(u["total"], 11 + 22 + 33 + 44)

    @staticmethod
    def _write_usage(ts, artifact, i, o, cc, cr):
        """A usage record that also carries a Write tool_use — identifies the subagent's stage."""
        rec = TokenAccountingTest._usage(ts, i, o, cc, cr)
        rec["message"]["content"] = [
            {"type": "tool_use", "name": "Write", "input": {"file_path": f"runs/x/{artifact}"}}]
        return rec

    def test_stage_usage_attributes_by_written_artifact(self):
        with open(run_report.window_path(DATE), "w", encoding="utf-8") as f:
            json.dump({"start": "2026-07-16T09:00:00.000Z",
                       "end": "2026-07-16T09:30:00.000Z"}, f)
        ts = "2026-07-16T09:10:00.000Z"
        # a researcher subagent (writes research.json) and a writer subagent (writes draft.txt)
        self._transcript(os.path.join("s", "subagents", "r.jsonl"),
                         [self._write_usage(ts, "research.json", 1, 1, 1, 100)])
        self._transcript(os.path.join("s", "subagents", "w.jsonl"),
                         [self._write_usage(ts, "draft.txt", 1, 1, 1, 10)])
        # the parent (non-subagent) session -> orchestration
        self._transcript("parent.jsonl", [self._usage(ts, 1, 1, 1, 50)])
        # an out-of-window record must not count anywhere
        self._transcript(os.path.join("s", "subagents", "late.jsonl"),
                         [self._write_usage("2026-07-16T23:00:00.000Z", "review.json", 9, 9, 9, 9)])

        by = run_report.stage_usage(DATE)
        self.assertEqual(set(by), {"researcher", "writer", "orchestration"})
        self.assertEqual(by["researcher"]["total"], 1 + 1 + 1 + 100)
        self.assertEqual(by["writer"]["cache_read"], 10)
        self.assertEqual(by["orchestration"]["cache_read"], 50)
        self.assertEqual(sum(b["total"] for b in by.values()), 103 + 13 + 53)

    def test_report_tokens_per_word(self):
        with open(run_report.window_path(DATE), "w", encoding="utf-8") as f:
            json.dump({"start": "2026-07-16T00:00:00.000Z",
                       "end": "2026-07-16T23:59:59.000Z"}, f)
        self._transcript("m.jsonl", [self._usage("2026-07-16T12:00:00.000Z", 0, 0, 0, 1000)])
        # a 100-word approved final -> 1000 tokens / 100 words = 10 tokens/word
        review = {"prompt_id": "a", "run_date": DATE, "decision": "approve",
                  "decision_reason": "ok", "issues_found": [], "changes_made": [],
                  "scores": {k: 8 for k in ("novelty", "factual_support", "analytical_depth",
                                            "editorial_quality", "audio_flow", "prompt_compliance",
                                            "overall")}}
        pdir = orchestrator.prompt_dir(DATE, "a")
        with open(os.path.join(pdir, "review.json"), "w", encoding="utf-8") as f:
            json.dump(review, f)
        with open(os.path.join(pdir, "final.txt"), "w", encoding="utf-8") as f:
            f.write(" ".join(["w"] * 100))
        # Status set directly, not via approve: the tokens/word assertion below needs exactly 100
        # words, which the script gate rejects as under floor. See _approve's note.
        state = orchestrator.load_state(DATE)
        orchestrator._find_entry(state, "a").update(status="approved", stage=None, reason=None)
        orchestrator._save_state(DATE, state)
        report = run_report.build_report(DATE)
        self.assertEqual(report["tokens"]["total"], 1000)
        self.assertEqual(report["tokens_per_word"], 10.0)
        self.assertIn("tokens/word", run_report.format_report(report))

    def test_mark_window_start_is_idempotent(self):
        first = run_report.mark_window(DATE, "start")["start"]
        second = run_report.mark_window(DATE, "start")["start"]
        self.assertEqual(first, second)  # start never moves on retry
        ended = run_report.mark_window(DATE, "end")
        self.assertIn("end", ended)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR,
                       config.CLAUDE_TRANSCRIPTS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        config.CLAUDE_TRANSCRIPTS_DIR = os.path.join(self.tmp, "transcripts")
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_id": "x", "orphans": "", "prompts": [
                {"id": "a", "name": "A", "prompt": "Make a 1200 to 1500 word briefing.",
                 "enabled": True, "last_episode_uri": None, "last_published": None}]}, f)

    def tearDown(self):
        (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR,
         config.CLAUDE_TRANSCRIPTS_DIR) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_history_newest_first_and_capped(self):
        for d in ("2026-07-20", "2026-07-21", "2026-07-22"):
            orchestrator.init_run(d, "strict")
        rows = run_report.build_history("2026-07-22", 2)
        self.assertEqual([r["date"] for r in rows], ["2026-07-22", "2026-07-21"])
        # no token windows written -> tokens n/a, but the row still builds
        self.assertTrue(all(r["tokens_total"] is None for r in rows))

    def test_history_excludes_future_and_dirs_without_run_json(self):
        orchestrator.init_run("2026-07-20", "strict")
        os.makedirs(os.path.join(config.RUNS_DIR, "2026-07-25"), exist_ok=True)  # no run.json
        rows = run_report.build_history("2026-07-21", 5)
        self.assertEqual([r["date"] for r in rows], ["2026-07-20"])


if __name__ == "__main__":
    unittest.main()


class ListenabilityMetricsTest(unittest.TestCase):
    """run_report surfaces the deterministic listenability signal.

    This is the series that decides when ``script_check.ENFORCE_LISTENABILITY`` can be flipped, so
    the counts must be visible per prompt, in the totals, and in the trend.
    """

    CLEAN = ("Good morning. The desk repriced that trade today. " * 30).strip()
    # One 60-word sentence: breaches max_sentence_words (hard bound 60 -> needs >60).
    LONG = "Good morning. " + " ".join(["word"] * 80) + "."

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_id": "x", "orphans": [], "prompts": [
                {"id": "a", "name": "A", "prompt": "a 1200 to 1500 word briefing",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
                {"id": "b", "name": "B", "prompt": "a 1200 to 1500 word briefing",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
            ]}, f)
        orchestrator.init_run(DATE, "strict")

    def tearDown(self):
        config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _final(self, pid, text):
        path = os.path.join(orchestrator.prompt_dir(DATE, pid), "final.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_missing_final_yields_zeros_not_an_error(self):
        m = run_report._listenability(os.path.join(self.tmp, "nope.txt"))
        self.assertEqual((m["listen_warn"], m["listen_hard"]), (0, 0))
        self.assertEqual(m["listen_breaches"], {})

    def test_clean_script_has_no_breaches(self):
        m = run_report._listenability(self._final("a", self.CLEAN))
        self.assertEqual((m["listen_warn"], m["listen_hard"]), (0, 0))

    def test_long_sentence_is_a_hard_breach(self):
        m = run_report._listenability(self._final("a", self.LONG))
        self.assertEqual(m["listen_breaches"].get("max_sentence_words"), "hard")
        self.assertGreaterEqual(m["listen_hard"], 1)

    def test_metrics_are_carried_for_trending(self):
        m = run_report._listenability(self._final("a", self.CLEAN))
        self.assertIn("max_sentence_words", m["listen_metrics"])
        self.assertIn("figures_per_100w", m["listen_metrics"])

    def test_totals_aggregate_and_count_affected_scripts(self):
        self._final("a", self.LONG)
        self._final("b", self.CLEAN)
        report = run_report.build_report(DATE)
        rows = {r["id"]: r for r in report["prompts"]}
        self.assertGreaterEqual(rows["a"]["listen_hard"], 1)
        self.assertEqual(rows["b"]["listen_hard"], 0)
        t = report["totals"]
        self.assertEqual(t["listen_would_fail"], 1)   # one script carries a hard breach
        self.assertGreaterEqual(t["listen_hard"], 1)

    def test_report_shows_the_column_and_the_enforcement_state(self):
        self._final("a", self.LONG)
        text = run_report.format_report(run_report.build_report(DATE))
        self.assertIn("lstn", text)
        self.assertIn("listenability", text)
        # The flag is off, so the report must say so rather than implying these are gates.
        self.assertIn("advisory only", text)
        self.assertIn("by metric:", text)

    def test_report_shows_reviewer_audio_flow_beside_the_computed_signal(self):
        self._final("a", self.LONG)
        self._final("a", self.LONG)
        path = os.path.join(orchestrator.prompt_dir(DATE, "a"), "review.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_review("a", []), f)
        report = run_report.build_report(DATE)
        row = {r["id"]: r for r in report["prompts"]}["a"]
        # The whole point: a self-graded 8 sitting next to a hard computed breach.
        self.assertEqual(row["review_audio_flow"], 8)
        self.assertGreaterEqual(row["listen_hard"], 1)
        self.assertIn("flow", run_report.format_report(report))

    def test_trend_carries_the_listenability_series(self):
        self._final("a", self.LONG)
        rows = run_report.build_history(DATE, 1)
        self.assertEqual(len(rows), 1)
        self.assertIn("listen_hard", rows[0])
        self.assertGreaterEqual(rows[0]["listen_hard"], 1)
        self.assertIn("lstn", run_report.format_history(rows))


class FinalReaderMetricsTest(unittest.TestCase):
    """run_report surfaces the stage-5 verdict and the send-back rate.

    The send-back rate is the escalation signal: 0 of 134 approvals is the baseline the final
    reader exists to move, and a sustained rate above ~25% is the trigger to split the reviewer
    into critic/revisor rather than patching it.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR)
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")
        config.RUNS_DIR = os.path.join(self.tmp, "runs")
        with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_id": "x", "orphans": [], "prompts": [
                {"id": "a", "name": "A", "prompt": "a 1200 to 1500 word briefing",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
                {"id": "b", "name": "B", "prompt": "a 1200 to 1500 word briefing",
                 "enabled": True, "last_episode_uri": None, "last_published": None},
            ]}, f)
        orchestrator.init_run(DATE, "strict")

    def tearDown(self):
        config.PROMPTS_FILE, config.BRIEFINGS_DIR, config.RUNS_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fc(self, pid, verdict, hard=0):
        defects = [{"severity": "hard", "kind": "clarity", "quote": "q", "why": "w", "fix": "f"}
                   for _ in range(hard)]
        doc = {"prompt_id": pid, "run_date": DATE, "revision_round": 1, "verdict": verdict,
               "listener_question": "q", "answer_heard": "a", "answered": True,
               "scores": {"clarity": 7, "listenability": 6, "payoff": 7},
               "defects": defects, "verdict_reason": "r"}
        pdir = orchestrator.prompt_dir(DATE, pid)
        with open(os.path.join(pdir, "final.txt"), "w", encoding="utf-8") as f:
            f.write("Good morning. " + " ".join(["word"] * 1300))
        with open(os.path.join(pdir, orchestrator.FINAL_CHECK_FILE), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_absent_final_check_reads_as_none_not_zero(self):
        """Historical runs predate stage 5 — they must show '-', not a fabricated pass."""
        report = run_report.build_report(DATE)
        row = {r["id"]: r for r in report["prompts"]}["a"]
        self.assertIsNone(row["final_verdict"])
        self.assertEqual(report["totals"]["final_pass"], 0)

    def test_verdicts_and_hard_defects_are_counted(self):
        self._fc("a", "pass")
        self._fc("b", "revise", hard=2)
        report = run_report.build_report(DATE)
        rows = {r["id"]: r for r in report["prompts"]}
        self.assertEqual(rows["a"]["final_verdict"], "pass")
        self.assertEqual(rows["b"]["final_hard_defects"], 2)
        t = report["totals"]
        self.assertEqual((t["final_pass"], t["final_revise"]), (1, 1))
        self.assertEqual(t["final_hard_defects"], 2)

    def test_revisions_come_from_run_json(self):
        self._fc("a", "revise", hard=1)
        orchestrator.record_revision("a", DATE)
        report = run_report.build_report(DATE)
        row = {r["id"]: r for r in report["prompts"]}["a"]
        self.assertEqual(row["revisions"], 1)
        self.assertEqual(report["totals"]["revisions"], 1)

    def test_report_shows_the_send_back_rate(self):
        self._fc("a", "pass")
        self._fc("b", "revise", hard=1)
        text = run_report.format_report(run_report.build_report(DATE))
        self.assertIn("final reader", text)
        self.assertIn("send-back rate", text)
        self.assertIn("fc", text)

    def test_trend_carries_the_send_back_series(self):
        self._fc("a", "revise", hard=1)
        rows = run_report.build_history(DATE, 1)
        self.assertEqual(rows[0]["final_revise"], 1)
        self.assertIn("back", run_report.format_history(rows))
