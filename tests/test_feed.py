"""Unit tests for feed.py: enclosure cache-busting and rolling-window pruning."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import feed  # noqa: E402


class FeedCacheBustTest(unittest.TestCase):
    def test_enclosure_url_carries_version_token(self):
        d = tempfile.mkdtemp()
        state_file = os.path.join(d, "state.json")
        feed_file = os.path.join(d, "feed.xml")
        # Two episodes published at different instants -> different ?v tokens.
        state = {"episodes": [
            {"guid": "a-2026-07-18", "prompt_id": "a", "title": "A", "summary": "s",
             "date": "2026-07-18", "seq": 0, "published_at": "2026-07-18T00:30:00-04:00",
             "audio_file": "a-2026-07-18.mp3", "length": 111, "duration": 60},
            {"guid": "a-2026-07-18b", "prompt_id": "a", "title": "A2", "summary": "s",
             "date": "2026-07-18", "seq": 1, "published_at": "2026-07-18T16:07:00-04:00",
             "audio_file": "a-2026-07-18.mp3", "length": 222, "duration": 60},
        ]}
        import json
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)

        with mock.patch.object(config, "FEED_STATE_FILE", state_file), \
             mock.patch.object(config, "FEED_FILE", feed_file):
            feed.build_feed()
            with open(feed_file, encoding="utf-8") as f:
                xml = f.read()

        # Same stable filename, but each enclosure has a distinct version token, so a
        # re-render of an already-ingested URL forces Spotify to re-download.
        self.assertIn("audio/a-2026-07-18.mp3?v=", xml)
        v_early = int(feed._episode_datetime(state["episodes"][0]).timestamp())
        v_late = int(feed._episode_datetime(state["episodes"][1]).timestamp())
        self.assertNotEqual(v_early, v_late)
        self.assertIn(f"?v={v_early}", xml)
        self.assertIn(f"?v={v_late}", xml)


class PruneOldTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.state_file = os.path.join(self.d, "state.json")
        self.audio = os.path.join(self.d, "audio")
        self.tx = os.path.join(self.d, "transcripts")
        os.makedirs(self.audio)
        os.makedirs(self.tx)
        self._ctx = [
            mock.patch.object(config, "FEED_STATE_FILE", self.state_file),
            mock.patch.object(config, "DOCS_AUDIO_DIR", self.audio),
            mock.patch.object(config, "DOCS_TRANSCRIPTS_DIR", self.tx),
        ]
        for c in self._ctx:
            c.start()

    def tearDown(self):
        for c in self._ctx:
            c.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def _episode(self, date):
        guid = f"a-{date}"
        for folder, name in ((self.audio, f"{guid}.mp3"),
                             (self.tx, f"{guid}.txt"), (self.tx, f"{guid}.html")):
            with open(os.path.join(folder, name), "w") as f:
                f.write("x")
        return {"guid": guid, "prompt_id": "a", "title": "A", "summary": "s", "date": date,
                "seq": 0, "audio_file": f"{guid}.mp3",
                "transcript_txt": f"{guid}.txt", "transcript_html": f"{guid}.html"}

    def _write(self, episodes):
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"episodes": episodes}, f)

    def test_prunes_old_keeps_window_and_deletes_files(self):
        # today = 2026-07-24, keep_days = 10 -> cutoff 2026-07-15; 07-14 pruned, 07-15 kept.
        self._write([self._episode(d) for d in
                     ("2026-07-14", "2026-07-15", "2026-07-20", "2026-07-24")])
        pruned = feed.prune_old(keep_days=10, today="2026-07-24")
        self.assertEqual(pruned, ["a-2026-07-14"])
        kept = {e["guid"] for e in json.load(open(self.state_file, encoding="utf-8"))["episodes"]}
        self.assertEqual(kept, {"a-2026-07-15", "a-2026-07-20", "a-2026-07-24"})
        # pruned episode's files gone; boundary-kept episode's files intact
        self.assertFalse(os.path.exists(os.path.join(self.audio, "a-2026-07-14.mp3")))
        self.assertFalse(os.path.exists(os.path.join(self.tx, "a-2026-07-14.html")))
        self.assertTrue(os.path.exists(os.path.join(self.audio, "a-2026-07-15.mp3")))

    def test_boundary_is_inclusive(self):
        # exactly keep_days-1 old (07-15) is kept; one day older (07-14) is not.
        self._write([self._episode("2026-07-14"), self._episode("2026-07-15")])
        self.assertEqual(feed.prune_old(keep_days=10, today="2026-07-24"), ["a-2026-07-14"])

    def test_nothing_to_prune_is_noop(self):
        self._write([self._episode("2026-07-24")])
        self.assertEqual(feed.prune_old(keep_days=10, today="2026-07-24"), [])

    def test_missing_files_tolerated(self):
        ep = self._episode("2026-07-01")
        os.remove(os.path.join(self.audio, "a-2026-07-01.mp3"))  # already gone
        self._write([ep])
        self.assertEqual(feed.prune_old(keep_days=10, today="2026-07-24"), ["a-2026-07-01"])


if __name__ == "__main__":
    unittest.main()
