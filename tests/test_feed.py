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


class ReleaseHostingTest(unittest.TestCase):
    """add_episode / build_feed / prune_old with audio on GitHub Releases (network mocked)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.audio = os.path.join(self.d, "audio")
        self.tx = os.path.join(self.d, "transcripts")
        self.brief = os.path.join(self.d, "briefings")
        for p in (self.audio, self.tx, self.brief):
            os.makedirs(p)
        self.state_file = os.path.join(self.d, "state.json")
        self.feed_file = os.path.join(self.d, "feed.xml")
        with open(os.path.join(self.brief, "a.txt"), "w", encoding="utf-8") as f:
            f.write("Good morning.\n\nBody paragraph.")
        self.mp3 = os.path.join(self.d, "src.mp3")
        with open(self.mp3, "wb") as f:
            f.write(b"\xff\xfb\x90\x00" + b"x" * 500)
        self._ctx = [
            mock.patch.object(config, "FEED_STATE_FILE", self.state_file),
            mock.patch.object(config, "FEED_FILE", self.feed_file),
            mock.patch.object(config, "DOCS_DIR", self.d),
            mock.patch.object(config, "DOCS_AUDIO_DIR", self.audio),
            mock.patch.object(config, "DOCS_TRANSCRIPTS_DIR", self.tx),
            mock.patch.object(config, "BRIEFINGS_DIR", self.brief),
            mock.patch.object(config, "AUDIO_HOST", "release"),
        ]
        for c in self._ctx:
            c.start()

    def tearDown(self):
        for c in self._ctx:
            c.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_release_hosted_episode_sets_url_and_skips_docs_audio(self):
        import github_release
        url = "https://github.com/o/r/releases/download/audio/a-2026-07-25.mp3"
        with mock.patch.object(github_release, "upload_asset", return_value=url) as up:
            rec = feed.add_episode("a", "A", "summary", self.mp3, "2026-07-25")
        up.assert_called_once()
        self.assertEqual(rec["audio_url"], url)
        # release hosting must NOT write a docs/audio copy
        self.assertFalse(os.path.exists(os.path.join(self.audio, "a-2026-07-25.mp3")))
        # feed enclosure uses the release URL (with cache-bust)
        feed.build_feed()
        xml = open(self.feed_file, encoding="utf-8").read()
        self.assertIn(url + "?v=", xml)

    def test_upload_failure_falls_back_to_pages(self):
        import github_release
        with mock.patch.object(github_release, "upload_asset", side_effect=RuntimeError("boom")):
            rec = feed.add_episode("a", "A", "summary", self.mp3, "2026-07-25")
        self.assertIsNone(rec["audio_url"])
        # fallback copied into docs/audio, and the feed uses the Pages URL
        self.assertTrue(os.path.exists(os.path.join(self.audio, "a-2026-07-25.mp3")))
        feed.build_feed()
        xml = open(self.feed_file, encoding="utf-8").read()
        self.assertIn("/audio/a-2026-07-25.mp3?v=", xml)

    def test_prune_deletes_release_asset_not_files(self):
        import github_release
        import json
        old = {"guid": "a-2026-07-01", "prompt_id": "a", "title": "A", "summary": "s",
               "date": "2026-07-01", "seq": 0, "audio_file": "a-2026-07-01.mp3",
               "audio_url": "https://github.com/o/r/releases/download/audio/a-2026-07-01.mp3",
               "transcript_txt": "a-2026-07-01.txt", "transcript_html": "a-2026-07-01.html"}
        open(os.path.join(self.tx, old["transcript_txt"]), "w").close()
        open(os.path.join(self.tx, old["transcript_html"]), "w").close()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"episodes": [old]}, f)
        with mock.patch.object(github_release, "delete_asset", return_value=True) as dl:
            feed.prune_old(keep_days=10, today="2026-07-25")
        dl.assert_called_once_with("a-2026-07-01.mp3")  # release asset deleted, not a local file
        self.assertFalse(os.path.exists(os.path.join(self.tx, "a-2026-07-01.txt")))  # transcript gone


class DayLastOrderingTest(unittest.TestCase):
    """Prompts in config.FEED_DAY_LAST_PROMPTS sort to the bottom of their publish day."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.brief = os.path.join(self.d, "briefings")
        self.audio = os.path.join(self.d, "audio")
        self.tx = os.path.join(self.d, "transcripts")
        for p in (self.brief, self.audio, self.tx):
            os.makedirs(p)
        for pid in ("a", "tl", "fc"):
            with open(os.path.join(self.brief, pid + ".txt"), "w", encoding="utf-8") as f:
                f.write("Good morning.\n\nBody.")
        self.mp3 = os.path.join(self.d, "src.mp3")
        with open(self.mp3, "wb") as f:
            f.write(b"\xff\xfb\x90\x00" + b"x" * 500)
        self._ctx = [
            mock.patch.object(config, "FEED_STATE_FILE", os.path.join(self.d, "state.json")),
            mock.patch.object(config, "DOCS_DIR", self.d),
            mock.patch.object(config, "DOCS_AUDIO_DIR", self.audio),
            mock.patch.object(config, "DOCS_TRANSCRIPTS_DIR", self.tx),
            mock.patch.object(config, "BRIEFINGS_DIR", self.brief),
            mock.patch.object(config, "AUDIO_HOST", "pages"),
            mock.patch.object(config, "FEED_DAY_LAST_PROMPTS", ("tl", "fc")),
        ]
        for c in self._ctx:
            c.start()

    def tearDown(self):
        for c in self._ctx:
            c.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_backdated_below_the_days_earliest_episode(self):
        first = feed.add_episode("a", "A", "s", self.mp3, "2026-08-25")
        last = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")
        self.assertLess(feed._episode_datetime(last), feed._episode_datetime(first))

    def test_tuple_order_decides_placement_not_publish_order(self):
        feed.add_episode("a", "A", "s", self.mp3, "2026-08-25")
        # publish the two day-last prompts in the "wrong" order: fc first, tl second
        fc = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")
        tl = feed.add_episode("tl", "TL", "s", self.mp3, "2026-08-25")
        # tuple is ("tl", "fc"), so fc still ends up below tl
        self.assertLess(feed._episode_datetime(fc), feed._episode_datetime(tl))

    def test_republishing_does_not_drift_further_down(self):
        feed.add_episode("a", "A", "s", self.mp3, "2026-08-25")
        first = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")["published_at"]
        again = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")["published_at"]
        self.assertEqual(first, again)

    def test_other_days_do_not_constrain_it(self):
        feed.add_episode("a", "A", "s", self.mp3, "2026-08-24")   # yesterday, irrelevant
        rec = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")
        self.assertEqual(rec["published_at"][:10], "2026-08-25")

    def test_no_regular_episode_yet_falls_back_to_now(self):
        rec = feed.add_episode("fc", "FC", "s", self.mp3, "2026-08-25")
        self.assertEqual(rec["published_at"][:10], "2026-08-25")
        later = feed.add_episode("a", "A", "s", self.mp3, "2026-08-25")
        self.assertLess(feed._episode_datetime(rec), feed._episode_datetime(later))


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
