"""Unit tests for github_release.py — release-asset hosting logic (HTTP mocked)."""
import io
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import github_release as gr  # noqa: E402

UPLOAD = "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}"
DL = "https://github.com/o/r/releases/download/audio/x.mp3"


def _http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))


class GithubReleaseTest(unittest.TestCase):
    def setUp(self):
        self._tag = config.GITHUB_RELEASE_TAG
        self._repo = config.GITHUB_REPO
        config.GITHUB_RELEASE_TAG = "audio"
        config.GITHUB_REPO = "o/r"
        self.calls = []

    def tearDown(self):
        config.GITHUB_RELEASE_TAG = self._tag
        config.GITHUB_REPO = self._repo

    def _fake_api(self, existing_assets):
        def api(method, url, token, data=None, ctype="application/json"):
            self.calls.append((method, url))
            if method == "GET" and "/releases/tags/audio" in url:
                return 200, {"id": 1, "upload_url": UPLOAD, "assets": existing_assets}
            if method == "POST" and url.startswith("https://uploads.github.com"):
                return 201, {"browser_download_url": DL}
            if method == "DELETE":
                return 204, b""
            if method == "POST" and url.endswith("/releases"):
                return 201, {"id": 1, "upload_url": UPLOAD, "assets": []}
            raise AssertionError(f"unexpected {method} {url}")
        return api

    def test_upload_returns_download_url(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"audio"); path = f.name
        try:
            with mock.patch.object(gr, "_token", return_value="tok"), \
                 mock.patch.object(gr, "_api", side_effect=self._fake_api(existing_assets=[])):
                url = gr.upload_asset(path, "x.mp3")
            self.assertEqual(url, DL)
            # no DELETE when the asset didn't already exist
            self.assertNotIn("DELETE", [m for m, _ in self.calls])
            self.assertTrue(any(m == "POST" and u.startswith("https://uploads") for m, u in self.calls))
        finally:
            os.remove(path)

    def test_upload_replaces_existing_same_name(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"audio"); path = f.name
        try:
            with mock.patch.object(gr, "_token", return_value="tok"), \
                 mock.patch.object(gr, "_api",
                                   side_effect=self._fake_api([{"name": "x.mp3", "id": 99}])):
                gr.upload_asset(path, "x.mp3")
            # existing asset 99 deleted before re-upload
            self.assertIn(("DELETE", f"{gr._repo_api()}/releases/assets/99"), self.calls)
        finally:
            os.remove(path)

    def test_ensure_release_creates_when_absent(self):
        def api(method, url, token, data=None, ctype="application/json"):
            self.calls.append((method, url))
            if method == "GET":
                raise _http_error(404)
            if method == "POST" and url.endswith("/releases"):
                return 201, {"id": 1, "upload_url": UPLOAD, "assets": []}
            raise AssertionError(method)
        with mock.patch.object(gr, "_api", side_effect=api):
            rel = gr.ensure_release("tok")
        self.assertEqual(rel["id"], 1)
        self.assertIn(("POST", f"{gr._repo_api()}/releases"), self.calls)

    def test_delete_asset_missing_returns_false(self):
        with mock.patch.object(gr, "_token", return_value="tok"), \
             mock.patch.object(gr, "_api", side_effect=self._fake_api(existing_assets=[])):
            self.assertFalse(gr.delete_asset("nope.mp3"))

    def test_delete_asset_present_returns_true(self):
        with mock.patch.object(gr, "_token", return_value="tok"), \
             mock.patch.object(gr, "_api", side_effect=self._fake_api([{"name": "x.mp3", "id": 7}])):
            self.assertTrue(gr.delete_asset("x.mp3"))
        self.assertIn(("DELETE", f"{gr._repo_api()}/releases/assets/7"), self.calls)

    def test_delete_asset_swallows_errors(self):
        with mock.patch.object(gr, "_token", side_effect=RuntimeError("no cred")):
            self.assertFalse(gr.delete_asset("x.mp3"))  # best-effort, never raises


if __name__ == "__main__":
    unittest.main()
