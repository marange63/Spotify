"""Unit tests for script_check.py -- the deterministic text gate.

The false-positive cases matter more than the positive ones here. Every hard pattern was calibrated
against all 268 August 2026 drafts+finals and fires only on the 9 genuine leaks; the "must NOT fire"
tests below lock in the near-misses that were measured in that archive (Monday.com, "deep dive",
"the plan", "treatment") so a later widening of a regex cannot silently start failing the batch.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import script_check  # noqa: E402


def _clean(words=1300):
    """A well-formed script of roughly ``words`` words: short sentences, small paragraphs."""
    sentence = "The desk repriced the trade today. "
    per_para = 10
    paras, made = [], 0
    while made < words:
        paras.append((sentence * per_para).strip())
        made += per_para * len(sentence.split())
    return "\n\n".join(paras)


class StatedRangeTest(unittest.TestCase):
    def test_range(self):
        self.assertEqual(script_check.stated_range("a 1200 to 1500 word briefing"), (1200, 1500))

    def test_dashes(self):
        self.assertEqual(script_check.stated_range("1200-1500 words"), (1200, 1500))
        self.assertEqual(script_check.stated_range("800–1000 word digest"), (800, 1000))
        self.assertEqual(script_check.stated_range("1100—1300 words"), (1100, 1300))

    def test_single_bound(self):
        self.assertEqual(script_check.stated_range("about 700 words"), (700, None))

    def test_default(self):
        self.assertEqual(script_check.stated_range("no length stated"),
                         (script_check.DEFAULT_FLOOR, None))
        self.assertEqual(script_check.stated_range(""), (script_check.DEFAULT_FLOOR, None))


class FigureCountTest(unittest.TestCase):
    def test_counts_quantities(self):
        text = "Marvell issued 58,970,907 shares at $206.58, up 12 percent and 40 basis points."
        self.assertEqual(len(script_check.count_figures(text)), 4)

    def test_excludes_dates_years_quarters_ordinals(self):
        text = ("On August 18th, 2026 the Q3 print landed, the first since 2024. "
                "The 19th was quiet.")
        self.assertEqual(script_check.count_figures(text), [])

    def test_numerals_superset_of_figures(self):
        text = "On August 18, 2026 the index rose 12 percent."
        self.assertGreater(len(script_check.count_numerals(text)),
                           len(script_check.count_figures(text)))

    def test_date_stripping_does_not_eat_adjacent_figure(self):
        text = "On July 29 the Fed held at 4.25 percent."
        self.assertEqual(len(script_check.count_figures(text)), 1)


class HardPatternTest(unittest.TestCase):
    def _codes(self, text):
        return {p["code"] for p in script_check.check(text, floor=None)
                if p["severity"] == "error"}

    def test_markdown_header(self):
        self.assertIn("md_header", self._codes("# Heading\n\nBody text here."))

    def test_bullets(self):
        self.assertIn("bullet", self._codes("Intro.\n\n- first item\n- second item"))
        self.assertIn("bullet", self._codes("Intro.\n\n1. first item"))

    def test_stage_direction(self):
        self.assertIn("stage_direction", self._codes("[PAUSE] And now the market."))
        self.assertIn("stage_direction", self._codes("(music) Good morning."))

    def test_instruction_leak(self):
        # The literal 2026-08-24 strategic-power failure.
        self.assertIn("instruction_leak",
                      self._codes("Say clearly that these are letters of intent, not contracts."))

    def test_artifact_leak(self):
        self.assertIn("artifact_leak", self._codes("The dossier puts the figure near ten billion."))
        self.assertIn("artifact_leak", self._codes("Per the editorial plan we lead with rates."))

    def test_spoken_url(self):
        self.assertIn("spoken_url", self._codes("Read it at https://sec.gov/filing."))
        self.assertIn("spoken_url", self._codes("See www.example.org for the release."))

    # --- measured false positives: these MUST stay clean -----------------------------------------
    def test_company_dot_com_is_not_a_url(self):
        self.assertEqual(self._codes("Monday.com beat estimates and GitLab.com held flat."), set())

    def test_ordinary_english_is_not_artifact_leak(self):
        for phrase in ("We take a deep dive into the filings.",
                       "The plan announced yesterday changes the math.",
                       "Treatment of capex is the whole argument.",
                       "The lead story is the warrant structure."):
            self.assertEqual(self._codes(phrase), set(), phrase)

    def test_clean_script_has_no_hard_problems(self):
        self.assertEqual(self._codes(_clean()), set())


class LengthTest(unittest.TestCase):
    def _codes(self, text, **kw):
        return {(p["code"], p["severity"]) for p in script_check.check(text, **kw)}

    def test_final_well_under_floor_is_hard(self):
        text = _clean(500)
        self.assertIn(("under_floor", "error"), self._codes(text, floor=1200, stage="final"))

    def test_draft_under_floor_is_advisory_only(self):
        # 70% of drafts run under floor by design today; a hard draft gate would fail the batch.
        text = _clean(500)
        self.assertIn(("under_floor", "advisory"), self._codes(text, floor=1200, stage="draft"))

    def test_within_tolerance_is_soft(self):
        text = _clean(1300)
        words = script_check.word_count(text)
        floor = int(words / 0.97)  # just under floor, inside the 5% tolerance
        codes = self._codes(text, floor=floor, stage="final")
        self.assertIn(("under_floor_soft", "advisory"), codes)
        self.assertNotIn(("under_floor", "error"), codes)

    def test_over_ceiling_is_advisory(self):
        text = _clean(1300)
        self.assertIn(("over_ceiling", "advisory"),
                      self._codes(text, floor=200, ceiling=300, stage="final"))


class ListenabilityTest(unittest.TestCase):
    LONG = ("Because the warrant vests across two hundred and forty separate tranches tied to "
            "revenue that Google may or may not choose to buy in any given quarter, and because "
            "the filing never uses the word exclusive anywhere in its text, the accurate reading "
            "is sole supplier by practice rather than a contractual lock that competitors cannot "
            "dislodge before the end of the decade in question here.")

    def test_advisory_by_default(self):
        problems = script_check.check(self.LONG, floor=None, enforce_listenability=False)
        listen = [p for p in problems if p["code"].startswith("listen_")]
        self.assertTrue(listen)
        self.assertTrue(all(p["severity"] == "advisory" for p in listen))

    def test_hard_when_enforced(self):
        problems = script_check.check(self.LONG, floor=None, enforce_listenability=True)
        listen = [p for p in problems if p["code"].startswith("listen_")]
        self.assertTrue(any(p["severity"] == "error" for p in listen))

    def test_module_default_is_advisory(self):
        # The rollout depends on this staying False until a calibration run says otherwise.
        self.assertFalse(script_check.ENFORCE_LISTENABILITY)

    def test_clean_script_trips_nothing(self):
        problems = script_check.check(_clean(), floor=None, enforce_listenability=True)
        self.assertEqual([p for p in problems if p["code"].startswith("listen_")], [])


class MetricsTest(unittest.TestCase):
    def test_empty_text_does_not_raise(self):
        m = script_check.metrics("")
        self.assertEqual(m["words"], 0)
        self.assertEqual(m["max_figures_in_paragraph"], 0)
        self.assertEqual(m["figures_per_100w"], 0.0)

    def test_shapes(self):
        m = script_check.metrics("One two three. Four five.\n\nSix seven eight nine.")
        self.assertEqual(m["paragraphs"], 2)
        self.assertEqual(m["sentences"], 3)
        self.assertEqual(m["words"], 9)

    def test_errors_sort_before_advisories(self):
        text = "# Header\n\n" + _clean(300)
        problems = script_check.check(text, floor=1200, stage="final")
        self.assertEqual(problems[0]["severity"], "error")


if __name__ == "__main__":
    unittest.main()
