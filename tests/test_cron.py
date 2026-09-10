import contextlib
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from delivery import Delivery
from main import execute, run_scheduled
from schedule import EASTERN, Occurrence, candidates
from state import State, state_path


class ScheduleTests(unittest.TestCase):
    def test_dst_keeps_the_same_eastern_slots(self):
        for stamp in ("2026-03-06T14:45:40+00:00", "2026-03-09T13:45:40+00:00",
                      "2026-10-30T13:45:40+00:00", "2026-11-02T14:45:40+00:00"):
            slots = candidates(datetime.fromisoformat(stamp))
            self.assertEqual([(x.job, x.at.strftime("%H:%M")) for x in slots], [("fear", "09:45")])

    def test_exchange_holidays_and_weekends(self):
        for day in ("2026-04-03", "2026-11-26", "2026-09-12", "2026-09-07"):
            self.assertEqual(candidates(datetime.fromisoformat(day+"T09:45").replace(tzinfo=EASTERN)), [])
        # Columbus Day is a federal holiday, but the stock exchange is open.
        self.assertEqual(len(candidates(datetime(2026, 10, 12, 9, 45, tzinfo=EASTERN))), 1)

    def test_late_start_grace_and_no_unlimited_catch_up(self):
        self.assertEqual(len(candidates(datetime(2026, 9, 10, 9, 53, tzinfo=EASTERN))), 1)
        self.assertEqual(candidates(datetime(2026, 9, 10, 10, 1, tzinfo=EASTERN)), [])
        self.assertEqual(candidates(datetime(2026, 9, 10, 8, 0, tzinfo=EASTERN)), [])

    def test_idle_runner_does_not_import_heavy_report_libraries(self):
        result = subprocess.run([sys.executable, "-c",
            "import main,tasks,sys; assert not any(x in sys.modules for x in "
            "('pandas','matplotlib','yfinance','fear_and_greed'))"], timeout=15)
        self.assertEqual(result.returncode, 0)


class StateAndDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"state.sqlite3"
        self.state = State(self.path)
        self.key = "2026-09-10:09:45:fear"
        self.env = patch.dict(os.environ, {"WEBHOOK_URL": "https://example.invalid/test-webhook"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.state.close()
        self.tmp.cleanup()

    def test_delivery_is_deduplicated_across_restart_and_value_persists(self):
        self.assertTrue(self.state.claim(self.key))
        second = State(self.path)
        try:
            self.assertFalse(second.claim(self.key))
        finally:
            second.close()
        response = SimpleNamespace(status_code=200, content=b'{}', json=lambda: {"id": "message-1"})
        with patch("requests.post", return_value=response) as post:
            Delivery(self.state, self.key)({"embeds": []}, state_updates={"previous_fear_value": 42.1})
            with self.assertRaises(RuntimeError):
                Delivery(self.state, self.key)({"embeds": []})
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["params"], {"wait": "true"})
            self.assertEqual(post.call_args.kwargs["timeout"], (10, 40))
        self.state.close()
        self.state = State(self.path)
        self.assertEqual(self.state.get("previous_fear_value"), 42.1)
        self.assertFalse(self.state.claim(self.key))

    def test_http_rejection_is_retryable_and_does_not_advance_comparison(self):
        self.state.claim(self.key)
        with patch("requests.post", return_value=SimpleNamespace(status_code=429)):
            with self.assertRaises(RuntimeError):
                Delivery(self.state, self.key)({}, state_updates={"previous_fear_value": 50})
        self.assertIsNone(self.state.get("previous_fear_value"))
        self.assertTrue(self.state.claim(self.key))

    def test_lost_response_is_not_resent(self):
        self.state.claim(self.key)
        with patch("requests.post", side_effect=requests.Timeout("response lost")):
            with self.assertRaises(RuntimeError):
                Delivery(self.state, self.key)({})
        self.assertEqual(self.state.run(self.key)["status"], "uncertain")
        self.assertFalse(self.state.claim(self.key))

    def test_ambiguous_server_error_is_not_resent(self):
        self.state.claim(self.key)
        with patch("requests.post", return_value=SimpleNamespace(status_code=502)):
            with self.assertRaises(RuntimeError):
                Delivery(self.state, self.key)({})
        self.assertFalse(self.state.can_run(self.key))

    def test_crash_during_send_is_not_resent(self):
        self.state.claim(self.key)
        self.state.before_send(self.key)
        with self.state.db:
            self.state.db.execute("UPDATE runs SET updated=0 WHERE id=?", (self.key,))
        self.assertFalse(self.state.claim(self.key))

    def test_pre_delivery_failures_have_bounded_retries(self):
        for _ in range(3):
            self.assertTrue(self.state.claim(self.key))
            self.state.fail(self.key, "Source unavailable")
        self.assertFalse(self.state.claim(self.key))

    def test_cloud_requires_persistent_volume(self):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_ID": "test"}, clear=True):
            with self.assertRaises(RuntimeError):
                state_path()


class RunnerTests(unittest.TestCase):
    def test_full_day_restarts_keep_all_six_slots_without_extra_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"state.sqlite3"
            sent = []
            current = [datetime(2026, 9, 10, 8, 0, 35, tzinfo=EASTERN)]

            def sleep(seconds):
                current[0] += timedelta(seconds=seconds)

            def launch(args, **kwargs):
                at = datetime.fromisoformat(args[args.index("--at")+1])
                slot = Occurrence(args[args.index("--execute")+1], at)
                self.assertGreaterEqual(current[0], at)
                store = State(path)
                try:
                    self.assertTrue(store.claim(slot.key))
                    store.before_send(slot.key)
                    store.sent(slot.key, "fake", {})
                finally:
                    store.close()
                sent.append((slot.job, at.strftime("%H:%M")))
                return SimpleNamespace(returncode=0)

            with contextlib.redirect_stdout(io.StringIO()):
                for minute in range(8*60, 18*60, 5):
                    current[0] = datetime(2026, 9, 10, minute//60, minute%60, 35, tzinfo=EASTERN)
                    self.assertEqual(run_scheduled(path, clock=lambda: current[0], sleep=sleep, launch=launch), 0)
            self.assertEqual(sent, [("fear", "09:45"), ("fear", "11:45"), ("fear", "13:45"),
                                    ("fear", "15:45"), ("breadth", "16:30"), ("reddit", "16:42")])

    def test_timeout_exits_and_releases_claim_for_a_later_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"state.sqlite3"
            stamp = datetime(2026, 9, 10, 9, 45, tzinfo=EASTERN)
            key = Occurrence("fear", stamp).key

            def launch(args, **kwargs):
                state = State(path)
                state.claim(key)
                state.close()
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])

            self.assertEqual(run_scheduled(path, clock=lambda: stamp, launch=launch), 1)
            state = State(path)
            try:
                self.assertTrue(state.can_run(key))
            finally:
                state.close()

    def test_task_without_delivery_is_not_marked_successful(self):
        with tempfile.TemporaryDirectory() as tmp, patch("tasks.run_reddit_task"):
            slot = Occurrence("reddit", datetime(2026, 9, 10, 16, 42, tzinfo=EASTERN))
            path = Path(tmp)/"state.sqlite3"
            self.assertEqual(execute(slot, path), 1)
            state = State(path)
            try:
                self.assertEqual(state.run(slot.key)["status"], "failed")
            finally:
                state.close()


class ReportTests(unittest.TestCase):
    def test_fear_report_preserves_previous_value_comparison(self):
        import tasks
        sender = Mock()
        with patch("fear_and_greed.get", return_value=SimpleNamespace(value=48.5, description="neutral")):
            tasks.run_fear_greed_task(sender, previous_value=42.0)
        payload = sender.call_args.args[0]
        self.assertIn("升高了 6.5", payload["embeds"][0]["description"])
        self.assertEqual(sender.call_args.kwargs["state_updates"], {"previous_fear_value": 48.5})

    def test_reddit_report_keeps_ranking_and_mentions(self):
        import tasks
        sender = Mock()
        with patch("tasks.get_apewisdom_data", return_value=[
            {"rank": 1, "ticker": "AAA", "name": "Test", "mentions": 123, "rank_24h_ago": 3}]):
            tasks.run_reddit_task(sender)
        desc = sender.call_args.args[0]["embeds"][0]["description"]
        self.assertIn("🔺2", desc)
        self.assertIn("**$AAA**", desc)
        self.assertIn("`123`", desc)

    def test_breadth_renders_png_and_preserves_calculation(self):
        import pandas as pd
        import tasks
        index = pd.date_range("2026-01-01", periods=120, freq="B", tz="America/New_York")
        prices = pd.DataFrame({("Close", "UP"): range(1, 121),
                               ("Close", "DOWN"): range(120, 0, -1)}, index=index)
        sender = Mock()

        def inspect_payload(payload, **kwargs):
            self.assertIn("`50.0%`", payload["embeds"][0]["description"])
            buffer = kwargs["files"]["file"][1]
            self.assertTrue(buffer.getvalue().startswith(b"\x89PNG"))
            self.assertGreater(len(buffer.getvalue()), 10000)

        sender.side_effect = inspect_payload
        with patch("requests.get", return_value=SimpleNamespace(text="unused", raise_for_status=lambda: None)), \
             patch("pandas.read_html", return_value=[pd.DataFrame({"Symbol": ["UP", "DOWN"]})]), \
             patch("yfinance.download", return_value=prices):
            tasks.run_breadth_task(sender)
        self.assertEqual(sender.call_count, 1)
        self.assertTrue(sender.call_args.kwargs["files"]["file"][1].closed)


if __name__ == "__main__":
    unittest.main()
