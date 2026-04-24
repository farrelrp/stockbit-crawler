"""Regression tests for per-day pagination — no silent 'success' on mid-fetch errors."""
import unittest
from unittest.mock import MagicMock

from stockbit_client import StockbitClient, FetchEndReason, NORMAL_FETCH_END_REASONS


class TestFetchRunningTrade(unittest.TestCase):
    def setUp(self):
        self.tm = MagicMock()
        self.tm.get_valid_token.return_value = "fake-token"
        self.client = StockbitClient(self.tm)

    def test_second_page_timeout_is_not_success(self):
        # first page full, second page fails — must not look like a finished day
        def fake_fetch(ticker, date, limit, trade_number, retry_count=3):
            if trade_number is None:
                return {
                    "success": True,
                    "data": [
                        {"trade_number": n, "time": "15:00:00"} for n in range(50)
                    ],
                    "count": 50,
                }
            return {"success": False, "error": "Request timeout after 3 attempts"}

        self.client._fetch_page = fake_fetch
        r = self.client.fetch_running_trade(
            "BBCA", "2024-01-02", limit=50, retry_count=1, cancel_check=None
        )
        self.assertFalse(r.get("success"))
        self.assertTrue(r.get("partial"))
        self.assertEqual(r.get("end_reason"), FetchEndReason.FETCH_INTERRUPTED)
        self.assertEqual(len(r.get("data", [])), 50)
        self.assertIn("timeout", (r.get("error") or "").lower())

    def test_short_page_is_normal_end(self):
        def fake_fetch(ticker, date, limit, trade_number, retry_count=3):
            return {
                "success": True,
                "data": [{"trade_number": 1, "time": "15:00:00"}],
                "count": 1,
            }

        self.client._fetch_page = fake_fetch
        r = self.client.fetch_running_trade(
            "BBCA", "2024-01-02", limit=50, retry_count=1, cancel_check=None
        )
        self.assertTrue(r.get("success"))
        self.assertEqual(r.get("end_reason"), FetchEndReason.OK_SHORT_PAGE)
        self.assertIn(r.get("end_reason"), NORMAL_FETCH_END_REASONS)
        self.assertEqual(r.get("partial"), False)

    def test_empty_first_page(self):
        def fake_fetch(ticker, date, limit, trade_number, retry_count=3):
            return {"success": True, "data": [], "count": 0}

        self.client._fetch_page = fake_fetch
        r = self.client.fetch_running_trade(
            "BBCA", "2024-01-02", limit=50, retry_count=1, cancel_check=None
        )
        self.assertTrue(r.get("success"))
        self.assertEqual(r.get("end_reason"), FetchEndReason.OK_EMPTY_PAGE)
        self.assertIn(r.get("end_reason"), NORMAL_FETCH_END_REASONS)
