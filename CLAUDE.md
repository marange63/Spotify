# Daily Briefings project

**Goal:** a public daily podcast of expert-level audio briefings, **Cautious Optimism Briefings**. You
keep a library of standing prompts (topics); on command, Claude Code turns each into a fresh, researched
spoken briefing and publishes it as a new episode in the show.
co
**How it's built (current — public podcast via self-hosted RSS):** a key-free Tkinter window (`main.py`)
manages the prompt library (`prompts.json`); Claude Code does the research + writing; `edge-tts` makes
the audio; **`feed.py` builds a podcast RSS feed and the audio + cover + `feed.xml` + per-episode
transcripts are served publicly by GitHub Pages out of `./docs`; Spotify for Creators ingests that feed
URL.** This makes episodes public and shareable (links work for anyone; the show is searchable on
Spotify). Publishing is a `git push` — Pages serves it and Spotify re-ingests on its refresh schedule.

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
one-line sign-off. Voice: `en-US-AndrewNeural`.

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
2. In Claude Code, say **"make my daily briefing."** **First re-read `prompts.json` fresh from disk**
   (do not trust an earlier read from this session — the user may have added prompts in the window since)
   and write a script for **every** currently-enabled prompt; the count can change mid-session. For
   **each enabled prompt**, Claude researches it (editorial standard + preferred sources above; honor
   any length the prompt states, else ~700 words) and writes the script to `briefings/<id>.txt`.

   **Synthesis prompts (`"kind": "synthesis"` in `prompts.json`, e.g. `throughline` — "The
   Throughline"):** these are NOT researched. Write them **last**, after every normal briefing for
   today is on disk, by reading those other `briefings/<id>.txt` scripts and synthesizing across them
   (the day's cross-domain through-lines + a "where the briefings disagree" beat). Do **no** fresh web
   research for a synthesis prompt — it only connects and elevates what the other briefings already
   said. `publish_feed.py` publishes synthesis prompts last so they sort to the top of the feed.

   Then publish the whole batch to the public feed:

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

   **Run to completion with zero further input.** "Make my daily briefing" is a single standing
   command: from that point Claude researches every enabled prompt, writes the scripts, publishes,
   and reports back — **without pausing to ask or confirm anything.** Do not ask which prompts,
   whether to publish, whether to push, about novelty, or about summaries — all of those have
   defaults below. Make every routine judgment call silently and keep going. The **only** reasons to
   stop mid-run are a hard blocker Claude cannot resolve itself (e.g. every prompt's research failed,
   or `git push` is rejected). Defaults, applied without asking:
   - **Which prompts:** every `enabled` prompt in `prompts.json`.
   - **Novelty:** relaxed (interactive runs are treated as testing — see the novelty policy below);
     only apply the no-repeat rule if the user explicitly asks in the same message.
   - **Summaries:** let `publish_feed.py` auto-derive them from each script (pass `--summaries` only
     if the user supplied summaries); never stop to hand-write them.
   - **Publish + push:** always, automatically. No "ready to publish?" checkpoint.
   - **Research judgment:** pick sources and framing per the editorial standard; never ask the user
     to choose an angle or resolve an ambiguity that Claude can decide reasonably on its own.
4. **Confirmation email — ⚠️ CURRENTLY DISABLED (since 2026-07-08); do not send it.** There is no
   working delivery path (the SMTP env vars `BRIEFING_SMTP_USER` / `BRIEFING_SMTP_PASS` aren't set, and
   the Gmail integration's token is expired and can only draft anyway). So the send is commented out in
   `publish_feed.py`, the `--email` flag is removed from `daily_run.ps1`, and interactive runs should
   **not** try to send via Gmail either. Publishing is unaffected — just skip the email and say so.
   Re-enable per the `publish-confirmation-email-blocked` memory once creds/re-auth are fixed.
   - **Intended design (for when it's revived):** after a successful publish, a summary email (subject
     with the date + episode count; body listing each briefing → transcript/audio links, failed/skipped
     prompts flagged) to **wamfour@gmail.com**, fired only when ≥1 episode published. Scheduled run:
     `publish_feed.py --email` via `notify.py` (Gmail SMTP; missing creds → warn + skip, never fails a
     publish). Interactive: Claude sends it after publishing (do **not** also pass `--email`, to avoid
     a double-send).

Identity is by **GUID** (`<prompt_id>-<date>`), unique per topic per day. Re-running the same prompt on
the same date overwrites that day's episode in place (idempotent); a new date adds a new episode.

### Novelty policy (avoid repeating the prior day)

The **scheduled 5 AM run is strict by default**: for each prompt it first reads the existing
`briefings/<id>.txt` (the previous run's briefing on that topic — it's still on disk before the
overwrite) and must **not** repeat the same topics/themes/framing **unless there's genuinely new
news or data** since then. This is 1-day memory (just the immediately-prior briefing), and it's an
extension of the editorial standard's "lead with what's new."

- **Scheduled (`tools/daily_run.ps1`, no args):** strict — the novelty clause is in the phase-1 prompt.
- **Manual testing (`tools/daily_run.ps1 -RepeatOK`):** relaxed — writes fresh regardless of yesterday.
- **Interactive ("make my daily briefing" via Claude in a session):** treat as **relaxed by default**
  (this is testing); only apply the no-repeat rule if the user asks for it.

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
  `FEED_BASE_URL` (`https://marange63.github.io/Spotify`), `DOCS_DIR`/`DOCS_AUDIO_DIR`/
  `DOCS_TRANSCRIPTS_DIR`/`FEED_FILE`/`COVER_FILE`, `FEED_STATE_FILE`. Legacy: `SHOW_ID`, `VOICE`,
  `TTS_MAX_RETRIES`, `S2S`.
- `main.py` — the prompt-library manager window (project entry point / green Run button).
- `prompts.json` — the prompt library. Edited by the window, read by the batch. A prompt may carry
  `"kind": "synthesis"` (currently `throughline`, "The Throughline") — a NOT-researched prompt authored
  last by synthesizing the day's other briefings (see step 2 + the Novelty section). (The
  `last_episode_uri`/`last_published` fields are legacy Save-to-Spotify tracking; the public feed tracks
  episodes in `feed_state.json` instead.)
- `library.py` — read/write + add/update/delete helpers for `prompts.json`. Mutations from the window go
  through the clobber-proof `apply_new`/`apply_update`/`apply_delete` (reload file → apply one change →
  write), so an external edit (e.g. Claude fixing an id) made while the window is open is never
  overwritten; `save_merged` (window-safe merge) and `save` (authoritative write) preserve the `kind` field.
- **`feed.py`** — podcast RSS. `add_episode(prompt_id, name, summary, mp3_path, date)` copies the mp3 to
  `docs/audio/<id>-<date>.mp3`, records it (bytes + duration via `mutagen`, plus a tz-aware
  `published_at` and transcript filenames) in `feed_state.json`, and writes the verbatim transcript to
  `docs/transcripts/<id>-<date>.txt` + `.html` (from `briefings/<id>.txt`). `build_feed()` renders
  `docs/feed.xml` (iTunes tags, newest-first, `<pubDate>` from the real `published_at`, `<podcast:transcript>`
  tags + a "Read the full transcript" link in each description). Archive model, stable per-day GUIDs.
- **`publish_feed.py`** — the daily batch: synth → `add_episode` per enabled prompt → `build_feed` →
  git commit + push. `_ordered_enabled` publishes `kind:"synthesis"` prompts LAST, so The Throughline
  gets the newest timestamp and sorts to the top of the feed. Flags: `--date`, `--summaries <json>`,
  `--no-push`, `--require-fresh`, `--email` (the email send is currently disabled — see step 4).
- **`notify.py`** — composes + sends the "briefings published" confirmation email to `config.NOTIFY_EMAIL`
  (wamfour@gmail.com). `build_message(results, date)` is a pure, tested composer; `send_publish_summary`
  sends via Gmail SMTP using a **Google App Password** from env vars (`BRIEFING_SMTP_USER`,
  `BRIEFING_SMTP_PASS`; optional `BRIEFING_NOTIFY_TO`). Missing creds → warn + skip (never raises). See
  the module docstring for the one-time `setx` setup.
- **`feed_state.json`** — the accumulating episode archive the feed is built from (source of truth).
- **`docs/`** — the GitHub Pages site: `cover.jpg` (1500×1500), `index.html`, `.nojekyll`, `feed.xml`,
  `audio/<id>-<date>.mp3`, `transcripts/<id>-<date>.txt` + `.html`. Served at
  `https://marange63.github.io/Spotify/` from `main` `/docs`. (Transcripts: Apple Podcasts and
  Podcasting 2.0 apps read the `<podcast:transcript>` tag; Spotify ignores RSS transcripts, so the
  description link is how Spotify listeners reach the hosted page.)
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
