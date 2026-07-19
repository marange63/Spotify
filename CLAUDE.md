# Daily Briefings project

**Goal:** a public daily podcast of expert-level audio briefings, **Cautious Optimism Briefings**. You
keep a library of standing prompts (topics); on command, Claude Code turns each into a fresh, researched
spoken briefing and publishes it as a new episode in the show.

**How it's built (public podcast via self-hosted RSS):** a key-free Tkinter window (`main.py`) manages
the prompt library (`prompts.json`); Claude Code does the research + writing; `edge-tts` makes the
audio; `feed.py` builds a podcast RSS feed and the audio + cover + `feed.xml` + per-episode transcripts
are served publicly by GitHub Pages out of `./docs`; Spotify for Creators ingests that feed URL.
Publishing is a `git push` — Pages serves it and Spotify re-ingests on its refresh schedule.

**Archive model:** each publish is a **new, permanent episode** with a unique per-day GUID
(`<prompt_id>-<YYYY-MM-DD>`), so followers get normal new-episode notifications and a browsable
back-catalogue.

- **Show:** Cautious Optimism Briefings · Feed: https://marange63.github.io/Spotify/feed.xml
- **Pages site:** https://marange63.github.io/Spotify/ (served from `main` branch, `/docs` folder)
- **Owner/verification email:** wamfour@gmail.com (in the feed's `itunes:owner`)

The editorial standard below is the always-on core — the soul of the product. Operational mechanics
live on demand: the **`daily-briefing` skill** (the four-stage pipeline, publishing, novelty policy,
failure rules) and **`docs/ARCHITECTURE.md`** (every module, TTS internals, the staging feed, and the
known-working-tree note). A retired *private* "Save to Spotify" path still lingers in `episode.py`
(kept only for its `synthesize` TTS helper); details in `docs/ARCHITECTURE.md`.

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
- Cut filler and hedging — every sentence should carry information or judgment. But high signal
  means selective, not compressed: give each point the sentences it needs to land (see
  Listenability below), and cut whole low-value items rather than squeezing all of them in.
- Use the **freshest** available data (search at authoring time); flag what changed vs. the prior read.

### Listenability (audio-first delivery)

These briefings are heard once, straight through — the listener cannot skim back or re-read.
Keep the density of *insight* high, but pace the *delivery* for the ear. This is not
simplification: cut breadth and clutter, never sophistication.

- **One spine per briefing.** Choose an ordering with a logic to it (a through-line, an
  escalation, cause→effect) and make each item hand off to the next. Never deliver N
  unrelated blocks back-to-back.
- **Fewer items, fully developed.** Better to cover 3–4 developments with real analysis
  than 7 in headline form. If an item only merits two sentences, fold it into a related
  item or drop it.
- **Signpost every transition.** One short clause that tells the listener where we're
  going and why it follows ("The same capex logic shows up in..."; "Now the other side
  of that trade..."). Never jump topics cold.
- **Spoken syntax.** One idea per sentence. No nested subordinate clauses, no mid-sentence
  parenthetical asides, subject up front. If a sentence needs a comma map, split it.
- **Pace the numbers.** At most one or two figures per point, each anchored to a comparison
  ("double last quarter's run-rate"), never a string of statistics in one breath. Prefer
  the one number that carries the argument over three that decorate it.
- **Re-ground on return.** When a company, person, or figure reappears after a gap,
  re-name it briefly — no long pronoun chains or "the former" / "the latter."
- **Land each point before leaving it.** Close every item with one plain sentence of
  "so what" before the transition, so the listener banks the takeaway.

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

## Operations (on demand)

The prompt library (`prompts.json`) drives everything; edit it in the `main.py` window (no model, no
API key — it only edits `prompts.json`). Two on-demand homes hold the mechanics so this file stays
lean:

- **`daily-briefing` skill** — invoke it for **"make my daily briefing"** and all pipeline work. Key
  always-on facts to know without loading it: "make my daily briefing" is a **standing, auto-publishing
  command** — re-read `prompts.json` fresh, run the four-stage pipeline (researcher → analyst-editor →
  writer → reviewer, gated by `orchestrator.py`, published by `publish_feed.py`) for every enabled
  prompt, publish and push, and **run to completion without pausing to confirm**. Novelty defaults:
  **relaxed** for interactive runs, **strict** for the scheduled 5 AM job. The confirmation email is
  **disabled** — never send it (an ntfy phone push replaces it, fired automatically).
- **`docs/ARCHITECTURE.md`** — read it for how any module works (`config.py`, `feed.py`,
  `orchestrator.py`, `publish_feed.py`, the four `.claude/agents/`, TTS reliability + pronunciation
  internals, enclosure-URL cache-busting, the staging feed, and the known-working-tree note).
