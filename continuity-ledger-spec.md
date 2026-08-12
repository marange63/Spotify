# Continuity Ledger — design spec

## Context / problem

Today the pipeline's cross-day memory is shallow and single-axis (see the "temporal connection"
analysis, 2026-07-17):

- **Cross-day is same-topic only.** The analyst-editor reads one prior briefing
  (`briefings/<id>.txt`, overwritten each run) plus, discretionarily, "a few" prior transcripts for
  the *same* `prompt_id`.
- **Cross-topic is same-day only.** The Throughline links topics but reads only *today's* briefings.
- The **diagonal** — the same person / company / issue tracked across *topics* and across *days* —
  is not represented anywhere. Nor is any forecast/claim tracked to its resolution.

The **continuity ledger** (parked idea, `feature-idea-continuity-ledger`, 2026-07-09) closes both
gaps with one persistent structure and gives the show two differentiators: **accountable callbacks**
("last week we flagged X; here's what happened") and a **weekly scorecard episode** grading the
show's own calls.

## Core idea

One git-tracked JSON store, `ledger.json` (beside `feed_state.json`), is the cross-topic, cross-day
memory. The pipeline **writes** to it when a briefing is approved and **reads** a relevant slice of
it when the next briefing is planned. Entities are the join key that makes the diagonal work.

Design principles (match the existing codebase):
- **Deterministic gatekeeping, agent authoring** — same split as `orchestrator.py` vs the agents.
  A new stdlib `ledger.py` owns load/save/merge/query; agents only emit/consume JSON.
- **No new per-briefing agent** — ledger extraction is folded into the **reviewer** (it already
  read the final script, research, and quotes), and recall is a deterministic query the
  **analyst-editor** reads. Net new model cost ≈ 0.
- **Best-effort, never blocks publishing** — a malformed ledger update is logged and skipped, like
  a TTS failure; `approve`/publish still succeed.

## Data model — `ledger.json`

```json
{
  "version": 1,
  "entries": [
    {
      "id": "genius-act-stablecoin-rules",         // stable slug, unique
      "kind": "forecast|claim|catalyst|thread",     // see below
      "statement": "GENIUS Act stablecoin implementing rules are due 2026-07-18.",
      "entities": ["GENIUS Act", "stablecoins", "US Treasury"],  // the cross-cutting keys
      "topics": ["digital-money", "strategic-power"],            // prompt_ids that touched it
      "origin_date": "2026-07-16",
      "origin_guid": "digital-money-2026-07-16",
      "resolve_by": "2026-07-18",                   // catalysts/forecasts only; null otherwise
      "status": "open|resolved-correct|resolved-wrong|partial|expired",
      "last_seen_date": "2026-07-17",
      "history": [
        {"date": "2026-07-16", "prompt_id": "digital-money", "note": "flagged as due 07-18"},
        {"date": "2026-07-17", "prompt_id": "digital-money", "note": "still unpublished with 1 day left"}
      ],
      "resolution": null                            // {date, outcome, note} once resolved
    }
  ]
}
```

**`kind` semantics**
- `forecast` — a prediction with a truth value that will land by `resolve_by` (feeds the scorecard).
- `catalyst` — a scheduled event to watch by `resolve_by` (earnings, deadline, launch).
- `claim` — an asserted fact/thesis worth tracking for consistency/callbacks (may lack `resolve_by`).
- `thread` — a running entity storyline (e.g. "DeepMind talent attrition") with no single resolve
  date; the cross-topic narrative spine.

## Write path — extraction at approve time

1. The **reviewer** (already reading final.txt + research.json) additionally emits a
   `ledger_updates` array in `review.json`:
   ```json
   "ledger_updates": [
     {"op": "add|update|resolve", "id": "...", "kind": "...", "statement": "...",
      "entities": [...], "resolve_by": "YYYY-MM-DD|null",
      "note": "one line for history", "resolution": {"outcome": "...", "note": "..."}}
   ]
   ```
   Guidance added to `reviewer.md`: extract only genuinely trackable items (dated forecasts, named
   catalysts, load-bearing claims, recurring entity threads) — quality over quantity, ≤ ~5 per
   briefing. For any `resolve`, cite what in *today's* material resolves it.
2. `orchestrator.py approve` (the existing gate) calls `ledger.merge_updates(prompt_id, date,
   updates)` **after** copying final.txt. Merge rules (deterministic, in `ledger.py`):
   - `add` new id → append; `add` existing id → treated as `update`.
   - `update` → append to `history`, refresh `last_seen_date`, union `entities`/`topics`.
   - `resolve` → set `status` + `resolution`, append history.
   - Same-day re-run of a prompt first removes that prompt's contributions for `date`, then re-applies
     (idempotent, mirrors `feed.add_episode` replacing a same-day GUID).
   - Malformed entry → log warning, skip it; never raise.
3. New `validate_ledger_updates` in `orchestrator.py` (same style as `validate_review`), enforced by
   a new `validate ledger <review.json>` subcommand and inside `approve`.

## Read path — recall at plan time

New deterministic command:
```
python orchestrator.py ledger brief --prompt <id> --date <today>  [--days N] [--out <path>]
```
`ledger.brief(prompt_id, date)` returns the relevant slice, written to
`runs/<date>/<id>/ledger_brief.json`:
- **open** `forecast`/`catalyst` entries whose `resolve_by <= date + 7d` (coming due — prompt for
  follow-up), **or** whose `entities`/`topics` intersect this prompt's domain,
- active `thread` entries touching this prompt's entities/topic,
- recently `resolved-*` entries (last ~7 days) for callback material.

Entity/topic matching is lexical to start (normalized case, alias table in `ledger.py` for the
obvious ones — "Google/Alphabet/DeepMind", tickers). The analyst-editor invocation is given the
brief path and instructed to:
- treat already-tracked items as **not novel** unless `resolve_by` has passed or there's genuine
  movement (strengthens the novelty gate),
- surface **callbacks** ("we flagged X on <date>") into the editorial plan when the writer should
  open with one,
- record cross-topic links it notices as `thread` updates.

The **Throughline** writer also gets today's `ledger brief --prompt throughline` (all open threads +
items resolving this week) so it can spot **multi-day** cross-topic arcs, not just same-day ones —
directly fixing the "cross-topic is same-day only" limit.

## Weekly scorecard episode

- New synthesis prompt `scorecard` in `prompts.json` (`"kind": "synthesis"`), enabled but **gated to
  one weekday** (e.g. Sunday). `orchestrator.ordered_enabled` / `daily_run.ps1` skip it on other
  days; simplest gate: the init step drops `scorecard` unless `date` is the chosen weekday.
- Source material: `ledger.mature(date)` → entries with `resolve_by <= date` and
  `status in (resolved-*, expired)` since the last scorecard. Writer + reviewer only (no research),
  same flow as Throughline; reviewer confirms every grade traces to a ledger `resolution`.
- Publishes via the existing `publish_feed.py` path; sorts with the other synthesis prompt.

## New / changed files

- **`ledger.py`** (new, stdlib) — `load()/save()`, `merge_updates()`, `brief()`, `mature()`,
  `resolve()`, alias normalization. Source of truth = `config.LEDGER_FILE` (`ledger.json`).
- **`config.py`** — add `LEDGER_FILE`.
- **`orchestrator.py`** — `validate_ledger_updates`; `approve` merges updates; new `ledger`
  subcommand (`brief`/`list`/`resolve`); init-time weekday gate for `scorecard`.
- **`.claude/agents/reviewer.md`** — emit `ledger_updates` in review.json (with extraction rules).
- **`.claude/agents/analyst-editor.md`** — read `ledger_brief.json`; use for novelty + callbacks +
  thread updates.
- **`.claude/agents/writer.md`** — when the plan carries a callback, open with it.
- **`prompts.json`** — add the `scorecard` synthesis prompt.
- **`CLAUDE.md`** — document the ledger in the pipeline section + files list; note it's git-tracked
  like `feed_state.json`.
- **`tests/`** — `ledger.py` merge/brief/mature unit tests; `validate_ledger_updates` cases;
  idempotent same-day re-run; malformed-update tolerance.

## Rollout

1. Ship `ledger.py` + config + validation + `approve` merge + reviewer extraction (writes accrue).
2. Add the recall brief + analyst-editor/throughline reads (callbacks + cross-day linking switch on).
3. Add the scorecard prompt once the ledger has ~1–2 weeks of matured entries.

Stages 1–2 are inert until entries accumulate, so they can ship immediately with no behavior shock.

## Open decisions (for you)

1. **Extraction owner** — fold into the reviewer (recommended, ~0 extra cost) vs a dedicated
   `ledger` micro-agent (cleaner separation, one more call per briefing).
2. **Scorecard cadence + day** — weekly on Sunday? Monthly? A standalone show vs an episode in the
   main feed?
3. **Entity normalization depth** — start lexical + a small alias table (recommended) vs a richer
   entity-resolution pass.
4. **Ledger horizon** — keep entries forever, or auto-`expire` open items after N days unseen to
   bound the file.

## Verification

- Unit: `ledger.merge_updates` add/update/resolve + idempotent same-day replace; `brief` returns
  entity/topic/resolve-window matches; `mature` selects due entries; `validate_ledger_updates`
  rejects malformed ops; malformed update never raises through `approve`.
- End-to-end (one prompt, no publish): run pipeline → confirm `ledger.json` gains entries at approve
  → re-run same prompt same day → confirm no duplication → `ledger brief` next day surfaces the
  entry → analyst-editor plan shows a callback.
- Scorecard dry run once ≥1 entry has `resolve_by <= today`: confirm it grades only resolved items
  and every grade cites a `resolution`.
