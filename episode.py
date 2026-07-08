"""Episode helpers for the Daily Briefings "Save to Spotify" pipeline.

Thin glue over two existing tools (edge-tts + the save-to-spotify CLI). Turns a
plain-text script into an MP3, then manages the episode lifecycle: upload, wait
for readiness, and delete a prior version. Not a briefing generator — script text
is authored separately (by you, or by Claude Code) and saved to a .txt first.

    from episode import publish_replacing, SHOW_ID
    uri = publish_replacing("briefings/frontier-ai-labs.txt",
                            "Frontier AI Labs — 2026-07-08", "…", SHOW_ID, prev_uri)
"""
import asyncio
import logging
import os
import re
import subprocess
import time

import aiohttp
import edge_tts

import config

log = logging.getLogger(__name__)

# Convenience re-export so `from episode import SHOW_ID` keeps working.
SHOW_ID = config.SHOW_ID

# edge-tts / Microsoft TTS drops the WebSocket mid-stream on long inputs; these are the
# retryable transport errors. A real bug (anything else) propagates instead of being retried.
_RETRYABLE = (aiohttp.ClientError, ConnectionResetError, OSError, asyncio.TimeoutError, RuntimeError)


async def _synth_one(text: str) -> bytes:
    """Synthesize a single chunk to raw MP3 bytes over one WebSocket."""
    audio = b""
    async for chunk in edge_tts.Communicate(text, config.VOICE).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


async def _synthesize(text: str, mp3_path: str) -> None:
    """Resilient TTS: one short WebSocket per paragraph, with retries, then
    concatenate. Microsoft's TTS endpoint intermittently drops mid-stream on long
    inputs; per-paragraph retries mean a drop re-does one paragraph, not the whole file.
    """
    paras = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    out = b""
    for i, para in enumerate(paras, 1):
        for attempt in range(1, config.TTS_MAX_RETRIES + 1):
            try:
                data = await _synth_one(para)
                if not data:
                    raise RuntimeError("empty audio")
                out += data
                break
            except _RETRYABLE as e:
                log.debug("tts para %d: attempt %d failed (%s); retrying", i, attempt, type(e).__name__)
                time.sleep(1.5 * attempt)
        else:
            raise RuntimeError(f"TTS failed for paragraph {i} after {config.TTS_MAX_RETRIES} attempts")
    with open(mp3_path, "wb") as f:
        f.write(out)


def synthesize(text_path: str) -> str:
    """Render the script at ``text_path`` to a sibling .mp3 (resilient TTS). Returns the mp3 path."""
    with open(text_path, encoding="utf-8") as f:
        text = f.read()
    mp3_path = os.path.splitext(text_path)[0] + ".mp3"
    asyncio.run(_synthesize(text, mp3_path))
    log.info("wrote %s (%d bytes)", mp3_path, os.path.getsize(mp3_path))
    return mp3_path


def _episode_id(uri_or_id: str) -> str:
    """Accept a spotify:episode:<id> URI or a bare id; return the bare id."""
    return uri_or_id.rsplit(":", 1)[-1]


def _run(cmd: list[str]) -> str:
    """Run a save-to-spotify CLI command, log its output, raise on non-zero. Returns stdout."""
    log.debug("run: %s", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in (result.stdout or "").splitlines():
        if line.strip() and not line.startswith("<claude-code-hint"):
            log.info("%s", line)
    if result.stderr and result.stderr.strip():
        log.debug("stderr: %s", result.stderr.strip())
    result.check_returncode()
    return result.stdout


def upload_episode(mp3_path: str, title: str, summary: str, show_id: str | None = None) -> str:
    """Upload an mp3 as a new episode in ``show_id`` (default: config.SHOW_ID); return the new URI."""
    show_id = show_id or config.SHOW_ID
    out = _run([config.S2S, "upload", mp3_path, "--title", title, "--summary", summary,
                "--show-id", show_id])
    m = re.search(r"spotify:episode:[A-Za-z0-9]+", out)
    if not m:
        raise RuntimeError(f"Could not find episode URI in upload output:\n{out}")
    return m.group(0)


def wait_ready(uri_or_id: str, timeout: str = "5m") -> None:
    """Block until the episode is processed/playable (save-to-spotify episodes status --wait)."""
    _run([config.S2S, "episodes", "status", _episode_id(uri_or_id), "--wait", timeout])


def delete_episode(uri_or_id: str) -> None:
    """Delete an episode by URI/id. Tolerates an already-deleted episode."""
    try:
        _run([config.S2S, "episodes", "delete", _episode_id(uri_or_id)])
    except subprocess.CalledProcessError as e:
        log.warning("delete of %s failed (may already be gone): %s", uri_or_id, e)


def publish_replacing(text_path: str, title: str, summary: str,
                      show_id: str | None = None, prev_uri: str | None = None) -> str:
    """Per-prompt lifecycle: synth -> upload -> wait ready -> delete previous -> return new URI.

    The previous episode is deleted only AFTER the new one is READY, so the prompt
    is never without a live episode. ``show_id`` defaults to config.SHOW_ID.
    """
    mp3_path = synthesize(text_path)
    new_uri = upload_episode(mp3_path, title, summary, show_id)
    wait_ready(new_uri)
    if prev_uri and prev_uri != new_uri:
        delete_episode(prev_uri)
    return new_uri
