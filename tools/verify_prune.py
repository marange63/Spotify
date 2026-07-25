"""Non-destructively verify the rolling-retention prune for a given run date.

Exercises the REAL code (`feed.prune_old` -> `build_feed`, and `publish_feed._prune_local`) against
temporary COPIES of the live data, so the repo, feed, and docs/ are never touched. Use it to confirm
what a future publish will prune before it happens.

    python tools/verify_prune.py                      # simulate today
    python tools/verify_prune.py --date 2026-07-26    # simulate a specific run date
    python tools/verify_prune.py --date 2026-07-26 --keep-days 10

Exit code is non-zero if any check fails.
"""
import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import feed  # noqa: E402
import publish_feed  # noqa: E402


def verify(date: str, keep_days: int) -> bool:
    cutoff = (datetime.date.fromisoformat(date)
              - datetime.timedelta(days=keep_days - 1)).isoformat()
    tmp = tempfile.mkdtemp()
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("  PASS " if cond else "  FAIL ") + name)

    try:
        # Stand up temp docs from the real feed_state: placeholder media, real filenames.
        state_file = os.path.join(tmp, "feed_state.json")
        shutil.copyfile(config.FEED_STATE_FILE, state_file)
        audio_dir, tx_dir, docs_dir = (os.path.join(tmp, d) for d in ("audio", "transcripts", "docs"))
        for d in (audio_dir, tx_dir, docs_dir):
            os.makedirs(d)
        real = json.load(open(state_file, encoding="utf-8"))["episodes"]
        for e in real:
            open(os.path.join(audio_dir, e["audio_file"]), "w").close()
            for key in ("transcript_txt", "transcript_html"):
                if e.get(key):
                    open(os.path.join(tx_dir, e[key]), "w").close()
        feed_file = os.path.join(docs_dir, "feed.xml")

        expect_prune = sorted(e["guid"] for e in real if e["date"] < cutoff)
        expect_keep = sorted(e["guid"] for e in real if e["date"] >= cutoff)

        with mock.patch.object(config, "FEED_STATE_FILE", state_file), \
             mock.patch.object(config, "DOCS_AUDIO_DIR", audio_dir), \
             mock.patch.object(config, "DOCS_TRANSCRIPTS_DIR", tx_dir), \
             mock.patch.object(config, "DOCS_DIR", docs_dir), \
             mock.patch.object(config, "FEED_FILE", feed_file):
            pruned = feed.prune_old(keep_days=keep_days, today=date)
            feed.build_feed()
            after = json.load(open(state_file, encoding="utf-8"))["episodes"]
            xml = open(feed_file, encoding="utf-8").read()
            left_audio = set(os.listdir(audio_dir))
            left_tx = set(os.listdir(tx_dir))

        print(f"date={date}  cutoff={cutoff}  keep_days={keep_days}")
        print(f"before: {len(real)} episodes | pruned: {len(pruned)} | kept: {len(after)}")

        pruned_set = set(pruned)
        pruned_eps = [e for e in real if e["guid"] in pruned_set]
        kept_eps = [e for e in real if e["guid"] not in pruned_set]

        check("pruned set == all episodes older than the window", sorted(pruned) == expect_prune)
        check("state now holds exactly the kept episodes",
              sorted(e["guid"] for e in after) == expect_keep)
        check("all kept dates >= cutoff", all(e["date"] >= cutoff for e in after))
        check("pruned audio files deleted", all(e["audio_file"] not in left_audio for e in pruned_eps))
        check("pruned transcripts deleted",
              all(e.get("transcript_txt") not in left_tx and e.get("transcript_html") not in left_tx
                  for e in pruned_eps))
        check("kept audio files intact", all(e["audio_file"] in left_audio for e in kept_eps))
        check("audio file count == kept episode count", len(left_audio) == len(kept_eps))
        check("feed.xml lists exactly the kept episodes", xml.count("<item>") == len(kept_eps))
        check("feed.xml references no pruned audio", all(e["audio_file"] not in xml for e in pruned_eps))
        check("feed.xml references every kept audio", all(e["audio_file"] in xml for e in kept_eps))

        # _prune_local against a temp runs/logs/analyses mirror of the real run dates.
        runs, logs, analyses = (os.path.join(tmp, d) for d in ("runs", "logs", "analyses"))
        for d in (runs, logs, analyses):
            os.makedirs(d)
        run_dates = [d for d in os.listdir(config.RUNS_DIR)
                     if os.path.isdir(os.path.join(config.RUNS_DIR, d)) and d[:2] == "20"]
        for d in run_dates:
            os.makedirs(os.path.join(runs, d))
            open(os.path.join(logs, f"daily-{d}.log"), "w").close()
            open(os.path.join(analyses, f"{d}.md"), "w").close()
        with mock.patch.object(config, "RUNS_DIR", runs), mock.patch.object(config, "HERE", tmp):
            publish_feed._prune_local(date, keep_days)
        check("runs/ swept to >= cutoff", all(d >= cutoff for d in os.listdir(runs)))
        check("logs swept to >= cutoff",
              all(f.replace("daily-", "").replace(".log", "") >= cutoff for f in os.listdir(logs)))
        check("analyses/ untouched regardless of age", len(os.listdir(analyses)) == len(run_dates))

        print("\nRESULT:", "ALL CHECKS PASS" if ok else "FAILURES ABOVE")
        if pruned:
            print("would prune:", ", ".join(pruned[:5]) + (" …" if len(pruned) > 5 else ""))
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="run date to simulate (default today)")
    ap.add_argument("--keep-days", type=int, default=config.RETENTION_DAYS,
                    help=f"retention window (default config.RETENTION_DAYS={config.RETENTION_DAYS})")
    args = ap.parse_args(argv)
    return 0 if verify(args.date, args.keep_days) else 1


if __name__ == "__main__":
    sys.exit(main())
