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
#      leak in. The Opus retry below is kept as a safety net. The orchestrator's run state is
#      idempotent, so any retry RESUMES - it re-does only pending/failed prompts, never approved.
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
# Everything is logged to logs\daily-<date>.log. Exit code is non-zero if publishing failed.
param([switch]$RepeatOK, [switch]$NoPublish)

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
$mode = "$novelty novelty" + $(if ($NoPublish) { ' + dry run (-NoPublish)' } else { '' })
Log "=== daily run start ($today) - $mode ==="

# Stamp the token-window START before any model work, so run_report.py can total this run's
# grand-total token usage (tip to tail) from the Claude transcripts. Idempotent (a retry won't
# move the start). Phase 2 spends no model tokens, so start->phase-1-end covers the whole run.
& $conda run -n Spotify --no-capture-output python run_report.py --date $today --start *>> $log

# Phase 1 - four-stage pipeline: research -> edit -> write -> review (no publishing, no git) ----
# The prompt is resume-aware (skip already-approved prompts), so the SAME prompt drives both the
# Sonnet primary run and the Opus retry - the retry just picks up whatever wasn't finished.
$prompt = @"
Run today's four-stage briefing pipeline for EVERY enabled prompt in prompts.json, following the
'Four-stage pipeline' procedure in CLAUDE.md exactly, with NOVELTY MODE: $novelty. Use --date
$today. Start with: python orchestrator.py init --date $today --novelty $novelty ; then follow its
plan and the CLAUDE.md failure rules (validate every JSON artifact, one repair attempt, mark
failures/skips, continue the batch). RESUME SEMANTICS: the init plan lists each prompt's current
status; if a prompt is already 'approved' (finished by an earlier attempt in today's run), SKIP it
entirely - do NOT re-run its agents. Only process prompts whose status is 'pending' or 'failed'.
Handle synthesis prompts (kind "synthesis", e.g. throughline) LAST, Writer then Reviewer, from the
day's APPROVED briefings. Do NOT publish, do NOT run publish_feed.py, and do NOT git commit or push
- only orchestrator.py may copy approved scripts to briefings/<id>.txt. When finished, run
python orchestrator.py status --date $today and report it. FINALLY, run
python run_report.py --date $today and write the run's agent-performance analysis to
analyses/$today.md, following the 'Run analysis' section of the daily-briefing skill (fixed
template, numbers from run_report). This is local-only; do NOT commit it.
"@

# How many prompts are still unfinished (pending/failed) per the orchestrator's run state.
# -1 means the state couldn't be read (e.g. init never ran because phase 1 died immediately).
function Get-IncompleteCount {
    $raw = & $conda run -n Spotify --no-capture-output python orchestrator.py status --date $today --json 2>> $log
    try {
        $st = ($raw -join "`n") | ConvertFrom-Json
        return @($st.prompts | Where-Object { $_.status -eq 'pending' -or $_.status -eq 'failed' }).Count
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
    & $claude -p $prompt --model claude-opus-4-8 --dangerously-skip-permissions *>> $log
    Log "phase 1 (Opus 4.8 retry) exit code: $LASTEXITCODE"
    $incomplete = Get-IncompleteCount
    Log "phase 1: $incomplete prompt(s) still unfinished after Opus 4.8 retry"
} else {
    Log "phase 1: all prompts finished on the Sonnet 5 primary run (no Opus retry needed)"
}

# Stamp the token-window END now that all model work (phase 1) is done. The in-run analysis
# already read an open-ended window; this records the precise end for any later run_report re-run.
& $conda run -n Spotify --no-capture-output python run_report.py --date $today --end *>> $log

# Phase 2 - deterministic publish (TTS -> feed -> git push) -------------------
# NOTE: confirmation email temporarily disabled (no working delivery path yet - see the
# 'publish-confirmation-email-blocked' memory). Re-add --email once BRIEFING_SMTP_USER /
# BRIEFING_SMTP_PASS are set; the send in publish_feed.py must also be un-commented.
if ($NoPublish) {
    Log "phase 2: SKIPPED (-NoPublish dry run - no TTS, no feed update, no commit, no push)"
    Log "=== daily run done (dry run) ==="
    exit 0
}

Log "phase 2: publish_feed.py --require-fresh"
& $conda run -n Spotify --no-capture-output python publish_feed.py --date $today --require-fresh *>> $log
$pubExit = $LASTEXITCODE
Log "phase 2 exit code: $pubExit"

Log "=== daily run done ==="
exit $pubExit
