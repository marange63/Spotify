# Shared phase-1 prompt for the briefing pipeline.
#
# Dot-sourced by BOTH tools\daily_run.ps1 (the nightly job) and tools\completion_run.ps1 (the 08:20
# pass that finishes whatever the session cap truncated). It lives in one file so the resume
# semantics can never drift between them - the completion pass depends entirely on those semantics
# being obeyed, and a stale copy would silently restart finished prompts.
#
# Since 2026-09-03 it also holds the USAGE-CAP HANDLING (Invoke-ClaudeSession / Get-LimitReset /
# Wait-ForReset) and the cross-job RUN LOCK (Wait-RunLock / Remove-RunLock) - see the notes on each.

# Locate the Claude Code executable. Resolved at RUN TIME rather than hard-coded, because the
# install location moved once already: this project originally used the native installer's
# %USERPROFILE%\.local\bin\claude.exe, which vanished when that install was removed in favour of
# the global npm one (2026-08-21). Preference order is npm-global, then the native installer,
# then whatever is on PATH.
#
# NOTE we deliberately target the npm package's REAL bin\claude.exe and never the
# %APPDATA%\npm\claude.cmd shim: the phase-1 prompts are multi-line strings, and routing them
# through cmd.exe mangles the embedded newlines. The Get-Command fallback is filtered to a .exe
# for the same reason.
#
# Returns $null when nothing usable is found, so the caller can abort loudly instead of failing
# once per chunk and leaving a run that quietly published nothing.
function Resolve-ClaudeExe {
    $candidates = @(
        (Join-Path $env:APPDATA 'npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe'),
        (Join-Path $env:USERPROFILE '.local\bin\claude.exe')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue |
           Where-Object { $_.Source -like '*.exe' } | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

# --- deadlines ------------------------------------------------------------------------------
# Turn a 'HH:mm' clock (the job's -Deadline argument) into the NEXT occurrence of that time:
# today if it is still ahead, otherwise tomorrow. The 22:00 half passes 02:45 (= tomorrow),
# the 03:15 job passes 07:30 (= today). $null in -> $null out.
function Resolve-Deadline {
    param([string]$Clock)
    if (-not $Clock) { return $null }
    $t = [datetime]::ParseExact($Clock.Trim(), 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
    $d = (Get-Date).Date.AddHours($t.Hour).AddMinutes($t.Minute)
    if ($d -le (Get-Date)) { $d = $d.AddDays(1) }
    return $d
}

# --- usage-cap handling -----------------------------------------------------------------------
# WHY. The nightly batch runs inside Claude's rolling ~5-hour usage window. When the window is
# spent, every `claude -p` is refused instantly with one line on stdout:
#     You've hit your session limit - resets 10:10pm (America/New_York)
# Before 2026-09-03 nothing read that line: the job just moved on to the next chunk, which was
# refused too, and the whole slot was lost (2026-09-03 22:00: reset was 10 minutes away; 2026-09-03
# 08:20: reset at 10:50 and five episodes never shipped). The fix is to READ the reset time, sleep
# past it (zero tokens), and re-run the same chunk once. The resume semantics make the re-run
# cheap: a session refused up front did nothing, and one cut short mid-batch left artifacts that
# `resume --prune` picks up at the stage level.
#
# BOUNDED. The sleep is capped by the job's deadline (Resolve-Deadline) so a 22:00 half can never
# still be running when the 03:15 job fires, and a 03:15 job never waits past the morning. A reset
# beyond the deadline means GIVE UP: log it, push an ntfy alert, and leave the rest to the next
# scheduled job. One wait per job - a second cap in the same job means the batch outgrew a window.
# The Task Scheduler execution limits were raised alongside this (a 2h limit would have killed the
# waiting job before the reset).

# Run one headless Claude session. Output is appended to the log exactly as before AND captured,
# so the caller can look for the cap line. Returns the captured text.
function Invoke-ClaudeSession {
    param(
        [Parameter(Mandatory)][string]$Claude,
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$Log,
        [string]$Model = 'claude-sonnet-5',
        [string]$Fallback = 'claude-opus-4-8',
        [string]$Effort = 'medium'
    )
    $lines = & $Claude -p $Prompt --model $Model --fallback-model $Fallback --effort $Effort --dangerously-skip-permissions 2>&1 |
             ForEach-Object { "$_" } | Tee-Object -FilePath $Log -Append
    return (@($lines) -join "`n")
}

# Parse the CLI's cap line. Returns $null when there is none, else a hashtable:
#   Kind  - 'session' / 'weekly' / whatever the CLI said
#   Text  - the raw "resets ..." phrase, for the log
#   At    - [datetime] of the reset (local), or $null when the phrase could not be parsed
function Get-LimitReset {
    param([string]$Text)
    if (-not $Text) { return $null }
    $m = [regex]::Match($Text, "hit your (\w+) limit[^\r\n]*?resets\s+([^\r\n(]+?)\s*(?:\(|\r|\n|$)",
                        [Text.RegularExpressions.RegexOptions]'IgnoreCase, Multiline')
    if (-not $m.Success) { return $null }
    $kind = $m.Groups[1].Value.ToLower()
    $when = $m.Groups[2].Value.Trim()
    $at   = $null
    $inv  = [Globalization.CultureInfo]::InvariantCulture
    $norm = ($when -replace '\s+', ' ')
    # Casing candidates: month/day names parse case-sensitively under the invariant culture,
    # the AM/PM designator wants upper case - try the phrase as-is, upper-cased, and title-cased.
    $cands = @($norm, $norm.ToUpper(), ((Get-Culture).TextInfo.ToTitleCase($norm.ToLower()) -replace '(?i)(\d)(am|pm)\b', { $_.Groups[1].Value + $_.Groups[2].Value.ToUpper() }))
    $now  = Get-Date
    # Time-only ("10:10pm", "10pm"): today, or tomorrow if that time is already well past.
    # A reset a few minutes in the past means it already happened - retry straight away.
    foreach ($f in 'h:mmtt','htt','h:mm tt','h tt') {
        foreach ($c in $cands) {
            try {
                $t = [datetime]::ParseExact($c, $f, $inv)
                $at = $now.Date.AddHours($t.Hour).AddMinutes($t.Minute)
                if ($at -lt $now) {
                    if (($now - $at).TotalMinutes -le 30) { $at = $now } else { $at = $at.AddDays(1) }
                }
                break
            } catch { }
        }
        if ($at) { break }
    }
    if (-not $at) {
        # Weekday + time ("Tuesday 9am", "Tue at 9:30am") - .NET will not parse a bare day name
        # (it checks it against its default date), so split it off and step forward to that day.
        $wd = [regex]::Match($norm, '^(?i)(mon|tue|wed|thu|fri|sat|sun)\w*\s+(?:at\s+)?(.+)$')
        if ($wd.Success) {
            $days = @{ mon='Monday'; tue='Tuesday'; wed='Wednesday'; thu='Thursday'; fri='Friday'; sat='Saturday'; sun='Sunday' }
            $want = [DayOfWeek]$days[$wd.Groups[1].Value.ToLower()]
            $tp = $wd.Groups[2].Value.Trim().ToUpper()
            foreach ($f in 'h:mmtt','htt','h:mm tt','h tt') {
                try {
                    $t = [datetime]::ParseExact($tp, $f, $inv)
                    $at = $now.Date.AddHours($t.Hour).AddMinutes($t.Minute)
                    while ($at.DayOfWeek -ne $want -or $at -lt $now) { $at = $at.AddDays(1) }
                    break
                } catch { }
            }
        }
    }
    if (-not $at) {
        # Month day + time ("Sep 8 at 9am") - the other weekly-cap phrasing.
        foreach ($f in 'MMM d \a\t htt','MMM d \a\t h:mmtt','MMM d htt','MMM d h:mmtt') {
            foreach ($c in $cands) {
                try {
                    $t = [datetime]::ParseExact($c, $f, $inv)
                    $at = New-Object datetime ($now.Year, $t.Month, $t.Day, $t.Hour, $t.Minute, 0)
                    if ($at -lt $now.AddDays(-1)) { $at = $at.AddYears(1) }
                    break
                } catch { }
            }
            if ($at) { break }
        }
    }
    return @{ Kind = $kind; Text = "$kind limit, resets $when"; At = $at }
}

# Send the give-up alert to the owner's phone (best-effort; never throws).
function Send-RunAlert {
    param([string]$Conda, [string]$Title, [string]$Body, [string]$Log)
    try {
        & $Conda run -n Spotify --no-capture-output python ntfy_push.py --title $Title --tags warning $Body *>> $Log
    } catch { "$(Get-Date -Format 'HH:mm:ss')  alert push failed ($_)" | Tee-Object -FilePath $Log -Append | Out-Null }
}

# Sleep until the cap resets (plus a cushion). Returns $true when the caller may retry, $false
# when the job should give up (reset unparseable, or past the deadline) - in which case the alert
# has already been pushed and $script:CapBlocked is set so the caller skips further model work.
function Wait-ForReset {
    param(
        [Parameter(Mandatory)]$Cap,
        $Deadline = $null,
        [Parameter(Mandatory)][string]$Log,
        [string]$Conda = '',
        [string]$Job = 'briefing run',
        [string]$Left = '',
        [int]$CushionMinutes = 5
    )
    function _wlog($m) { "$(Get-Date -Format 'HH:mm:ss')  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
    $script:CapBlocked = $false
    if (-not $Cap.At) {
        _wlog "cap: could not parse the reset time from '$($Cap.Text)' - giving up; the next scheduled job will resume"
        $script:CapBlocked = $true
        if ($Conda) { Send-RunAlert -Conda $Conda -Log $Log -Title "Briefing run paused ($Job)" -Body "Claude $($Cap.Text) - could not parse the reset time. Left unfinished: $Left. The next scheduled job will resume." }
        return $false
    }
    $until = $Cap.At.AddMinutes($CushionMinutes)
    if ($Deadline -and $until -gt $Deadline) {
        _wlog "cap: $($Cap.Text) -> would resume at $($until.ToString('HH:mm')) but this job's deadline is $($Deadline.ToString('ddd HH:mm')) - giving up; the next scheduled job will resume"
        $script:CapBlocked = $true
        if ($Conda) { Send-RunAlert -Conda $Conda -Log $Log -Title "Briefing run paused ($Job)" -Body "Claude $($Cap.Text) (past this job's $($Deadline.ToString('HH:mm')) deadline). Left unfinished: $Left. The next scheduled job will resume." }
        return $false
    }
    $mins = [Math]::Max(0, [int]([Math]::Ceiling(($until - (Get-Date)).TotalMinutes)))
    $dl = 'none'
    if ($Deadline) { $dl = $Deadline.ToString('ddd HH:mm') }
    _wlog "cap: $($Cap.Text) -> waiting ~$mins min until $($until.ToString('HH:mm')) (deadline $dl), no tokens spent"
    while ((Get-Date) -lt $until) {
        $rem = ($until - (Get-Date)).TotalSeconds
        Start-Sleep -Seconds ([int][Math]::Max(1, [Math]::Min(900, $rem)))
        if ((Get-Date) -lt $until) { _wlog "cap: still waiting - resume at $($until.ToString('HH:mm'))" }
    }
    _wlog "cap: reset reached - resuming"
    return $true
}

# --- run lock ---------------------------------------------------------------------------------
# WHY. Once a job can wait for a reset, two scheduled jobs could in principle overlap on the same
# runs\<date>\ tree (a 22:00 half still running at 03:15; a long 03:15 publish still going at
# 08:20). The deadlines above make that unlikely; this lock makes it impossible. Each job writes
# its PID to logs\run.lock; a later job whose start finds a LIVE owner waits (up to its own
# deadline) for that owner to exit, then proceeds - the earlier job's results are on disk and the
# resume plan skips them. A dead owner (crash, Task Scheduler kill) is a stale lock and is taken
# over at once, so this can never wedge the schedule.
function Wait-RunLock {
    param(
        [Parameter(Mandatory)][string]$Proj,
        [Parameter(Mandatory)][string]$Log,
        [string]$Job = 'job',
        $Deadline = $null
    )
    function _llog($m) { "$(Get-Date -Format 'HH:mm:ss')  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
    $lock = Join-Path $Proj 'logs\run.lock'
    $announced = $false
    while ($true) {
        $owner = $null
        if (Test-Path $lock) {
            try {
                $info = (Get-Content $lock -Raw | ConvertFrom-Json)
                $p = Get-Process -Id ([int]$info.pid) -ErrorAction SilentlyContinue
                if ($p -and $p.ProcessName -like 'powershell*') { $owner = $info }
            } catch { $owner = $null }
        }
        if (-not $owner) { break }
        if ($Deadline -and (Get-Date) -gt $Deadline) {
            _llog "lock: '$($owner.job)' (pid $($owner.pid), since $($owner.since)) is STILL running past this job's deadline - aborting rather than overlapping it"
            return $false
        }
        if (-not $announced) {
            _llog "lock: '$($owner.job)' (pid $($owner.pid), since $($owner.since)) is still running - waiting for it to finish"
            $announced = $true
        }
        Start-Sleep -Seconds 30
    }
    if ($announced) { _llog "lock: previous job finished - proceeding" }
    @{ pid = $PID; job = $Job; since = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') } | ConvertTo-Json -Compress | Set-Content -Path $lock -Encoding ASCII
    return $true
}

function Remove-RunLock {
    param([Parameter(Mandatory)][string]$Proj)
    $lock = Join-Path $Proj 'logs\run.lock'
    try {
        if (Test-Path $lock) {
            $info = (Get-Content $lock -Raw | ConvertFrom-Json)
            if ([int]$info.pid -eq $PID) { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
        }
    } catch { }
}

function Get-Phase1Prompt {
    param(
        [Parameter(Mandatory)][string]$Today,
        [Parameter(Mandatory)][string]$Novelty,
        # Extra sentence(s) appended for the completion pass (context about why it is running).
        [string]$Preamble = '',
        # Restrict this run to these prompt ids (the midnight half of a split batch). Empty = all.
        [string[]]$Only = @(),
        # Suppress the run-analysis step - a partial run must not overwrite the day's analysis.
        [switch]$SkipAnalysis,
        # The caller already ran init + resume --prune once for the whole batch (chunked runs) - so
        # this session must NOT re-run them (a per-chunk --prune could disturb sibling chunks).
        [switch]$SkipInit
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

    if ($SkipInit) {
        $startLine = @"
Use --date $Today. The batch is ALREADY initialised and pruned for the whole day - do NOT run
``orchestrator.py init`` or ``resume --prune``. Run python orchestrator.py resume --date $Today
(read-only, no --prune) to see each in-scope prompt's resume_stage; then follow the plan and the
CLAUDE.md failure rules (validate every JSON artifact, one repair attempt, mark failures/skips).
"@
    } else {
        $startLine = @"
Use --date $Today. Start with: python orchestrator.py init --date $Today --novelty $Novelty ; then
python orchestrator.py resume --date $Today --prune ; then follow the plan and the CLAUDE.md
failure rules (validate every JSON artifact, one repair attempt, mark failures/skips, continue the
batch).
"@
    }

    $body = @"
$scope
$startLine

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

Handle synthesis-family prompts LAST (after every normal prompt is approved), each with its
writer-role agent then the Reviewer: kind "synthesis" (e.g. throughline) uses the Writer from the
day's APPROVED briefings; kind "forecast" (e.g. forward-curve) uses the Forecaster from the day's
APPROVED briefings PLUS the last 5 days of every topic's docs/transcripts/<id>-*.txt and its own
prior docs/transcripts/forward-curve-*.txt. Do NOT publish, do NOT run publish_feed.py, and do NOT git commit or push
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


# Run phase 1 as SEVERAL small sessions instead of one, to bound the parent session's context
# accumulation (the "orchestration" token cost) and keep the batch inside one usage-cap window.
# Reads the run state, chunks the UNFINISHED normal prompts by $ChunkSize (fresh `claude -p` per
# chunk), then runs any unfinished synthesis prompt LAST in its own session. The caller must have
# already run `orchestrator.py init` + `resume --prune` once, so chunk prompts use -SkipInit.
# A large $ChunkSize collapses this back to a single session (the pre-segmentation behavior).
#
# USAGE CAP: every session's output is checked for the cap line. The first cap in a job waits for
# the reset (bounded by -Deadline, see Wait-ForReset), re-primes the resume plan with
# `resume --prune` (safe here: chunks run one after another, and prune only touches artifacts of
# UNFINISHED prompts), and re-runs the same chunk once. A cap that cannot be waited out, or a
# second cap in the same job, stops the loop - the remaining chunks would only be refused too - and
# sets $script:CapBlocked so the caller skips its own follow-up sessions.
function Invoke-Phase1Chunked {
    param(
        [Parameter(Mandatory)][string]$Claude,
        [Parameter(Mandatory)][string]$Conda,
        [Parameter(Mandatory)][string]$Today,
        [Parameter(Mandatory)][string]$Novelty,
        [Parameter(Mandatory)][string]$Log,
        [string]$Model = 'claude-sonnet-5',
        [string]$Fallback = 'claude-opus-4-8',
        # Reasoning effort for the headless sessions. Pinned explicitly for the same reason
        # as -Model: settings.json's effortLevel is user-global, so an interactive /effort
        # would otherwise silently change what the unattended run costs. Subagents inherit it.
        [ValidateSet('low','medium','high','xhigh','max')][string]$Effort = 'medium',
        [int]$ChunkSize = 3,
        [string[]]$Only = @(),
        [string]$Preamble = '',
        # Latest moment a cap wait may end (from Resolve-Deadline). $null = no bound.
        $Deadline = $null,
        [string]$Job = 'briefing run'
    )
    function _clog($m) { "$(Get-Date -Format 'HH:mm:ss')  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
    if ($ChunkSize -lt 1) { $ChunkSize = 1 }
    if ($null -eq $script:CapBlocked) { $script:CapBlocked = $false }
    if ($null -eq $script:CapWaited)  { $script:CapWaited  = $false }
    if ($script:CapBlocked) { _clog "chunked: skipped - a usage cap already blocked this job"; return }

    # Run one session for $ids; on a cap, wait (once per job) and re-run it. Returns $true to
    # carry on with the next session, $false to stop the loop.
    function _session([string[]]$ids, [string]$label) {
        _clog "chunked: $label [$($ids -join ', ')]"
        $p = Get-Phase1Prompt -Today $Today -Novelty $Novelty -Only $ids -SkipAnalysis -SkipInit -Preamble $Preamble
        $out = Invoke-ClaudeSession -Claude $Claude -Prompt $p -Log $Log -Model $Model -Fallback $Fallback -Effort $Effort
        $cap = Get-LimitReset $out
        if (-not $cap) { return $true }
        if ($script:CapWaited) {
            _clog "cap: $($cap.Text) - hit again after already waiting once this job; leaving the rest to the next scheduled job"
            $script:CapBlocked = $true
            return $false
        }
        $script:CapWaited = $true
        $left = ($ids -join ', ')
        if (-not (Wait-ForReset -Cap $cap -Deadline $Deadline -Log $Log -Conda $Conda -Job $Job -Left $left)) { return $false }
        # The refused/truncated session may have left a half-written stage: re-derive the resume
        # points and drop anything superseded before the retry reads the tree.
        & $Conda run -n Spotify --no-capture-output python orchestrator.py resume --date $Today --prune *>> $Log
        _clog "chunked: retry after cap reset - $label [$($ids -join ', ')]"
        $out = Invoke-ClaudeSession -Claude $Claude -Prompt $p -Log $Log -Model $Model -Fallback $Fallback -Effort $Effort
        $cap = Get-LimitReset $out
        if ($cap) {
            _clog "cap: $($cap.Text) - refused again right after the reset; leaving the rest to the next scheduled job"
            $script:CapBlocked = $true
            return $false
        }
        return $true
    }

    $raw = & $Conda run -n Spotify --no-capture-output python orchestrator.py status --date $Today --json 2>> $Log
    $st = $null
    try { $st = ($raw -join "`n") | ConvertFrom-Json } catch { $st = $null }
    if (-not $st) {
        # No readable state (e.g. init never ran): one full session that DOES init, as a safe fallback.
        _clog "chunked: no run state - running one full session that initialises the batch"
        $p = Get-Phase1Prompt -Today $Today -Novelty $Novelty -Only $Only -SkipAnalysis -Preamble $Preamble
        $out = Invoke-ClaudeSession -Claude $Claude -Prompt $p -Log $Log -Model $Model -Fallback $Fallback -Effort $Effort
        $cap = Get-LimitReset $out
        if ($cap -and -not $script:CapWaited) {
            $script:CapWaited = $true
            if (Wait-ForReset -Cap $cap -Deadline $Deadline -Log $Log -Conda $Conda -Job $Job -Left 'whole batch') {
                _clog "chunked: retry after cap reset - full session"
                $out = Invoke-ClaudeSession -Claude $Claude -Prompt $p -Log $Log -Model $Model -Fallback $Fallback -Effort $Effort
                if (Get-LimitReset $out) { $script:CapBlocked = $true }
            }
        }
        return
    }

    $scope = @($st.prompts)
    if ($Only.Count) { $scope = @($scope | Where-Object { $Only -contains $_.id }) }
    $unfinished = @($scope | Where-Object { $_.status -eq 'pending' -or $_.status -eq 'failed' })
    if (-not $unfinished.Count) { _clog "chunked: nothing unfinished in scope - no sessions needed"; return }

    # The synthesis family (synthesis + forecast, e.g. throughline + forward-curve) runs LAST.
    $synthKinds = @('synthesis', 'forecast')
    $normals = @($unfinished | Where-Object { $synthKinds -notcontains $_.kind } | ForEach-Object { $_.id })
    $synth   = @($unfinished | Where-Object { $synthKinds -contains $_.kind } | ForEach-Object { $_.id })

    $chunks = @()
    for ($i = 0; $i -lt $normals.Count; $i += $ChunkSize) {
        $end = [Math]::Min($i + $ChunkSize - 1, $normals.Count - 1)
        $chunks += , @($normals[$i..$end])
    }
    $n = $chunks.Count + $(if ($synth.Count) { 1 } else { 0 })
    _clog "chunked: $($unfinished.Count) unfinished -> $n session(s) (ChunkSize=$ChunkSize, $Model, effort=$Effort)"

    $ci = 0
    foreach ($chunk in $chunks) {
        $ci++
        if (-not (_session $chunk "normal session $ci/$($chunks.Count)")) { return }
    }
    if ($synth.Count) {
        # Synthesis runs LAST - it reads the day's already-approved briefings from disk.
        [void](_session $synth 'synthesis session')
    }
}
