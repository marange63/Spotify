# Cautious Optimism Briefings - 08:20 completion pass (Windows Task Scheduler).
#
# WHY THIS EXISTS. The nightly batch now costs more than a single Claude session window allows. On
# 2026-07-25 the primary run spent ~58M tokens in 16 minutes, finished 9 of 11 prompts, and the
# session cap ("resets 10am") stopped the rest - three episodes had to be finished by hand at 11:00.
# No scheduling tweak fixes that: the quota is per rolling ~5-hour window, so idle time inside the
# window restores nothing and starting EARLIER only risks colliding with the previous evening's
# session. The one pause that works is one that crosses the reset boundary. This job is that pause.
#
# It runs at 08:20, just after the window the 03:15 publish job opened has reset, and finishes
# whatever was truncated - in a fresh quota, automatically.
#
# WHY :20 AND NOT :05 PAST THE RESET. The reset boundary is not fixed at the top of the hour - it
# tracks when the window actually opened, so it drifts. On 2026-08-25 the cap message read "resets
# 10:10am" and the pass, then scheduled five minutes after the expected reset, fired early: every
# session died instantly, it spent its one shot on nothing, and four prompts had to be finished by
# hand. The 15-minute cushion is margin against that drift. If a future run again dies instantly
# with a "session limit" line, read the reset time in logs\daily-<date>.log and push this later
# still, rather than assuming the pass itself is broken.
#
# TIMES SHIFTED 4h EARLIER (2026-08-26): the batch now runs 22:00 -> 03:15 -> 08:20 (was
# 00:00 -> 05:15 -> 10:20) so a normal night is live by ~03:55, well before 07:00 ET. Spacing is
# unchanged; only this pass can now land after 07:00, and only when the 03:15 run was truncated.
#
# IT IS CHEAP WHEN THERE IS NOTHING TO DO. It asks orchestrator.py resume whether any prompt is
# unfinished; if none are, it spends ZERO model tokens and only runs the (deterministic) publish
# with --skip-published to catch anything approved but not yet in the feed. On a normal morning
# that is a few seconds and no cost.
#
# Everything is appended to the SAME logs\daily-<date>.log as the 03:15 run, so one file tells the
# whole story of the day. Exit code is non-zero only if publishing failed.
# Reasoning effort for every headless Claude session this script starts. Pinned for the same
# reason as --model: effortLevel in %USERPROFILE%\.claude\settings.json is user-global, so an
# interactive /effort would otherwise silently change what the unattended run costs. The
# pipeline subagents inherit it (none pin an effort in their frontmatter).
# -Deadline 'HH:mm' / -MaxRuntimeMinutes (since 2026-09-03): if this pass ITSELF hits the usage
# cap (2026-09-03: "resets 10:50am"), it now waits for the reset and resumes, instead of publishing
# a partial day - but only when the reset lands before this deadline (the task passes 13:00).
# See Wait-ForReset in tools\phase1_prompt.ps1.
param([switch]$NoPublish, [int]$ChunkSize = 3,
      [ValidateSet('low','medium','high','xhigh','max')][string]$Effort = 'medium',
      [string]$Deadline = '', [int]$MaxRuntimeMinutes = 0)

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
# $claude is resolved below via Resolve-ClaudeExe (tools\phase1_prompt.ps1) - see the note there.
$conda  = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today  = Get-Date -Format 'yyyy-MM-dd'
$jobStart = Get-Date

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

Log "=== completion pass start ($today) ==="

# Shared helpers: phase-1 prompts + chunking, usage-cap wait, run lock (see the notes there).
. (Join-Path $PSScriptRoot 'phase1_prompt.ps1')
$jobName    = "completion pass ($today)"
$deadlineAt = Resolve-Deadline $Deadline
if ($MaxRuntimeMinutes -gt 0) {
    $cap = $jobStart.AddMinutes($MaxRuntimeMinutes)
    if (-not $deadlineAt -or $cap -lt $deadlineAt) { $deadlineAt = $cap }
}
if ($deadlineAt) { Log "cap-wait deadline: $($deadlineAt.ToString('ddd HH:mm'))" } else { Log "cap-wait deadline: none (a usage cap is not waited for)" }

# Never overlap the 03:15 job (it may have waited for a cap reset and still be publishing).
if (-not (Wait-RunLock -Proj $proj -Log $log -Job $jobName -Deadline $deadlineAt)) {
    Log "ABORT: another briefing job is still running - see the lock line above"
    exit 4
}

# Novelty must match the morning's run so a resumed prompt is judged on the same bar. Read it from
# the run state; fall back to strict (the scheduled default) if the 5 AM job never got as far as init.
$novelty = 'strict'
$runJson = Join-Path $proj "runs\$today\run.json"
if (Test-Path $runJson) {
    try {
        $rj = Get-Content $runJson -Raw | ConvertFrom-Json
        if ($rj.novelty) { $novelty = $rj.novelty }
    } catch { Log "completion: could not read run.json novelty ($_) - defaulting to $novelty" }
} else {
    Log "completion: no runs\$today\run.json - the 5 AM job never initialised; running the full batch"
}

# What is left? resume --prune also clears superseded artifacts before anything reads them.
$unfinished = -1
$raw = & $conda run -n Spotify --no-capture-output python orchestrator.py resume --date $today --prune --json 2>> $log
try {
    $rp = ($raw -join "`n") | ConvertFrom-Json
    $unfinished = @($rp.unfinished).Count
    if ($unfinished -gt 0) { Log "completion: $unfinished prompt(s) unfinished: $($rp.unfinished -join ', ')" }
} catch {
    # No run state at all (5 AM job never ran) - treat as "everything to do" so the day still ships.
    Log "completion: could not read resume plan ($_) - assuming a full batch is needed"
    $unfinished = -1
}

if ($unfinished -eq 0) {
    Log "completion: nothing unfinished - skipping phase 1 entirely (no model tokens spent)"
} else {
    # Open a NEW token-window segment. The 5 AM job closed its own, so run_report totals the two
    # sittings and excludes the interactive work in between - without this the tokens/word metric
    # is meaningless on any day this job does work.
    & $conda run -n Spotify --no-capture-output python run_report.py --date $today --start *>> $log

    # Resolve the Claude Code binary. Aborting here is deliberate: an unresolvable path used to
    # surface only as a per-chunk invocation error, and the run would carry on to publish nothing.
    $claude = Resolve-ClaudeExe
    if (-not $claude) {
        Log "ABORT: could not locate claude.exe (looked in %APPDATA%\npm, %USERPROFILE%\.local\bin, and PATH)"
        Remove-RunLock -Proj $proj
        exit 3
    }
    Log "claude: $claude"

    $preamble = @"
CONTEXT: this is the 08:20 COMPLETION PASS. The 03:15 scheduled run was cut short (usually by the
session usage cap) and left some prompts unfinished. A fresh session window is now available.
Finish ONLY what is outstanding, exactly per the resume semantics below - re-running a finished
prompt or a completed stage wastes the budget this pass exists to provide. Some briefings for today
are ALREADY PUBLISHED to the feed; that is expected and is not a reason to redo them.
"@
    # Ensure init + prune have run once (safe even if the 5 AM job never did), so the chunk sessions
    # can use -SkipInit. Both are idempotent - approvals and prompt.txt survive.
    & $conda run -n Spotify --no-capture-output python orchestrator.py init --date $today --novelty $novelty *>> $log
    & $conda run -n Spotify --no-capture-output python orchestrator.py resume --date $today --prune *>> $log

    Log "completion: chunked phase 1 (resume, ChunkSize=$ChunkSize) - parent Sonnet 5, novelty=$novelty"
    Invoke-Phase1Chunked -Claude $claude -Conda $conda -Today $today -Novelty $novelty -Log $log -ChunkSize $ChunkSize -Effort $Effort -Preamble $preamble -Deadline $deadlineAt -Job $jobName
    Log "completion: chunked phase 1 done"

    # The chunk sessions ran with -SkipAnalysis; write the day's final analysis once, in its own
    # small session (this pass completes the day, so it owns the analysis). Under a cap block the
    # session would only be refused - skip it and still publish what is approved.
    if ($script:CapBlocked) {
        Log "completion: run analysis skipped (usage cap blocked this pass - see the cap lines above)"
    } else {
    Log "completion: writing run analysis (dedicated session)"
    $analysisPrompt = @"
Run python run_report.py --date $today and write the run's agent-performance analysis to
analyses/$today.md, following the 'Run analysis' section of the daily-briefing skill (fixed template,
numbers from run_report). Local-only; do NOT commit it. runs/$today/token_window.json holds more than
one segment (the 5 AM run plus this completion pass) - say so and note which prompts ran when. Do NOT
run any pipeline agents or touch any briefing; this session is analysis only.
"@
    [void](Invoke-ClaudeSession -Claude $claude -Prompt $analysisPrompt -Log $log -Model claude-sonnet-5 -Fallback claude-opus-4-8 -Effort $Effort)
    }

    & $conda run -n Spotify --no-capture-output python run_report.py --date $today --end *>> $log

    $raw = & $conda run -n Spotify --no-capture-output python orchestrator.py status --date $today --json 2>> $log
    try {
        $st = ($raw -join "`n") | ConvertFrom-Json
        $left = @($st.prompts | Where-Object { $_.status -eq 'pending' -or $_.status -eq 'failed' }).Count
        Log "completion: $left prompt(s) still unfinished after the completion pass"
    } catch { Log "completion: could not read status after phase 1" }
}

if ($NoPublish) {
    Log "completion: phase 2 SKIPPED (-NoPublish)"
    Log "=== completion pass done (dry run) ==="
    Remove-RunLock -Proj $proj
    exit 0
}

# --skip-published is what makes a second same-day publish safe: episodes already in the feed are
# left alone. Re-running TTS on them would rewrite their audio and enclosure URL, forcing Spotify
# to re-download identical episodes (see the cache-busting note in docs/ARCHITECTURE.md).
Log "completion: phase 2 publish_feed.py --require-fresh --skip-published"
& $conda run -n Spotify --no-capture-output python publish_feed.py --date $today --require-fresh --skip-published *>> $log
$pubExit = $LASTEXITCODE
Log "completion: phase 2 exit code: $pubExit"

Log "=== completion pass done ==="
Remove-RunLock -Proj $proj
exit $pubExit
