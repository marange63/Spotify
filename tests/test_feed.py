"""Unit test for feed.build_feed enclosure cache-busting (state + output patched to temp)."""
import os
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


if __name__ == "__main__":
    unittest.main()
