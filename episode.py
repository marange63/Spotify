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

# Pronunciation fixes for terms edge-tts consistently mis-says. Applied ONLY to the text
# sent to TTS — the published transcript is built from briefings/<id>.txt separately, so
# these respellings never leak into the written transcript. Order matters (first match wins
# per pattern); case-sensitive except where noted so the ordinary lowercase word is left
# alone. Grow this list as new offenders turn up.
_PRONUNCIATION = [
    (re.compile(r"\bDRAM\b"), "dee-ram"),          # else read as "dram" (rhymes with ham)
    (re.compile(r"\bHBM\b"), "H B M"),             # spell the letters, don't say "hbm"
    (re.compile(r"\bPJM\b"), "P J M"),             # the grid operator, letter-by-letter
    (re.compile(r"\bGENIUS\b"), "Genius"),         # the GENIUS Act — the word, not letters
    (re.compile(r"\bcapex\b", re.IGNORECASE), "cap-ex"),  # else mis-stressed
]


def _apply_pronunciation(text: str) -> str:
    """Rewrite known-mispronounced terms into TTS-friendly respellings (audio only)."""
    for pat, repl in _PRONUNCIATION:
        text = pat.sub(repl, text)
    return text


def _chunk_paragraphs(paras: list[str], budget: int) -> list[list[str]]:
    """Group consecutive paragraphs into chunks whose joined length stays under ``budget``.
    Returns groups (lists of paragraphs) so a failed chunk can fall back to per-paragraph
    synthesis. Fewer, larger chunks mean fewer concatenation joins and smoother cadence.
    """
    groups: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        add = len(p) + (1 if cur else 0)
        if cur and cur_len + add > budget:
            groups.append(cur)
            cur, cur_len = [p], len(p)
        else:
            cur.append(p)
            cur_len += add
    if cur:
        groups.append(cur)
    return groups


async def _synth_one(text: str) -> bytes:
    """Synthesize a single chunk to raw MP3 bytes over one WebSocket."""
    kwargs = {}
    rate = getattr(config, "TTS_RATE", "+0%")
    if rate and rate != "+0%":
        kwargs["rate"] = rate
    audio = b""
    async for chunk in edge_tts.Communicate(text, config.VOICE, **kwargs).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


async def _synth_with_retry(text: str, label: str) -> bytes | None:
    """Synthesize ``text`` with retries. Returns the audio bytes, or None if every
    attempt dropped (so the caller can degrade to a smaller unit)."""
    for attempt in range(1, config.TTS_MAX_RETRIES + 1):
        try:
            data = await _synth_one(text)
            if not data:
                raise RuntimeError("empty audio")
            return data
        except _RETRYABLE as e:
            log.debug("tts %s: attempt %d failed (%s); retrying", label, attempt, type(e).__name__)
            time.sleep(1.5 * attempt)
    return None


async def _synthesize(text: str, mp3_path: str) -> None:
    """Resilient TTS with natural cadence: apply pronunciation fixes, group paragraphs
    into ~``TTS_CHUNK_CHARS`` chunks (fewer joins = fewer unnatural mid-thought pauses),
    synthesize each over one WebSocket with retries, then concatenate. A chunk that keeps
    dropping falls back to per-paragraph synthesis, preserving the old resilience (a drop
    re-does one paragraph, not the whole file).
    """
    text = _apply_pronunciation(text)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()] or [text]
    groups = _chunk_paragraphs(paras, getattr(config, "TTS_CHUNK_CHARS", 1500))
    out = b""
    for gi, group in enumerate(groups, 1):
        data = await _synth_with_retry(" ".join(group), f"chunk {gi}")
        if data is not None:
            out += data
            continue
        # Chunk kept dropping — fall back to one paragraph at a time.
        if len(group) > 1:
            for pi, para in enumerate(group, 1):
                pdata = await _synth_with_retry(para, f"chunk {gi} para {pi}")
                if pdata is None:
                    raise RuntimeError(
                        f"TTS failed for chunk {gi} para {pi} after {config.TTS_MAX_RETRIES} attempts")
                out += pdata
        else:
            raise RuntimeError(f"TTS failed for chunk {gi} after {config.TTS_MAX_RETRIES} attempts")
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
