"""Podcast RSS feed for 'Cautious Optimism Briefings'.

Self-hosted publishing path (replaces the private "Save to Spotify" flow for the
public show): each briefing MP3 is copied into ``docs/audio/`` and recorded in
``feed_state.json``; ``build_feed`` renders ``docs/feed.xml``. GitHub Pages serves
``docs/`` publicly, and Spotify for Creators ingests the feed URL.

Archive model (podcast-native): every publish is a new, permanent episode with a
unique GUID, so followers get a normal new-episode notification and a browsable
back-catalogue — unlike the old private flow, which replaced the prior version.

    from feed import add_episode, build_feed
    rec = add_episode("frontier-ai-labs", "Frontier AI Lab Competition",
                      "summary…", "briefings/frontier-ai-labs.mp3", "2026-07-08")
    build_feed()
"""
import datetime as _dt
import json
import logging
import os
import shutil
from email.utils import format_datetime
from xml.sax.saxutils import escape

from mutagen.mp3 import MP3

import config

log = logging.getLogger(__name__)


def _load_state() -> dict:
    if not os.path.exists(config.FEED_STATE_FILE):
        return {"episodes": []}
    with open(config.FEED_STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    with open(config.FEED_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _human_date(date_str: str) -> str:
    """'2026-07-08' -> 'July 8, 2026'."""
    d = _dt.date.fromisoformat(date_str)
    return f"{d:%B} {d.day}, {d.year}"


def _tz_label(dt: _dt.datetime) -> str:
    """A friendly timezone label. The feed is published from US Eastern, so the
    two Eastern offsets (EST -5, EDT -4) render as 'ET'. Any other offset falls
    back to a compact 'UTC-n' form. Derived from the offset (not the zone name)
    because the ISO round-trip through feed_state keeps only the offset — so this
    is stable and identical on Windows and POSIX."""
    off = dt.utcoffset()
    if not off:
        return "UTC"
    total = int(off.total_seconds())
    if total in (-5 * 3600, -4 * 3600):  # US Eastern (EST / EDT)
        return "ET"
    sign = "+" if total >= 0 else "-"
    hours, mins = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours}" + (f":{mins:02d}" if mins else "")


def _human_datetime(dt: _dt.datetime) -> str:
    """A tz-aware datetime -> 'July 8, 2026 at 5:23 PM (UTC-4)'. Built manually
    (not strftime) so it's identical on Windows and POSIX."""
    hour12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt:%B} {dt.day}, {dt.year} at {hour12}:{dt.minute:02d} {ampm} ({_tz_label(dt)})"


def _pub_datetime(date_str: str, seq: int) -> _dt.datetime:
    """Fallback timestamp for legacy episodes with no recorded ``published_at``.
    ``seq`` nudges same-day episodes apart (by minutes) so their order within a
    day is deterministic. New episodes carry a real ``published_at`` instead."""
    d = _dt.date.fromisoformat(date_str)
    return _dt.datetime(d.year, d.month, d.day, 12, 0, tzinfo=_dt.timezone.utc) + _dt.timedelta(minutes=seq)


def _episode_datetime(e: dict) -> _dt.datetime:
    """The episode's publish instant: the real recorded time if present, else the
    legacy noon-UTC placeholder. Always tz-aware so records sort/compare cleanly."""
    pa = e.get("published_at")
    if pa:
        return _dt.datetime.fromisoformat(pa)
    return _pub_datetime(e["date"], e.get("seq", 0))


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def add_episode(prompt_id: str, name: str, summary: str, mp3_path: str,
                date: str) -> dict:
    """Copy the MP3 into docs/audio/ under a unique name and append a feed record.

    GUID is ``<prompt_id>-<date>`` — unique per topic per day. Re-publishing the
    same prompt on the same date overwrites that day's episode in place (idempotent).
    Returns the episode record.
    """
    os.makedirs(config.DOCS_AUDIO_DIR, exist_ok=True)
    guid = f"{prompt_id}-{date}"
    audio_name = f"{guid}.mp3"
    dest = os.path.join(config.DOCS_AUDIO_DIR, audio_name)
    shutil.copyfile(mp3_path, dest)

    length = os.path.getsize(dest)
    try:
        duration = int(round(MP3(dest).info.length))
    except Exception as e:  # pragma: no cover - duration is best-effort metadata
        log.warning("could not read duration for %s: %s", dest, e)
        duration = 0

    state = _load_state()
    eps = [e for e in state["episodes"] if e["guid"] != guid]  # replace same-day rerun
    same_day = sum(1 for e in eps if e["date"] == date)
    rec = {
        "guid": guid,
        "prompt_id": prompt_id,
        "title": f"{name} — {_human_date(date)}",
        "summary": summary,
        "date": date,
        "seq": same_day,
        "published_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "audio_file": audio_name,
        "length": length,
        "duration": duration,
    }
    eps.append(rec)
    state["episodes"] = eps
    _save_state(state)
    log.info("feed: recorded episode %s (%d bytes, %s)", guid, length, _fmt_duration(duration))
    return rec


def build_feed() -> str:
    """Render docs/feed.xml from feed_state.json (newest episode first). Returns the path."""
    state = _load_state()
    base = config.FEED_BASE_URL.rstrip("/")
    cover_url = f"{base}/cover.jpg"
    feed_url = f"{base}/feed.xml"

    # newest first: by real publish instant (falls back to legacy placeholder)
    episodes = sorted(state["episodes"], key=_episode_datetime, reverse=True)

    items = []
    for e in episodes:
        dt = _episode_datetime(e)
        pub = format_datetime(dt)
        desc = e["summary"]
        if e.get("published_at"):  # only stamp a time we actually recorded
            desc = f"{desc}\n\nPublished {_human_datetime(dt)}."
        audio_url = f"{base}/audio/{e['audio_file']}"
        item = f"""    <item>
      <title>{escape(e['title'])}</title>
      <description>{escape(desc)}</description>
      <itunes:summary>{escape(desc)}</itunes:summary>
      <enclosure url="{escape(audio_url)}" length="{e['length']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(e['guid'])}</guid>
      <pubDate>{pub}</pubDate>
      <itunes:duration>{_fmt_duration(e.get('duration', 0))}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <itunes:episodeType>full</itunes:episodeType>
    </item>"""
        items.append(item)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{escape(config.PODCAST_TITLE)}</title>
    <link>{escape(base)}/</link>
    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>
    <language>{escape(config.PODCAST_LANGUAGE)}</language>
    <description>{escape(config.PODCAST_DESCRIPTION)}</description>
    <itunes:summary>{escape(config.PODCAST_DESCRIPTION)}</itunes:summary>
    <itunes:author>{escape(config.PODCAST_AUTHOR)}</itunes:author>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{escape(cover_url)}"/>
    <image>
      <url>{escape(cover_url)}</url>
      <title>{escape(config.PODCAST_TITLE)}</title>
      <link>{escape(base)}/</link>
    </image>
    <itunes:category text="{escape(config.PODCAST_CATEGORY)}">
      <itunes:category text="{escape(config.PODCAST_SUBCATEGORY)}"/>
    </itunes:category>
    <itunes:owner>
      <itunes:name>{escape(config.PODCAST_OWNER_NAME)}</itunes:name>
      <itunes:email>{escape(config.PODCAST_EMAIL)}</itunes:email>
    </itunes:owner>
{chr(10).join(items)}
  </channel>
</rss>
"""
    os.makedirs(config.DOCS_DIR, exist_ok=True)
    with open(config.FEED_FILE, "w", encoding="utf-8") as f:
        f.write(xml)
    log.info("feed: wrote %s (%d episodes)", config.FEED_FILE, len(episodes))
    return config.FEED_FILE
