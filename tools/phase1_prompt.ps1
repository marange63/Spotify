# Shared phase-1 prompt for the briefing pipeline.
#
# Dot-sourced by BOTH tools\daily_run.ps1 (the 5 AM job) and tools\completion_run.ps1 (the 10:05
# pass that finishes whatever the session cap truncated). It lives in one file so the resume
# semantics can never drift between them - the completion pass depends entirely on those semantics
# being obeyed, and a stale copy would silently restart finished prompts.

function Get-Phase1Prompt {
    param(
        [Parameter(Mandatory)][string]$Today,
        [Parameter(Mandatory)][string]$Novelty,
        # Extra sentence(s) appended for the completion pass (context about why it is running).
        [string]$Preamble = '',
        # Restrict this run to these prompt ids (the midnight half of a split batch). Empty = all.
        [string[]]$Only = @(),
        # Suppress the run-analysis step - a partial run must not overwrite the day's analysis.
        [switch]$SkipAnalysis
    )

    if ($Only.Count) {
        $list = ($Only -join ', ')
        $scope = @"
Run today's four-stage briefing pipeline for EXACTLY these prompt ids and NO OTHERS:
$list
This is the first half of a deliberately SPLIT batch - the remaining prompts are handled by a
later run in a different session window, and touching them here would defeat the split. Any prompt
not in that list must be left strictly untouched: do not research it, do not plan it, do not write
it, and do not mark it. It stays 'pending' on purpose and that is a correct end state for this run.
Follow the 'Four-stage pipeline' procedure in CLAUDE.md exactly for the listed prompts, with
NOVELTY MODE: $Novelty.
"@
    } else {
        $scope = @"
Run today's four-stage briefing pipeline for EVERY enabled prompt in prompts.json, following the
'Four-stage pipeline' procedure in CLAUDE.md exactly, with NOVELTY MODE: $Novelty.
"@
    }

    $body = @"
$scope
Use --date
$Today. Start with: python orchestrator.py init --date $Today --novelty $Novelty ; then
python orchestrator.py resume --date $Today --prune ; then follow the plan and the CLAUDE.md
failure rules (validate every JSON artifact, one repair attempt, mark failures/skips, continue the
batch).

RESUME SEMANTICS - obey these exactly; they are what makes a retry cheap enough to finish:
(a) PROMPT level: if a prompt's resume_stage is 'done' (already approved or skipped by an earlier
attempt today), SKIP it entirely - do NOT re-run any of its agents.
(b) STAGE level: for every other prompt, start at the stage named by its resume_stage from the
resume command - research, plan, deep, write, review or finalize - and run ONLY that stage and the
ones after it. Do NOT re-run an earlier stage whose artifact is already on disk; read that artifact
and use it. A prompt showing resume_stage 'write' means research.json, editorial_plan.json and any
deep_research.json are already valid and current - pass them to the Writer as-is.
(c) 'finalize' means every artifact is present and consistent: just run orchestrator.py approve
(or mark, if review.json's decision is skip/failed). Do not re-run any agent.
(d) The --prune flag has ALREADY deleted superseded artifacts (e.g. a deep_research.json or
draft.txt written against an editorial plan that was later rewritten). Never resurrect them and
never assume a file that is absent was merely 'not reached' - if an artifact is missing, build it.

Handle synthesis prompts (kind "synthesis", e.g. throughline) LAST, Writer then Reviewer, from the
day's APPROVED briefings. Do NOT publish, do NOT run publish_feed.py, and do NOT git commit or push
- only orchestrator.py may copy approved scripts to briefings/<id>.txt. When finished, run
python orchestrator.py status --date $Today and report it.
"@

    if ($SkipAnalysis) {
        # A partial run has nothing meaningful to analyse and would clobber the day's real
        # analysis, which the final run writes once every prompt's outcome is known.
        $body += @"

Do NOT write analyses/$Today.md and do NOT run run_report.py - this run covers only part of the
batch, and the day's analysis is written by the later run once all outcomes are final.
"@
    } else {
        $body += @"

FINALLY, run python run_report.py --date $Today and write the run's agent-performance analysis to
analyses/$Today.md, following the 'Run analysis' section of the daily-briefing skill (fixed
template, numbers from run_report). This is local-only; do NOT commit it. If runs/$Today/
token_window.json holds more than one segment, the batch was SPLIT across session windows (or
completed by a later pass) - say so in the analysis and note which prompts ran in which sitting.
"@
    }

    if ($Preamble) { return "$Preamble`n`n$body" }
    return $body
}
