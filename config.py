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

# Spotify
SHOW_ID = "spotify:show:033LxzC8UHlbiJmWLw3n2K"
SHOW_NAME = "Daily Briefings"

# Text-to-speech
VOICE = "en-US-GuyNeural"
TTS_MAX_RETRIES = 6

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
