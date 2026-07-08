# Cautious Optimism Briefings — unattended daily run (Windows Task Scheduler).
#
# Two phases, deliberately separated so publishing can't be skipped by an AI hiccup:
#   1. Headless Claude Code researches + writes briefings/<id>.txt for every enabled prompt.
#   2. publish_feed.py (deterministic) synthesizes audio, updates the RSS feed, and git-pushes.
#      --require-fresh means only briefings actually rewritten today get published (never stale).
#
# Novelty policy: by default (scheduled runs) each briefing must avoid repeating the prior
# day's topics/themes unless there's genuinely new news. Pass -RepeatOK for manual testing to
# relax that constraint and just write fresh.
#
# Everything is logged to logs\daily-<date>.log. Exit code is non-zero if publishing failed.
param([switch]$RepeatOK)

$ErrorActionPreference = 'Continue'
$proj   = 'C:\Users\wamfo\ClaudeDev\Spotify'
$claude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
$conda  = Join-Path $env:USERPROFILE 'anaconda3\Scripts\conda.exe'
$today  = Get-Date -Format 'yyyy-MM-dd'

Set-Location $proj
New-Item -ItemType Directory -Force -Path (Join-Path $proj 'logs') | Out-Null
$log = Join-Path $proj "logs\daily-$today.log"

function Log($msg) { "$(Get-Date -Format 'HH:mm:ss')  $msg" | Tee-Object -FilePath $log -Append }

$mode = if ($RepeatOK) { 'relaxed (-RepeatOK)' } else { 'strict novelty' }
Log "=== daily run start ($today) — $mode ==="

# Phase 1 — research + write only (no publishing, no git) --------------------
# The novelty clause is included by default and dropped when -RepeatOK is set.
$novelty = @'

Before writing each briefing, FIRST read the existing briefings/<id>.txt — that is your most
recent prior briefing on that topic (from the previous run). Still write a full new briefing for
EVERY enabled prompt, but it MUST NOT repeat the same specific subjects, themes, angles, or framing
as that prior one UNLESS there is a genuinely new development, data point, or piece of news since
then. Lead with what has changed at the margin; where a subject was already covered and nothing new
has happened, cover different developments or angles within the topic rather than restating it. The
goal is fresh content each day, not skipping — always produce a complete briefing per prompt. (On
the first run a topic may have no prior file — that is fine.)
'@
if ($RepeatOK) { $novelty = '' }

$prompt = @"
Research and write today's daily briefings. For EVERY enabled prompt in prompts.json,
research it using fresh web search following the editorial standard and preferred sources
in CLAUDE.md, then write the script to briefings/<id>.txt (overwrite the old one). Honor any
word length stated in the prompt text. Do NOT publish, do NOT run publish_feed.py, and do NOT
git commit or push — ONLY write the briefings/<id>.txt files.$novelty
When finished, list the files you wrote.
"@

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
