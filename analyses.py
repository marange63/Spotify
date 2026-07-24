"""Read access to the per-run agent-performance analyses (``analyses/<date>.md``).

These are markdown files authored after each run (see ``run_report.py`` and the
``daily-briefing`` skill's "Run analysis" step) and read by the ``main.py`` viewer tab. They are
local-only (git-ignored). This module is the viewer's data layer — it mirrors ``library``'s role
for prompts, so ``main.py`` stays free of filesystem logic and the listing is unit-testable.
"""
import os
import re

import config

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def path_for(date: str) -> str:
    """Absolute path of the analysis file for ``date`` (need not exist)."""
    return os.path.join(config.ANALYSES_DIR, f"{date}.md")


def list_dates() -> list[str]:
    """Dates (``YYYY-MM-DD``) that have an analysis file, newest first.

    Empty list if the directory doesn't exist yet. Only well-formed ``<date>.md`` names count, so
    stray files can't crash the viewer. Lexical sort works because the names are ISO dates.
    """
    try:
        names = os.listdir(config.ANALYSES_DIR)
    except FileNotFoundError:
        return []
    dates = [m.group(1) for m in (_DATE_RE.match(n) for n in names) if m]
    return sorted(dates, reverse=True)


def read(date: str) -> str:
    """Contents of ``analyses/<date>.md``, or ``""`` if it is missing."""
    try:
        with open(path_for(date), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
