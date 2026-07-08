# Cautious Optimism Briefings — unattended daily run (Windows Task Scheduler).
#
# Two phases, deliberately separated so publishing can't be skipped by an AI hiccup:
#   1. Headless Claude Code researches + writes briefings/<id>.txt for every enabled prompt.
#   2. publish_feed.py (deterministic) synthesizes audio, updates the RSS feed, and git-pushes.
#      --require-fresh means only briefings actually rewritten today get published (never stale).
#
# Everything is logged to logs\daily-<date>.log. Exit code is non-zero if publishing failed.

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
$conda  = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today  = Get-Date -Format 'yyyy-MM-dd'

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

Log "=== daily run start ($today) ==="

# Phase 1 — research + write only (no publishing, no git) --------------------
$prompt = @'
Research and write today's daily briefings. For EVERY enabled prompt in prompts.json,
research it using fresh web search following the editorial standard and preferred sources
in CLAUDE.md, then write the script to briefings/<id>.txt (overwrite the old one). Honor any
word length stated in the prompt text. Do NOT publish, do NOT run publish_feed.py, and do NOT
git commit or push — ONLY write the briefings/<id>.txt files. When finished, list the files you wrote.
'@

Log "phase 1: headless Claude research + write"
& $claude -p $prompt --dangerously-skip-permissions *>> $log
Log "phase 1 exit code: $LASTEXITCODE"

# Phase 2 — deterministic publish (TTS -> feed -> git push) -------------------
Log "phase 2: publish_feed.py --require-fresh"
& $conda run -n Spotify --no-capture-output python publish_feed.py --date $today --require-fresh *>> $log
$pubExit = $LASTEXITCODE
Log "phase 2 exit code: $pubExit"

Log "=== daily run done ==="
exit $pubExit
