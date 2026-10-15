"""GitHub credentials for the unattended publish — never interactive, never hangs.

Why this exists: the publish used to take its token from Git Credential Manager (GCM). The scheduled
tasks run as S4U (no desktop), and after the 2026-10-13 Windows-update restart GCM had no usable
GitHub credential, so it waited forever on a sign-in prompt nobody could see. ``git credential fill``
had a 30s timeout, but ``subprocess.run``'s timeout kills only the git shim — the real git.exe and
GCM grandchildren kept the pipes open — so publish_feed.py hung ~19h holding run.lock and blocked the
next three scheduled jobs (no episodes 2026-10-14 or 10-15).

Two layers, so a restart or logout can't silently break publishing again:

1. **A dedicated token file** (``config.GITHUB_TOKEN_FILE``): a fine-grained PAT scoped to this repo
   only, Contents read/write. It's a plain file in the user profile, so an S4U task can read it
   whether or not anyone is logged in (Credential Manager is DPAPI-backed and can't promise that).
   Preferred whenever present.
2. **Fallback: GCM, non-interactively** — ``GCM_INTERACTIVE=never`` / ``GIT_TERMINAL_PROMPT=0`` make
   a missing credential fail in seconds, and the timeout kills the whole process tree.

``git_env()`` gives git subprocesses (``git push``) the same treatment: the token travels as an
``http.extraheader`` via ``GIT_CONFIG_*`` env vars (never on a command line, never logged), plus the
no-prompt switches. The token is never written anywhere by this module.
"""
import base64
import os
import subprocess
import urllib.error
import urllib.request

import config

_NO_PROMPT = {"GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}


def _file_token() -> str | None:
    try:
        with open(config.GITHUB_TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
    else:
        os.kill(pid, 9)


def run(cmd: list[str], timeout: float, input: str | None = None,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """``subprocess.run`` whose timeout actually works on Windows: on expiry the whole process tree
    is killed (not just the direct child), so grandchildren can't hold the pipes open. Re-raises
    ``subprocess.TimeoutExpired`` after the kill."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env, cwd=config.HERE)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def token() -> str:
    """The GitHub token: the token file if present, else GCM (non-interactive). Raises RuntimeError
    with a one-line, actionable reason when neither yields one — within ~30s, never a hang."""
    tok = _file_token()
    if tok:
        return tok
    try:
        r = run(["git", "credential", "fill"], timeout=30,
                input="protocol=https\nhost=github.com\n\n", env={**os.environ, **_NO_PROMPT})
    except subprocess.TimeoutExpired:
        raise RuntimeError("Git Credential Manager timed out (process tree killed)") from None
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError(f"no GitHub credential: no token file at {config.GITHUB_TOKEN_FILE} and Git "
                       "Credential Manager has none (it needs an interactive sign-in)")


def git_env() -> dict:
    """Environment for git subprocesses that talk to GitHub: never prompt, and authenticate with the
    token file (if present) via an extraheader passed through GIT_CONFIG_* env vars."""
    env = {**os.environ, **_NO_PROMPT}
    tok = _file_token()
    if tok:
        basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        n = int(env.get("GIT_CONFIG_COUNT") or 0)
        env.update({"GIT_CONFIG_COUNT": str(n + 1),
                    f"GIT_CONFIG_KEY_{n}": "http.https://github.com/.extraheader",
                    f"GIT_CONFIG_VALUE_{n}": f"AUTHORIZATION: basic {basic}"})
    return env


def check() -> str | None:
    """Pre-flight before publishing. Returns a one-line reason if GitHub auth is definitely broken
    (no credential at all, or GitHub rejects the token), else None. A network error returns None —
    transient blips are left to the push's own retries rather than aborting the publish."""
    try:
        tok = token()
    except RuntimeError as e:
        return str(e)
    req = urllib.request.Request(f"https://api.github.com/repos/{config.GITHUB_REPO}", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "User-Agent": "cautious-optimism-briefings"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return f"GitHub rejected the token (HTTP {e.code}) — expired, revoked, or under-scoped"
    except OSError:
        pass
    return None
