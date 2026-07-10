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

    # ---- disk-atomic apply_* helpers (the window's clobber-proof path) ----
    def test_apply_update_preserves_external_edits(self):
        # This is the exact bug we hit: the window loads the file, an external
        # process then renames an id and adds a prompt, and the window saves an
        # edit to a *different* prompt. The external changes must survive.
        disk = self._empty()
        library.add(disk, "A", "pa")  # id "a"
        library.add(disk, "B", "pb")  # id "b"
        library.save(disk)

        ext = library.load()                       # external process opens the file
        ext["prompts"][0]["id"] = "alpha"          # ...renames A's id
        library.add(ext, "C", "pc")                # ...and adds C
        library.save(ext)

        # window (which still calls the second prompt "b") saves an edit to it
        _data, pid = library.apply_update("b", name="B2", prompt="pb2", enabled=False)
        self.assertEqual(pid, "b")

        d = library.load()
        self.assertIsNotNone(library.find(d, "alpha"))  # external rename survived
        self.assertIsNotNone(library.find(d, "c"))      # external addition survived
        p = library.find(d, "b")
        self.assertEqual((p["name"], p["prompt"], p["enabled"]), ("B2", "pb2", False))

    def test_apply_new_dedups_against_disk(self):
        disk = self._empty()
        library.add(disk, "Markets", "p")  # id "markets"
        library.save(disk)
        _data, nid = library.apply_new("Markets", "q")
        self.assertEqual(nid, "markets-2")  # deduped vs disk, not a stale in-memory set
        self.assertEqual(len(library.load()["prompts"]), 2)

    def test_apply_update_readds_if_externally_deleted(self):
        disk = self._empty()
        library.add(disk, "A", "p")  # id "a"
        library.save(disk)
        gone = library.load()
        library.delete(gone, "a")    # external delete while window still edits "a"
        library.save(gone)

        _data, pid = library.apply_update("a", name="A back", prompt="p2", enabled=True)
        d = library.load()
        self.assertIsNotNone(library.find(d, pid))
        self.assertEqual(library.find(d, pid)["name"], "A back")

    def test_apply_delete_removes_and_tombstones(self):
        disk = self._empty()
        library.add(disk, "A", "p")
        disk["prompts"][0]["last_episode_uri"] = "spotify:episode:Z"
        library.save(disk)

        library.apply_delete("a")
        d = library.load()
        self.assertIsNone(library.find(d, "a"))
        self.assertIn("spotify:episode:Z", d["orphans"])

    # ---- synthesis prompts carry a "kind" field that must survive saves ----
    def test_apply_update_preserves_kind(self):
        disk = self._empty()
        e = library.add(disk, "The Throughline", "synthesize")
        disk["prompts"][0]["kind"] = "synthesis"
        library.save(disk)
        library.apply_update(e["id"], name="The Throughline v2")
        p = library.find(library.load(), e["id"])
        self.assertEqual(p.get("kind"), "synthesis")   # kind preserved
        self.assertEqual(p["name"], "The Throughline v2")

    def test_save_merged_preserves_kind(self):
        disk = self._empty()
        library.add(disk, "The Throughline", "synthesize")
        disk["prompts"][0]["kind"] = "synthesis"
        library.save(disk)
        stale = copy.deepcopy(disk)
        stale["prompts"][0]["name"] = "renamed"       # a window edit
        library.save_merged(stale)
        p = library.find(library.load(), disk["prompts"][0]["id"])
        self.assertEqual(p.get("kind"), "synthesis")


if __name__ == "__main__":
    unittest.main()
