"""Central configuration for the Daily Briefings pipeline.

Single source of truth for shared constants and paths. Import this module and
reference ``config.X`` at call time (rather than ``from config import X``) so the
values stay patchable in tests.

Note on precedence: the runtime Spotify show id comes from ``prompts.json["show_id"]``
(the data). ``SHOW_ID`` here is only the seed default and the fallback for helpers
that take a ``show_id`` argument.
"""
import logging
import os
import shutil

HERE = os.path.dirname(os.path.abspath(str(__file__)))
PROMPTS_FILE = os.path.join(HERE, "prompts.json")
BRIEFINGS_DIR = os.path.join(HERE, "briefings")
# Per-day working artifacts of the three-agent pipeline (research.json,
# editorial_plan.json, draft/review/final) — git-ignored, see orchestrator.py.
RUNS_DIR = os.path.join(HERE, "runs")

# Spotify (legacy private "Save to Spotify" pipeline — kept for reference)
SHOW_ID = "spotify:show:033LxzC8UHlbiJmWLw3n2K"
SHOW_NAME = "Daily Briefings"

# --- Public podcast (self-hosted RSS -> Spotify for Creators) ---
# Audio + cover + feed.xml are served by GitHub Pages out of ./docs on the repo's
# default branch. Enable once: repo Settings -> Pages -> Source: main / docs.
PODCAST_TITLE = "Cautious Optimism Briefings"
PODCAST_AUTHOR = "Cautious Optimism"
PODCAST_OWNER_NAME = "Cautious Optimism"
PODCAST_EMAIL = "wamfour@gmail.com"  # Spotify verifies show ownership via this address
PODCAST_LANGUAGE = "en-us"
PODCAST_DESCRIPTION = (
    "Expert-level daily audio briefings for a sophisticated listener fluent in "
    "economics, capital markets, technology, artificial intelligence, and digital "
    "assets. Each episode leads with what is genuinely new or non-consensus and "
    "favors analysis over recap. One signal at a time."
)
# iTunes primary category (Spotify requires a valid one).
PODCAST_CATEGORY = "Business"
PODCAST_SUBCATEGORY = "Investing"

# GitHub Pages hosting
FEED_BASE_URL = "https://marange63.github.io/Spotify"
DOCS_DIR = os.path.join(HERE, "docs")
DOCS_AUDIO_DIR = os.path.join(DOCS_DIR, "audio")
DOCS_TRANSCRIPTS_DIR = os.path.join(DOCS_DIR, "transcripts")
FEED_FILE = os.path.join(DOCS_DIR, "feed.xml")
COVER_FILE = os.path.join(DOCS_DIR, "cover.jpg")
# Accumulating archive of published episodes that the feed is generated from.
FEED_STATE_FILE = os.path.join(HERE, "feed_state.json")

# Where the "briefings published" confirmation email goes (see notify.py). The
# SMTP credentials themselves come from env vars, never the repo.
NOTIFY_EMAIL = "wamfour@gmail.com"

# ntfy.sh push notification (the live "briefings published" alert to the owner's
# phone). publish_feed.py POSTs a one-line summary here after a successful push.
# The topic is effectively a shared secret (anyone who knows it can publish/read),
# so it can be overridden out-of-repo via the BRIEFING_NTFY_TOPIC env var. Set the
# topic to "" (or env BRIEFING_NTFY_TOPIC="") to disable the ping entirely.
NTFY_SERVER = os.environ.get("BRIEFING_NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("BRIEFING_NTFY_TOPIC", "will-briefings-9f3k2x7q")

# Text-to-speech
VOICE = "en-US-AndrewNeural"
TTS_MAX_RETRIES = 6
# A slight slowdown reads as more deliberate/natural for a spoken briefing (edge-tts
# accepts rate/volume/pitch even though it ignores full SSML). "+0%" disables it.
TTS_RATE = "-4%"
# Group consecutive paragraphs into single-synthesis chunks of ~this many characters.
# Each edge-tts call pads its output with a little silence, so one-call-per-paragraph
# left an audible gap after every paragraph (unnatural mid-thought pauses). Bigger
# chunks = far fewer joins = smoother cadence; still small enough that a mid-stream
# drop only re-does one chunk, with a per-paragraph fallback if a chunk keeps failing.
TTS_CHUNK_CHARS = 1500

# save-to-spotify CLI: prefer whatever is on PATH, else the standard install location.
S2S = shutil.which("save-to-spotify") or os.path.join(
    os.environ.get("USERPROFILE", HERE), "bin", "save-to-spotify.exe"
)

_LOG_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent logging setup for entry points (batch, window). Safe to call more than once."""
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _LOG_CONFIGURED = True
