"""Unit tests for episode.py Spotify CLI helpers (subprocess mocked; no network)."""
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import episode  # noqa: E402


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class SpotifyHelpersTest(unittest.TestCase):
    def test_episode_id_strips_uri(self):
        self.assertEqual(episode._episode_id("spotify:episode:ABC123"), "ABC123")
        self.assertEqual(episode._episode_id("ABC123"), "ABC123")

    def test_upload_parses_uri(self):
        out = ("Upload complete.\nEpisode created successfully.\n"
               "  URI:    spotify:episode:XYZ789\n  Status: UPLOADING\n")
        with mock.patch("episode.subprocess.run", return_value=_completed(out)):
            uri = episode.upload_episode("a.mp3", "Title", "Summary", "spotify:show:1")
        self.assertEqual(uri, "spotify:episode:XYZ789")

    def test_upload_missing_uri_raises(self):
        with mock.patch("episode.subprocess.run", return_value=_completed("no uri here")):
            with self.assertRaises(RuntimeError):
                episode.upload_episode("a.mp3", "Title", "Summary")

    def test_delete_tolerates_missing_episode(self):
        # returncode=1 -> _run's check_returncode raises CalledProcessError -> delete swallows it
        with mock.patch("episode.subprocess.run", return_value=_completed("", returncode=1, stderr="not found")):
            episode.delete_episode("spotify:episode:GONE")  # must not raise

    def test_publish_replacing_calls_in_order_and_deletes_prev(self):
        calls = []
        with mock.patch("episode.synthesize", side_effect=lambda p: calls.append("synth") or "x.mp3"), \
             mock.patch("episode.upload_episode",
                        side_effect=lambda *a, **k: calls.append("upload") or "spotify:episode:NEW"), \
             mock.patch("episode.wait_ready", side_effect=lambda *a, **k: calls.append("wait")), \
             mock.patch("episode.delete_episode", side_effect=lambda u: calls.append(("delete", u))):
            uri = episode.publish_replacing("s.txt", "T", "S", "spotify:show:1", "spotify:episode:OLD")
        self.assertEqual(uri, "spotify:episode:NEW")
        self.assertEqual([c if isinstance(c, str) else c[0] for c in calls],
                         ["synth", "upload", "wait", "delete"])
        self.assertEqual(calls[-1], ("delete", "spotify:episode:OLD"))

    def test_publish_replacing_no_prev_skips_delete(self):
        deleted = []
        with mock.patch("episode.synthesize", return_value="x.mp3"), \
             mock.patch("episode.upload_episode", return_value="spotify:episode:NEW"), \
             mock.patch("episode.wait_ready"), \
             mock.patch("episode.delete_episode", side_effect=lambda u: deleted.append(u)):
            episode.publish_replacing("s.txt", "T", "S", prev_uri=None)
        self.assertEqual(deleted, [])


if __name__ == "__main__":
    unittest.main()
