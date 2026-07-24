---
name: daily-briefing
description: Produce and publish the daily podcast briefings. Invoke for "make my daily briefing" (the standing auto-publish command), for running/re-running the four-stage research→edit→write→review pipeline for any prompt, for publishing or re-publishing an episode to the feed, and for the novelty policy or pipeline failure rules.
---

# Daily briefing pipeline

The full operational workflow for producing and publishing Cautious Optimism Briefings. The
always-on editorial standard (voice, listenability, sources) is in `CLAUDE.md`; module-level
reference is in `docs/ARCHITECTURE.md`.

## "Make my daily briefing" — the standing command

When the user says **"make my daily briefing,"** this is a single standing, **auto-publishing**
command. **First re-read `prompts.json` fresh from disk** (do not trust an earlier read this
session — the user may have added prompts in the window since; the count can change mid-session).
Then run the four-stage pipeline (below) for **every** currently-enabled prompt and publish the
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

**Confirmation email is DISABLED (since 2026-07-08) — do not send it** (no working delivery path;
see the `publish-confirmation-email-blocked` memory). The ntfy phone push replaces it and fires
automatically inside `publish_feed.py`. Report the results table and skip the email.

## Four-stage pipeline (how each script is produced)

Each script is produced by four separated Claude Code subagents (in `.claude/agents/`) with file
handoffs under `runs/<date>/<prompt_id>/`, gated by `orchestrator.py` (stdlib CLI; the ONLY path
allowed to copy a script into `briefings/`). Writer and Reviewer are deliberately **separate agents
with separate contexts** so the reviewer never grades its own writing.

1. **Init:** `python orchestrator.py init --date <today> --novelty strict|relaxed` — creates
   `runs/<date>/<id>/` for every enabled prompt (normal first, synthesis last), records the batch in
   `runs/<date>/run.json`, prints the plan. Idempotent: re-init preserves statuses, so an interrupted
   batch (or a newly-added prompt) resumes/joins cleanly.
2. **For each normal prompt, in plan order:**
   - **Researcher** (`researcher`; web allowed): strongest recent material → `research.json`. Pass the
     prompt id/name/text, the date, output path. Then `orchestrator.py validate research <path>`.
   - **Analyst-Editor** (`analyst-editor`; no web): judges the dossier vs. the prior briefing
     (`briefings/<id>.txt`, still on disk), the **last 5 days** of this topic's transcripts AND the
     last 5 Throughline transcripts, and the editorial standard; decides write-or-skip, thesis, lead,
     ordering, emergent 5-day patterns → `editorial_plan.json`. Pass the novelty mode. Then
     `validate plan <path>`. If `decision` is `skip`:
     `orchestrator.py mark <id> --date <today> --status skipped --stage plan --reason "…"`, next prompt.
   - **Deep Researcher** (`deep-researcher`; web allowed) — **optional; runs in every mode,
     including the 5 AM job.** Run it whenever the plan's `deep_dive_requests` is non-empty (and
     skip it silently when empty, which is the common case). Pass the prompt id/name, the date,
     the research + plan paths, and the output path
     `deep_research.json`. Then `orchestrator.py validate deep <path>`. It answers the analyst's
     named evidence gaps on ONE approved item (≤3 questions, ≤6 web calls) so the Writer can make
     the arguments the plan requires instead of hedging them. On failure or invalid output after one
     repair attempt, **delete `deep_research.json` and continue to the Writer** — the stage is an
     enhancement, never a reason to fail a prompt. See "Deep-dive stage" below.
   - **Writer** (`writer`; no web): script from dossier + plan (+ `deep_research.json` if it exists)
     → `draft.txt` only. May use only figures carrying a verbatim `quote` in those files.
   - **Reviewer** (`reviewer`; no web; fresh context — did NOT write the draft): critiques vs. dossier,
     plan (+ deep dive), standard, **audits every figure against the research quotes**, revises once → `review.json`
     + `final.txt`. Approve is not its default. Then `validate review <path>` and
     `orchestrator.py approve <id> --date <today>` — copies `final.txt` to `briefings/<id>.txt` **only
     if** the review says `approve`.
3. **Synthesis prompts last** (`"kind": "synthesis"`, e.g. `throughline`): NOT researched, no plan.
   Run **Writer then Reviewer** only, giving both the day's APPROVED `briefings/<id>.txt` files as
   source (no fresh web research, no new facts). The Throughline is a **front-page digest** (headline +
   a fixed-order tour of every brief that shipped, ≤3 sentences each + an optional cross-cutting close
   — see its prompt in `prompts.json`). Both also read the last 5
   `docs/transcripts/throughline-*.txt`; a continuing/escalating/broken pattern is named only in the
   optional close and only when compelling (never forced), and the reviewer audits any cross-day claim
   against those transcripts. Same `review.json`/`final.txt`/`approve` flow. If zero prompts were
   approved today, mark the synthesis prompt skipped. `publish_feed.py` publishes synthesis prompts
   last so they sort to the top of the feed.
4. **Report:** `python orchestrator.py status --date <today>` — per-prompt outcomes + approved ids.

## Failure rules (always continue the batch; one bad prompt never stops the rest)

- `validate` fails → have the same agent (or fix directly) repair the artifact **once**; if it still
  fails, `mark <id> --status failed --stage <stage> --reason "…"` and move on.
- Research `status: "insufficient"` → the Analyst-Editor may still decide, but skipping is expected;
  `failed` research → mark failed, move on.
- Writer or Reviewer failure → retry that subagent **once**, then mark failed.
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

**It runs in the 5 AM job.** That is the point: the scheduled run is the one that publishes to
Spotify, so a quality stage gated out of it improves nothing. It is on in both novelty modes.

**Watch the usage cap.** The 5 AM phase 1 has a known failure mode where later prompts get skipped
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
