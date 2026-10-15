"""Unit tests for github_auth.py — non-interactive, non-hanging GitHub credentials."""
import base64
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import github_auth as ga  # noqa: E402
import publish_feed  # noqa: E402


class GithubAuthTest(unittest.TestCase):
    def setUp(self):
        self._file = config.GITHUB_TOKEN_FILE
        self.tmp = tempfile.mkdtemp()
        config.GITHUB_TOKEN_FILE = os.path.join(self.tmp, "github_token")

    def tearDown(self):
        config.GITHUB_TOKEN_FILE = self._file

    def _write_token(self, tok):
        with open(config.GITHUB_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok + "\n")

    def test_token_file_preferred_over_gcm(self):
        self._write_token("ghp_file")
        with mock.patch.object(ga, "run") as run:
            self.assertEqual(ga.token(), "ghp_file")
        run.assert_not_called()

    def test_gcm_fallback_is_non_interactive(self):
        done = subprocess.CompletedProcess([], 0, "protocol=https\npassword=gcm_tok\n", "")
        with mock.patch.object(ga, "run", return_value=done) as run:
            self.assertEqual(ga.token(), "gcm_tok")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["GCM_INTERACTIVE"], "never")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_no_credential_raises_actionable_error(self):
        empty = subprocess.CompletedProcess([], 128, "", "fatal: Cannot prompt")
        with mock.patch.object(ga, "run", return_value=empty):
            with self.assertRaisesRegex(RuntimeError, "no GitHub credential"):
                ga.token()

    def test_gcm_timeout_raises_not_hangs(self):
        with mock.patch.object(ga, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                ga.token()

    def test_git_env_carries_token_as_extraheader(self):
        self._write_token("ghp_file")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_CONFIG_COUNT", None)
            env = ga.git_env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        expected = base64.b64encode(b"x-access-token:ghp_file").decode()
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], f"AUTHORIZATION: basic {expected}")
        self.assertEqual(env["GCM_INTERACTIVE"], "never")

    def test_git_env_without_token_file_only_disables_prompts(self):
        env = ga.git_env()
        self.assertNotIn("http.https://github.com/.extraheader",
                         [v for k, v in env.items() if k.startswith("GIT_CONFIG_KEY_")])
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_check_reports_missing_credential(self):
        with mock.patch.object(ga, "token", side_effect=RuntimeError("no GitHub credential: x")):
            self.assertIn("no GitHub credential", ga.check())

    def test_run_timeout_kills_grandchildren_holding_pipes(self):
        # The 2026-10-14 hang: a grandchild (GCM) inherited the pipes, so a plain subprocess.run
        # timeout never returned. run() must come back promptly after killing the whole tree.
        grandchild = "import time; time.sleep(120)"
        child = (f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
                 "time.sleep(120)")
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            ga.run([sys.executable, "-c", child], timeout=2)
        self.assertLess(time.monotonic() - start, 30)


class PublishPreflightTest(unittest.TestCase):
    def test_broken_auth_aborts_before_any_tts_and_alerts(self):
        with mock.patch.object(publish_feed.github_auth, "check", return_value="no GitHub credential"), \
             mock.patch.object(publish_feed, "synthesize") as synth, \
             mock.patch.object(publish_feed, "_alert_ntfy") as alert:
            results = publish_feed.publish("2026-10-15", {}, push=True)
        synth.assert_not_called()
        alert.assert_called_once()
        self.assertTrue(results[0][1].startswith("FAILED"))
        self.assertFalse(any(publish_feed._is_success(s) for _, s in results))

    def test_push_failure_alerts_then_raises(self):
        err = subprocess.CalledProcessError(1, ["git"], "", "fatal: could not read Username")
        with mock.patch.object(publish_feed, "_push_with_retry", side_effect=err), \
             mock.patch.object(publish_feed, "_alert_ntfy") as alert:
            with self.assertRaises(subprocess.CalledProcessError):
                publish_feed._push_or_alert("2026-10-15", notify=True)
        self.assertIn("could not read Username", alert.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
