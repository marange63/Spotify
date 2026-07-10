"""Daily Briefings — prompt library manager.

Run this file (green Run button in PyCharm, or `python main.py`) to open a window
where you keep a permanent library of briefing prompts: add, edit, enable/disable,
and delete them. Everything is stored in prompts.json in this folder.

Then, in Claude Code, say "make my daily briefing": Claude turns each ENABLED
prompt into its own standalone briefing, publishes each as a dated episode in your
"Daily Briefings" show, and deletes the previous version of each (tracked by
Spotify episode URI) so the feed never piles up duplicates.

This window has no language model and needs no API key — it only edits the library.
The research, writing, audio, and publishing all happen when you tell Claude Code.
"""
import tkinter as tk
from tkinter import messagebox, scrolledtext

import library


class PromptManager:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.data = library.load()
        self.current_id: str | None = None

        root.title("Daily Briefings - prompt library")
        root.geometry("860x520")
        root.minsize(700, 420)

        # ---- left: list of prompts + New/Delete ----
        left = tk.Frame(root)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        tk.Label(left, text="Prompts", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, width=30, height=20, font=("Segoe UI", 10),
                                  exportselection=False, activestyle="none")
        self.listbox.pack(fill="y", expand=True, pady=(4, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        list_btns = tk.Frame(left)
        list_btns.pack(fill="x")
        tk.Button(list_btns, text="New", width=8, command=self._new).pack(side="left")
        tk.Button(list_btns, text="Delete", width=8, command=self._delete).pack(side="right")
        # Pull in edits made to prompts.json by another process (e.g. Claude Code)
        # since this window loaded — see _reload.
        tk.Button(left, text="Reload from disk", command=self._reload).pack(fill="x", pady=(4, 0))

        # ---- right: editor ----
        right = tk.Frame(root)
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)

        name_row = tk.Frame(right)
        name_row.pack(fill="x")
        tk.Label(name_row, text="Name", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.name_var = tk.StringVar()
        tk.Entry(name_row, textvariable=self.name_var, font=("Segoe UI", 11)).pack(
            side="left", fill="x", expand=True, padx=(8, 12))
        self.enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(name_row, text="Enabled", variable=self.enabled_var).pack(side="right")

        tk.Label(right, text="Prompt (the full instruction for this briefing)",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.prompt_text = scrolledtext.ScrolledText(right, wrap="word", font=("Segoe UI", 11))
        self.prompt_text.pack(fill="both", expand=True)

        save_row = tk.Frame(right)
        save_row.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value=f"{len(self.data['prompts'])} prompt(s). "
                                             f"Tell Claude Code: “make my daily briefing”.")
        tk.Label(save_row, textvariable=self.status_var, anchor="w", fg="#333",
                 font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
        tk.Button(save_row, text="Save", width=12, command=self._save).pack(side="right")

        root.bind("<Control-Return>", lambda _e: self._save())
        self._refresh_list(select_first=True)

    # ---- list handling ----
    def _refresh_list(self, select_id: str | None = None, select_first: bool = False):
        self.listbox.delete(0, tk.END)
        for p in self.data["prompts"]:
            mark = "●" if p.get("enabled", True) else "○"
            self.listbox.insert(tk.END, f" {mark}  {p['name']}")
        ids = [p["id"] for p in self.data["prompts"]]
        target = select_id if select_id in ids else (ids[0] if (select_first and ids) else None)
        if target:
            idx = ids.index(target)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self._load_into_editor(target)
        elif not ids:
            self.current_id = None
            self._clear_editor()

    def _on_select(self, _event):
        sel = self.listbox.curselection()
        if not sel:
            return
        pid = self.data["prompts"][sel[0]]["id"]
        self._load_into_editor(pid)

    def _load_into_editor(self, pid: str):
        p = library.find(self.data, pid)
        if not p:
            return
        self.current_id = pid
        self.name_var.set(p["name"])
        self.enabled_var.set(p.get("enabled", True))
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", p.get("prompt", ""))
        last = p.get("last_published")
        self.status_var.set(
            f"Editing “{p['name']}”." + (f"  Last published {last}." if last else "  Never published.")
        )

    def _clear_editor(self):
        self.name_var.set("")
        self.enabled_var.set(True)
        self.prompt_text.delete("1.0", tk.END)

    # ---- actions ----
    # Every mutation goes through library.apply_* which reload prompts.json, apply
    # just this one change, and persist — so edits another process made to the file
    # while this window was open (id fixes, batch tracking) are never clobbered.
    def _new(self):
        self.data, new_id = library.apply_new("New prompt", "", enabled=True)
        self.current_id = new_id
        self._refresh_list(select_id=new_id)
        self.status_var.set("Added a new prompt — set its name and instruction, then Save.")

    def _save(self):
        name = self.name_var.get().strip()
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        enabled = self.enabled_var.get()
        if self.current_id is None:
            if not name and not prompt:
                return
            self.data, self.current_id = library.apply_new(name or "Untitled", prompt, enabled)
        else:
            self.data, self.current_id = library.apply_update(
                self.current_id, name=name, prompt=prompt, enabled=enabled)
        self._refresh_list(select_id=self.current_id)
        self.status_var.set(f"Saved “{name or 'Untitled'}”.  Tell Claude Code: “make my daily briefing”.")

    def _delete(self):
        if self.current_id is None:
            return
        p = library.find(self.data, self.current_id)
        if not p:
            return
        if not messagebox.askyesno("Delete prompt",
                                   f"Delete “{p['name']}”?\n\nIts Spotify episode will be removed on "
                                   f"the next “make my daily briefing”."):
            return
        self.data = library.apply_delete(self.current_id)
        self.current_id = None
        self._clear_editor()
        self._refresh_list(select_first=True)
        self.status_var.set(f"Deleted “{p['name']}”. Its episode is queued for removal on the next run.")

    def _reload(self):
        """Discard the in-memory copy and re-read prompts.json from disk, so external
        edits (e.g. Claude Code renaming ids) show up. Overwrites the editor with the
        on-disk version of the selected prompt, so unsaved edits here are dropped."""
        self.data = library.load()
        keep = self.current_id if library.find(self.data, self.current_id) else None
        self._refresh_list(select_id=keep, select_first=(keep is None))
        self.status_var.set(f"Reloaded from disk — {len(self.data['prompts'])} prompt(s).")


def main() -> None:
    root = tk.Tk()
    PromptManager(root)
    root.mainloop()


if __name__ == "__main__":
    main()
