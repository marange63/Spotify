"""Unit tests for library.py (prompts.json storage + save_merged merge/tombstone logic)."""
import copy
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import library  # noqa: E402


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._pf, self._bd = config.PROMPTS_FILE, config.BRIEFINGS_DIR
        config.PROMPTS_FILE = os.path.join(self.tmp, "prompts.json")
        config.BRIEFINGS_DIR = os.path.join(self.tmp, "briefings")

    def tearDown(self):
        config.PROMPTS_FILE, config.BRIEFINGS_DIR = self._pf, self._bd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _empty(self):
        return {"version": 1, "show_id": config.SHOW_ID, "prompts": [], "orphans": []}

    def test_slugify(self):
        self.assertEqual(library.slugify("Frontier AI Labs!!"), "frontier-ai-labs")
        self.assertEqual(library.slugify("   "), "prompt")

    def test_new_id_dedup(self):
        self.assertEqual(library.new_id("Markets", []), "markets")
        self.assertEqual(library.new_id("Markets", ["markets"]), "markets-2")
        self.assertEqual(library.new_id("Markets", ["markets", "markets-2"]), "markets-3")

    def test_add_defaults_and_stable_id(self):
        d = self._empty()
        e = library.add(d, "Daily Markets", "cover macro")
        self.assertEqual(e["id"], "daily-markets")
        self.assertTrue(e["enabled"])
        self.assertIsNone(e["last_episode_uri"])
        self.assertIsNone(e["last_published"])
        e2 = library.add(d, "Daily Markets", "x")
        self.assertEqual(e2["id"], "daily-markets-2")  # id disambiguated, name may repeat

    def test_update_preserves_tracking(self):
        d = self._empty()
        e = library.add(d, "A", "p")
        e["last_episode_uri"] = "spotify:episode:LIVE"
        e["last_published"] = "2026-07-07"
        library.update(d, e["id"], name="B", prompt="q", enabled=False)
        p = library.find(d, e["id"])
        self.assertEqual((p["name"], p["prompt"], p["enabled"]), ("B", "q", False))
        self.assertEqual(p["last_episode_uri"], "spotify:episode:LIVE")
        self.assertEqual(p["last_published"], "2026-07-07")

    def test_delete_tombstones(self):
        d = self._empty()
        e = library.add(d, "A", "p")
        e["last_episode_uri"] = "spotify:episode:X"
        library.delete(d, e["id"])
        self.assertIsNone(library.find(d, e["id"]))
        self.assertIn("spotify:episode:X", d["orphans"])

    def test_load_seeds_when_missing(self):
        self.assertFalse(os.path.exists(config.PROMPTS_FILE))
        d = library.load()
        self.assertTrue(os.path.exists(config.PROMPTS_FILE))
        self.assertEqual(len(d["prompts"]), 1)  # seeded

    def test_save_merged_preserves_disk_tracking(self):
        disk = self._empty()
        library.add(disk, "A", "p")  # id "a"
        disk["prompts"][0]["last_episode_uri"] = "spotify:episode:DISK"
        disk["prompts"][0]["last_published"] = "2026-07-07"
        library.save(disk)  # authoritative write

        stale = copy.deepcopy(disk)  # window loaded before tracking existed
        stale["prompts"][0]["last_episode_uri"] = None
        stale["prompts"][0]["last_published"] = None
        stale["prompts"][0]["name"] = "A renamed"
        library.save_merged(stale)

        p = library.find(library.load(), "a")
        self.assertEqual(p["name"], "A renamed")                       # window change applied
        self.assertEqual(p["last_episode_uri"], "spotify:episode:DISK")  # disk tracking preserved
        self.assertEqual(p["last_published"], "2026-07-07")

    def test_save_merged_tombstones_deleted(self):
        disk = self._empty()
        library.add(disk, "A", "p")
        library.add(disk, "B", "q")
        for p in disk["prompts"]:
            p["last_episode_uri"] = "spotify:episode:" + p["id"].upper()
        library.save(disk)

        stale = copy.deepcopy(disk)
        stale["prompts"] = [p for p in stale["prompts"] if p["id"] != "b"]  # window deleted B
        stale["orphans"] = []
        library.save_merged(stale)

        d2 = library.load()
        self.assertIsNone(library.find(d2, "b"))
        self.assertIn("spotify:episode:B", d2["orphans"])

    def test_save_merged_new_prompt_gets_none_tracking(self):
        disk = self._empty()
        library.add(disk, "A", "p")
        library.save(disk)

        win = copy.deepcopy(disk)
        library.add(win, "New One", "n")  # added in the window, not on disk
        library.save_merged(win)

        p = library.find(library.load(), "new-one")
        self.assertIsNotNone(p)
        self.assertIsNone(p["last_episode_uri"])


if __name__ == "__main__":
    unittest.main()
