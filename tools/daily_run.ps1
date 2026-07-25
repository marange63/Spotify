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
param([switch]$RepeatOK, [switch]$NoPublish, [string]$Only = '')

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
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
# The prompt is resume-aware at BOTH the prompt and stage level, so the SAME prompt drives the
# Sonnet primary run and the Opus retry - the retry picks up at the first missing/superseded
# artifact. Defined in tools\phase1_prompt.ps1, shared with tools\completion_run.ps1.
. (Join-Path $PSScriptRoot 'phase1_prompt.ps1')
$prompt = Get-Phase1Prompt -Today $today -Novelty $novelty -Only $onlyIds -SkipAnalysis:($onlyIds.Count -gt 0)

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
Log "phase 1: headless Claude - four-stage pipeline (novelty=$novelty), parent Sonnet 5"
& $claude -p $prompt --model claude-sonnet-5 --fallback-model claude-opus-4-8 --dangerously-skip-permissions *>> $log
Log "phase 1 (Sonnet 5) exit code: $LASTEXITCODE"

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
    & $claude -p $prompt --model claude-opus-4-8 --dangerously-skip-permissions *>> $log
    Log "phase 1 (Opus 4.8 retry) exit code: $LASTEXITCODE"
    $incomplete = Get-IncompleteCount
    Log "phase 1: $incomplete prompt(s) still unfinished after Opus 4.8 retry"
} else {
    $what = if ($onlyIds.Count) { 'all in-scope prompts' } else { 'all prompts' }
    Log "phase 1: $what finished on the Sonnet 5 primary run (no Opus retry needed)"
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
