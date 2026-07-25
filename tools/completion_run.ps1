# Cautious Optimism Briefings - 10:05 completion pass (Windows Task Scheduler).
#
# WHY THIS EXISTS. The 5 AM batch now costs more than a single Claude session window allows. On
# 2026-07-25 the primary run spent ~58M tokens in 16 minutes, finished 9 of 11 prompts, and the
# session cap ("resets 10am") stopped the rest - three episodes had to be finished by hand at 11:00.
# No scheduling tweak fixes that: the quota is per rolling ~5-hour window, so idle time inside the
# window restores nothing and starting EARLIER only risks colliding with the previous evening's
# session. The one pause that works is one that crosses the reset boundary. This job is that pause.
#
# It runs at 10:05, just after the window the 5 AM job opened has reset, and finishes whatever was
# truncated - in a fresh quota, automatically.
#
# IT IS CHEAP WHEN THERE IS NOTHING TO DO. It asks orchestrator.py resume whether any prompt is
# unfinished; if none are, it spends ZERO model tokens and only runs the (deterministic) publish
# with --skip-published to catch anything approved but not yet in the feed. On a normal morning
# that is a few seconds and no cost.
#
# Everything is appended to the SAME logs\daily-<date>.log as the 5 AM run, so one file tells the
# whole story of the day. Exit code is non-zero only if publishing failed.
param([switch]$NoPublish)

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
$conda  = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today  = Get-Date -Format 'yyyy-MM-dd'

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

Log "=== completion pass start ($today) ==="

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

    . (Join-Path $PSScriptRoot 'phase1_prompt.ps1')
    $preamble = @"
CONTEXT: this is the 10:05 COMPLETION PASS. The 5 AM scheduled run was cut short (usually by the
session usage cap) and left some prompts unfinished. A fresh session window is now available.
Finish ONLY what is outstanding, exactly per the resume semantics below - re-running a finished
prompt or a completed stage wastes the budget this pass exists to provide. Some briefings for today
are ALREADY PUBLISHED to the feed; that is expected and is not a reason to redo them.
"@
    $prompt = Get-Phase1Prompt -Today $today -Novelty $novelty -Preamble $preamble

    Log "completion: phase 1 (resume) - parent Sonnet 5, novelty=$novelty"
    & $claude -p $prompt --model claude-sonnet-5 --fallback-model claude-opus-4-8 --dangerously-skip-permissions *>> $log
    Log "completion: phase 1 exit code: $LASTEXITCODE"

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
exit $pubExit
