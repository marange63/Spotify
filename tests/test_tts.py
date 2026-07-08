"""Unit test for episode._synthesize resilient TTS (edge-tts mocked; no network)."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import episode  # noqa: E402


class TTSTest(unittest.TestCase):
    def test_synthesize_retries_per_paragraph_and_concatenates(self):
        state = {"calls": 0}

        async def fake_synth_one(text):
            state["calls"] += 1
            if state["calls"] == 1:          # drop the first paragraph's first attempt
                raise ConnectionResetError("WinError 64")
            return b"AUDIO:" + text.encode()[:3]

        mp3 = os.path.join(tempfile.mkdtemp(), "o.mp3")
        with mock.patch("episode._synth_one", side_effect=fake_synth_one), \
             mock.patch("episode.time.sleep"):  # don't actually back off
            asyncio.run(episode._synthesize("Para one.\n\nPara two.", mp3))

        with open(mp3, "rb") as f:
            data = f.read()
        # 1 failed + 1 retry (para 1) + 1 (para 2) == 3 successful reads concatenated
        self.assertGreaterEqual(state["calls"], 3)
        self.assertEqual(data.count(b"AUDIO:"), 2)   # both paragraphs present

    def test_synthesize_raises_after_max_retries(self):
        async def always_fail(text):
            raise ConnectionResetError("persistent drop")

        mp3 = os.path.join(tempfile.mkdtemp(), "o.mp3")
        with mock.patch("episode._synth_one", side_effect=always_fail), \
             mock.patch("episode.time.sleep"):
            with self.assertRaises(RuntimeError):
                asyncio.run(episode._synthesize("only one paragraph", mp3))


if __name__ == "__main__":
    unittest.main()
