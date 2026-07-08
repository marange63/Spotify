"""Publish the enabled prompts' briefings to the public podcast feed.

New daily path (replaces the private Save-to-Spotify batch): assumes each enabled
prompt already has a fresh script at ``briefings/<id>.txt`` (written by Claude Code
for today). For each one it synthesizes audio, records a new archive episode, then
rebuilds ``docs/feed.xml`` and pushes to GitHub. GitHub Pages serves the update and
Spotify for Creators re-ingests the feed on its next refresh.

    python publish_feed.py                      # publish all enabled prompts for today
    python publish_feed.py --date 2026-07-09    # override the episode date
    python publish_feed.py --no-push            # build locally without git push

Summaries: pass a JSON map of {prompt_id: summary} via --summaries <file>, else the
prompt name is used as the episode description.
"""
import argparse
import datetime
import json
import logging
import os
import subprocess
import sys

import config
import library
from episode import synthesize
from feed import add_episode, build_feed

log = logging.getLogger("publish_feed")


def _git(*args: str) -> None:
    subprocess.run(["git", "-C", config.HERE, *args], check=True)


def publish(date: str, summaries: dict, push: bool = True) -> list[tuple[str, str]]:
    data = library.load()
    results = []
    for p in data["prompts"]:
        if not p.get("enabled"):
            continue
        pid, name = p["id"], p["name"]
        text_path = os.path.join(config.BRIEFINGS_DIR, pid + ".txt")
        if not os.path.exists(text_path):
            log.warning("%s: no script at %s — skipping", name, text_path)
            results.append((name, "NO SCRIPT"))
            continue
        try:
            mp3 = synthesize(text_path)
            rec = add_episode(pid, name, summaries.get(pid, name), mp3, date)
            results.append((name, rec["guid"]))
            log.info("published %s", rec["guid"])
        except Exception as e:  # keep going; one failure shouldn't sink the batch
            log.exception("%s failed", name)
            results.append((name, f"FAILED: {e}"))

    build_feed()

    if push:
        _git("add", "docs", "feed_state.json")
        msg = f"Publish briefings for {date}"
        # commit may be a no-op if nothing changed; tolerate that
        r = subprocess.run(["git", "-C", config.HERE, "commit", "-m", msg])
        if r.returncode == 0:
            _git("push", "origin", "main")
            log.info("pushed to origin/main")
        else:
            log.info("nothing to commit")
    return results


def main() -> None:
    config.configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--summaries", help="JSON file: {prompt_id: summary}")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    summaries = {}
    if args.summaries:
        with open(args.summaries, encoding="utf-8") as f:
            summaries = json.load(f)

    results = publish(args.date, summaries, push=not args.no_push)
    print("\n===== RESULTS =====")
    for name, status in results:
        print(f"{name}: {status}")


if __name__ == "__main__":
    sys.exit(main())
