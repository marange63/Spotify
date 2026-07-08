# Daily Briefings project

**Goal:** a public daily podcast of expert-level audio briefings, **Cautious Optimism Briefings**. You
keep a library of standing prompts (topics); on command, Claude Code turns each into a fresh, researched
spoken briefing and publishes it as a new episode in the show.

**How it's built (current — public podcast via self-hosted RSS):** a key-free Tkinter window (`main.py`)
manages the prompt library (`prompts.json`); Claude Code does the research + writing; `edge-tts` makes
the audio; **`feed.py` builds a podcast RSS feed and the audio + cover + `feed.xml` are served publicly
by GitHub Pages out of `./docs`; Spotify for Creators ingests that feed URL.** This makes episodes
public and shareable (links work for anyone; the show is searchable on Spotify). Publishing is a
`git push` — Pages serves it and Spotify re-ingests on its refresh schedule.

**Archive model:** each publish is a **new, permanent episode** with a unique per-day GUID
(`<prompt_id>-<YYYY-MM-DD>`), so followers get normal new-episode notifications and a browsable
back-catalogue. This replaced the older "replace the prior version" model, which existed only to keep a
*private* single-listener library tidy.

**Legacy (private) path:** the `save-to-spotify` CLI + `episode.publish_replacing` published to a
*private* "Daily Briefings" show whose episodes were not shareable (links dead-ended for other people).
`episode.py` is kept for its TTS helpers (`synthesize`) but the private publish path is retired.

- **Show:** Cautious Optimism Briefings · Feed: https://marange63.github.io/Spotify/feed.xml
- **Pages site:** https://marange63.github.io/Spotify/ (served from `main` branch, `/docs` folder)
- **Owner/verification email:** wamfour@gmail.com (in the feed's `itunes:owner`)

## Editorial standard for every briefing

The listener is a sophisticated reader, fluent in **Economics, Capital Markets, Technology, and
Artificial Intelligence**. Write for that expert audience and optimize for **novel, high-signal**
information and analysis — keeping them abreast of what's genuinely new and important.

- Lead with what's **new or non-consensus** since the last update — the marginal, market-moving,
  or surprising development. Do not recap well-known context.
- Assume fluency: no 101-level definitions, no over-explaining. Use precise domain vocabulary.
- **Analysis, not just reporting**: second-order effects, why it matters, what it implies,
  competing interpretations, and risks to the thesis.
- Be specific and quantitative: figures, dates, deltas vs. prior expectations/consensus, and the
  source of the number.
- Cut filler and hedging. Every sentence should carry information or judgment.
- Use the **freshest** available data (search at authoring time); flag what changed vs. the prior read.

## Format

Conversational single narrator; no headers/bullets/stage directions in the script itself. **Length is
set per prompt** (state it in the prompt text, e.g. "1500 words"); default ~700 if unspecified (~150
words per spoken minute). Open with a one-line greeting + the date, then the content, and close with a
one-line sign-off. Voice: `en-US-GuyNeural`.

## Preferred sources

Prioritize these when researching each briefing (freshest primary material first, then analysis).

- **Economics & Capital Markets** — Primary/official: SEC filings (8-K/10-Q), earnings-call
  transcripts, Fed/central-bank releases. Press: FT, WSJ, Bloomberg, The Economist. Research:
  sell-side notes (Goldman, Morgan Stanley), S&P Global. Independent: Matt Levine's Money Stuff,
  The Transcript.
- **Technology** — Primary: company blogs, earnings calls, product launches. Analysis: Stratechery,
  The Information. Semis/hardware: SemiAnalysis. Press: Ars Technica, The Verge, Semafor Tech.
- **Artificial Intelligence** — Primary: lab blogs (Anthropic, OpenAI, Google DeepMind), arXiv.
  Independent: Interconnects (Nathan Lambert), Import AI (Jack Clark), The Batch. Compute/economics:
  SemiAnalysis. Business: The Information, Stratechery.
- **Digital assets / crypto** — Primary: issuer & ETF disclosures, SEC/CFTC releases, exchange data.
  On-chain/market data: Coin Metrics, Glassnode, Kaiko. News/analysis: The Block, plus the markets
  press above.

## How briefings get made (day to day)

The project now runs on a **prompt library** (`prompts.json`), not a single daily steer.

1. Run `main.py` (PyCharm Run button, or `python main.py`) — the prompt-library window opens. Add,
   edit, enable/disable, and delete named prompts. Each prompt is a full instruction for one briefing.
   The window has no model and needs no API key; it only edits `prompts.json`.
2. In Claude Code, say **"make my daily briefing."** For **each enabled prompt**, Claude researches it
   (editorial standard + preferred sources above; honor any length the prompt states, else ~700 words)
   and writes the script to `briefings/<id>.txt`. Then it publishes the whole batch to the public feed:

   ```bash
   # after all briefings/<id>.txt are written for today; runs in the Spotify conda env
   conda run -n Spotify --no-capture-output python publish_feed.py --summaries <summaries.json>
   ```

   `publish_feed.py` — for each enabled prompt: TTS (`episode.synthesize`) → `feed.add_episode(...)`
   (copies the mp3 to `docs/audio/<id>-<date>.mp3`, records it in `feed_state.json`) → then
   `feed.build_feed()` rewrites `docs/feed.xml` → `git add docs feed_state.json && commit && push`.
   GitHub Pages serves the update; Spotify re-ingests on its next refresh (minutes to a few hours).
   `--summaries` is a JSON map `{prompt_id: summary}` for episode descriptions (else the prompt name).
3. This **auto-publishes** — no approval step (standing authorization). Claude reports a table of
   name → episode, and stops only to surface an error (a failed prompt is skipped and the batch
   continues; the feed still rebuilds/pushes with the successful ones).

Identity is by **GUID** (`<prompt_id>-<date>`), unique per topic per day. Re-running the same prompt on
the same date overwrites that day's episode in place (idempotent); a new date adds a new episode.

### Re-publishing one prompt manually

```python
import feed
from episode import synthesize
mp3 = synthesize("briefings/<id>.txt")
feed.add_episode("<id>", "<Name>", "<summary>", mp3, "<YYYY-MM-DD>")
feed.build_feed()
# then: git add docs feed_state.json && git commit -m "…" && git push origin main
```

## Files & tools

- `config.py` — single source of shared constants + `configure_logging()`. Public-podcast constants:
  `PODCAST_TITLE`/`AUTHOR`/`OWNER_NAME`/`EMAIL`/`LANGUAGE`/`DESCRIPTION`/`CATEGORY`/`SUBCATEGORY`,
  `FEED_BASE_URL` (`https://marange63.github.io/Spotify`), `DOCS_DIR`/`DOCS_AUDIO_DIR`/`FEED_FILE`/
  `COVER_FILE`, `FEED_STATE_FILE`. Legacy: `SHOW_ID`, `VOICE`, `TTS_MAX_RETRIES`, `S2S`.
- `main.py` — the prompt-library manager window (project entry point / green Run button).
- `prompts.json` — the prompt library. Edited by the window, read by the batch. (The
  `last_episode_uri`/`last_published` fields are legacy Save-to-Spotify tracking; the public feed tracks
  episodes in `feed_state.json` instead.)
- `library.py` — read/write + add/update/delete helpers for `prompts.json`.
- **`feed.py`** — podcast RSS. `add_episode(prompt_id, name, summary, mp3_path, date)` copies the mp3 to
  `docs/audio/<id>-<date>.mp3` and records it (bytes + duration via `mutagen`) in `feed_state.json`;
  `build_feed()` renders `docs/feed.xml` (iTunes tags, newest-first). Archive model, stable per-day GUIDs.
- **`publish_feed.py`** — the daily batch: synth → `add_episode` per enabled prompt → `build_feed` →
  git commit + push. Flags: `--date`, `--summaries <json>`, `--no-push`.
- **`feed_state.json`** — the accumulating episode archive the feed is built from (source of truth).
- **`docs/`** — the GitHub Pages site: `cover.jpg` (1500×1500), `index.html`, `.nojekyll`, `feed.xml`,
  `audio/<id>-<date>.mp3`. Served at `https://marange63.github.io/Spotify/` from `main` `/docs`.
- **`tools/`** — `make_cover.py` (regenerates the cover via Pillow), `seed_feed.py` (one-off backfill).
- `episode.py` — **used only for TTS now**: `synthesize` + resilient paragraph-wise `_synthesize`. The
  `publish_replacing`/`upload_episode`/`wait_ready`/`delete_episode` helpers are the retired private path.
- `briefings/<id>.txt` / `.mp3` — per-prompt scripts and working audio (the `.mp3` is git-ignored; the
  published copy lives under `docs/audio/`).
- Dependencies (in the **`Spotify`** conda env): `edge-tts`, `aiohttp`, `Pillow`, `mutagen`.
- Legacy private path: binary `%USERPROFILE%\bin\save-to-spotify.exe`; private show **Daily Briefings**
  = `spotify:show:033LxzC8UHlbiJmWLw3n2K` (episodes there are not publicly shareable).
- `tests/` — stdlib `unittest` suite. Run: `python -m unittest discover -s tests -t .`
- Legacy: root `briefing.txt` / `briefing.mp3` are leftovers from the earlier single-file flow.

## TTS reliability

Microsoft's edge-tts endpoint intermittently drops mid-stream on long inputs (WinError 64 /
ClientConnectorError), truncating the mp3. `episode._synthesize` handles this by synthesizing **one
short WebSocket per paragraph with retries**, then concatenating — a drop only re-does one paragraph.
Expect retry noise in the batch log; a complete run is what matters.
