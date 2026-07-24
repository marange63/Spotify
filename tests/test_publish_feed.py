"""Unit tests for publish_feed ordering (synthesis prompts publish last) and local pruning."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import publish_feed  # noqa: E402


class OrderTest(unittest.TestCase):
    def test_synthesis_published_last_and_disabled_dropped(self):
        data = {"prompts": [
            {"id": "a", "enabled": True},
            {"id": "throughline", "enabled": True, "kind": "synthesis"},
            {"id": "b", "enabled": True},
            {"id": "off", "enabled": False},
        ]}
        order = [p["id"] for p in publish_feed._ordered_enabled(data)]
        self.assertEqual(order, ["a", "b", "throughline"])  # off dropped, synthesis last, stable

    def test_multiple_synthesis_kept_after_normals(self):
        data = {"prompts": [
            {"id": "s1", "enabled": True, "kind": "synthesis"},
            {"id": "a", "enabled": True},
            {"id": "s2", "enabled": True, "kind": "synthesis"},
        ]}
        order = [p["id"] for p in publish_feed._ordered_enabled(data)]
        self.assertEqual(order, ["a", "s1", "s2"])  # normals first; synthesis order preserved


class PruneLocalTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.runs = os.path.join(self.d, "runs")
        self.logs = os.path.join(self.d, "logs")
        self.analyses = os.path.join(self.d, "analyses")
        for p in (self.runs, self.logs, self.analyses):
            os.makedirs(p)
        self._ctx = [mock.patch.object(config, "RUNS_DIR", self.runs),
                     mock.patch.object(config, "HERE", self.d)]
        for c in self._ctx:
            c.start()

    def tearDown(self):
        for c in self._ctx:
            c.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_sweeps_old_runs_and_logs_but_never_analyses(self):
        for date in ("2026-07-14", "2026-07-15", "2026-07-24"):
            os.makedirs(os.path.join(self.runs, date))
            open(os.path.join(self.logs, f"daily-{date}.log"), "w").close()
            open(os.path.join(self.analyses, f"{date}.md"), "w").close()
        # non-date dir must be left alone
        os.makedirs(os.path.join(self.runs, "scratch"))

        publish_feed._prune_local("2026-07-24", keep_days=10)  # cutoff 2026-07-15

        runs_left = sorted(os.listdir(self.runs))
        self.assertEqual(runs_left, ["2026-07-15", "2026-07-24", "scratch"])
        logs_left = sorted(os.listdir(self.logs))
        self.assertEqual(logs_left, ["daily-2026-07-15.log", "daily-2026-07-24.log"])
        # analyses are the kept history — untouched regardless of age
        self.assertEqual(len(os.listdir(self.analyses)), 3)


if __name__ == "__main__":
    unittest.main()
