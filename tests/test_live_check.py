"""Unit tests for live_check.py — the "is the public feed the one we built?" check (HTTP, git and
ntfy all mocked; no network)."""
import io
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import live_check as lc  # noqa: E402

DATE = "2026-11-21"
BASE = "https://example.test/show"
HEAD = "a" * 40


def _feed(items):
    """items: [(guid, v_token, title)] -> RSS text shaped like feed.build_feed's output."""
    body = "".join(
        f"""
    <item>
      <title>{title}</title>
      <enclosure url="{BASE}/audio/{guid}.mp3?v={v}" length="100" type="audio/mpeg"/>
      <guid isPermaLink="false">{guid}</guid>
    </item>""" for guid, v, title in items)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" '
            'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>'
            f"<title>Show &amp; Tell</title>{body}\n</channel></rss>")


YESTERDAY = [("radar-2026-11-20", 1, "Radar"), ("labs-2026-11-20", 2, "Labs")]
TODAY = [("radar-2026-11-21", 3, "Radar"), ("labs-2026-11-21", 4, "Labs")]
OLD = [("radar-2026-11-10", 0, "Radar")]          # pruned locally by today's publish


def _run(conclusion="success", status="completed", sha=HEAD, number=32):
    return {"run_number": number, "status": status, "conclusion": conclusion, "head_sha": sha,
            "html_url": "https://github.test/runs/32"}


def _http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b""))


class ParseCompareTest(unittest.TestCase):
    def test_parse_items_reads_guid_enclosure_and_title(self):
        items = lc.parse_items(_feed(TODAY))
        self.assertEqual(items["radar-2026-11-21"],
                         (f"{BASE}/audio/radar-2026-11-21.mp3?v=3", "Radar"))
        self.assertEqual(len(items), 2)

    def test_compare_identical_is_in_sync(self):
        a = lc.parse_items(_feed(YESTERDAY + TODAY))
        self.assertTrue(lc.compare(a, dict(a)).in_sync)

    def test_compare_separates_missing_stale_and_extra(self):
        local = lc.parse_items(_feed(YESTERDAY + TODAY))
        live = lc.parse_items(_feed(OLD + [("radar-2026-11-20", 1, "Radar"),
                                           ("labs-2026-11-20", 99, "Labs")]))
        d = lc.compare(local, live)
        self.assertEqual(d.missing, ["labs-2026-11-21", "radar-2026-11-21"])
        self.assertEqual(d.stale, ["labs-2026-11-20"])          # same guid, older ?v= token
        self.assertEqual(d.extra, ["radar-2026-11-10"])
        self.assertFalse(d.in_sync)


class VerifyTest(unittest.TestCase):
    """verify() end to end with the network, git and the clock replaced."""

    def setUp(self):
        self._feed_file, self._base = config.FEED_FILE, config.FEED_BASE_URL
        fd, self.path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(_feed(YESTERDAY + TODAY))
        config.FEED_FILE, config.FEED_BASE_URL = self.path, BASE
        self.clock = [0.0]
        self.sleeps = []
        self.audio_status = {}                  # guid -> exception to raise on HEAD
        self.live = []                          # successive live-feed responses (last one repeats)
        self.feed_fetches = 0
        patches = [
            mock.patch.object(lc, "_http", side_effect=self._http),
            mock.patch.object(lc, "commits_ahead", return_value=0),
            mock.patch.object(lc, "origin_head", return_value=HEAD),
            mock.patch.object(lc, "latest_deploy", return_value=_run()),
            mock.patch.object(lc.time, "monotonic", side_effect=lambda: self.clock[0]),
            mock.patch.object(lc.time, "sleep", side_effect=self._sleep),
        ]
        self.mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.m_ahead, self.m_deploy = self.mocks[1], self.mocks[3]

    def tearDown(self):
        config.FEED_FILE, config.FEED_BASE_URL = self._feed_file, self._base
        os.remove(self.path)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def _http(self, url, method="GET", timeout=30, headers=None):
        if "/feed.xml" in url:
            resp = self.live[min(self.feed_fetches, len(self.live) - 1)]
            self.feed_fetches += 1
            if isinstance(resp, Exception):
                raise resp
            return 200, resp.encode("utf-8")
        guid = url.rsplit("/", 1)[1].split(".mp3")[0]
        self.assertEqual(method, "HEAD")
        err = self.audio_status.get(guid)
        if err:
            raise err
        return 200, b""

    def test_live_when_feeds_match_and_audio_downloads(self):
        self.live = [_feed(YESTERDAY + TODAY)]
        r = lc.verify(DATE, wait=600)
        self.assertEqual(r.status, lc.LIVE)
        self.assertEqual(sorted(r.today), ["labs-2026-11-21", "radar-2026-11-21"])
        self.assertEqual(self.sleeps, [])                       # no waiting on a normal morning
        self.assertIn("all 2 episode(s)", lc.describe(r))

    def test_failed_deploy_is_not_live_and_does_not_wait_out_the_clock(self):
        # 2026-11-21: push succeeded, deploy failed, the site kept serving yesterday's build.
        self.live = [_feed(OLD + YESTERDAY)]
        self.m_deploy.return_value = _run(conclusion="failure")
        r = lc.verify(DATE, wait=600)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertEqual(self.sleeps, [])                       # failure known -> stop at once
        text = lc.describe(r)
        self.assertIn("2 of 2 episode(s) for 2026-11-21 are NOT on the live feed", text)
        self.assertIn("run #32 ended in failure", text)
        self.assertIn("https://github.test/runs/32", text)

    def test_waits_for_a_deploy_in_flight_then_passes(self):
        self.live = [_feed(OLD + YESTERDAY), _feed(OLD + YESTERDAY), _feed(YESTERDAY + TODAY)]
        self.m_deploy.return_value = _run(conclusion=None, status="in_progress")
        r = lc.verify(DATE, wait=600, interval=30)
        self.assertEqual(r.status, lc.LIVE)
        self.assertEqual(self.sleeps, [30, 30])

    def test_gives_up_after_the_wait_and_reports_the_stuck_deploy(self):
        self.live = [_feed(OLD + YESTERDAY)]
        self.m_deploy.return_value = _run(conclusion=None, status="in_progress")
        r = lc.verify(DATE, wait=90, interval=30)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertEqual(self.sleeps, [30, 30, 30])
        self.assertIn("still in_progress after 90s", lc.describe(r))

    def test_a_failed_run_for_an_older_commit_does_not_cut_the_wait_short(self):
        # The run for the commit we just pushed has not been created yet; the latest run on record
        # is yesterday's. Its outcome says nothing about today's deploy.
        self.live = [_feed(OLD + YESTERDAY), _feed(YESTERDAY + TODAY)]
        self.m_deploy.return_value = _run(conclusion="failure", sha="b" * 40)
        r = lc.verify(DATE, wait=600, interval=30)
        self.assertEqual(r.status, lc.LIVE)
        self.assertEqual(self.sleeps, [30])

    def test_unpushed_commits_are_named_and_never_waited_for(self):
        self.live = [_feed(OLD + YESTERDAY)]
        self.m_ahead.return_value = 1
        r = lc.verify(DATE, wait=600)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertEqual(self.sleeps, [])
        self.assertIn("1 publish commit(s) exist only on this machine", lc.describe(r))

    def test_stale_enclosure_url_counts_as_not_live(self):
        # Same GUIDs, but today's Radar was republished and the site still has the old ?v= token.
        self.live = [_feed(YESTERDAY + [("radar-2026-11-21", 1, "Radar"),
                                        ("labs-2026-11-21", 4, "Labs")])]
        self.m_deploy.return_value = _run(conclusion="failure")
        r = lc.verify(DATE)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertEqual(r.diff.stale, ["radar-2026-11-21"])
        self.assertIn("1 of 2 episode(s)", lc.describe(r))

    def test_older_entries_out_of_sync_with_today_present(self):
        self.live = [_feed(OLD + YESTERDAY + TODAY)]            # prune not deployed
        r = lc.verify(DATE)
        self.assertEqual(r.status, lc.NOT_LIVE)
        text = lc.describe(r)
        self.assertIn("1 episode entry differs; none dated 2026-11-21", text)
        self.assertIn("succeeded, yet the site still serves an older feed", text)

    def test_missing_audio_fails_a_current_feed(self):
        self.live = [_feed(YESTERDAY + TODAY)]
        self.audio_status["labs-2026-11-21"] = _http_error(404)
        r = lc.verify(DATE)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertEqual(r.audio, [("labs-2026-11-21", "HTTP 404")])
        self.assertIn("1 of 2 audio file(s)", lc.describe(r))
        self.assertIn("Labs (HTTP 404)", lc.describe(r))

    def test_unreachable_site_is_inconclusive_not_an_alarm(self):
        self.live = [lc.Unreachable("getaddrinfo failed")]
        r = lc.verify(DATE, wait=60, interval=30)
        self.assertEqual(r.status, lc.INCONCLUSIVE)
        self.assertEqual(self.sleeps, [30, 30])                 # it did keep trying first
        self.assertIn("did not respond", lc.describe(r))

    def test_site_answering_404_is_not_live(self):
        self.live = [_http_error(404)]
        r = lc.verify(DATE)
        self.assertEqual(r.status, lc.NOT_LIVE)
        self.assertIn("answered HTTP 404", lc.describe(r))

    def test_no_episodes_for_the_date_still_checks_the_build(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(_feed(YESTERDAY))
        self.live = [_feed(YESTERDAY)]
        r = lc.verify(DATE)
        self.assertEqual(r.status, lc.LIVE)
        self.assertEqual(r.today, {})
        self.assertIn("no episodes dated 2026-11-21", lc.describe(r))


class MainTest(unittest.TestCase):
    def _main(self, result, argv=()):
        with mock.patch.object(sys, "argv", ["live_check.py", "--date", DATE, *argv]), \
             mock.patch.object(lc.config, "configure_logging"), \
             mock.patch.object(lc, "log"), \
             mock.patch.object(lc, "verify", **result), \
             mock.patch.object(lc.ntfy_push, "push") as push:
            return lc.main(), push

    def test_live_exits_0_without_a_push(self):
        code, push = self._main({"return_value": lc.Result(lc.LIVE, DATE)})
        self.assertEqual(code, 0)
        push.assert_not_called()

    def test_not_live_exits_1_and_alerts(self):
        r = lc.Result(lc.NOT_LIVE, DATE, {"radar-2026-11-21": "Radar"},
                      lc.Diff(missing=["radar-2026-11-21"]), cause="Deploy Pages run #32 ended.")
        code, push = self._main({"return_value": r})
        self.assertEqual(code, 1)
        title, body, tags = push.call_args[0]
        self.assertEqual(title, "Briefings NOT live (2026-11-21)")
        self.assertIn("1 of 1 episode(s)", body)
        self.assertEqual(tags, "warning")

    def test_no_notify_suppresses_the_alert_but_not_the_exit_code(self):
        r = lc.Result(lc.NOT_LIVE, DATE, {}, lc.Diff(extra=["x-2026-11-10"]), cause="c")
        code, push = self._main({"return_value": r}, ["--no-notify"])
        self.assertEqual(code, 1)
        push.assert_not_called()

    def test_inconclusive_and_crashes_exit_2_silently(self):
        code, push = self._main({"return_value": lc.Result(lc.INCONCLUSIVE, DATE, cause="x")})
        self.assertEqual(code, 2)
        push.assert_not_called()
        code, push = self._main({"side_effect": RuntimeError("boom")})
        self.assertEqual(code, 2)
        push.assert_not_called()


class HelperTest(unittest.TestCase):
    def test_check_audio_retries_once_before_reporting(self):
        calls = []

        def http(url, method="GET", timeout=30, headers=None):
            calls.append(url)
            if len(calls) == 1:
                raise lc.Unreachable("reset")
            return 200, b""

        with mock.patch.object(lc, "_http", side_effect=http):
            self.assertEqual(lc.check_audio({"g": "https://example.test/g.mp3"}), [])
        self.assertEqual(len(calls), 2)

    def test_latest_deploy_swallows_errors(self):
        with mock.patch.object(lc, "_http", side_effect=lc.Unreachable("down")):
            self.assertIsNone(lc.latest_deploy())

    def test_cause_when_no_run_started_for_the_push(self):
        text = lc._cause(0, _run(sha="b" * 40, number=31), HEAD, 600)
        self.assertIn("No Deploy Pages run has started for the latest push", text)


if __name__ == "__main__":
    unittest.main()
