# Cautious Optimism Briefings

A do-it-yourself **daily podcast of expert-level audio briefings**. You keep a library of
standing "prompts" (each one is a topic + detailed instructions), and on command an AI
assistant (Claude Code) researches each topic fresh, writes a spoken-word script, turns it
into narrated audio, and publishes it as a new episode of a real podcast that anyone can
find and follow on Spotify (and Apple Podcasts, and any other podcast app).

This README explains, in plain English, **how the whole thing works** and **exactly what
you'd have to do to run your own version with your own Spotify account**. It assumes you
can install software, edit a text file, and run a command in a terminal, but it does *not*
assume you already know anything about podcasts, RSS, or text-to-speech.

---

## 1. The big idea (and the one trick that makes it work)

Most people think publishing a podcast to Spotify requires uploading audio files to
Spotify. It doesn't. **Spotify — and every other podcast app — just reads an RSS feed.**

An RSS feed is a single XML file sitting at a public web address. It's basically a list:
"here is my show's name, here's the cover art, and here are the episodes — each with a
title, a description, and a link to an MP3 file." When you "submit a podcast to Spotify,"
all you're really doing is handing Spotify that one URL. From then on, Spotify checks that
URL on its own schedule, and whenever it sees a new episode listed, it pulls it in
automatically.

So this project never talks to Spotify's servers directly. Instead it:

1. **Generates the audio** (MP3 files) from AI-written scripts using text-to-speech.
2. **Hosts those MP3s and the RSS feed on the public internet — for free — using GitHub
   Pages** (GitHub will serve any files you put in a folder of a public repository at a
   public web address).
3. **"Publishing" is literally just `git push`.** You push new files to GitHub, GitHub
   Pages serves them, and Spotify notices the updated feed on its next refresh (usually
   minutes to a few hours later).

That's the entire architecture. There's no server to run, no hosting bill, no Spotify API
key. The "hard" parts — the research and the writing — are done by Claude Code, an AI
coding assistant that can search the web, write files, and run commands.

> **Note on the name "Spotify" everywhere.** The project folder is called `Spotify` and
> there's some leftover code from an older approach that *did* upload to Spotify privately.
> Ignore that. The live system is the RSS-feed-over-GitHub-Pages approach described above.
> See the "Legacy bits you can ignore" section near the end.

---

## 2. The editorial concept

The show is written for a **sophisticated listener** — someone fluent in economics,
capital markets, technology, AI, and digital assets. The whole editorial standard (which
lives in `CLAUDE.md`, the instruction file the AI reads) boils down to: lead with what's
genuinely **new or non-consensus**, assume the listener is an expert (no 101-level
explanations), be **quantitative and specific**, and give **analysis, not just reporting**.

Each episode is a single narrator speaking conversationally — no headings, no bullet
points, no "[pause]" stage directions in the script. It opens with a one-line greeting and
the date, delivers the content, and closes with a one-line sign-off. Length is set per
prompt (e.g. "1200 to 1500 words"); roughly 150 words equals one spoken minute.

You can point this at any subject matter you like — the topics are entirely defined by the
prompts you write, which brings us to how the pieces fit together.

---

## 3. How the pieces fit together

Here's the flow of a single day, and the files involved.

```
   prompts.json  ──────────────►  Claude Code (research + writing)
   (your topics)                        │
                                        ▼
                            briefings/<id>.txt   (the written scripts)
                                        │
                       publish_feed.py  │  (the deterministic publisher)
                                        ▼
              edge-tts turns each script into an MP3
                                        │
                                        ▼
              feed.py copies the MP3 into docs/audio/,
              records it in feed_state.json, writes a
              transcript into docs/transcripts/, and
              rebuilds docs/feed.xml
                                        │
                              git commit && git push
                                        ▼
              GitHub Pages serves docs/ at a public URL
                                        │
                                        ▼
              Spotify re-reads feed.xml on its schedule
                     → new episode appears in the app
```

### The key files

| File / folder | What it does |
|---|---|
| **`prompts.json`** | Your **prompt library** — the list of topics. Each prompt has an `id`, a display `name`, the full `prompt` text (the research instructions), and an `enabled` flag. This is the one file you'll edit most. |
| **`main.py`** | A small desktop window (built with Tkinter) that lets you add / edit / enable / disable / delete prompts without hand-editing JSON. It has **no AI in it and needs no API key** — it's just a friendly editor for `prompts.json`. |
| **`library.py`** | The helper code that reads and writes `prompts.json`. Used by `main.py` and the publisher. |
| **`briefings/<id>.txt`** | The written script for each prompt, produced fresh each run. `<id>` matches the prompt's `id`. |
| **`episode.py`** | The **text-to-speech** engine. Its `synthesize()` function turns a script file into an MP3 using Microsoft's free `edge-tts` voices. (This file also contains retired upload code — ignore that part.) |
| **`feed.py`** | The **podcast machinery**. `add_episode()` files a finished MP3 into `docs/audio/`, records its details in `feed_state.json`, and writes a transcript. `build_feed()` regenerates `docs/feed.xml` from that archive. |
| **`feed_state.json`** | The **source of truth**: an accumulating list of every episode ever published. The RSS feed is rebuilt from this, so it's the real archive. |
| **`publish_feed.py`** | The **daily publisher** that ties it together: for each enabled prompt it runs TTS → `add_episode` → then `build_feed` → then `git commit` and `git push`. |
| **`notify.py`** | Optional. Sends a confirmation email after a successful publish (via Gmail). Currently disabled by default — see section 9. |
| **`config.py`** | **All the shared settings in one place**: the show title, author, description, category, the owner email Spotify uses to verify you, and — importantly — `FEED_BASE_URL`, the public web address of your feed. **This is the main file you'll change to make the project your own.** |
| **`docs/`** | The **public website**. Everything in here is served by GitHub Pages. Contains `feed.xml` (the RSS feed), `cover.jpg` (the 1500×1500 cover art), `audio/` (the episode MP3s), `transcripts/` (HTML + text transcripts), plus `index.html` and a `.nojekyll` marker file. |
| **`tools/`** | Utility scripts: `make_cover.py` regenerates the cover image; `seed_feed.py` was a one-off backfill; `daily_run.ps1` is the unattended 5 AM automation (Windows). |
| **`CLAUDE.md`** | The **instruction sheet Claude Code reads automatically**. It contains the editorial standard, the preferred research sources, and the step-by-step publishing procedure. This is how the AI "knows" how your show should sound and how to publish it. |
| **`tests/`** | A test suite (plain Python `unittest`). |

---

## 4. How you actually use it, day to day

Once it's set up (section 6), a normal day looks like this:

1. **(Optional) Edit your topics.** Run `python main.py`, and in the window add or tweak
   prompts. Or just edit `prompts.json` directly. Each enabled prompt becomes one episode.

2. **Ask the AI to make the briefings.** In Claude Code (the AI assistant, running in this
   folder), you say **"make my daily briefing."** Claude reads `CLAUDE.md` and
   `prompts.json`, and for **each enabled prompt** it:
   - searches the web for the freshest relevant information,
   - writes a script following the editorial standard, and
   - saves it to `briefings/<id>.txt`.

3. **It publishes automatically.** After the scripts are written, Claude runs the
   publisher, which makes the audio, updates the feed, and pushes to GitHub:

   ```bash
   conda run -n Spotify --no-capture-output python publish_feed.py --summaries <summaries.json>
   ```

   (`--summaries` is an optional JSON file mapping each prompt id to a one-line episode
   description; without it, the publisher auto-derives a description from the script.)

4. **Spotify catches up on its own.** GitHub Pages serves the new files immediately, and
   Spotify re-reads your feed on its next refresh cycle. The new episodes show up in the
   app a little while later — you don't do anything else.

Each publish creates a **new, permanent episode** with a unique id of the form
`<prompt_id>-<YYYY-MM-DD>` (the "GUID") — for example, the `capital-markets-radar` prompt
published on July 9, 2026 becomes the episode `capital-markets-radar-2026-07-09`. So every
day adds to a browsable back-catalogue, and followers get normal new-episode notifications.
Running the same prompt twice on the *same* date just overwrites that day's episode (it's
idempotent); a new date adds a new one.

(The current library ships with four prompts, whose ids are `frontier-ai-labs`,
`capital-markets-radar`, `digital-money`, and `strategic-power` — so, for instance, that
first prompt's script lives at `briefings/frontier-ai-labs.txt`. Wherever this README says
`<id>`, substitute one of those.)

> There's also a **fully unattended** mode: `tools/daily_run.ps1` is a Windows PowerShell
> script wired to Task Scheduler to run at 5 AM. It runs Claude headlessly to write the
> briefings, then runs `publish_feed.py`, with everything logged to `logs/`. This is
> optional — you can run everything by hand.

---

## 5. What you need before you start

- **A GitHub account** and the `git` command-line tool. This is where your feed and audio
  are hosted, for free, via GitHub Pages.
- **Python 3.11+.** The project was developed with a **conda** environment named `Spotify`
  (via Anaconda/Miniconda), but any Python environment works — conda is just how the
  author manages it.
- **Claude Code** — Anthropic's AI coding assistant (the thing that does the research and
  writing). You run it in a terminal inside the project folder. This is what "make my daily
  briefing" is said to. (You could, in principle, write the scripts yourself by hand and
  skip the AI — the publishing half is just ordinary Python.)
- **A Spotify account** and access to **Spotify for Creators** (formerly "Spotify for
  Podcasters"), which is free. This is where you submit your feed URL once, at the start.
- **A little cover art** — a square image at least 1400×1400 (the project uses 1500×1500).
  There's a script to generate a simple one for you.
- The Python packages the pipeline needs: **`edge-tts`** (free Microsoft text-to-speech),
  **`aiohttp`**, **`Pillow`** (image handling, for the cover), and **`mutagen`** (reads
  MP3 duration/size for the feed).

There is **no paid hosting and no Spotify API key** involved. The only account that costs
anything is Claude Code (the AI), and even that's optional if you write scripts yourself.

---

## 6. Setting up your own copy, step by step

Here's the full path from zero to "my own show is live on Spotify." Take it slowly; each
step is small.

### Step 1 — Get the code into your own GitHub repository

Fork or copy this project into **your own** public GitHub repo. It has to be public for
the free GitHub Pages hosting to serve your feed and audio to Spotify.

Let's say your GitHub username is `yourname` and you call the repo `Spotify`. Then your
files under `docs/` will end up served at:

```
https://yourname.github.io/Spotify/
```

### Step 2 — Turn on GitHub Pages

In your repo on github.com: **Settings → Pages**. Set the **Source** to
**"Deploy from a branch"**, choose the **`main`** branch and the **`/docs`** folder, and
save. After a minute, anything in `docs/` is live on the web at the URL above. (The
`docs/.nojekyll` file is already there; it tells GitHub Pages to serve your files as-is
without trying to process them as a Jekyll blog.)

### Step 3 — Make the project "yours" in `config.py`

Open `config.py` and change the public-podcast constants to your own show. The important
ones:

- **`FEED_BASE_URL`** — set this to *your* Pages URL, e.g.
  `"https://yourname.github.io/Spotify"`. **This is the single most important change** —
  every audio link and transcript link in your feed is built from it, so if it's wrong,
  Spotify won't be able to fetch your episodes.
- **`PODCAST_TITLE`**, **`PODCAST_AUTHOR`**, **`PODCAST_OWNER_NAME`**, **`PODCAST_DESCRIPTION`** —
  your show's name, author, and blurb.
- **`PODCAST_EMAIL`** — **use an email you control.** Spotify verifies that you own the
  show by emailing a code to the address listed in the feed. If this isn't yours, you can't
  claim the show.
- **`PODCAST_CATEGORY`** / **`PODCAST_SUBCATEGORY`** — an Apple Podcasts category pair
  (e.g. Business / Investing). Spotify requires a valid category.
- **`VOICE`** — the text-to-speech voice, default `en-US-AndrewNeural`. You can list all
  available free voices by running `edge-tts --list-voices` after installing `edge-tts`.

### Step 4 — Create your Python environment and install the dependencies

Using conda (matching how the project is set up) — this creates an environment named
`Spotify` and installs everything in one step:

```bash
conda env create -f environment.yml
conda activate Spotify
```

Or, in any existing Python environment, just use pip:

```bash
pip install -r requirements.txt
```

Both files pull in the same four packages (`edge-tts`, `aiohttp`, `Pillow`, `mutagen`)
plus their own dependencies — that's all you need. `git` must also be installed and on
your PATH, since publishing calls it.

### Step 5 — Make your cover art

Podcast apps require square cover art of at least 1400×1400. The repo ships with one at
`docs/cover.jpg`. To generate your own simple version, edit and run:

```bash
python tools/make_cover.py
```

Or just drop your own 1500×1500 JPG in at `docs/cover.jpg`. (If you rename it, update
`COVER_FILE` in `config.py`.)

### Step 6 — Write your prompts

Run the prompt manager window:

```bash
python main.py
```

Add one prompt per topic you want as a daily episode. Give each a short **name** and paste
in a detailed **prompt** — the instruction Claude will follow to research and write that
briefing. Look at the existing prompts in `prompts.json` for the style: they're long and
specific about audience, what to cover, what sources to prioritize, how quantitative to be,
and the required format (greeting + date, no headers/bullets, one-line sign-off, target
word count). The more precise the prompt, the better the episode. Enable the ones you want
in the daily run.

### Step 7 — Do a test publish

You have two options for generating the scripts:

- **With the AI:** open Claude Code in the project folder and say **"make my daily
  briefing."** It writes each `briefings/<id>.txt` and then publishes.
- **By hand (to test the plumbing):** create a `briefings/<id>.txt` file yourself for one
  enabled prompt (any text will do for a test), then run the publisher:

  ```bash
  conda run -n Spotify --no-capture-output python publish_feed.py
  ```

Useful flags while testing:

- `--no-push` — build everything locally (audio, feed) **without** doing the git push, so
  you can inspect the results first.
- `--date 2026-01-31` — override the episode date.
- `--require-fresh` — only publish briefings whose script file was actually written today
  (a safety net so the unattended run never republishes a stale, unchanged script).

After a real run (without `--no-push`), check that `docs/feed.xml` exists and looks right,
and that your feed is reachable in a browser at `https://yourname.github.io/Spotify/feed.xml`.

### Step 8 — Submit your feed to Spotify (one time only)

Go to **Spotify for Creators** (podcasters.spotify.com), and choose to **add an existing
podcast / add via RSS feed**. Paste your feed URL:

```
https://yourname.github.io/Spotify/feed.xml
```

Spotify will send a verification code to the `PODCAST_EMAIL` in your feed to confirm you
own the show. Enter it, fill in any remaining show details, and submit. Within a short
while your show is live and searchable on Spotify.

**You only do this once.** From then on, every `git push` of a new episode is picked up
automatically — you never touch Spotify's site again.

(The same feed URL also works for Apple Podcasts and any other podcast directory, if you
want to list it in more places. Fun detail: Apple and "Podcasting 2.0" apps read the
per-episode transcripts this project generates; Spotify ignores RSS transcripts, which is
why each episode description also includes a plain "read the full transcript" link to the
hosted page.)

---

## 7. What "publishing" actually does, under the hood

When `publish_feed.py` runs, for each enabled prompt it:

1. Reads the script at `briefings/<id>.txt`.
2. Calls `episode.synthesize()` to turn that script into an MP3 with `edge-tts`.
3. Calls `feed.add_episode()`, which:
   - copies the MP3 to `docs/audio/<id>-<date>.mp3`,
   - reads its byte size and duration (with `mutagen`),
   - records all of that — plus a timestamp and transcript filenames — in
     `feed_state.json` (the archive),
   - and writes the verbatim transcript to `docs/transcripts/<id>-<date>.txt` and `.html`.
4. After all prompts are processed, calls `feed.build_feed()`, which regenerates
   `docs/feed.xml` from `feed_state.json` — newest episode first, with all the iTunes tags,
   real publish dates, and transcript links.
5. Runs `git add docs feed_state.json`, commits, and pushes to `origin main`.

If one prompt fails (say, a network hiccup during TTS), it's logged and skipped, and the
batch continues — the feed still rebuilds and pushes with the episodes that succeeded.

**Why a separate `feed_state.json` archive?** Because the feed is *generated*, not
hand-edited. The archive is the durable record of every episode; the `feed.xml` is just a
rendering of it. That's what makes the whole thing safe to re-run and easy to reason about.

### Re-publishing a single episode by hand

If you ever need to redo just one episode:

```python
import feed
from episode import synthesize
# (example: the "capital-markets-radar" prompt for a given date)
mp3 = synthesize("briefings/capital-markets-radar.txt")
feed.add_episode("capital-markets-radar", "Capital Markets Cross-Asset Radar",
                 "<summary>", mp3, "2026-01-31")
feed.build_feed()
# then, in the terminal:
#   git add docs feed_state.json && git commit -m "Republish capital-markets-radar" && git push origin main
```

---

## 8. A note on text-to-speech reliability

The free Microsoft `edge-tts` endpoint occasionally drops the connection partway through a
long piece of text, which would otherwise leave you with a truncated MP3. To handle this,
`episode._synthesize` doesn't send the whole script at once — it synthesizes **one short
request per paragraph, with retries**, then stitches the pieces together. A dropped
connection only forces a re-try of one paragraph, not the whole episode. So when you run a
batch, expect to see some retry noise in the log; a run that finishes is what matters, not
a clean-looking log.

---

## 9. The optional confirmation email

The project can email you a summary after each successful publish (subject line with the
date and episode count; body listing each briefing with links to its transcript and audio).
This is handled by `notify.py`, which sends through Gmail using a **Google App Password**
(not your normal password) supplied via environment variables — no secret is ever stored in
the repo:

```
BRIEFING_SMTP_USER   the Gmail address it sends from
BRIEFING_SMTP_PASS   a Google App Password (create one at myaccount.google.com/apppasswords)
BRIEFING_NOTIFY_TO   (optional) where to send it; defaults to the address in config.py
```

On Windows you'd set these once as user environment variables (`setx BRIEFING_SMTP_USER ...`).
If the variables aren't set, the email is simply **skipped with a warning** — it never
fails a publish that already went out.

**As shipped, this email is disabled** (the send call in `publish_feed.py` and the
`--email` flag in `daily_run.ps1` are commented out) because the author hadn't set up the
credentials. To turn it on: create the App Password, set the environment variables, and
un-comment the marked block in `publish_feed.py` (and add `--email` back in `daily_run.ps1`
if you use the scheduled run).

---

## 10. Running it automatically at 5 AM (optional, Windows)

`tools/daily_run.ps1` is the unattended version. It runs in two deliberately separate
phases so an AI hiccup can never accidentally skip publishing:

1. **Phase 1 — research + write only.** It launches Claude Code headlessly with
   instructions to research every enabled prompt and write the `briefings/<id>.txt` files —
   and *only* that (no publishing, no git).
2. **Phase 2 — deterministic publish.** It runs `publish_feed.py --require-fresh`, which
   makes the audio, rebuilds the feed, and pushes — but thanks to `--require-fresh`, only
   for briefings that were actually rewritten today.

Everything is logged to `logs/daily-<date>.log`. You wire it into **Windows Task
Scheduler** to run each morning. There's also a **novelty policy**: on scheduled runs, each
briefing is told to read yesterday's script for that topic first and *not* repeat it unless
there's genuinely new news. Pass `-RepeatOK` for manual testing to relax that. (When you
run interactively via "make my daily briefing," it's relaxed by default — that's treated as
testing.)

---

## 11. Running the tests

There's a standard-library `unittest` suite:

```bash
python -m unittest discover -s tests -t .
```

No special setup needed — it doesn't touch the network or your real feed.

---

## 12. Legacy bits you can safely ignore

Because this project evolved, a few files and settings are leftovers from an **older
approach** that published to a *private* Spotify show via a `save-to-spotify` command-line
tool. That path is retired — private-show episodes weren't shareable, which is exactly the
problem the public-RSS approach solves. You can ignore:

- The `SHOW_ID`, `SHOW_NAME`, and `S2S` constants in `config.py`, and the `show_id` and
  per-prompt `last_episode_uri` / `last_published` fields in `prompts.json` (the public feed
  tracks episodes in `feed_state.json` instead).
- The `publish_replacing` / `upload_episode` / `delete_episode` helpers in `episode.py`
  (only its `synthesize` TTS function is still used).
- The root-level `briefing.txt` / `briefing.mp3` and various `*.txt` planning files —
  scratch from earlier iterations.
- The `main.py` docstring still mentions the old "publish + delete previous version"
  behavior; the live behavior is the archive model described in this README.

None of these affect the working pipeline. If you're setting up fresh, just leave them
alone or delete the ones that bother you.

---

## 13. Quick reference — the commands you'll actually type

```bash
# edit your topics in a window
python main.py

# (in Claude Code) research + write + publish everything:
#   "make my daily briefing"

# publish by hand after scripts exist:
conda run -n Spotify --no-capture-output python publish_feed.py

# build locally without pushing (for testing):
python publish_feed.py --no-push

# only publish today's freshly written scripts:
python publish_feed.py --require-fresh

# regenerate the cover art:
python tools/make_cover.py

# run the tests:
python -m unittest discover -s tests -t .
```

And the two web addresses that matter, once you've made it yours:

- **Your show page / site:** `https://yourname.github.io/Spotify/`
- **Your feed (what you give Spotify once):** `https://yourname.github.io/Spotify/feed.xml`

That's the whole system. Write good prompts, say "make my daily briefing," and a fresh,
researched, narrated episode goes live on your podcast every day.
