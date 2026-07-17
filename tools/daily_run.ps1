# Cautious Optimism Briefings - unattended daily run (Windows Task Scheduler).
#
# Two phases, deliberately separated so publishing can't be skipped by an AI hiccup:
#   1. Headless Claude Code runs the three-agent pipeline (Researcher -> Analyst-Editor ->
#      Writer-Reviewer, see CLAUDE.md) for every enabled prompt; only reviewed-and-approved
#      scripts land in briefings/<id>.txt (enforced by orchestrator.py).
#      MODEL PINNING: the three subagents pin their own models in .claude/agents/*.md frontmatter
#      (researcher=sonnet, analyst-editor=opus, writer-reviewer=sonnet), which OVERRIDES the
#      --model/--fallback-model below for the actual research/editing/writing work. So --model
#      claude-fable-5 + --fallback-model claude-opus-4-8 now govern only the lightweight PARENT
#      orchestrator session (reading files, running orchestrator.py, dispatching subagents).
#      The parent does little token work, so the classic Fable usage-limit death is now unlikely;
#      the Opus retry below is kept as a harmless safety net. The orchestrator's run state is
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

# Phase 1 - three-agent pipeline: research -> edit -> write/review (no publishing, no git) ----
# The prompt is resume-aware (skip already-approved prompts), so the SAME prompt drives both the
# Fable primary run and the Opus retry - the retry just picks up whatever Fable didn't finish.
$prompt = @"
Run today's three-agent briefing pipeline for EVERY enabled prompt in prompts.json, following the
'Three-agent pipeline' procedure in CLAUDE.md exactly, with NOVELTY MODE: $novelty. Use --date
$today. Start with: python orchestrator.py init --date $today --novelty $novelty ; then follow its
plan and the CLAUDE.md failure rules (validate every JSON artifact, one repair attempt, mark
failures/skips, continue the batch). RESUME SEMANTICS: the init plan lists each prompt's current
status; if a prompt is already 'approved' (finished by an earlier attempt in today's run), SKIP it
entirely - do NOT re-run its agents. Only process prompts whose status is 'pending' or 'failed'.
Handle synthesis prompts (kind "synthesis", e.g. throughline) LAST, Writer-Reviewer only, from the
day's APPROVED briefings. Do NOT publish, do NOT run publish_feed.py, and do NOT git commit or push
- only orchestrator.py may copy approved scripts to briefings/<id>.txt. When finished, run
python orchestrator.py status --date $today and report it.
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

# Primary attempt - PARENT session pinned to Fable 5 (so a changed interactive default can't flip
# it), with automatic fallback to Opus 4.8 if Fable is overloaded/unavailable mid-run. Note the
# subagents ignore this and use their frontmatter models (sonnet/opus); this governs orchestration.
Log "phase 1: headless Claude - three-agent pipeline (novelty=$novelty), parent Fable 5"
& $claude -p $prompt --model claude-fable-5 --fallback-model claude-opus-4-8 --dangerously-skip-permissions *>> $log
Log "phase 1 (Fable 5) exit code: $LASTEXITCODE"

# If prompts remain unfinished, retry the leftovers with the parent session on Opus 4.8. Now that
# the subagents are model-pinned this mainly guards against the parent Fable session dying (rare);
# idempotent init means this resumes - approved prompts are skipped.
$incomplete = Get-IncompleteCount
if ($incomplete -ne 0) {
    if ($incomplete -lt 0) {
        $why = "run state unreadable - Fable may have hit its limit before init"
    } else {
        $why = "$incomplete prompt(s) unfinished after Fable (likely Fable usage limit)"
    }
    Log "phase 1: $why - retrying on Opus 4.8"
    & $claude -p $prompt --model claude-opus-4-8 --dangerously-skip-permissions *>> $log
    Log "phase 1 (Opus 4.8 retry) exit code: $LASTEXITCODE"
    $incomplete = Get-IncompleteCount
    Log "phase 1: $incomplete prompt(s) still unfinished after Opus 4.8 retry"
} else {
    Log "phase 1: all prompts finished on Fable 5 (no Opus fallback needed)"
}

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
