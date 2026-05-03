"""Tests for 429 handling — Retry-After parsing and fetch shape."""
import unittest
from unittest.mock import MagicMock

from stockbit_client import StockbitClient, FetchEndReason, parse_retry_after_header
from jobs import JobManager, Job, Task, JobStatus, TaskStatus
class TestRetryAfterHeader(unittest.TestCase):
    def test_digit_seconds(self):
        self.assertEqual(parse_retry_after_header("120", 10.0), 120.0)

    def test_missing_uses_fallback(self):
        self.assertEqual(parse_retry_after_header(None, 42.0), 42.0)

    def test_float_string(self):
        self.assertEqual(parse_retry_after_header("15.5", 1.0), 15.5)


class TestFetchRunningTrade429(unittest.TestCase):
    def setUp(self):
        self.tm = MagicMock()
        self.tm.get_valid_token.return_value = "tok"
        self.client = StockbitClient(self.tm)

    def test_first_page_429_returns_rate_limited(self):
        def fake_fetch(*args, **kwargs):
            return {
                "success": False,
                "error": "Rate limited (429)",
                "status_code": 429,
                "rate_limited": True,
                "retry_after_seconds": 30.0,
            }

        self.client._fetch_page = fake_fetch
        r = self.client.fetch_running_trade("BBCA", "2024-01-02", limit=50, retry_count=1)
        self.assertFalse(r.get("success"))
        self.assertTrue(r.get("rate_limited"))
        self.assertEqual(r.get("retry_after_seconds"), 30.0)
        self.assertEqual(r.get("status_code"), 429)

    def test_second_page_429_partial(self):
        calls = {"n": 0}

        def fake_fetch(ticker, date, limit, trade_number, retry_count=3):
            calls["n"] += 1
            if trade_number is None:
                return {
                    "success": True,
                    "data": [{"trade_number": i, "time": "15:00:00"} for i in range(50)],
                }
            return {
                "success": False,
                "error": "Rate limited (429)",
                "rate_limited": True,
                "retry_after_seconds": 45.0,
                "status_code": 429,
            }

        self.client._fetch_page = fake_fetch
        r = self.client.fetch_running_trade("BBCA", "2024-01-02", limit=50, retry_count=1)
        self.assertFalse(r.get("success"))
        self.assertTrue(r.get("partial"))
        self.assertTrue(r.get("rate_limited"))
        self.assertEqual(r.get("end_reason"), FetchEndReason.RATE_LIMITED)
        self.assertEqual(len(r.get("data", [])), 50)


class TestJobManager429(unittest.TestCase):
    def test_rate_limit_keeps_task_pending_and_sets_per_task_retry_gate(self):
        client = MagicMock()
        client.fetch_running_trade.return_value = {
            "success": False,
            "error": "Rate limited (429)",
            "rate_limited": True,
            "retry_after_seconds": 12.0,
        }
        storage = MagicMock()
        jm = JobManager(client, storage)
        job = Job(
            job_id="j1",
            tickers=["A"],
            from_date="2024-01-01",
            until_date="2024-01-01",
            delay_seconds=0,
            limit=50,
            parallel_workers=1,
            status=JobStatus.RUNNING,
            tasks=[Task(ticker="A", date="2024-01-01")],
        )
        jm._process_task(job, job.tasks[0])
        self.assertEqual(job.tasks[0].status, TaskStatus.PENDING)
        # this task alone is blocked — not a job-wide pause
        self.assertIsNotNone(job.tasks[0].retry_after_monotonic)
        self.assertGreater(job.tasks[0].retry_after_monotonic, __import__("time").monotonic())
        # didn't burn the attempt counter as a "real" try
        self.assertEqual(job.tasks[0].attempts, 0)
