"""Unit tests for notify.build_message (publish-confirmation email composition)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import notify  # noqa: E402


class BuildMessageTest(unittest.TestCase):
    def test_none_when_all_failed_or_skipped(self):
        results = [("A", "FAILED: boom"), ("B", "NO SCRIPT"), ("C", "STALE — skipped")]
        self.assertIsNone(notify.build_message(results, "2026-07-08"))

    def test_success_lists_episodes_with_links(self):
        results = [("Frontier AI", "frontier-ai-2026-07-08")]
        subject, text, html = notify.build_message(results, "2026-07-08")
        self.assertIn("1 episode published", subject)
        self.assertIn("July 8, 2026", subject)
        # per-episode transcript + audio links use the guid
        self.assertIn("transcripts/frontier-ai-2026-07-08.html", text)
        self.assertIn("audio/frontier-ai-2026-07-08.mp3", text)
        self.assertIn("transcripts/frontier-ai-2026-07-08.html", html)
        self.assertIn("Frontier AI", html)

    def test_pluralization_and_failures_flagged(self):
        results = [
            ("A", "a-2026-07-08"),
            ("B", "b-2026-07-08"),
            ("C", "FAILED: tts drop"),
        ]
        subject, text, html = notify.build_message(results, "2026-07-08")
        self.assertIn("2 episodes published", subject)
        self.assertIn("Not published this run:", text)
        self.assertIn("FAILED: tts drop", text)
        # failure does not appear as a successful episode link
        self.assertNotIn("audio/c-2026-07-08.mp3", text)

    def test_is_success_classifier(self):
        self.assertTrue(notify._is_success("some-guid-2026-07-08"))
        self.assertFalse(notify._is_success("FAILED: x"))
        self.assertFalse(notify._is_success("NO SCRIPT"))
        self.assertFalse(notify._is_success("STALE — skipped"))


if __name__ == "__main__":
    unittest.main()
