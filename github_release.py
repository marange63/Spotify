"""Host episode audio as GitHub Release assets instead of committing it to the repo.

Release assets live OUTSIDE the git object store — they don't count toward repo size and deleting
one truly frees the space — so hosting audio here keeps ``.git`` flat and lets the rolling-retention
prune (feed.prune_old) hold audio at a flat ~10-day footprint. Everything reuses the GitHub token
that ``git push`` already uses (retrieved non-interactively via Git Credential Manager); nothing new
to install or configure, and the token is never stored or logged.

All episode audio lives on one permanent release (``config.GITHUB_RELEASE_TAG``); assets are named
``<guid>.mp3``. Public download URL:
``https://github.com/<repo>/releases/download/<tag>/<guid>.mp3`` — served unauthenticated, supports
range requests (seeking), tolerates the ``?v=`` cache-bust query. (GitHub serves assets as
``application/octet-stream``, but the feed's ``<enclosure type="audio/mpeg">`` and the ``.mp3``
extension are what podcast clients key off.)

HTTP lives in ``_token`` and ``_api`` so callers/tests can isolate it.
"""
import json
import subprocess
import urllib.error
import urllib.request

import config

_API = "https://api.github.com"


def _token() -> str:
    """The GitHub token ``git push`` uses, from Git Credential Manager (non-interactive)."""
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, timeout=30, cwd=config.HERE)
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("no GitHub credential returned by Git Credential Manager")


def _api(method: str, url: str, token: str, data=None, ctype="application/json"):
    """One GitHub API/upload call. Returns (status, parsed-json-or-bytes). Raises on HTTP error."""
    body = data if isinstance(data, (bytes, type(None))) else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "User-Agent": "cautious-optimism-briefings", "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        parsed = json.loads(raw) if raw and "json" in (r.headers.get("Content-Type") or "") else raw
        return r.status, parsed


def _repo_api() -> str:
    return f"{_API}/repos/{config.GITHUB_REPO}"


def ensure_release(token: str) -> dict:
    """Return the permanent audio release, creating it (and its tag) once if absent."""
    tag = config.GITHUB_RELEASE_TAG
    try:
        _, rel = _api("GET", f"{_repo_api()}/releases/tags/{tag}", token)
        return rel
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    _, rel = _api("POST", f"{_repo_api()}/releases", token, {
        "tag_name": tag, "name": "Episode audio",
        "body": "Rolling window of episode audio for the podcast feed. Managed automatically by "
                "publish_feed.py; assets are added on publish and pruned after the retention window.",
        "prerelease": True})
    return rel


def _find_asset(release: dict, name: str):
    for a in release.get("assets") or []:
        if a.get("name") == name:
            return a
    return None


def upload_asset(mp3_path: str, asset_name: str) -> str:
    """Upload ``mp3_path`` as ``asset_name`` on the audio release; return its public download URL.
    Replaces an existing asset of the same name (idempotent same-day republish)."""
    token = _token()
    release = ensure_release(token)
    existing = _find_asset(release, asset_name)
    if existing:
        _api("DELETE", f"{_repo_api()}/releases/assets/{existing['id']}", token)
    with open(mp3_path, "rb") as f:
        blob = f.read()
    upload_url = release["upload_url"].split("{")[0] + f"?name={asset_name}"
    _, asset = _api("POST", upload_url, token, blob, ctype="audio/mpeg")
    return asset["browser_download_url"]


def delete_asset(asset_name: str) -> bool:
    """Delete the release asset named ``asset_name`` (real reclamation on prune). Best-effort:
    returns True if deleted, False if it wasn't there or the call failed."""
    try:
        token = _token()
        asset = _find_asset(ensure_release(token), asset_name)
        if not asset:
            return False
        _api("DELETE", f"{_repo_api()}/releases/assets/{asset['id']}", token)
        return True
    except Exception:
        return False
