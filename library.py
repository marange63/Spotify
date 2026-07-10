"""Prompt library storage for the Daily Briefings pipeline.

Single home for reading/writing ``prompts.json`` — used by the manager window
(``main.py``) and by the batch run (Claude Code / ``episode.py``). No language
model, no API key: this module only manages the list of saved prompts and the
Spotify episode URI each one currently maps to.

Schema (prompts.json):
    {
      "version": 1,
      "show_id": "spotify:show:...",
      "prompts": [
        {"id", "name", "prompt", "enabled", "last_episode_uri", "last_published"}
      ],
      "orphans": ["spotify:episode:..."]   # deleted prompts' episodes, cleaned up next batch
    }
"""
import json
import logging
import os
import re

import config

log = logging.getLogger(__name__)

# Seeded on first run so the library isn't empty. last_episode_uri is pinned to the
# currently-published Frontier episode so the first batch run replaces it, not duplicates it.
_SEED = {
    "version": 1,
    "show_id": config.SHOW_ID,
    "prompts": [
        {
            "id": "frontier-ai-labs",
            "name": "Frontier AI Labs",
            "prompt": (
                "Make a 1500 word briefing on developments over the last few days relevant to "
                "competition between the Frontier AI Labs including, but not limited to, OpenAI, "
                "Anthropic, Google, Tesla, SpaceX, Mistral, and the Chinese Open Source alternatives."
            ),
            "enabled": True,
            "last_episode_uri": "spotify:episode:6lxtIZUEqSsoo6h2kcvbwn",
            "last_published": "2026-07-07",
        }
    ],
    "orphans": [],
}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "prompt"


def new_id(name: str, existing_ids) -> str:
    """A stable slug id, disambiguated with a numeric suffix if the slug is taken."""
    base = slugify(name)
    existing = set(existing_ids)
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _load_raw() -> dict:
    """Read prompts.json from disk as-is, or {} if it doesn't exist. No seeding, no defaults."""
    if not os.path.exists(config.PROMPTS_FILE):
        return {}
    with open(config.PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _write(data: dict) -> None:
    os.makedirs(config.BRIEFINGS_DIR, exist_ok=True)
    with open(config.PROMPTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load() -> dict:
    """Load prompts.json, creating it from the seed if it doesn't exist yet."""
    if not os.path.exists(config.PROMPTS_FILE):
        _write(_SEED)
        return json.loads(json.dumps(_SEED))  # return a copy
    data = _load_raw()
    data.setdefault("version", 1)
    data.setdefault("show_id", config.SHOW_ID)
    data.setdefault("prompts", [])
    data.setdefault("orphans", [])
    return data


def save(data: dict) -> None:
    """Authoritative full write. Use from the batch, which owns the episode-tracking fields."""
    _write(data)


def save_merged(data: dict) -> None:
    """Window-safe save: persist the window-owned fields (name/prompt/enabled and the set of
    prompts) while PRESERVING the batch-owned tracking fields (last_episode_uri, last_published,
    orphans) from whatever is currently on disk. Prevents a still-open window from clobbering
    tracking the batch wrote while the window was open. Mutates ``data`` to match what was written.
    """
    disk = _load_raw()
    disk_by_id = {p["id"]: p for p in disk.get("prompts", [])}
    win_ids = {p["id"] for p in data["prompts"]}

    out_prompts = []
    for p in data["prompts"]:
        dk = disk_by_id.get(p["id"], {})  # existing on disk -> keep its tracking
        entry = {
            "id": p["id"],
            "name": p.get("name"),
            "prompt": p.get("prompt", ""),
            "enabled": bool(p.get("enabled", True)),
            "last_episode_uri": dk.get("last_episode_uri", p.get("last_episode_uri")),
            "last_published": dk.get("last_published", p.get("last_published")),
        }
        if p.get("kind"):  # e.g. "synthesis" — preserve special prompt kinds
            entry["kind"] = p["kind"]
        out_prompts.append(entry)

    # Tombstone the live episode of any prompt that existed on disk but was deleted in the window.
    orphans = list(disk.get("orphans", [])) + list(data.get("orphans", []))
    for pid, dk in disk_by_id.items():
        if pid not in win_ids and dk.get("last_episode_uri"):
            orphans.append(dk["last_episode_uri"])
    seen, merged_orphans = set(), []
    for uri in orphans:
        if uri and uri not in seen:
            seen.add(uri)
            merged_orphans.append(uri)

    out = {
        "version": disk.get("version", data.get("version", 1)),
        "show_id": disk.get("show_id", data.get("show_id", config.SHOW_ID)),
        "prompts": out_prompts,
        "orphans": merged_orphans,
    }
    _write(out)
    # keep the window's in-memory copy consistent with what we just wrote
    data["prompts"] = out_prompts
    data["orphans"] = merged_orphans


def find(data: dict, prompt_id: str):
    for p in data["prompts"]:
        if p["id"] == prompt_id:
            return p
    return None


def add(data: dict, name: str, prompt: str, enabled: bool = True) -> dict:
    """Create a new prompt with a stable id and return it."""
    pid = new_id(name, (p["id"] for p in data["prompts"]))
    entry = {
        "id": pid,
        "name": name.strip() or "Untitled",
        "prompt": prompt.strip(),
        "enabled": enabled,
        "last_episode_uri": None,
        "last_published": None,
    }
    data["prompts"].append(entry)
    return entry


def update(data: dict, prompt_id: str, *, name=None, prompt=None, enabled=None) -> dict:
    """Edit name/prompt/enabled in place. id and episode tracking are preserved."""
    p = find(data, prompt_id)
    if p is None:
        raise KeyError(prompt_id)
    if name is not None:
        p["name"] = name.strip() or p["name"]
    if prompt is not None:
        p["prompt"] = prompt.strip()
    if enabled is not None:
        p["enabled"] = bool(enabled)
    return p


def delete(data: dict, prompt_id: str) -> None:
    """Remove a prompt; tombstone its last episode URI so the next batch deletes it from Spotify."""
    p = find(data, prompt_id)
    if p is None:
        return
    if p.get("last_episode_uri"):
        data.setdefault("orphans", []).append(p["last_episode_uri"])
    data["prompts"] = [x for x in data["prompts"] if x["id"] != prompt_id]


# --- disk-atomic mutations (read-modify-write against the current file) --------
# The manager window is long-lived: it loads prompts.json once and stays open for
# minutes while, potentially, another process (Claude Code fixing ids, the batch
# writing tracking fields) edits the same file. Writing the window's whole stale
# in-memory copy back would silently clobber those external edits. These helpers
# instead RELOAD the file, apply just the one mutation the user made, and persist —
# so an external change to any *other* prompt (or to an id) is always preserved.
# They return the freshly-loaded data so the caller can rebind its in-memory copy.

def apply_new(name: str = "New prompt", prompt: str = "", enabled: bool = True):
    """Reload from disk, append a new prompt, persist. Returns ``(data, new_id)``.
    The id is deduplicated against whatever is on disk now, not a stale set."""
    data = load()
    entry = add(data, name, prompt, enabled)
    save(data)
    return data, entry["id"]


def apply_update(prompt_id: str, *, name=None, prompt=None, enabled=None):
    """Reload from disk, update one prompt in place, persist. Returns ``(data, id)``.
    If the prompt was deleted externally while the window was open, re-add it so the
    user's in-progress edit isn't silently lost (returns the possibly-new id)."""
    data = load()
    if find(data, prompt_id) is None:
        entry = add(data, name or "Untitled", prompt or "",
                    True if enabled is None else bool(enabled))
        save(data)
        return data, entry["id"]
    update(data, prompt_id, name=name, prompt=prompt, enabled=enabled)
    save(data)
    return data, prompt_id


def apply_delete(prompt_id: str):
    """Reload from disk, delete one prompt (tombstoning its episode), persist. Returns data."""
    data = load()
    delete(data, prompt_id)
    save(data)
    return data
