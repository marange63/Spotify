"""Unit tests for publish_feed ordering (synthesis prompts publish last)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


if __name__ == "__main__":
    unittest.main()
