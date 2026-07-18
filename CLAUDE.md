# Daily Briefings project

**Goal:** a public daily podcast of expert-level audio briefings, **Cautious Optimism Briefings**. You
keep a library of standing prompts (topics); on command, Claude Code turns each into a fresh, researched
spoken briefing and publishes it as a new episode in the show.

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

**Staging feed (in progress):** a second public show, "Cautious Optimism Briefings (Staging)", for
trialing briefings without touching production. A minimal hand-seeded feed is live at
`docs/test/feed.xml` (state: `feed_state_test.json`; seeded 2026-07-09, commit `73b812b`) so there's a
real URL to submit at creators.spotify.com. **Status: seed published; the user still has to submit +
verify the show on Spotify, and the code wiring (`Channel` in `config.py`, channel-aware `feed.py`,
`publish_feed.py --test`/`--only`) is NOT built yet** — no Python currently reads `docs/test/` or
`feed_state_test.json`, so nothing publishes there; the daily batch touches production only. Design +
sequencing live in the `feature-idea-staging-feed` memory.

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

## How briefings get made (day to day)

The project now runs on a **prompt library** (`prompts.json`), not a single daily steer.

1. Run `main.py` (PyCharm Run button, or `python main.py`) — the prompt-library window opens. Add,
   edit, enable/disable, and delete named prompts. Each prompt is a full instruction for one briefing.
   The window has no model and needs no API key; it only edits `prompts.json`.
2. In Claude Code, say **"make my daily briefing."** **First re-read `prompts.json` fresh from disk**
   (do not trust an earlier read from this session — the user may have added prompts in the window
   since); the count can change mid-session. Then run the **four-stage pipeline** (next section) for
   **every** currently-enabled prompt — interactive runs default to **relaxed** novelty — and publish
   the batch:

   ```bash
   # after the pipeline has approved today's scripts; runs in the Spotify conda env
   conda run -n Spotify --no-capture-output python publish_feed.py --require-fresh
   ```

   `publish_feed.py` — for each enabled prompt: TTS (`episode.synthesize`) → `feed.add_episode(...)`
   (copies the mp3 to `docs/audio/<id>-<date>.mp3`, records it in `feed_state.json`) → then
   `feed.build_feed()` rewrites `docs/feed.xml` → `git add docs feed_state.json && commit && push`.
   GitHub Pages serves the update; Spotify re-ingests on its next refresh (minutes to a few hours).
   `--require-fresh` publishes only scripts written (approved) today — skipped/failed prompts keep a
   stale `briefings/<id>.txt` and are excluded automatically. `--summaries` is a JSON map
   `{prompt_id: summary}` for episode descriptions (else auto-derived from the script).

### Four-stage pipeline (how each script is produced)

Each briefing script is produced by four separated Claude Code subagents (defined in
`.claude/agents/`) with persistent file handoffs under `runs/<date>/<prompt_id>/`, gated by
`orchestrator.py` (stdlib CLI; the ONLY path allowed to copy a script into `briefings/`). The
Writer and Reviewer are deliberately **separate agents with separate contexts** so the reviewer
never grades its own writing:

1. **Init:** `python orchestrator.py init --date <today> --novelty strict|relaxed` — creates
   `runs/<date>/<id>/` for every enabled prompt (normal prompts first, synthesis last), records the
   batch in `runs/<date>/run.json`, and prints the plan. Idempotent: re-init preserves statuses, so
   an interrupted batch can resume.
2. **For each normal prompt, in the plan's order:**
   - **Researcher** (subagent `researcher`; web search allowed): gathers the strongest recent
     material → `runs/<date>/<id>/research.json`. Pass it the prompt id/name/text, the date, and
     the output path. Then `python orchestrator.py validate research <path>`.
   - **Analyst-Editor** (subagent `analyst-editor`; no web): judges the dossier against the prior
     briefing (`briefings/<id>.txt`, still on disk), the **last 5 days** of this topic's
     transcripts AND the last 5 Throughline transcripts (the compressed cross-topic memory), and
     the editorial standard; decides write-or-skip, thesis, lead, ordering, and any **emergent
     5-day patterns/callbacks** → `runs/<date>/<id>/editorial_plan.json`. Pass it the
     novelty mode. Then `validate plan <path>`. If `decision` is `skip`:
     `python orchestrator.py mark <id> --date <today> --status skipped --stage plan --reason "…"`
     and move to the next prompt (no Writer).
   - **Writer** (subagent `writer`; no web): writes the script from the dossier + plan →
     `draft.txt` only. It may use only figures that carry a verbatim `quote` in `research.json`.
   - **Reviewer** (subagent `reviewer`; no web; fresh context — did NOT write the draft): critiques
     the draft against the dossier, plan, and editorial standard, **audits every figure against the
     research quotes**, revises once → `review.json` + `final.txt`. Approve is not its default
     outcome. Then `validate review <path>` and
     `python orchestrator.py approve <id> --date <today>` — which copies `final.txt` to
     `briefings/<id>.txt` **only if** the review says `approve`.
3. **Synthesis prompts last** (`"kind": "synthesis"`, e.g. `throughline` — "The Throughline"): NOT
   researched and no editorial plan. Run **Writer then Reviewer** (no Researcher/Analyst-Editor),
   giving both the day's APPROVED `briefings/<id>.txt` files as source material (no fresh web
   research, no new facts). Both also read the last 5 `docs/transcripts/throughline-*.txt` for
   cross-day memory — continuing/escalating/broken patterns are named explicitly, and the
   reviewer audits cross-day claims against those transcripts. Same
   `review.json`/`final.txt`/`approve` flow. If zero prompts were approved today, mark the
   synthesis prompt skipped. `publish_feed.py` publishes synthesis prompts last so they sort to the
   top of the feed.
4. **Report:** `python orchestrator.py status --date <today>` — per-prompt outcomes + approved ids.

**Failure rules (always continue the batch; one bad prompt never stops the rest):**
- `validate` fails → tell the same agent (or fix directly) to repair the artifact **once**; if it
  still fails, `mark <id> --status failed --stage <stage> --reason "…"` and move on.
- Research `status: "insufficient"` → the Analyst-Editor may still decide, but skipping is the
  expected outcome; `failed` research → mark failed, move on.
- Writer or Reviewer failure → retry that subagent **once**, then mark failed.
- Review decision `skip`/`failed` → mark accordingly (a reviewer skip is a normal editorial
  outcome, not an error); `approve` will refuse the copy, so the prompt cannot publish.
- TTS/feed/git failures keep their existing behavior in `publish_feed.py` (per-prompt try/except;
  the batch still publishes the successful episodes).
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
   - **Novelty:** relaxed (interactive runs are treated as testing — pass `--novelty relaxed` to
     `orchestrator.py init`; see the novelty policy below); use strict only if the user explicitly
     asks in the same message.
   - **Skips:** an Analyst-Editor or reviewer skip is a normal outcome, not an error — report it in
     the results table and keep going; never force a weak briefing through.
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

Novelty is enforced by the **Analyst-Editor** stage, which reads the existing `briefings/<id>.txt`
(the previous run's briefing on that topic — still on disk before the overwrite) and recent
transcripts. The mode is set at `orchestrator.py init --novelty strict|relaxed` and passed to each
Analyst-Editor invocation. It's an extension of the editorial standard's "lead with what's new."

- **strict** (scheduled `tools/daily_run.ps1`, no args): reject material repeating the prior
  briefing's topics/themes/framing **unless there's genuinely new news or data** since then. If
  nothing worthwhile clears the bar, the prompt is **skipped** for the day (no episode) — a skipped
  day beats a padded one.
- **relaxed** (`tools/daily_run.ps1 -RepeatOK`, and interactive "make my daily briefing" runs):
  repeated material may be used when helpful; fresh evidence and framing still preferred. Skips can
  still happen if the Analyst-Editor finds nothing worth saying.

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
  `DOCS_TRANSCRIPTS_DIR`/`FEED_FILE`/`COVER_FILE`, `FEED_STATE_FILE`. Notifications: `NOTIFY_EMAIL`,
  plus `NTFY_SERVER`/`NTFY_TOPIC` (the ntfy.sh phone push; topic overridable via env
  `BRIEFING_NTFY_TOPIC`, set to `""` to disable). Legacy: `SHOW_ID`, `VOICE`, `TTS_MAX_RETRIES`, `S2S`.
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
  git commit + push. Publishes `kind:"synthesis"` prompts LAST (ordering shared with
  `orchestrator.ordered_enabled`), so The Throughline gets the newest timestamp and sorts to the top
  of the feed. Flags: `--date`, `--summaries <json>`, `--no-push`, `--require-fresh`, `--email` (the
  email send is currently disabled — see step 4), `--no-notify`. **ntfy push:** after a successful
  git push with ≥1 episode published, `_notify_ntfy` POSTs a one-line "briefings published" summary
  to the owner's phone via `config.NTFY_TOPIC` on ntfy.sh (stdlib `urllib`, best-effort — a
  notification failure is logged and never fails the publish). On by default (interactive AND the
  5 AM scheduled run, which calls `publish_feed.py --require-fresh`); suppress with `--no-notify` or
  by setting `NTFY_TOPIC`/env `BRIEFING_NTFY_TOPIC` to `""`. This is the working replacement for the
  disabled confirmation email.
- **`orchestrator.py`** — deterministic gates of the four-stage pipeline (stdlib only, no agent
  runner): `init` (run dirs + `runs/<date>/run.json`, idempotent), `validate research|plan|review`
  (schema checks with readable errors — `validate research` also enforces the verbatim-quote
  contract on each lead candidate's `important_facts`), `approve` (the ONLY path that copies a
  `final.txt` to `briefings/<id>.txt` — refuses unless `review.json` says `approve`), `mark`
  (record skip/failure), `status` (outcome table + approved ids). See the pipeline section above.
- **`.claude/agents/`** — the four subagent definitions: `researcher.md` (web search → structured
  `research.json` dossier with a verbatim `quote` per important fact; never writes the briefing),
  `analyst-editor.md` (no web; novelty + skepsis + story selection → `editorial_plan.json`, may
  decide `skip`), `writer.md` (no web; drafts the script → `draft.txt` only, using only quoted
  figures; also drafts synthesis prompts from the day's approved briefings), `reviewer.md` (no web;
  independent fresh-context editor — critiques the draft, audits every figure against the research
  quotes, revises once → `review.json` + `final.txt`; approve is not its default outcome). Each
  pins a `model:` in its frontmatter — `sonnet` for the throughput stages (researcher, writer),
  `opus` for the judgment stages (analyst-editor, reviewer) — and these pins take precedence over
  whatever model the invoking session uses (interactive **or** the 5 AM CLI `--model`). Change a
  role's cost/quality by editing its frontmatter `model:`, not the caller.
- **`runs/<date>/<prompt_id>/`** — git-ignored per-day pipeline artifacts: `research.json`,
  `editorial_plan.json`, `draft.txt`, `review.json`, `final.txt`, plus `runs/<date>/run.json` (batch
  state). Same-day re-runs overwrite in place; the audit trail for "why did this episode say that /
  why was it skipped" lives here.
- **`notify.py`** — composes + sends the "briefings published" confirmation email to `config.NOTIFY_EMAIL`
  (wamfour@gmail.com). `build_message(results, date)` is a pure, tested composer; `send_publish_summary`
  sends via Gmail SMTP using a **Google App Password** from env vars (`BRIEFING_SMTP_USER`,
  `BRIEFING_SMTP_PASS`; optional `BRIEFING_NOTIFY_TO`). Missing creds → warn + skip (never raises). See
  the module docstring for the one-time `setx` setup.
- **`feed_state.json`** — the accumulating episode archive the feed is built from (source of truth).
- **`feed_state_test.json`** / **`docs/test/`** — the staging feed's state + hosted files (see the
  Staging feed note above). Hand-seeded; not yet wired into any code.
- **`docs/`** — the GitHub Pages site: `cover.jpg` (1500×1500), `index.html`, `.nojekyll`, `feed.xml`,
  `audio/<id>-<date>.mp3`, `transcripts/<id>-<date>.txt` + `.html`. Served at
  `https://marange63.github.io/Spotify/` from `main` `/docs`. (Transcripts: Apple Podcasts and
  Podcasting 2.0 apps read the `<podcast:transcript>` tag; Spotify ignores RSS transcripts, so the
  description link is how Spotify listeners reach the hosted page.)
- **`tools/`** — `daily_run.ps1` (the unattended 5 AM Task Scheduler entry point: phase 1 headless
  Claude runs the four-stage pipeline, phase 2 `publish_feed.py --require-fresh` publishes; flags:
  `-RepeatOK` = relaxed novelty, `-NoPublish` = dry run that skips phase 2 entirely — agents and
  `runs/` artifacts only, no TTS/feed/commit/push; logs to `logs\daily-<date>.log`). **Model
  pinning + fallback:** the four subagents pin their own models in `.claude/agents/*.md`
  frontmatter (researcher=`sonnet`, analyst-editor=`opus`, writer=`sonnet`, reviewer=`opus`), and
  those pins **override** the CLI `--model`/`--fallback-model` for the actual research/editing/
  writing/reviewing.
  Phase 1's `--model claude-sonnet-5` + `--fallback-model claude-opus-4-8` govern only the
  lightweight **parent orchestrator session** (reading files, running `orchestrator.py`,
  dispatching subagents). **Fable 5 is deliberately not used anywhere in the scheduled job** (its
  usage-limit deaths killed past runs), and because the job passes explicit `--model` flags, an
  interactive terminal left on Fable (or anything else) can never leak into the 5 AM run. The
  Opus-4.8 re-invoke on any still-pending/failed prompt is kept as a safety net, resuming via the
  idempotent orchestrator (only pending/failed prompts re-done).
  `make_cover.py` (regenerates the cover via Pillow), `seed_feed.py` (one-off backfill).
- **`logs/`** — git-ignored per-day logs from the scheduled run (`daily-<YYYY-MM-DD>.log`); check the
  latest one first when asked how the morning run went.
- `episode.py` — **used only for TTS now**: `synthesize` + resilient paragraph-wise `_synthesize`. The
  `publish_replacing`/`upload_episode`/`wait_ready`/`delete_episode` helpers are the retired private path.
- `briefings/<id>.txt` / `.mp3` — per-prompt scripts and working audio (the `.mp3` is git-ignored; the
  published copy lives under `docs/audio/`).
- Dependencies (in the **`Spotify`** conda env): `edge-tts`, `aiohttp`, `Pillow`, `mutagen` —
  declared in `environment.yml` (conda) / `requirements.txt` (pip); both files are the same list.
- Legacy private path: binary `%USERPROFILE%\bin\save-to-spotify.exe`; private show **Daily Briefings**
  = `spotify:show:033LxzC8UHlbiJmWLw3n2K` (episodes there are not publicly shareable).
- `tests/` — stdlib `unittest` suite. Run: `python -m unittest discover -s tests -t .`
- Legacy: root `briefing.txt` / `briefing.mp3` are leftovers from the earlier single-file flow.

## TTS reliability & quality

Microsoft's edge-tts endpoint intermittently drops mid-stream on long inputs (WinError 64 /
ClientConnectorError), truncating the mp3. `episode._synthesize` handles this by **grouping
paragraphs into ~`TTS_CHUNK_CHARS` (1500) chunks**, synthesizing each over one WebSocket **with
retries**, then concatenating. A mid-stream drop re-does only that chunk; a chunk that exhausts its
retries **falls back to per-paragraph** synthesis (the old resilient behavior), so a drop still costs
one paragraph at most. Expect retry noise in the batch log; a complete run is what matters.

**Cadence:** edge-tts pads each call's audio with a little silence, so the previous
one-call-per-paragraph approach left an audible gap after *every* paragraph (unnatural mid-thought
pauses). Grouping into fewer, larger chunks removes most joins; `config.TTS_RATE` (`-4%`) slows
delivery slightly for a more natural read. Set `TTS_RATE="+0%"` to disable.

**Pronunciation:** `episode._PRONUNCIATION` is a respelling table applied **only to the TTS input**
(DRAM→"dee-ram", HBM/PJM→spelled letters, GENIUS→"Genius", capex→"cap-ex"). The published transcript
is built from `briefings/<id>.txt` separately, so respellings never leak into the written text. Add
new offenders to that list in `episode.py`. Deeper control (true phonemes/lexicons) would require
moving off free edge-tts to Azure Speech (same Andrew voice, paid, full SSML) — not done yet.

A chunk can still exhaust all `TTS_MAX_RETRIES` (6) attempts even after the per-paragraph fallback,
which fails **that episode only** (the batch continues and still publishes the rest — e.g.
2026-07-12, when capital-markets-radar failed and 7 of 8 episodes shipped). The script is still on
disk, so the fix is the "Re-publishing one prompt manually" snippet above once the endpoint recovers.

## Known working-tree state

The scheduled run rewrites `briefings/<id>.txt` daily, but `publish_feed.py` commits only `docs/` +
`feed_state.json` — so **modified `briefings/*.txt` in `git status` after a morning run is normal**,
not a sign of an interrupted publish. Root `briefing.txt`/`briefing.mp3` are inert legacy files.
