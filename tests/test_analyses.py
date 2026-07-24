"""Unit tests for analyses.py — the run-analysis viewer's data layer."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyses  # noqa: E402
import config  # noqa: E402


class AnalysesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = config.ANALYSES_DIR
        config.ANALYSES_DIR = os.path.join(self.tmp, "analyses")

    def tearDown(self):
        config.ANALYSES_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, body="x"):
        os.makedirs(config.ANALYSES_DIR, exist_ok=True)
        with open(os.path.join(config.ANALYSES_DIR, name), "w", encoding="utf-8") as f:
            f.write(body)

    def test_missing_dir_is_empty_not_error(self):
        self.assertEqual(analyses.list_dates(), [])
        self.assertEqual(analyses.read("2026-07-24"), "")

    def test_list_dates_newest_first_and_ignores_stray_files(self):
        for name in ("2026-07-22.md", "2026-07-24.md", "2026-07-23.md",
                     "notes.txt", "2026-7-2.md", "README.md"):
            self._write(name)
        self.assertEqual(analyses.list_dates(),
                         ["2026-07-24", "2026-07-23", "2026-07-22"])

    def test_read_round_trip_and_missing(self):
        self._write("2026-07-24.md", "# hello\nworld")
        self.assertEqual(analyses.read("2026-07-24"), "# hello\nworld")
        self.assertEqual(analyses.read("2026-07-25"), "")

    def test_path_for(self):
        self.assertEqual(analyses.path_for("2026-07-24"),
                         os.path.join(config.ANALYSES_DIR, "2026-07-24.md"))


if __name__ == "__main__":
    unittest.main()
