"""Unit tests for episode TTS: pronunciation fixes, paragraph chunking, resilient
retries with per-paragraph fallback (edge-tts mocked; no network)."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import episode  # noqa: E402


class PronunciationTest(unittest.TestCase):
    def test_respells_known_terms(self):
        out = episode._apply_pronunciation(
            "DRAM and HBM prices, PJM load, the GENIUS Act, and capex/CAPEX.")
        self.assertIn("dee-ram", out)
        self.assertIn("H B M", out)
        self.assertIn("P J M", out)
        self.assertIn("Genius Act", out)
        self.assertEqual(out.lower().count("cap-ex"), 2)   # both cases rewritten
        self.assertNotIn("DRAM", out)

    def test_respells_hormuz_and_robotaxi(self):
        out = episode._apply_pronunciation(
            "the Strait of Hormuz, one robotaxi, many Robotaxis")
        self.assertIn("Hor-mooz", out)
        self.assertNotIn("Hormuz", out)
        self.assertIn("Roh-bo-taxi", out)      # singular
        self.assertIn("Roh-bo-taxis", out)     # plural -s preserved
        self.assertNotIn("obotaxi", out)       # no unrewritten remnant

    def test_leaves_ordinary_words_alone(self):
        # lowercase 'genius' is an ordinary word and must not be touched
        text = "a genius idea about a program"
        self.assertEqual(episode._apply_pronunciation(text), text)


class ChunkTest(unittest.TestCase):
    def test_groups_paragraphs_under_budget(self):
        # "aaa" + " " + "bbb" == 7 <= 8 -> one group; "cccc" starts a new one
        self.assertEqual(
            episode._chunk_paragraphs(["aaa", "bbb", "cccc"], budget=8),
            [["aaa", "bbb"], ["cccc"]])

    def test_oversized_paragraph_is_its_own_group(self):
        self.assertEqual(
            episode._chunk_paragraphs(["x" * 20, "y"], budget=8),
            [["x" * 20], ["y"]])


class SynthTest(unittest.TestCase):
    def _run(self, text, fake, chunk_chars=1500):
        mp3 = os.path.join(tempfile.mkdtemp(), "o.mp3")
        with mock.patch("episode._synth_one", side_effect=fake), \
             mock.patch("episode.time.sleep"), \
             mock.patch.object(config, "TTS_CHUNK_CHARS", chunk_chars):
            asyncio.run(episode._synthesize(text, mp3))
        with open(mp3, "rb") as f:
            return f.read()

    def test_short_paragraphs_merge_into_one_chunk(self):
        calls = []

        async def fake(text):
            calls.append(text)
            return b"AUDIO:" + text.encode()[:3]

        data = self._run("Para one.\n\nPara two.", fake)
        self.assertEqual(len(calls), 1)                    # a single synthesis, not two
        self.assertEqual(calls[0], "Para one. Para two.")  # paragraphs joined by a space
        self.assertEqual(data.count(b"AUDIO:"), 1)

    def test_retries_then_succeeds(self):
        state = {"n": 0}

        async def fake(text):
            state["n"] += 1
            if state["n"] == 1:                            # first attempt drops
                raise ConnectionResetError("WinError 64")
            return b"AUDIO"

        data = self._run("One paragraph only.", fake)
        self.assertEqual(state["n"], 2)
        self.assertEqual(data, b"AUDIO")

    def test_chunk_falls_back_to_per_paragraph(self):
        # The joined chunk always drops, but each paragraph alone succeeds -> the
        # fallback recovers both without failing the whole file.
        async def fake(text):
            if "A." in text and "B." in text:             # the merged chunk
                raise ConnectionResetError("chunk drop")
            return b"SEG:" + text.encode()[:2]

        data = self._run("A.\n\nB.", fake)
        self.assertEqual(data.count(b"SEG:"), 2)

    def test_raises_after_max_retries(self):
        async def always_fail(text):
            raise ConnectionResetError("persistent drop")

        mp3 = os.path.join(tempfile.mkdtemp(), "o.mp3")
        with mock.patch("episode._synth_one", side_effect=always_fail), \
             mock.patch("episode.time.sleep"):
            with self.assertRaises(RuntimeError):
                asyncio.run(episode._synthesize("only one paragraph", mp3))


if __name__ == "__main__":
    unittest.main()
