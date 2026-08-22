# Cautious Optimism Briefings - unattended daily run (Windows Task Scheduler).
#
# Two phases, deliberately separated so publishing can't be skipped by an AI hiccup:
#   1. Headless Claude Code runs the four-stage pipeline (Researcher -> Analyst-Editor ->
#      Writer -> Reviewer, see CLAUDE.md) for every enabled prompt; only reviewed-and-approved
#      scripts land in briefings/<id>.txt (enforced by orchestrator.py).
#      MODEL PINNING: the four subagents pin their own models in .claude/agents/*.md frontmatter
#      (researcher=sonnet, analyst-editor=opus, writer=sonnet, reviewer=opus), which OVERRIDES the
#      --model/--fallback-model below for the actual research/editing/writing work. So --model
#      claude-sonnet-5 + --fallback-model claude-opus-4-8 govern only the lightweight PARENT
#      orchestrator session (reading files, running orchestrator.py, dispatching subagents).
#      Fable 5 is deliberately NOT used anywhere in this job (its usage-limit deaths killed past
#      runs); the explicit --model flags also mean an interactive terminal left on Fable can never
#      leak in. The Opus retry below is kept as a safety net.
#      RESUME: the retry resumes at two levels. Prompt level - run.json skips already-approved
#      prompts. STAGE level - `orchestrator.py resume --prune` reports, per unfinished prompt, the
#      first missing/invalid/superseded artifact and deletes everything downstream of it, so the
#      retry restarts at that stage instead of re-running the whole pipeline. Before 2026-07-25 it
#      restarted from the Researcher and burned the very budget that made it a retry.
#   2. publish_feed.py (deterministic) synthesizes audio, updates the RSS feed, and git-pushes.
#      --require-fresh means only briefings actually approved today get published (never stale).
#
# Novelty policy: by default (scheduled runs) the Analyst-Editor runs in STRICT mode - no
# repeating the prior day's topics/themes unless there's genuinely new news; weak days may be
# skipped. Pass -RepeatOK for manual testing to run RELAXED (repetition allowed when helpful).
#
# Dry run: pass -NoPublish to run phase 1 only (all agents, all runs/<date>/ artifacts, approved
# briefings/<id>.txt copies) with NO TTS, NO feed update, NO git commit, NO push.
#
# SPLIT BATCH (-Only): pass a comma-separated list of prompt ids to pipeline ONLY those. This is
# how the batch is divided across two session windows - the midnight task runs the least
# time-sensitive half with -Only ... -NoPublish, and this same script at 05:00 (no -Only) picks up
# everything still outstanding and publishes the whole day at once. The 05:00 run needs no list:
# orchestrator's resume reports the midnight half as 'done' and skips it. A NEWLY added prompt
# therefore falls to the 05:00 run by default, which is the safe side. -Only is also handy for a
# manual single-prompt rerun. Unknown ids abort the run rather than silently doing nothing.
#
# Everything is logged to logs\daily-<date>.log. Exit code is non-zero if publishing failed.
# -ChunkSize: how many normal prompts each phase-1 Claude session handles. Small values bound the
# parent session's context accumulation (the "orchestration" token cost) and help the batch finish
# inside one usage-cap window. A value >= the prompt count restores the old single-session behavior.
param([switch]$RepeatOK, [switch]$NoPublish, [string]$Only = '', [int]$ChunkSize = 3)

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
# $claude is resolved below via Resolve-ClaudeExe (tools\phase1_prompt.ps1) - see the note there.
$conda  = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today  = Get-Date -Format 'yyyy-MM-dd'

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

$novelty = if ($RepeatOK) { 'relaxed' } else { 'strict' }
$onlyIds = @()
if ($Only) { $onlyIds = @($Only -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }

# Validate -Only against the library BEFORE spending anything: a typo'd id would otherwise produce
# a run that quietly does nothing and a batch half that never gets written.
if ($onlyIds.Count) {
    try {
        $lib = Get-Content (Join-Path $proj 'prompts.json') -Raw | ConvertFrom-Json
        $libPrompts = if ($lib.PSObject.Properties.Name -contains 'prompts') { $lib.prompts } else { $lib }
        $enabled = @($libPrompts | Where-Object { $_.enabled } | ForEach-Object { $_.id })
        $unknown = @($onlyIds | Where-Object { $enabled -notcontains $_ })
        if ($unknown.Count) {
            Log "ABORT: -Only lists unknown or disabled prompt id(s): $($unknown -join ', ')"
            Log "       enabled ids are: $($enabled -join ', ')"
            exit 2
        }
    } catch {
        Log "ABORT: could not validate -Only against prompts.json ($_)"
        exit 2
    }
}

$mode = "$novelty novelty" + $(if ($NoPublish) { ' + dry run (-NoPublish)' } else { '' }) +
        $(if ($onlyIds.Count) { " + split batch ($($onlyIds.Count) prompt(s))" } else { '' })
Log "=== daily run start ($today) - $mode ==="
if ($onlyIds.Count) { Log "scope: $($onlyIds -join ', ')" }

# Stamp the token-window START before any model work, so run_report.py can total this run's
# grand-total token usage (tip to tail) from the Claude transcripts. Idempotent (a retry won't
# move the start). Phase 2 spends no model tokens, so start->phase-1-end covers the whole run.
& $conda run -n Spotify --no-capture-output python run_report.py --date $today --start *>> $log

# Phase 1 - four-stage pipeline: research -> edit -> write -> review (no publishing, no git) ----
# Run as SEVERAL small sessions (Invoke-Phase1Chunked) instead of one, to bound the parent session's
# context accumulation. Both the chunk prompts and the resume/retry semantics live in
# tools\phase1_prompt.ps1, shared with tools\completion_run.ps1.
. (Join-Path $PSScriptRoot 'phase1_prompt.ps1')
# Resolve the Claude Code binary now that the helper is loaded. Aborting here is deliberate: an
# unresolvable path used to surface only as a per-chunk invocation error, and the run would carry on
# to publish nothing.
$claude = Resolve-ClaudeExe
if (-not $claude) {
    Log "ABORT: could not locate claude.exe (looked in %APPDATA%\npm, %USERPROFILE%\.local\bin, and PATH)"
    exit 3
}
Log "claude: $claude"

# Initialise + prune ONCE for the whole batch (writes runs/<date>/<id>/prompt.txt too); the per-chunk
# sessions then use -SkipInit so a per-chunk --prune can never disturb a sibling chunk.
& $conda run -n Spotify --no-capture-output python orchestrator.py init --date $today --novelty $novelty *>> $log
& $conda run -n Spotify --no-capture-output python orchestrator.py resume --date $today --prune *>> $log

# How many prompts are still unfinished (pending/failed) per the orchestrator's run state.
# -1 means the state couldn't be read (e.g. init never ran because phase 1 died immediately).
# SCOPED to -Only when given: on a split batch the other half is pending BY DESIGN, and counting it
# would fire the Opus retry every single night for work this run was never meant to do.
function Get-IncompleteCount {
    $raw = & $conda run -n Spotify --no-capture-output python orchestrator.py status --date $today --json 2>> $log
    try {
        $st = ($raw -join "`n") | ConvertFrom-Json
        $ps = $st.prompts | Where-Object { $_.status -eq 'pending' -or $_.status -eq 'failed' }
        if ($onlyIds.Count) { $ps = $ps | Where-Object { $onlyIds -contains $_.id } }
        return @($ps).Count
    } catch {
        Log "phase 1: could not read orchestrator status JSON ($_)"
        return -1
    }
}

# Primary attempt - PARENT session pinned to Sonnet 5 (an explicit --model always overrides any
# interactive /model default, so a terminal left on Fable/Opus can never leak in; Fable is
# deliberately NOT used anywhere in this job). Automatic fallback to Opus 4.8 if Sonnet is
# overloaded/unavailable mid-run. Note the subagents ignore this and use their frontmatter models
# (sonnet/opus); this governs only the lightweight orchestration session.
Log "phase 1: chunked primary run (ChunkSize=$ChunkSize), parent Sonnet 5"
Invoke-Phase1Chunked -Claude $claude -Conda $conda -Today $today -Novelty $novelty -Log $log -ChunkSize $ChunkSize -Only $onlyIds
Log "phase 1: chunked primary run done"

# If prompts remain unfinished, retry the leftovers with the parent session on Opus 4.8. Now that
# the subagents are model-pinned this mainly guards against the parent session dying (rare);
# idempotent init means this resumes - approved prompts are skipped.
$incomplete = Get-IncompleteCount
if ($incomplete -ne 0) {
    if ($incomplete -lt 0) {
        $why = "run state unreadable - primary run may have died before init"
    } else {
        $why = "$incomplete prompt(s) unfinished after the Sonnet primary run"
    }
    Log "phase 1: $why - retrying on Opus 4.8"
    # Log where the retry will pick up (and drop superseded artifacts) BEFORE spending model
    # tokens, so a truncated run leaves a readable record of what was salvaged vs. rebuilt. The
    # retry prompt re-runs this itself; --prune is idempotent once the tree is clean.
    & $conda run -n Spotify --no-capture-output python orchestrator.py resume --date $today --prune *>> $log
    Invoke-Phase1Chunked -Claude $claude -Conda $conda -Today $today -Novelty $novelty -Log $log -ChunkSize $ChunkSize -Only $onlyIds -Model claude-opus-4-8 -Fallback claude-opus-4-8
    Log "phase 1: Opus 4.8 retry done"
    $incomplete = Get-IncompleteCount
    Log "phase 1: $incomplete prompt(s) still unfinished after Opus 4.8 retry"
} else {
    $what = if ($onlyIds.Count) { 'all in-scope prompts' } else { 'all prompts' }
    Log "phase 1: $what finished on the Sonnet 5 primary run (no Opus retry needed)"
}

# Write the day's analysis ONCE, in its own small session - the chunk sessions all ran with
# -SkipAnalysis. A split-batch half (-Only) skips it; the run that completes the day writes it.
if (-not $onlyIds.Count) {
    Log "phase 1: writing run analysis (dedicated session)"
    $analysisPrompt = @"
Run python run_report.py --date $today and write the run's agent-performance analysis to
analyses/$today.md, following the 'Run analysis' section of the daily-briefing skill (fixed template,
numbers from run_report). Local-only; do NOT commit it. If runs/$today/token_window.json holds more
than one segment, the batch was split across sittings - say so and note which prompts ran when. Do
NOT run any pipeline agents or touch any briefing; this session is analysis only.
"@
    & $claude -p $analysisPrompt --model claude-sonnet-5 --fallback-model claude-opus-4-8 --dangerously-skip-permissions *>> $log
}

# Stamp the token-window END now that all model work (phase 1) is done. The in-run analysis
# already read an open-ended window; this records the precise end for any later run_report re-run.
& $conda run -n Spotify --no-capture-output python run_report.py --date $today --end *>> $log

# Phase 2 - deterministic publish (TTS -> feed -> git push) -------------------
# NOTE: confirmation email temporarily disabled (no working delivery path yet - see the
# 'publish-confirmation-email-blocked' memory). Re-add --email once BRIEFING_SMTP_USER /
# BRIEFING_SMTP_PASS are set; the send in publish_feed.py must also be un-commented.
if ($NoPublish) {
    if ($onlyIds.Count) {
        # Split batch: this half's scripts are approved into briefings/ and wait for the later
        # run to publish the whole day in one go (one commit, one push, one ntfy).
        Log "phase 2: SKIPPED (split-batch half - the later run publishes the full day)"
        Log "=== daily run done (split half) ==="
    } else {
        Log "phase 2: SKIPPED (-NoPublish dry run - no TTS, no feed update, no commit, no push)"
        Log "=== daily run done (dry run) ==="
    }
    exit 0
}

Log "phase 2: publish_feed.py --require-fresh"
& $conda run -n Spotify --no-capture-output python publish_feed.py --date $today --require-fresh *>> $log
$pubExit = $LASTEXITCODE
Log "phase 2 exit code: $pubExit"

Log "=== daily run done ==="
exit $pubExit
