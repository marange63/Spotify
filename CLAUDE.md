# Daily Briefings project

**Goal:** a personal daily podcast of expert-level audio briefings. You keep a library of standing
prompts (topics); on command, Claude Code turns each into a fresh, researched spoken briefing and
publishes it as an episode in the **Daily Briefings** Spotify show — replacing the prior version of
each so the feed stays current.

**How it's built:** a key-free Tkinter window (`main.py`) manages the prompt library (`prompts.json`);
Claude Code does the research + writing; `edge-tts` makes the audio; the `save-to-spotify` CLI
publishes. One episode per enabled prompt, identified by its Spotify episode URI.

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
2. In Claude Code, say **"make my daily briefing."** For **each enabled prompt**, Claude:
   - researches it (editorial standard + preferred sources above; honor any length the prompt states,
     else ~700 words) and writes the script to `briefings/<id>.txt`;
   - runs `episode.publish_replacing(...)` — TTS → upload a **dated** episode (`"<name> — <date>"`) to
     the Daily Briefings show → wait until READY → **delete that prompt's previous episode** (tracked
     by `last_episode_uri`) → save the new URI back into `prompts.json`.
   Then it deletes any `orphans` (episodes of prompts you removed in the window) and clears the list.
3. This **auto-publishes** — no approval step (standing authorization). Claude reports a table of
   name → link, and stops only to surface an error (a failed prompt keeps its old episode and the
   batch continues).

Identity is by **Spotify episode URI**, not by name — that's what makes "replace the previous version"
reliable. Deletion happens only *after* the replacement is READY, so a prompt is never without a live
episode. Exactly one live episode per enabled prompt.

### Re-running one prompt manually

To (re)publish a single prompt without the full batch:

```python
from episode import publish_replacing, SHOW_ID
publish_replacing("briefings/<id>.txt", "<Name> — <date>", "<summary>", SHOW_ID, "<prev_episode_uri or None>")
```

## Files & tools

- `config.py` — single source of shared constants (`SHOW_ID`, `VOICE`, `TTS_MAX_RETRIES`, paths, and
  `S2S` resolved via `shutil.which`) + `configure_logging()`. `library.py`/`episode.py` reference
  `config.*` at call time (so it's patchable in tests).
- `main.py` — the prompt-library manager window (project entry point / green Run button).
- `prompts.json` — the library: `{prompts: [{id, name, prompt, enabled, last_episode_uri,
  last_published}], orphans: []}`. Edited by the window, read + written by the batch.
- `library.py` — read/write + add/update/delete helpers for `prompts.json` (id stable across renames;
  delete tombstones the episode URI into `orphans`). The **window** saves via `save_merged`, which
  preserves the batch-owned tracking fields (`last_episode_uri` / `last_published` / `orphans`) from
  disk so a still-open window can't clobber them; the **batch** uses the authoritative `save`.
- `episode.py` — lifecycle helpers: `publish_replacing(text_path, title, summary, show_id, prev_uri)`,
  plus `synthesize` / `upload_episode` / `wait_ready` / `delete_episode`. Resilient paragraph-wise TTS.
- `briefings/<id>.txt` / `.mp3` — per-prompt scripts and audio.
- Binary: `%USERPROFILE%\bin\save-to-spotify.exe`. edge-tts runs in the **`Spotify`** conda env.
  Show: **Daily Briefings** = `spotify:show:033LxzC8UHlbiJmWLw3n2K`.
- CLI surface used: `upload --title/--summary/--show-id` (prints the new URI),
  `episodes status <id> --wait`, `episodes delete <id>`.
- `tests/` — stdlib `unittest` suite (no network, no extra deps): `test_library.py` (CRUD +
  `save_merged` merge/tombstone), `test_spotify.py` (CLI helpers, `subprocess` mocked), `test_tts.py`
  (resilient synth, edge-tts mocked). Run: `python -m unittest discover -s tests -t .`
- Legacy: root `briefing.txt` / `briefing.mp3` are leftovers from the earlier single-file flow;
  current per-prompt outputs live in `briefings/`. `daily_instructions.txt` is retired.

## TTS reliability

Microsoft's edge-tts endpoint intermittently drops mid-stream on long inputs (WinError 64 /
ClientConnectorError), truncating the mp3. `episode._synthesize` handles this by synthesizing **one
short WebSocket per paragraph with retries**, then concatenating — a drop only re-does one paragraph.
Expect retry noise in the batch log; a complete run is what matters.
