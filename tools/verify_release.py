"""Live, non-destructive end-to-end check of GitHub Release audio hosting.

Runs the REAL github_release code against a THROWAWAY release (unique tag), then deletes it — the
permanent audio release and the feed are never touched. Verifies the things a podcast host needs:
public unauthenticated download, HTTP range requests (seeking), and the ?v= cache-bust query.

    python tools/verify_release.py

Exits non-zero if any check fails. Reuses the GitHub token `git push` already uses (never printed).
"""
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import github_release as gr  # noqa: E402


def _get(url, rng=None):
    headers = {"User-Agent": "verify-release"}
    if rng:
        headers["Range"] = rng
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))

    tag = f"verify-release-{uuid.uuid4().hex[:8]}"
    real_tag = config.GITHUB_RELEASE_TAG
    config.GITHUB_RELEASE_TAG = tag  # point the real code at a throwaway release
    asset = "verify.mp3"
    payload = b"\xff\xfb\x90\x00" + b"VERIFY" * 5000  # ~30KB, mp3-ish header

    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.write(fd, payload)
    os.close(fd)
    print(f"repo={config.GITHUB_REPO}  throwaway tag={tag}")
    try:
        url = gr.upload_asset(path, asset)  # creates the throwaway release + uploads
        print("download URL:", url)
        check("upload returned a download URL", bool(url))

        r = _get(url)  # unauthenticated (no token) — what Spotify/listeners do
        body = r.read()
        check("serves unauthenticated (public)", r.status == 200, f"status {r.status}")
        check("full payload intact", len(body) == len(payload), f"{len(body)}/{len(payload)}")
        check("Accept-Ranges advertised", (r.headers.get("Accept-Ranges") or "").lower() == "bytes",
              r.headers.get("Accept-Ranges"))
        try:
            rr = _get(url, rng="bytes=0-99")
            part = rr.read()
            check("range request -> 206 + 100 bytes", rr.status == 206 and len(part) == 100,
                  f"status {rr.status}, {len(part)} bytes")
        except urllib.error.HTTPError as e:
            check("range request -> 206 + 100 bytes", False, f"HTTP {e.code}")
        try:
            rv = _get(url + "?v=12345")
            check("?v= cache-bust query works", rv.status == 200, f"status {rv.status}")
        except urllib.error.HTTPError as e:
            check("?v= cache-bust query works", False, f"HTTP {e.code}")

        check("delete_asset removes it", gr.delete_asset(asset))
    finally:
        os.remove(path)
        # tear down the throwaway release + tag so nothing lingers
        try:
            tok = gr._token()
            _, rel = gr._api("GET", f"{gr._repo_api()}/releases/tags/{tag}", tok)
            gr._api("DELETE", f"{gr._repo_api()}/releases/{rel['id']}", tok)
            gr._api("DELETE", f"{gr._repo_api()}/git/refs/tags/{tag}", tok)
            print("cleaned up throwaway release + tag")
        except Exception as e:
            print("cleanup note:", e)
        config.GITHUB_RELEASE_TAG = real_tag

    print("\nRESULT:", "ALL CHECKS PASS — release hosting works end to end" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
