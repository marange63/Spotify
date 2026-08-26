---
name: daily-briefing
description: Produce and publish the daily podcast briefings. Invoke for "make my daily briefing" (the standing auto-publish command), for running/re-running the five-stage research→edit→write→review→final-read pipeline for any prompt, for publishing or re-publishing an episode to the feed, and for the novelty policy or pipeline failure rules.
---

# Daily briefing pipeline

The full operational workflow for producing and publishing Cautious Optimism Briefings. The
always-on editorial standard (voice, listenability, sources) is in `CLAUDE.md`; module-level
reference is in `docs/ARCHITECTURE.md`.

## "Make my daily briefing" — the standing command

When the user says **"make my daily briefing,"** this is a single standing, **auto-publishing**
command. **First re-read `prompts.json` fresh from disk** (do not trust an earlier read this
session — the user may have added prompts in the window since; the count can change mid-session).
Then run the five-stage pipeline (below) for **every** currently-enabled prompt and publish the
batch — interactive runs default to **relaxed** novelty.

**Run to completion with zero further input.** From that point, research every enabled prompt,
write the scripts, publish, and report back — **without pausing to ask or confirm anything.** Do
not ask which prompts, whether to publish, whether to push, about novelty, or about summaries.
Make every routine judgment call silently and keep going. The **only** reasons to stop mid-run are
a hard blocker you cannot resolve (e.g. every prompt's research failed, or `git push` is rejected).
Defaults, applied without asking:

- **Which prompts:** every `enabled` prompt in `prompts.json`.
- **Novelty:** relaxed (interactive runs are testing — pass `--novelty relaxed`); strict only if the
  user explicitly asks in the same message.
- **Skips:** an Analyst-Editor or reviewer skip is a normal outcome, not an error — report it in the
  results table and keep going; never force a weak briefing through.
- **Summaries:** let `publish_feed.py` auto-derive them (pass `--summaries` only if the user supplied
  them); never stop to hand-write them.
- **Publish + push:** always, automatically. No "ready to publish?" checkpoint.
- **Research judgment:** pick sources and framing per the editorial standard; never ask the user to
  choose an angle you can decide reasonably yourself.

After the pipeline has approved today's scripts, publish (runs in the Spotify conda env):

```bash
conda run -n Spotify --no-capture-output python publish_feed.py --require-fresh
```

`publish_feed.py` — for each enabled prompt: TTS (`episode.synthesize`) → `feed.add_episode(...)`
(copies mp3 to `docs/audio/<id>-<date>.mp3`, records it in `feed_state.json`) → `feed.build_feed()`
rewrites `docs/feed.xml` → `git add docs feed_state.json && commit && push`. GitHub Pages serves the
update; Spotify re-ingests on its next refresh. `--require-fresh` publishes only scripts approved
**today** (skipped/failed prompts keep a stale `briefings/<id>.txt` and are excluded automatically).

**Publishing a second time on the same day** (finishing a run the usage cap truncated): add
`--skip-published`, which skips any prompt already in `feed_state.json` for that date and reports it
as `ALREADY PUBLISHED`. Without it you re-run TTS on live episodes and change their enclosure URLs,
making Spotify re-download identical audio. The scheduled 08:20 completion pass
(`tools/completion_run.ps1`) does exactly this automatically — so if the 03:15 run was truncated,
check whether that pass already fixed it before doing anything by hand.

**Confirmation email is DISABLED (since 2026-07-08) — do not send it** (no working delivery path;
see the `publish-confirmation-email-blocked` memory). The ntfy phone push replaces it and fires
automatically inside `publish_feed.py`. Report the results table and skip the email.

## Four-stage pipeline (how each script is produced)

Each script is produced by five separated Claude Code subagents (in `.claude/agents/`) with file
handoffs under `runs/<date>/<prompt_id>/`, gated by `orchestrator.py` (stdlib CLI; the ONLY path
allowed to copy a script into `briefings/`). Writer and Reviewer are deliberately **separate agents
with separate contexts** so the reviewer never grades its own writing.

1. **Init:** `python orchestrator.py init --date <today> --novelty strict|relaxed` — creates
   `runs/<date>/<id>/` for every enabled prompt (normal first, synthesis last), records the batch in
   `runs/<date>/run.json`, prints the plan. Idempotent: re-init preserves statuses, so an interrupted
   batch (or a newly-added prompt) resumes/joins cleanly. It also stamps the run's as-of **timestamp**
   (`run_started_at`) and writes `runs/<date>/run_context.txt` — the **freshness anchor** the
   research/review agents read so a print scheduled for 8:30am is known not to have happened on a 5am
   run. Keep `release_calendar.json` current with near-term scheduled releases (CPI/PPI/payrolls/FOMC):
   a listed release that is still pending at run time is hard-blocked from being stated as fact; a
   missing one silently loses that protection. The timestamp is preserved across idempotent re-inits.
   **Resuming an interrupted run:** run `python orchestrator.py resume --date <today> --prune`
   and obey it. For each prompt it gives a `resume_stage` — `research|plan|deep|write|review|
   finalize|done` — computed from which artifacts are present, valid, and newer than what they
   derive from. Start at that stage and run only it and the ones after; read the earlier artifacts
   instead of regenerating them. `done` = approved/skipped already, skip the prompt entirely;
   `finalize` = everything is consistent, just run `approve` (or `mark`, per `review.json`).
   `--prune` deletes superseded artifacts first — a `deep_research.json` or `draft.txt` written
   against an editorial plan that was later rewritten answers a *different story* and will look
   plausible if you feed it to the Writer (this shipped undetected on 2026-07-25). Never restart a
   prompt from the Researcher just because it is unfinished. Then stamp the token-window start:
   `python run_report.py --date <today> --start` (idempotent; lets the analysis total the run's
   grand-total token usage. The scheduled job also does this in `daily_run.ps1`, so this only matters for
   interactive runs).
2. **For each normal prompt, in plan order:**
   - **Researcher** (`researcher`; web allowed): strongest recent material → `research.json`. Pass the
     prompt id/name, the date, output path — **do NOT paste the standing prompt text into the
     dispatch; the agent reads it from `runs/<date>/<id>/prompt.txt`** (written at init). Embedding
     the ~800–1200-word prompt makes it accumulate in the parent session's context for the whole
     run — the biggest avoidable slice of the "orchestration" token cost. Same for every stage: keep
     dispatches short (ids, paths, mode), never re-paste the prompt. Then
     `orchestrator.py validate research <path>` — which now also runs the **freshness gate** (a figure
     for a still-pending calendar release, or a future-dated source URL, hard-fails; treat like any
     validate failure — repair once, else mark). Optionally, on a research/deep dossier whose lead
     hinges on a fresh externally-sourced figure, run `orchestrator.py verify-sources <path>` — a
     best-effort network check that fetches each figure's source and confirms the verbatim quote is on
     the page, writing `source_check.json`. Its **hard** results (future-dated URL, or a 404/DNS
     "source does not exist") should be fixed; its **advisory** results (quote-not-found, blocked/
     paywalled) are flags for the reviewer, not auto-rejections. It makes live web calls, so use it on
     the load-bearing dossier(s), not blanket across the batch, to protect the scheduled run's budget.
   - **Analyst-Editor** (`analyst-editor`; no web): judges the dossier vs. the prior briefing
     (`briefings/<id>.txt`, still on disk), the **last 5 days** of this topic's transcripts AND the
     last 5 Throughline transcripts, and the editorial standard; decides write-or-skip, thesis, lead,
     ordering, emergent 5-day patterns → `editorial_plan.json`. Pass the novelty mode. Then
     `validate plan <path>`. If `decision` is `skip`:
     `orchestrator.py mark <id> --date <today> --status skipped --stage plan --reason "…"`, next prompt.
   - **Deep Researcher** (`deep-researcher`; web allowed) — **optional; runs in every mode,
     including the scheduled job.** Run it whenever the plan's `deep_dive_requests` is non-empty (and
     skip it silently when empty, which is the common case). Pass the prompt id/name, the date,
     the research + plan paths, and the output path
     `deep_research.json`. Then `orchestrator.py validate deep <path>`. It answers the analyst's
     named evidence gaps on ONE approved item (≤3 questions, ≤6 web calls) so the Writer can make
     the arguments the plan requires instead of hedging them. On failure or invalid output after one
     repair attempt, **delete `deep_research.json` and continue to the Writer** — the stage is an
     enhancement, never a reason to fail a prompt. See "Deep-dive stage" below.
   - **Writer** (`writer`; no web): script from dossier + plan (+ `deep_research.json` if it exists)
     → `draft.txt` only. May use only figures carrying a verbatim `quote` in those files. Then
     **`orchestrator.py validate script <draft.txt>`** — a deterministic, zero-token check of the
     spoken text (markdown/bullets/stage directions, internal-artifact leakage such as the word
     "dossier" or a pasted "Say clearly that…", spoken URLs, and word count vs. the prompt's stated
     floor). Hard problems are a `validate` failure like any other: repair once, else mark. It also
     prints `[ADVISORY]` lines — the word-count shortfall at draft stage and the **listenability
     metrics** (sentence length, figures per paragraph, paragraph length). Those are warnings for
     now, not gates; **pass them to the Reviewer in its dispatch line** so its one revision pass has
     a concrete target instead of re-deriving them by eye.
   - **Reviewer** (`reviewer`; no web; fresh context — did NOT write the draft): critiques vs. dossier,
     plan (+ deep dive), standard, **audits every figure against the research quotes**, revises once → `review.json`
     + `final.txt`. Approve is not its default. Then `validate review <path>` and
     `orchestrator.py approve <id> --date <today>` — copies `final.txt` to `briefings/<id>.txt` **only
     if** the review says `approve` **and `final.txt` passes the same script gate** (approve runs it
     itself, so a stage direction or an under-floor script cannot ship even when the review approved
     it).
   - **Final Reader** (`final-reader`; no web; reads **only** `final.txt` + `prompt.txt`): the fresh
     pair of ears. Pass it the prompt id/name, the date, the two paths, and **the metrics block from
     `final_script_check.json`** (longest sentence, figures per paragraph, paragraph length) so it
     judges rather than recounts. Never pass it — or let it open — the dossier, plan, deep dive,
     draft, or review: those would tell it what the script *meant* to say, which is the exact bias it
     exists to remove. It writes `final_check.json` and **never rewrites the script**. Then
     `validate final_check <path>`.
     - `verdict: pass` → `orchestrator.py approve <id> --date <today>`.
     - `verdict: revise` → `orchestrator.py revision <id> --date <today>`. **Exit 0:** re-dispatch
       the **Reviewer** with the `defects` list (it does the rewriting), then re-run the Final Reader
       with `revision_round: 2`. **Exit 3 (budget exhausted), or a second `revise`:**
       `mark <id> --status skipped --stage final_check --reason "final reader could not clear: …"`.
     - `verdict: skip` → `mark` skipped immediately; no revision.
     Why it exists: across 134 consecutive August episodes the Reviewer returned **134 approvals and
     zero skips** while averaging seven defects found per review. Nobody can judge prose they just
     wrote, and `final.txt` is the Reviewer's own rewrite.
3. **Synthesis-family prompts last** (`throughline` and `forward-curve`): NOT researched, no plan;
   each runs its **writer-role agent, then the Reviewer, then the Final Reader**, over the day's
   APPROVED `briefings/<id>.txt` files (no fresh web research, no new facts). They run after every
   normal prompt is approved so those briefings exist on disk. Same
   `review.json`/`final.txt`/`final_check.json`/`approve` flow. **The Final Reader applies to these
   too** — they have no research or plan gate at all, and the Throughline is the archive's worst
   listenability offender, so they are the last prompts that should skip a fresh read.
   If zero prompts were approved today, mark them skipped. `publish_feed.py` publishes the whole
   synthesis family last so they sort to the top of the feed.
   - **The Throughline** (`"kind": "synthesis"`): run the **Writer** then Reviewer. It is a
     **front-page digest** (headline + a fixed-order tour of every brief that shipped, ≤3 sentences
     each + an optional cross-cutting close — see its prompt in `prompts.json`). Both agents also read
     the last 5 `docs/transcripts/throughline-*.txt`; a continuing/escalating/broken pattern is named
     only in the optional close and only when compelling (never forced), and the reviewer audits any
     cross-day claim against those transcripts.
   - **The Forward Curve** (`"kind": "forecast"`): run the **Forecaster** (`forecaster`, NOT the
     Writer) then Reviewer. Give both the day's approved `briefings/<id>.txt` files PLUS the last 5
     days of every topic's `docs/transcripts/<id>-*.txt` and the forecast's own prior
     `docs/transcripts/forward-curve-*.txt`. It opens by honestly self-scoring prior forecasts that
     came due, then makes 4–6 explicit, falsifiable probabilistic forecasts (each with a probability,
     a disconfirming risk, and a horizon), framed up front as guesses grounded in analysis, not
     certainties. The reviewer runs a **calibration audit** in place of the figure audit.
4. **Report:** `python orchestrator.py status --date <today>` — per-prompt outcomes + approved ids.
5. **Run analysis:** once outcomes are final, write the run's agent-performance analysis to
   `analyses/<today>.md` — see "Run analysis" below. Runs on every run (interactive and scheduled).

## Failure rules (always continue the batch; one bad prompt never stops the rest)

- `validate` fails → have the same agent (or fix directly) repair the artifact **once**; if it still
  fails, `mark <id> --status failed --stage <stage> --reason "…"` and move on.
- Research `status: "insufficient"` → the Analyst-Editor may still decide, but skipping is expected;
  `failed` research → mark failed, move on.
- Writer or Reviewer failure → retry that subagent **once**, then mark failed.
- `validate script` hard failure on `draft.txt` → re-dispatch the **Writer** once with the problem
  list, then mark failed. On `final.txt` (inside `approve`) → re-dispatch the **Reviewer** once with
  the problem list; if it still fails, `mark <id> --status skipped --stage review`. Never hand-edit a
  script to get it past the gate — the gate is measuring a real defect in what the agent produced.
- Final Reader failure or invalid output → retry it **once**, then `mark failed --stage final_check`.
  **Never approve a prompt by deleting or hand-writing its `final_check.json`.** A `revise` you
  cannot clear inside the revision budget is a **skip** — that is the stage working, not a blocker.
  A skipped day beats an episode nobody can follow.
- If the Reviewer's revision changes `final.txt`, the existing `final_check.json` is **stale** and
  `approve` will refuse it. Always re-run the Final Reader after a Reviewer pass; never reuse the
  verdict from the pre-revision script.
- Review decision `skip`/`failed` → mark accordingly (a reviewer skip is a normal editorial outcome,
  not an error); `approve` refuses the copy, so the prompt cannot publish.
- TTS/feed/git failures keep their `publish_feed.py` behavior (per-prompt try/except; the batch still
  publishes the successful episodes).

## Deep-dive stage (optional stage 2.5) — added 2026-07-23, on trial

**Why it exists.** The Analyst-Editor writes `required_arguments` and
`required_second_order_effects` knowing the Writer has no web access and may only use quoted
figures. When the dossier can't support what the plan demanded, the draft either hedges the figure
("reported near $17.9 billion") or omits the argument — which is why drafts land short of their
word target and reviewers spend their one revision pass expanding rather than polishing. The deep
dive closes that loop at the one point where the need is known and specific.

**It is deliberately bounded.** `orchestrator.py` enforces ≤1 request and ≤3 questions per plan
(`MAX_DEEP_DIVE_REQUESTS` / `MAX_DEEP_DIVE_QUESTIONS`); the agent caps itself at 6 web calls. Those
caps are a token-budget control, not style: a web-research agent's cost is superlinear in its tool
calls, so bounding the *request* is what keeps the batch predictable. Estimated cost when it runs is
~+12% on that prompt; an empty `deep_dive_requests` costs nothing.

**It runs in the scheduled job.** That is the point: the scheduled run is the one that publishes to
Spotify, so a quality stage gated out of it improves nothing. It is on in both novelty modes.

**Watch the usage cap.** The scheduled phase 1 has a known failure mode where later prompts get skipped
silently (see the `scheduled-run-usage-limit-risk` memory). This stage adds ~+12% tokens on a
prompt that uses it. A dry run of the analyst's first gap test over the 2026-07-23 artifacts fired
on **7 of 10** prompts, so budget ~+8% on the batch rather than the ~+5% a thinner hit rate would
imply — and watch the first few mornings for truncation. Mitigations already in place: the request is bounded in
`orchestrator.py`, the stage can never fail a prompt, and it is demand-driven so a thin day costs
nothing. If a morning does get truncated, the first diagnostic is `orchestrator.py status --date
<today>` — and the standing fix is to trim stage 1 (below), not to re-gate this stage.

**The follow-up that pays for it:** trim the Researcher from 5 leads + 3 secondary to ~4 + 2. The
dossier already over-supplies (only 2–5 items reach the plan), and a web agent's cost is
superlinear in tool calls, so removing ~5 searches saves several times what the deep dive spends.
Do this if token headroom gets tight — it should make the pair net-neutral or better.

**Judge it from the run artifacts, next morning** (compare against the 2026-07-23 baseline; ignore
reviewer `overall`, which is self-graded and pinned at 8, so a one-point move is noise):

1. `issues_found` entries of the "soft support / figure has no verbatim quote" kind should go to
   zero for the deep-dived item.
2. Draft word count should stop landing ~200 words under the standing prompt's floor **without**
   the reviewer being told to expand.

If neither moves after a handful of runs, delete the stage rather than keeping it on principle.

## Run analysis (after every run)

Once the batch outcomes are final (every prompt approved/skipped/failed), write a standing
agent-performance analysis to `analyses/<today>.md`. The user reads these in the `main.py`
window's "Run analyses" tab; they are **local-only** (git-ignored), one file per run. Purpose: a
reviewable record of how the agents performed and interacted, plus concrete improvement ideas.

**Numbers come from the tool, judgment from you.** First run
`python run_report.py --date <today>` — it emits the deterministic metrics block (per-prompt
deep-dive firing, new facts, contradictions, word count vs. the prompt's floor, reviewer score, the
"figure has no verbatim quote" soft-support flag count), the run's **grand-total token usage**
(tip to tail, including subagents and cache — with a tokens/word figure), a **per-stage token
breakdown** (researcher / analyst-editor / deep-researcher / writer / reviewer / orchestration —
which stage is the hog), and a **5-day trend**.
Read the last **5 days** of `analyses/<date>.md` too, so you can open with a trend and hold prior
suggestions accountable. Then wrap the narrative and suggestions around that block — never eyeball
or miscount the numbers.

Follow this fixed template so runs are comparable day to day (see `analyses/2026-07-24.md` as the
reference example):

1. **Header** — date, novelty mode, and a one-line verdict.
2. **Trend** — one short paragraph vs. the last 5 days (the `run_report` trend table + the prior
   `analyses/*.md`): what improved, regressed, or recurred, and — explicitly — whether a suggestion
   you made on a previous day was acted on, resolved, or still open. Skip only on a genuine first
   run with no history.
3. **Outcomes** — approved/skipped/failed from `orchestrator status`; note whether skips were
   legitimate (strict-novelty) or failures.
4. **Metrics** — paste the `run_report.py` table (including the token block) verbatim in a code
   block. Call out the grand-total tokens and tokens/word, and flag a large move vs. the trend.
   Then read the **`lstn` column (warn/hard)** and the `by metric:` line beneath the table — the
   deterministic listenability signal from `script_check`. Unlike the reviewer's `audio_flow`
   (self-graded, printed in the adjacent `flow` column, and never below 7 in 134 August reviews),
   these are measured off the shipped text. Report: the run's hard-breach count, which bound is
   binding, and any prompt where a **hard** breach sits beside a `flow` of 7–8 — that pairing is
   the clearest evidence the self-grade is decoupled from the script. Hard breaches are still
   **advisory** (`script_check.ENFORCE_LISTENABILITY` is False); the trend's `lstn` column is the
   series that decides when to flip it. Rule of thumb: flip only after ~5 runs at ≤1 hard breach
   per run, start with the binding metric alone, and never in the same run as another enforcement
   change.
   Also report the **`fc` column (verdict/hard defects)**, the `rev` column, and the **send-back
   rate** under the table. `0 of 134` approvals was the Reviewer's baseline before stage 5 existed;
   a send-back rate of exactly zero means the Final Reader is rubber-stamping, and a rate near 100%
   means it is miscalibrated. Sustained **>25%** is the trigger to split the Reviewer into
   critic/revisor — that would say partial repair is systematic, not occasional.
5. **Agent performance & interaction** — one short paragraph per stage: Researcher (dossier depth,
   any `insufficient`, JSON repairs), Analyst-Editor (skips, gap-check firing rate, emergent
   patterns), Deep-Researcher (fire rate, contradictions found and honored, any `insufficient`),
   Writer (word counts, contradictions honored), Reviewer (defects **caught vs. shipped**,
   expansions, and whether its revision **cleared or left** the listenability breaches the script
   gate flagged on the draft), Final Reader (verdicts, the defects it caught that every earlier
   stage passed, and whether a `revise` was cleared inside the revision budget). Call out where a
   handoff created friction or where one stage saved another.
6. **Notable events** — JSON repairs, agent retries, the Opus fallback, usage-cap pressure, and
   phase-1 wall time vs. the ~22 min baseline (from `logs/daily-<today>.log`).
7. **Suggestions for continual improvement** — prioritized and concrete, tied to what this run
   actually showed. Reference `soft_support_flags` as a pointer to read the real `issues_found`
   text, not as a score (it is a keyword proxy). Token cost is a first-class signal now: if
   tokens/word jumps, say why (more dives, retries, longer context) and whether it warrants the
   stage-1 trim. Name the **top one or two stages** from the per-stage breakdown and their share —
   that is where any token-reduction effort should go (typically researcher + orchestration).

## Novelty policy

Enforced by the **Analyst-Editor** stage, which reads the existing `briefings/<id>.txt` and recent
transcripts. Set at `orchestrator.py init --novelty strict|relaxed` and passed to each Analyst-Editor.
An extension of the editorial standard's "lead with what's new."

- **strict** (scheduled `tools/daily_run.ps1`, no args): reject material repeating the prior briefing's
  topics/themes/framing **unless there's genuinely new news or data**. If nothing clears the bar, the
  prompt is **skipped** (no episode) — a skipped day beats a padded one.
- **relaxed** (`daily_run.ps1 -RepeatOK`, and interactive "make my daily briefing"): repeated material
  may be used when helpful; fresh evidence and framing still preferred. Skips can still happen.

## Identity & idempotency

Identity is by **GUID** (`<prompt_id>-<date>`), unique per topic per day. Re-running the same prompt
on the same date overwrites that day's episode in place (idempotent); a new date adds a new episode.

## Re-publishing one prompt manually

```python
import feed
from episode import synthesize
mp3 = synthesize("briefings/<id>.txt")
feed.add_episode("<id>", "<Name>", "<summary>", mp3, "<YYYY-MM-DD>")
feed.build_feed()
# then: git add docs feed_state.json && git commit -m "…" && git push origin main
```

Run scripts like this via `conda run -n Spotify --no-capture-output python <file>.py` (the deps live
in the `Spotify` conda env). For a single new prompt mid-session, `orchestrator.py init` is idempotent
— re-init adds the new prompt as `pending` while preserving the others' statuses, then run its four
stages and publish just that episode with the snippet above.
