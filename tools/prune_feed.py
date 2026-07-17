"""Remove old episodes from the podcast feed (and therefore from Spotify).

The show is RSS-driven: Spotify mirrors docs/feed.xml, which build_feed() renders
from feed_state.json (the source of truth). To drop an episode from Spotify you
remove it from feed_state.json, delete its hosted files under docs/, rebuild the
feed, and push — Spotify drops it on the next re-ingest.

Usage (run in the Spotify conda env, from the project root):
    conda run -n Spotify python tools/prune_feed.py --before 2026-07-16            # DRY RUN (default): preview only
    conda run -n Spotify python tools/prune_feed.py --before 2026-07-16 --apply    # actually remove + rebuild feed
    conda run -n Spotify python tools/prune_feed.py --before 2026-07-16 --apply --push   # also git commit + push

--before <YYYY-MM-DD> removes every episode dated STRICTLY before that date (the
cutoff date itself is kept). Nothing is written without --apply.
"""
import argparse
import datetime as _dt
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import feed  # noqa: E402


def _remove_docs_file(name: str, subdir: str) -> None:
    if not name:
        return
    path = os.path.join(subdir, name)
    if os.path.exists(path):
        os.remove(path)
        print(f"    deleted {os.path.relpath(path, config.DOCS_DIR)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune old episodes from the feed.")
    ap.add_argument("--before", required=True, metavar="YYYY-MM-DD",
                    help="remove episodes dated strictly before this date (cutoff kept)")
    ap.add_argument("--apply", action="store_true",
                    help="actually remove + rebuild (default is a dry-run preview)")
    ap.add_argument("--push", action="store_true",
                    help="git add/commit/push after applying (implies --apply)")
    args = ap.parse_args()

    if args.push:
        args.apply = True

    try:
        cutoff = _dt.date.fromisoformat(args.before)
    except ValueError:
        print(f"error: --before must be YYYY-MM-DD, got {args.before!r}")
        return 2

    state = feed._load_state()
    episodes = state.get("episodes", [])
    to_remove = [e for e in episodes if _dt.date.fromisoformat(e["date"]) < cutoff]
    to_keep = [e for e in episodes if _dt.date.fromisoformat(e["date"]) >= cutoff]

    print(f"feed has {len(episodes)} episodes; cutoff = before {cutoff.isoformat()}")
    print(f"  REMOVE: {len(to_remove)}   KEEP: {len(to_keep)}\n")
    if not to_remove:
        print("nothing to remove.")
        return 0

    for e in sorted(to_remove, key=lambda e: (e["date"], e["guid"])):
        print(f"  - {e['date']}  {e['guid']}")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply (add --push to publish).")
        return 0

    print("\napplying...")
    for e in to_remove:
        _remove_docs_file(e.get("audio_file"), config.DOCS_AUDIO_DIR)
        _remove_docs_file(e.get("transcript_txt"), config.DOCS_TRANSCRIPTS_DIR)
        _remove_docs_file(e.get("transcript_html"), config.DOCS_TRANSCRIPTS_DIR)

    state["episodes"] = to_keep
    feed._save_state(state)
    print(f"  wrote feed_state.json ({len(to_keep)} episodes remain)")
    feed.build_feed()
    print("  rebuilt docs/feed.xml")

    if args.push:
        print("\npushing...")
        subprocess.run(["git", "add", "docs", "feed_state.json"], check=True)
        msg = f"Prune {len(to_remove)} episode(s) dated before {cutoff.isoformat()}"
        subprocess.run(["git", "commit", "-m", msg], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("  pushed — Spotify will drop the removed episodes on its next re-ingest.")
    else:
        print("\ndone (local only). Commit + push docs/ and feed_state.json to publish the removal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
