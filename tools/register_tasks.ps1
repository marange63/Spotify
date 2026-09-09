# Cautious Optimism Briefings - Windows Task Scheduler registration.
#
# The show runs on three scheduled tasks plus one safety net:
#
#   -Midnight    22:00  pipeline-only half of the batch for TOMORROW (-DayOffset 1, -NoPublish)
#   -Daily       03:15  the rest of the batch, then publishes the whole day
#   -Completion  08:20  finishes anything the 03:15 run left, in a fresh usage-quota window
#   -Catchup     logon  safety net: if the day is still incomplete, run the completion pass
#
# WHY -Catchup EXISTS. On 2026-09-09 Windows Update logged the user off at 01:29 and restarted the
# machine (KB5124008, KB5126052). The three production tasks are registered with
# LogonType=Interactive, which needs a logged-on session. There was none until 08:30, so the 03:15
# and 08:20 runs did not merely fail - Task Scheduler recorded NO RUN for either, and
# `StartWhenAvailable` (already True on both) did not catch them up at login. The failure was
# invisible to everything in the pipeline, because nothing in the pipeline ran. The whole day was
# produced and published by hand at 14:38.
#
# THE COMPLETE FIX IS -UpgradeToS4U. LogonType=S4U means "run whether the user is logged on or
# not", which removes the dependency on a desktop session entirely. Registering an S4U principal
# requires an ELEVATED shell (a non-elevated Register-ScheduledTask returns "Access is denied"),
# so it cannot be done from an ordinary agent session - run this yourself from an admin PowerShell:
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File tools\register_tasks.ps1 -UpgradeToS4U
#
# VERIFY IT AFTERWARDS, DO NOT ASSUME IT. S4U hands the task a restricted token with no stored
# credentials. That is fine for the filesystem and for conda, but it is exactly the situation in
# which DPAPI-protected secrets can fail to decrypt - so a headless `claude` session could in
# principle fail to authenticate under S4U while working perfectly when logged on. This script
# therefore also registers a one-shot probe task (-Probe) that runs a trivial Claude call under the
# new principal and writes the result to logs\s4u_probe.txt. Run the probe and read that file
# BEFORE trusting an overnight run to the new principal. If the probe cannot authenticate, revert
# with -RevertToInteractive and keep the logon catch-up as the safety net.
param(
    [switch]$UpgradeToS4U,
    [switch]$RevertToInteractive,
    [switch]$Probe,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$proj     = 'C:\Users\wamfo\ClaudeDev\Spotify'
$tools    = Join-Path $proj 'tools'
$user     = 'wamfo'
$prodrun  = @('CautiousOptimismBriefings-Midnight',
              'CautiousOptimismBriefings-Daily',
              'CautiousOptimismBriefings-Completion')
$catchup  = 'CautiousOptimismBriefings-Catchup'

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------------------------
# 1. The logon catch-up. Interactive by design: it exists precisely to fire when a session finally
#    appears, so a non-interactive principal would defeat its purpose. Needs no elevation.
# ---------------------------------------------------------------------------------------------
function Register-Catchup {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
        '-NoProfile -ExecutionPolicy Bypass -File "{0}\catchup_run.ps1" -Deadline 21:00 -MaxRuntimeMinutes 240' -f $tools)

    # 3-minute delay so the network stack, Google Drive and conda are settled before it probes.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = 'PT3M'

    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 5)

    if ($WhatIf) { "WHATIF: would register $catchup (AtLogOn +3m, Interactive)"; return }
    Register-ScheduledTask -TaskName $catchup -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    "registered: $catchup  (AtLogOn +3m, Interactive)"
}

# ---------------------------------------------------------------------------------------------
# 2. Principal swap for the three production tasks. Keeps each task's existing action, trigger and
#    settings untouched - only the principal changes.
# ---------------------------------------------------------------------------------------------
function Set-Principal([string]$logonType) {
    if (-not (Test-Elevated)) {
        throw "Changing a task principal to $logonType requires an ELEVATED PowerShell. Re-run this script from an admin shell."
    }
    foreach ($name in $prodrun) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
        $p = New-ScheduledTaskPrincipal -UserId $user -LogonType $logonType -RunLevel $t.Principal.RunLevel
        if ($WhatIf) { "WHATIF: would set $name -> LogonType=$logonType"; continue }
        Set-ScheduledTask -TaskName $name -Principal $p | Out-Null
        "updated: $name  -> LogonType=$logonType"
    }
}

# ---------------------------------------------------------------------------------------------
# 3. The S4U authentication probe. Proves a headless Claude session can actually authenticate under
#    the new principal before an overnight run depends on it.
# ---------------------------------------------------------------------------------------------
function Invoke-S4UProbe {
    if (-not (Test-Elevated)) { throw "Registering the S4U probe task requires an ELEVATED PowerShell." }
    $probeScript = Join-Path $tools 's4u_probe.ps1'
    $out = Join-Path $proj 'logs\s4u_probe.txt'
    @"
`$out = '$out'
"probe start `$(Get-Date -Format o)" | Set-Content -Encoding utf8 `$out
"whoami : `$(whoami)"               | Add-Content -Encoding utf8 `$out
"session: `$([System.Diagnostics.Process]::GetCurrentProcess().SessionId)" | Add-Content -Encoding utf8 `$out
try {
    `$r = & claude -p "Reply with exactly: PROBE_OK" --model claude-haiku-4-5-20251001 2>&1 | Out-String
    "claude exit: `$LASTEXITCODE"    | Add-Content -Encoding utf8 `$out
    "claude said: `$r"               | Add-Content -Encoding utf8 `$out
} catch { "claude threw: `$_"        | Add-Content -Encoding utf8 `$out }
"probe done `$(Get-Date -Format o)"  | Add-Content -Encoding utf8 `$out
"@ | Set-Content -Encoding utf8 -Path $probeScript

    $action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
        '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $probeScript)
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    if ($WhatIf) { "WHATIF: would register + run ZZ-Briefings-S4U-Probe"; return }

    Register-ScheduledTask -TaskName 'ZZ-Briefings-S4U-Probe' -Action $action `
        -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName 'ZZ-Briefings-S4U-Probe'
    "probe started - read logs\s4u_probe.txt in a few seconds; it must contain PROBE_OK."
    "when you are done: Unregister-ScheduledTask -TaskName 'ZZ-Briefings-S4U-Probe' -Confirm:`$false"
}

# ---------------------------------------------------------------------------------------------
# 4. Turn on the Task Scheduler operational log. It is DISABLED by default, which is why the
#    2026-09-09 failure left no trace at all: `Get-WinEvent -LogName
#    Microsoft-Windows-TaskScheduler/Operational` errored rather than showing the missed runs, and
#    the outage had to be reconstructed from kernel boot/shutdown events instead. Needs elevation.
# ---------------------------------------------------------------------------------------------
function Enable-TaskSchedulerLog {
    if (-not (Test-Elevated)) { throw "Enabling the Task Scheduler operational log requires an ELEVATED PowerShell." }
    if ($WhatIf) { "WHATIF: would enable Microsoft-Windows-TaskScheduler/Operational"; return }
    & wevtutil.exe sl 'Microsoft-Windows-TaskScheduler/Operational' /e:true
    if ($LASTEXITCODE -eq 0) { "enabled: Microsoft-Windows-TaskScheduler/Operational event log" }
    else { "WARNING: could not enable the Task Scheduler operational log (wevtutil exit $LASTEXITCODE)" }
}

# ---------------------------------------------------------------------------------------------
if ($UpgradeToS4U)       { Set-Principal 'S4U'; Enable-TaskSchedulerLog }
if ($RevertToInteractive){ Set-Principal 'Interactive' }
if ($Probe)              { Invoke-S4UProbe }
if (-not ($UpgradeToS4U -or $RevertToInteractive -or $Probe)) { Register-Catchup }

""
"current state:"
foreach ($name in ($prodrun + $catchup)) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $t) { "  {0,-45} NOT REGISTERED" -f $name; continue }
    $i = $t | Get-ScheduledTaskInfo
    "  {0,-45} {1,-12} last={2} result={3}" -f $name, $t.Principal.LogonType, $i.LastRunTime, $i.LastTaskResult
}
