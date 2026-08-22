# Shared phase-1 prompt for the briefing pipeline.
#
# Dot-sourced by BOTH tools\daily_run.ps1 (the 5 AM job) and tools\completion_run.ps1 (the 10:05
# pass that finishes whatever the session cap truncated). It lives in one file so the resume
# semantics can never drift between them - the completion pass depends entirely on those semantics
# being obeyed, and a stale copy would silently restart finished prompts.

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
function Invoke-Phase1Chunked {
    param(
        [Parameter(Mandatory)][string]$Claude,
        [Parameter(Mandatory)][string]$Conda,
        [Parameter(Mandatory)][string]$Today,
        [Parameter(Mandatory)][string]$Novelty,
        [Parameter(Mandatory)][string]$Log,
        [string]$Model = 'claude-sonnet-5',
        [string]$Fallback = 'claude-opus-4-8',
        [int]$ChunkSize = 3,
        [string[]]$Only = @(),
        [string]$Preamble = ''
    )
    function _clog($m) { "$(Get-Date -Format 'HH:mm:ss')  $m" | Tee-Object -FilePath $Log -Append | Out-Null }
    if ($ChunkSize -lt 1) { $ChunkSize = 1 }

    $raw = & $Conda run -n Spotify --no-capture-output python orchestrator.py status --date $Today --json 2>> $Log
    $st = $null
    try { $st = ($raw -join "`n") | ConvertFrom-Json } catch { $st = $null }
    if (-not $st) {
        # No readable state (e.g. init never ran): one full session that DOES init, as a safe fallback.
        _clog "chunked: no run state - running one full session that initialises the batch"
        $p = Get-Phase1Prompt -Today $Today -Novelty $Novelty -Only $Only -SkipAnalysis -Preamble $Preamble
        & $Claude -p $p --model $Model --fallback-model $Fallback --dangerously-skip-permissions *>> $Log
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
    _clog "chunked: $($unfinished.Count) unfinished -> $n session(s) (ChunkSize=$ChunkSize, $Model)"

    $ci = 0
    foreach ($chunk in $chunks) {
        $ci++
        _clog "chunked: normal session $ci/$($chunks.Count) [$($chunk -join ', ')]"
        $p = Get-Phase1Prompt -Today $Today -Novelty $Novelty -Only $chunk -SkipAnalysis -SkipInit -Preamble $Preamble
        & $Claude -p $p --model $Model --fallback-model $Fallback --dangerously-skip-permissions *>> $Log
    }
    if ($synth.Count) {
        # Synthesis runs LAST - it reads the day's already-approved briefings from disk.
        _clog "chunked: synthesis session [$($synth -join ', ')]"
        $p = Get-Phase1Prompt -Today $Today -Novelty $Novelty -Only $synth -SkipAnalysis -SkipInit -Preamble $Preamble
        & $Claude -p $p --model $Model --fallback-model $Fallback --dangerously-skip-permissions *>> $Log
    }
}
