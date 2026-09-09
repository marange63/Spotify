# Cautious Optimism Briefings - logon catch-up (Windows Task Scheduler).
#
# WHY THIS EXISTS. On 2026-09-09 Windows Update logged the user off at 01:29 and restarted the
# machine three times (KB5124008, KB5126052). The 03:15 publish and the 08:20 completion pass are
# registered with LogonType=Interactive, so they need a logged-on session to run at all. There was
# none between 01:29 and the 08:30 login, and Task Scheduler recorded NO RUN for either - their
# LastRunTime stayed on 2026-09-08 and NextRunTime jumped straight to 2026-09-10. `StartWhenAvailable`
# is already True on both tasks and did NOT catch them up on login. The whole day had to be produced
# and published by hand at 14:38.
#
# Nothing inside the pipeline can detect that failure, because nothing in the pipeline runs. The only
# moment the machine is guaranteed to be able to notice is the next interactive logon - which is
# exactly when this fires.
#
# WHAT IT DOES. Almost always nothing. It checks whether the show has already published everything it
# owes for today; if so it exits in a few seconds having spent zero model tokens. If the day is
# genuinely incomplete it delegates to tools\completion_run.ps1, which is already built for exactly
# this: it re-inits, resumes only unfinished prompts, publishes with --skip-published so a live
# episode is never re-TTS'd, and writes the run analysis. It also pushes an ntfy alert when it
# decides to act, so a silent scheduler failure becomes a visible one.
#
# THE TIME WINDOW MATTERS. The show's day is produced across a midnight boundary: the 22:00 job runs
# the evening BEFORE the date it is producing (-DayOffset 1), and the publish lands at 03:15. So
# "today has no episodes" is the NORMAL state between 22:00 and 03:15 and must not trigger a
# catch-up - firing then would pre-empt the 03:15 job, publish less-fresh research, and burn the
# quota window the real job needs. This script therefore only acts between -EarliestHour (default
# 04:00, comfortably after the 03:15 publish) and -LatestHour (default 21:00, comfortably before the
# 22:00 job starts the next day's batch).
#
# IT NEVER FIGHTS A RUNNING JOB. If logs\run.lock names a live process, a briefing job is already
# working and there is nothing to catch up - this exits immediately rather than queueing behind it.
#
# THIS IS A SAFETY NET, NOT THE FIX. It cannot run while nobody is logged on, so a day whose logon
# comes late still publishes late. The complete fix is to re-register the three scheduled tasks with
# LogonType=S4U ("run whether the user is logged on or not"), which requires an ELEVATED shell -
# see tools\register_tasks.ps1 -Elevated.
param(
    [int]$EarliestHour = 4,
    [int]$LatestHour   = 21,
    [string]$Deadline  = '',
    [int]$MaxRuntimeMinutes = 0,
    [switch]$WhatIfOnly   # probe only: report what it would do, change nothing
)

$ErrorActionPreference = 'Continue'
$proj  = 'C:\Users\wamfo\ClaudeDev\Spotify'
$conda = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today = Get-Date -Format 'yyyy-MM-dd'
$now   = Get-Date

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

Log "=== logon catch-up check ($today) ==="

# --- 1. Time window -----------------------------------------------------------------------------
# Outside 04:00-21:00 an incomplete day is either normal (the batch is mid-flight) or about to be
# superseded by the 22:00 job, so doing nothing is correct.
if ($now.Hour -lt $EarliestHour -or $now.Hour -ge $LatestHour) {
    Log ("catch-up: {0:HH:mm} is outside the {1:00}:00-{2:00}:00 window - nothing to do" -f $now, $EarliestHour, $LatestHour)
    exit 0
}

# --- 2. Is a briefing job already running? ------------------------------------------------------
# Wait-RunLock's own format: the lock file holds the owning PID. A live owner means a real job is
# in flight and this catch-up is redundant.
$lockPath = Join-Path $proj 'logs\run.lock'
if (Test-Path $lockPath) {
    $lockPid = (Get-Content $lockPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    $lockPid = ($lockPid -replace '[^\d]', '')
    if ($lockPid) {
        $owner = Get-Process -Id ([int]$lockPid) -ErrorAction SilentlyContinue
        if ($owner) {
            Log "catch-up: a briefing job is already running (pid $lockPid) - nothing to do"
            exit 0
        }
    }
    Log "catch-up: found a stale run.lock (pid '$lockPid' is gone) - continuing"
}

# --- 3. Does the feed already carry everything today owes? --------------------------------------
# feed_state.json is the published record. Compare it against the enabled prompts. A prompt the
# pipeline legitimately SKIPPED today (strict novelty) is not owed, so consult the run state too:
# anything already resolved to approved/skipped/failed is settled, published or not.
$probe = @'
import json, os, sys

# Deliberately imports nothing from the project: this runs from %TEMP%, where the repo is not on
# sys.path. The project root arrives as argv[2] instead.
date = sys.argv[1]
here = sys.argv[2]

with open(os.path.join(here, "prompts.json"), encoding="utf-8") as f:
    prompts = json.load(f)["prompts"]
enabled = [p["id"] for p in prompts if p.get("enabled", True)]

published = set()
state_path = os.path.join(here, "feed_state.json")
if os.path.exists(state_path):
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    for ep in state.get("episodes", state if isinstance(state, list) else []):
        if isinstance(ep, dict) and ep.get("date") == date:
            published.add(ep.get("prompt_id") or ep.get("id"))

settled = {}
run_path = os.path.join(here, "runs", date, "run.json")
if os.path.exists(run_path):
    with open(run_path, encoding="utf-8") as f:
        run = json.load(f)
    for p in run.get("prompts", []):
        settled[p.get("id")] = p.get("status")

# Owed = enabled, not published, and not deliberately skipped/failed by the pipeline.
owed = [pid for pid in enabled
        if pid not in published and settled.get(pid) not in ("skipped", "failed")]

print(json.dumps({"enabled": len(enabled), "published": len(published),
                  "owed": owed, "has_run": os.path.exists(run_path)}))
'@

$probePath = Join-Path $env:TEMP "briefing_catchup_probe.py"
Set-Content -Path $probePath -Value $probe -Encoding utf8

$raw = & $conda run -n Spotify --no-capture-output python $probePath $today $proj 2>> $log
$probeExit = $LASTEXITCODE
$text = ($raw | Out-String).Trim()

$info = $null
if ($probeExit -ne 0 -or -not $text) {
    Log "catch-up: publish-state probe produced nothing (exit $probeExit) - assuming the day needs finishing"
} else {
    try {
        $info = $text | ConvertFrom-Json
    } catch {
        Log "catch-up: could not parse the publish state ($_) - assuming the day needs finishing"
    }
}

if ($null -ne $info) {
    $owed = @($info.owed)
    Log ("catch-up: {0} enabled, {1} published for {2}, {3} still owed" -f `
         $info.enabled, $info.published, $today, $owed.Count)
    if ($owed.Count -eq 0) {
        Log "catch-up: today is complete - nothing to do (zero model tokens spent)"
        exit 0
    }
    Log ("catch-up: outstanding -> {0}" -f ($owed -join ', '))
}

# --- 4. Act -------------------------------------------------------------------------------------
if ($WhatIfOnly) {
    Log "catch-up: -WhatIfOnly set - would run tools\completion_run.ps1 now; stopping here"
    exit 0
}

Log "catch-up: the scheduled run did not complete today - starting the completion pass"

# Make the silent failure visible. Best-effort: never let a notification problem sink the run.
$body = "The 03:15 / 08:20 scheduled run did not complete for $today. Catching up now from the logon trigger."
& $conda run -n Spotify --no-capture-output python (Join-Path $proj 'ntfy_push.py') `
    --title "Briefing catch-up started" --tags warning $body *>> $log

# NB: not $args - that is an automatic variable in PowerShell.
$psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'completion_run.ps1'))
if ($Deadline)          { $psArgs += @('-Deadline', $Deadline) }
if ($MaxRuntimeMinutes) { $psArgs += @('-MaxRuntimeMinutes', "$MaxRuntimeMinutes") }

& powershell.exe @psArgs
$code = $LASTEXITCODE
Log "=== logon catch-up done (completion pass exit $code) ==="
exit $code
