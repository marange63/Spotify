"""Daily Briefings — prompt library manager + run-analysis viewer.

Run this file (green Run button in PyCharm, or `python main.py`) to open a window with two tabs:

  • Prompts — keep a permanent library of briefing prompts: add, edit, enable/disable, delete.
    Everything is stored in prompts.json in this folder.
  • Run analyses — read the after-each-run write-up of how the pipeline's agents performed and
    interacted, with suggestions for improvement. One file per run under analyses/<date>.md,
    written by Claude Code at the end of each run (see run_report.py and the daily-briefing skill).

Then, in Claude Code, say "make my daily briefing": Claude turns each ENABLED prompt into its own
standalone briefing, publishes each as a dated episode in your "Daily Briefings" show, and writes
that run's analysis into the second tab.

This window has no language model and needs no API key — it only edits the library and displays
files. The research, writing, audio, publishing, and analysis all happen when you tell Claude Code.
"""
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import analyses
import library


class PromptManager:
    """Tab 1 — the prompt library editor. Built on a parent frame (a notebook tab)."""

    def __init__(self, parent: tk.Widget):
        self.parent = parent
        self.data = library.load()
        self.current_id: str | None = None

        # ---- left: list of prompts + New/Delete ----
        left = tk.Frame(parent)
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
        right = tk.Frame(parent)
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

        # Save with Ctrl+Enter while editing (bound to the editor, not the whole window,
        # so it can't fire from the other tab).
        self.prompt_text.bind("<Control-Return>", self._save_shortcut)
        self._refresh_list(select_first=True)

    def _save_shortcut(self, _event):
        self._save()
        return "break"  # don't also insert a newline

    # ---- list handling ----
    def _refresh_list(self, select_id: str | None = None, select_first: bool = False):
        self.listbox.delete(0, tk.END)
        for p in self.data["prompts"]:
            mark = "●" if p.get("enabled", True) else "○"
            suffix = "  ∑" if p.get("kind") == "synthesis" else ""  # cross-briefing synthesis
            self.listbox.insert(tk.END, f" {mark}  {p['name']}{suffix}")
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
        keep = self.current_id if (self.current_id and library.find(self.data, self.current_id)) else None
        self._refresh_list(select_id=keep, select_first=(keep is None))
        self.status_var.set(f"Reloaded from disk — {len(self.data['prompts'])} prompt(s).")


class AnalysisViewer:
    """Tab 2 — read-only viewer for the per-run analyses under analyses/<date>.md."""

    EMPTY = ("No run analyses yet.\n\n"
             "One is written after each run to analyses/<date>.md — say “make my daily "
             "briefing” in Claude Code, or wait for the 5 AM job. Then press Reload.")

    def __init__(self, parent: tk.Widget):
        self.parent = parent

        left = tk.Frame(parent)
        left.pack(side="left", fill="y", padx=(12, 6), pady=12)
        tk.Label(left, text="Runs", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, width=16, height=20, font=("Consolas", 10),
                                  exportselection=False, activestyle="none")
        self.listbox.pack(fill="y", expand=True, pady=(4, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        tk.Button(left, text="Reload", command=self.refresh).pack(fill="x")

        right = tk.Frame(parent)
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)
        self.header_var = tk.StringVar(value="Run analyses")
        tk.Label(right, textvariable=self.header_var, anchor="w",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 2))
        # Read-only: kept "disabled" except during programmatic inserts. Monospace so the
        # metrics table in the markdown lines up.
        self.text = scrolledtext.ScrolledText(right, wrap="word", font=("Consolas", 10),
                                               state="disabled", background="#faf9f7")
        self.text.pack(fill="both", expand=True)

        self._dates: list[str] = []
        self.refresh()

    def refresh(self, select: str | None = None):
        """Re-scan analyses/ and repopulate the date list, keeping the current selection if it
        survives. Called on load, on the Reload button, and when this tab is shown."""
        keep = select or self._selected_date()
        self._dates = analyses.list_dates()
        self.listbox.delete(0, tk.END)
        for d in self._dates:
            self.listbox.insert(tk.END, f" {d}")
        if not self._dates:
            self.header_var.set("Run analyses")
            self._set_text(self.EMPTY)
            return
        target = keep if (keep and keep in self._dates) else self._dates[0]
        idx = self._dates.index(target)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self._show(target)

    def _selected_date(self) -> str | None:
        sel = self.listbox.curselection()
        return self._dates[int(sel[0])] if sel and int(sel[0]) < len(self._dates) else None

    def _on_select(self, _event):
        d = self._selected_date()
        if d:
            self._show(d)

    def _show(self, date: str):
        self.header_var.set(f"analyses/{date}.md")
        self._set_text(analyses.read(date) or self.EMPTY)

    def _set_text(self, content: str):
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0.0)


def main() -> None:
    root = tk.Tk()
    root.title("Daily Briefings")
    root.geometry("880x560")
    root.minsize(720, 440)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    tab_prompts = tk.Frame(notebook)
    tab_analyses = tk.Frame(notebook)
    notebook.add(tab_prompts, text="Prompts")
    notebook.add(tab_analyses, text="Run analyses")

    PromptManager(tab_prompts)
    viewer = AnalysisViewer(tab_analyses)

    # Re-scan analyses/ whenever the viewer tab is brought to the front, so a run that
    # finished while the window was open shows up without a manual reload.
    def _on_tab(_e):
        if notebook.index(notebook.select()) == 1:
            viewer.refresh()
    notebook.bind("<<NotebookTabChanged>>", _on_tab)

    root.mainloop()


if __name__ == "__main__":
    main()
