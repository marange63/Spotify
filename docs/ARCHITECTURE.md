# Architecture & file reference

Reference for the Cautious Optimism Briefings codebase. This is **on-demand** documentation —
read it when you need to know how a module works, debug the pipeline, or republish an episode.
The always-on editorial standard lives in `CLAUDE.md`; the daily pipeline workflow lives in the
`daily-briefing` skill.

## Files & tools

- `config.py` — single source of shared constants + `configure_logging()`. Public-podcast constants:
  `PODCAST_TITLE`/`AUTHOR`/`OWNER_NAME`/`EMAIL`/`LANGUAGE`/`DESCRIPTION`/`CATEGORY`/`SUBCATEGORY`,
  `FEED_BASE_URL` (`https://marange63.github.io/Spotify`), `DOCS_DIR`/`DOCS_AUDIO_DIR`/
  `DOCS_TRANSCRIPTS_DIR`/`FEED_FILE`/`COVER_FILE`, `FEED_STATE_FILE`. TTS: `VOICE`,
  `TTS_MAX_RETRIES`, `TTS_RATE`, `TTS_CHUNK_CHARS`. Notifications: `NOTIFY_EMAIL`,
  plus `NTFY_SERVER`/`NTFY_TOPIC` (the ntfy.sh phone push; topic overridable via env
  `BRIEFING_NTFY_TOPIC`, set to `""` to disable). Legacy: `SHOW_ID`, `S2S`.
- `main.py` — the key-free window (project entry point / green Run button). Two tabs: **Prompts**
  (the library editor, `PromptManager`) and **Run analyses** (`AnalysisViewer`, a read-only viewer
  of `analyses/<date>.md`). The viewer re-scans the folder when its tab is shown, so a run that
  finished while the window was open appears without a restart.
- `analyses.py` — the viewer's data layer: `list_dates()` (newest first) and `read(date)` over
  `config.ANALYSES_DIR`, mirroring `library`'s role so `main.py` holds no filesystem logic.
- **`run_report.py`** — stdlib CLI (`--date D [--json]`), read-only over `runs/<date>/`. Emits the
  deterministic per-run metrics — deep-dive firing, new facts, `contradictions`, `final.txt` word
  count vs. the prompt's stated floor, reviewer `overall`, and the "figure has no verbatim quote"
  soft-support flag count — that the after-run analysis cites. Reuses `orchestrator.run_status`.
- **`analyses/<date>.md`** — git-ignored (local-only) per-run agent-performance analysis, authored by
  Claude at the end of each run (see the `daily-briefing` skill's "Run analysis" step) and read in
  the `main.py` viewer tab. Fixed 6-section template; `run_report.py` supplies its numbers.
- `prompts.json` — the prompt library. Edited by the window, read by the batch. A prompt may carry
  `"kind": "synthesis"` (currently `throughline`, "The Throughline") — a NOT-researched prompt authored
  last by synthesizing the day's other briefings. (The `last_episode_uri`/`last_published` fields are
  legacy Save-to-Spotify tracking; the public feed tracks episodes in `feed_state.json` instead.)
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
  **Enclosure URLs carry a `?v=<publish-epoch>` cache-bust token** so replacing an already-ingested
  episode's audio in place forces Spotify to re-download it (see the `spotify-audio-url-cache-busting`
  memory); untouched episodes keep a stable URL.
- **`publish_feed.py`** — the daily batch: synth → `add_episode` per enabled prompt → `build_feed` →
  git commit + push. Publishes `kind:"synthesis"` prompts LAST (ordering shared with
  `orchestrator.ordered_enabled`), so The Throughline gets the newest timestamp and sorts to the top
  of the feed. Flags: `--date`, `--summaries <json>`, `--no-push`, `--require-fresh`, `--email` (the
  email send is currently disabled), `--no-notify`. **ntfy push:** after a successful
  git push with ≥1 episode published, `_notify_ntfy` POSTs a one-line "briefings published" summary
  to the owner's phone via `config.NTFY_TOPIC` on ntfy.sh (stdlib `urllib`, best-effort — a
  notification failure is logged and never fails the publish). On by default (interactive AND the
  5 AM scheduled run); suppress with `--no-notify` or by setting `NTFY_TOPIC`/env
  `BRIEFING_NTFY_TOPIC` to `""`. This is the working replacement for the disabled confirmation email.
- **`orchestrator.py`** — deterministic gates of the four-stage pipeline (stdlib only, no agent
  runner): `init` (run dirs + `runs/<date>/run.json`, idempotent), `validate research|plan|deep|review`
  (schema checks with readable errors — `validate research` also enforces the verbatim-quote
  contract on each lead candidate's `important_facts`; `validate deep` is an alias for the same
  checker, since `deep_research.json` shares the dossier schema; `validate plan` bounds
  `deep_dive_requests` at `MAX_DEEP_DIVE_REQUESTS`/`MAX_DEEP_DIVE_QUESTIONS`), `approve` (the ONLY path that copies a
  `final.txt` to `briefings/<id>.txt` — refuses unless `review.json` says `approve`), `mark`
  (record skip/failure), `status` (outcome table + approved ids).
- **`docs/pipeline-agent-flow.png`** — one-page visual of the agent flow: each stage's model, web access,
  inputs, outputs, responsibilities, the deterministic gate, and when the optional stage 2.5 runs.
  Regenerate with `python tools/make_pipeline_diagram.py` (base conda env — it needs matplotlib)
  after changing any stage.
- **`.claude/agents/`** — the four core subagent definitions plus one optional stage: `researcher.md`
  (web search → structured
  `research.json` dossier with a verbatim `quote` per important fact; never writes the briefing),
  `analyst-editor.md` (no web; novelty + skepsis + story selection → `editorial_plan.json`, may
  decide `skip`), `deep-researcher.md` (**optional stage 2.5**, web allowed — runs whenever the
  plan's `deep_dive_requests` is non-empty, in every novelty mode including the 5 AM job; answers ≤3 named evidence gaps
  on ONE approved item within ≤6 web calls → `deep_research.json`, same schema as the dossier plus a
  `contradictions` array; never proposes new stories, cannot reopen the plan — see the "Deep-dive
  stage (2.5)" section below for rationale, the do-not-re-gate decision, and run-reading notes),
  `writer.md` (no web;
  drafts the script → `draft.txt` only, using only quoted
  figures; also drafts synthesis prompts from the day's approved briefings), `reviewer.md` (no web;
  independent fresh-context editor — critiques the draft, audits every figure against the research
  quotes, revises once → `review.json` + `final.txt`; approve is not its default outcome). Each
  pins a `model:` in its frontmatter — `sonnet` for the throughput stages (researcher,
  deep-researcher, writer), `opus` for the judgment stages (analyst-editor, reviewer) — and these pins take precedence over
  whatever model the invoking session uses (interactive **or** the 5 AM CLI `--model`). Change a
  role's cost/quality by editing its frontmatter `model:`, not the caller.
- **`runs/<date>/<prompt_id>/`** — git-ignored per-day pipeline artifacts: `research.json`,
  `editorial_plan.json`, `deep_research.json` (optional — present only when a deep dive ran),
  `draft.txt`, `review.json`, `final.txt`, plus `runs/<date>/run.json` (batch
  state). Same-day re-runs overwrite in place; the audit trail for "why did this episode say that /
  why was it skipped" lives here.
- **`notify.py`** — composes + sends the "briefings published" confirmation email to `config.NOTIFY_EMAIL`
  (wamfour@gmail.com). `build_message(results, date)` is a pure, tested composer; `send_publish_summary`
  sends via Gmail SMTP using a **Google App Password** from env vars (`BRIEFING_SMTP_USER`,
  `BRIEFING_SMTP_PASS`; optional `BRIEFING_NOTIFY_TO`). Missing creds → warn + skip (never raises).
  **Currently disabled** — see the `publish-confirmation-email-blocked` memory.
- **`feed_state.json`** — the accumulating episode archive the feed is built from (source of truth).
- **`feed_state_test.json`** / **`docs/test/`** — the staging feed's state + hosted files (see
  Staging feed below). Hand-seeded; not yet wired into any code.
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
  writing/reviewing. Phase 1's `--model claude-sonnet-5` + `--fallback-model claude-opus-4-8` govern
  only the lightweight **parent orchestrator session** (reading files, running `orchestrator.py`,
  dispatching subagents). **Fable 5 is deliberately not used anywhere in the scheduled job** (its
  usage-limit deaths killed past runs), and because the job passes explicit `--model` flags, an
  interactive terminal left on Fable (or anything else) can never leak into the 5 AM run. The
  Opus-4.8 re-invoke on any still-pending/failed prompt is kept as a safety net, resuming via the
  idempotent orchestrator (only pending/failed prompts re-done).
  `make_cover.py` (regenerates the cover via Pillow), `seed_feed.py` (one-off backfill).
- **`logs/`** — git-ignored per-day logs from the scheduled run (`daily-<YYYY-MM-DD>.log`); check the
  latest one first when asked how the morning run went.
- `episode.py` — **used only for TTS now**: `synthesize` + resilient chunked `_synthesize`. The
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
disk, so the fix is the re-publish snippet in the `daily-briefing` skill once the endpoint recovers.

## Deep-dive stage (2.5) — rationale & operational notes

**Why it exists.** The Analyst-Editor writes `required_arguments` and
`required_second_order_effects` knowing the Writer has no web access and may only use figures
carrying a verbatim `quote`. When the dossier can't support what the plan demanded, the draft
hedges the figure or drops the argument, and the episode lands short. Measured on the 2026-07-23
run: 6 of 10 drafts finished under the 1,200-word floor, and 5 of 10 carried figures supported only
by dossier-summary prose that the reviewer then hedged (`"reported near $17.9 billion"`) or cut. The
5AM log the day before shows the workaround already in force — every reviewer told to spend its one
revision pass expanding rather than polishing. Stage 2.5 closes that loop at the one point where the
gap is known and specific. Fanning out **stage 1** research instead would have made this worse (more
breadth against a fixed word budget); the dossier already over-supplies (5 leads + 3 secondary, only
2–5 used).

**It runs in every novelty mode, including the 5AM publish job — deliberately.** It was first
shipped gated to relaxed/interactive runs to protect the scheduled job's token budget; that was the
wrong call. The 5AM run is the one that publishes to Spotify, so a quality stage excluded from it
improves nothing. **Do not re-gate it to interactive-only.** The standing lesson: weigh a stage's
cost against the *published episode*, not against the batch's budget. If token headroom gets tight,
the response is to trim stage 1 (5 leads + 2–3 secondary → ~4 + 2 — a web agent's cost is
superlinear in tool calls, so dropping ~5 searches saves several times what the deep dive spends),
**not** to gate this stage back off.

**Cost.** ≈ +12% tokens on a prompt that uses it; an empty `deep_dive_requests` costs nothing. The
request is bounded in `orchestrator.py` (`MAX_DEEP_DIVE_REQUESTS`=1, `MAX_DEEP_DIVE_QUESTIONS`=3),
and the stage can **never fail a prompt** — on error or invalid output after one repair,
`deep_research.json` is deleted and the Writer runs anyway.

**First live 5AM run (2026-07-24).** Worked end-to-end. Dives fired on 7 of 8 normal briefings (the
1 non-firing prompt reported a clean gap check — the correct outcome, not a miss); all 7 returned
`complete` with 3–5 new quoted facts, and 4 recorded `contradictions` the reviewers then honored.
Drafts under the 1,200 floor dropped from 6/10 to 3/8. Phase 1 ran 25.5 min vs. the 22 min baseline
(~+16% wall), with no usage-cap truncation and no Opus retry, so the stage-1 trim stayed in reserve.
All 9 episodes published and pushed cleanly; **zero hard defects shipped.**

**How to judge a run** (ignore the reviewer's `overall` score — self-graded, effectively pinned at
8): (1) "soft support / no verbatim quote" issues should go to zero *for the deep-dived item*;
(2) drafts should stop landing ~200 words short without the reviewer being told to expand. Read the
actual `issues_found` text, not a keyword count — a review's `issues_found` logs what the reviewer
found **and repaired** (paired with `changes_made`), so an entry there is not a shipped defect;
residual figure-audit flags typically sit on *other* items in the briefing (one dive closes one
gap); and a naive keyword scan also trips on the reviewer's contradiction-handling language.

## Staging feed (in progress)

A second public show, "Cautious Optimism Briefings (Staging)", for trialing briefings without
touching production. A minimal hand-seeded feed is live at `docs/test/feed.xml` (state:
`feed_state_test.json`; seeded 2026-07-09, commit `73b812b`) so there's a real URL to submit at
creators.spotify.com. **Status: seed published; the user still has to submit + verify the show on
Spotify, and the code wiring (`Channel` in `config.py`, channel-aware `feed.py`,
`publish_feed.py --test`/`--only`) is NOT built yet** — no Python currently reads `docs/test/` or
`feed_state_test.json`, so nothing publishes there; the daily batch touches production only. Design
+ sequencing live in the `feature-idea-staging-feed` memory.

## Known working-tree state

The scheduled run rewrites `briefings/<id>.txt` daily, but `publish_feed.py` commits only `docs/` +
`feed_state.json` — so **modified `briefings/*.txt` in `git status` after a morning run is normal**,
not a sign of an interrupted publish. Root `briefing.txt`/`briefing.mp3` are inert legacy files.
