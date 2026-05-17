"""Tests for 429 handling, task state transitions, and cooldown behavior."""
import unittest
from unittest.mock import MagicMock, patch

from stockbit_client import StockbitClient, FetchEndReason, parse_retry_after_header
from jobs import (
    JobManager,
    Job,
    Task,
    JobStatus,
    TaskStatus,
    DEFER_REASON_RATE_LIMIT,
)
from database import JobDatabase


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
        def fake_fetch(ticker, date, limit, trade_number, retry_count=3):
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


class JobManagerBase(unittest.TestCase):
    def make_manager(self):
        client = MagicMock()
        storage = MagicMock()
        storage.get_filename.return_value = "dummy.csv"
        storage.reset_task_stage.return_value = None
        storage.discard_task_stage.return_value = None
        storage.append_task_trades.return_value = {"success": True, "rows_written": 0}
        storage.finalize_task_trades.return_value = {"success": True, "rows_written": 0}
        return JobManager(client, storage)

    def make_job(self, **overrides):
        defaults = dict(
            job_id="j1",
            tickers=["A"],
            from_date="2024-01-01",
            until_date="2024-01-03",
            delay_seconds=0,
            limit=50,
            parallel_workers=1,
            status=JobStatus.RUNNING,
            tasks=[
                Task(ticker="A", date="2024-01-01"),
                Task(ticker="A", date="2024-01-02"),
                Task(ticker="A", date="2024-01-03"),
            ],
        )
        defaults.update(overrides)
        return Job(**defaults)


class TestJobManager429(JobManagerBase):
    def test_rate_limit_defers_task_and_preserves_attempt_counter(self):
        jm = self.make_manager()
        jm.client.fetch_running_trade.return_value = {
            "success": False,
            "error": "Rate limited (429)",
            "rate_limited": True,
            "retry_after_seconds": 12.0,
        }
        job = self.make_job(tasks=[Task(ticker="A", date="2024-01-01")])
        jm._process_task(job, job.tasks[0], "worker-1")
        self.assertEqual(job.tasks[0].status, TaskStatus.DEFERRED)
        self.assertEqual(job.tasks[0].defer_reason, DEFER_REASON_RATE_LIMIT)
        self.assertIsNotNone(job.tasks[0].retry_after_at)
        self.assertEqual(job.tasks[0].attempts, 0)
        self.assertEqual(job.tasks[0].rate_limit_count, 1)

    def test_default_behavior_allows_later_task(self):
        jm = self.make_manager()
        first = Task(
            ticker="A",
            date="2024-01-01",
            status=TaskStatus.DEFERRED,
            defer_reason=DEFER_REASON_RATE_LIMIT,
            retry_after_at="2999-01-01T00:00:00+07:00",
        )
        second = Task(ticker="A", date="2024-01-02")
        job = self.make_job(tasks=[first, second])
        tasks = jm._dispatchable_tasks(job, {}, 1)
        self.assertEqual([t.date for t in tasks], ["2024-01-02"])

    def test_same_ticker_inflight_blocks_parallel_dispatch(self):
        jm = self.make_manager()
        running = Task(ticker="A", date="2024-01-01", status=TaskStatus.RUNNING)
        pending = Task(ticker="A", date="2024-01-02")
        job = self.make_job(tasks=[running, pending])
        inflight = {object(): running}
        tasks = jm._dispatchable_tasks(job, inflight, 1)
        self.assertEqual(tasks, [])

    def test_different_tickers_can_dispatch_in_parallel(self):
        jm = self.make_manager()
        first = Task(ticker="A", date="2024-01-01")
        second = Task(ticker="B", date="2024-01-01")
        job = self.make_job(tickers=["A", "B"], tasks=[first, second])
        tasks = jm._dispatchable_tasks(job, {}, 2)
        self.assertEqual([(t.ticker, t.date) for t in tasks], [("A", "2024-01-01"), ("B", "2024-01-01")])

    def test_pause_on_rate_limit_blocks_new_dispatch_during_cooldown(self):
        jm = self.make_manager()
        first = Task(
            ticker="A",
            date="2024-01-01",
            status=TaskStatus.DEFERRED,
            defer_reason=DEFER_REASON_RATE_LIMIT,
            retry_after_at="2999-01-01T00:00:00+07:00",
        )
        second = Task(ticker="A", date="2024-01-02")
        other = Task(ticker="B", date="2024-01-01")
        job = self.make_job(
            tickers=["A", "B"],
            pause_on_rate_limit=True,
            rate_limit_pause_until="2999-01-01T00:00:00+07:00",
            tasks=[first, second, other],
        )
        tasks = jm._dispatchable_tasks(job, {}, 2)
        self.assertEqual(tasks, [])

    def test_expired_job_cooldown_clears_and_dispatches(self):
        jm = self.make_manager()
        second = Task(ticker="A", date="2024-01-02")
        job = self.make_job(
            pause_on_rate_limit=True,
            rate_limit_pause_until="2000-01-01T00:00:00+07:00",
            tasks=[second],
        )
        with patch.object(jm, '_persist_job') as persist_job:
            tasks = jm._dispatchable_tasks(job, {}, 1)
        self.assertEqual([t.date for t in tasks], ["2024-01-02"])
        self.assertIsNone(job.rate_limit_pause_until)
        persist_job.assert_called_once_with(job)

    def test_due_deferred_task_becomes_pending(self):
        jm = self.make_manager()
        task = Task(
            ticker="A",
            date="2024-01-01",
            status=TaskStatus.DEFERRED,
            defer_reason=DEFER_REASON_RATE_LIMIT,
            retry_after_at="2000-01-01T00:00:00+07:00",
        )
        job = self.make_job(tasks=[task])
        jm._promote_due_tasks(job)
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertIsNone(task.retry_after_at)

    def test_allocate_worker_id_uses_free_slot_not_future_count(self):
        self.assertEqual(
            JobManager._allocate_worker_id(3, ['worker-1', 'worker-3']),
            'worker-2',
        )
        self.assertEqual(
            JobManager._allocate_worker_id(3, ['worker-2']),
            'worker-1',
        )

    def test_requires_login_blocks_task_and_triggers_auto_refresh(self):
        jm = self.make_manager()
        refresh_calls = []
        jm.set_auto_refresh_callback(lambda job_id, ticker, date: refresh_calls.append((job_id, ticker, date)))
        jm.client.fetch_running_trade.return_value = {
            "success": False,
            "requires_login": True,
            "error": "Token expired",
        }
        job = self.make_job(tasks=[Task(ticker="A", date="2024-01-01")])
        jm._process_task(job, job.tasks[0], "worker-1")
        self.assertEqual(job.status, JobStatus.PAUSED)
        self.assertEqual(job.tasks[0].status, TaskStatus.BLOCKED)
        self.assertEqual(job.tasks[0].blocked_reason, "token_refresh")
        self.assertEqual(refresh_calls, [("j1", "A", "2024-01-01")])

    def test_auto_resume_unblocks_token_refresh_tasks(self):
        jm = self.make_manager()
        task = Task(
            ticker="A",
            date="2024-01-01",
            status=TaskStatus.BLOCKED,
            blocked_reason="token_refresh",
        )
        job = self.make_job(tasks=[task], status=JobStatus.PAUSED)
        jm.jobs[job.job_id] = job
        with patch.object(jm, 'start_worker'):
            resumed = jm.auto_resume_paused_jobs()
        self.assertEqual(resumed, 1)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(task.status, TaskStatus.PENDING)

    def test_legacy_waiting_retry_loads_as_deferred(self):
        fake_jobs = [{
            'job_id': 'legacy',
            'tickers': ['A'],
            'from_date': '2024-01-01',
            'until_date': '2024-01-01',
            'delay_seconds': 0,
            'limit_per_request': 50,
            'parallel_workers': 1,
            'max_backoff_seconds': 180,
            'pause_on_rate_limit': 0,
            'rate_limit_pause_until': None,
            'status': 'PAUSED',
            'created_at': '2024-01-01T00:00:00+07:00',
            'start_time': None,
            'end_time': None,
            'error': None,
        }]
        fake_tasks = [{
            'job_id': 'legacy',
            'ticker': 'A',
            'date': '2024-01-01',
            'status': 'WAITING_RETRY',
            'error': 'Rate limited (429)',
            'records_fetched': 0,
            'attempts': 2,
            'pages_fetched': 0,
            'current_page': 0,
            'end_reason': None,
            'skip_reason': None,
            'retry_after_at': None,
            'defer_reason': None,
            'blocked_reason': None,
            'rate_limit_count': 0,
            'last_error_at': None,
            'updated_at': None,
            'active_worker_id': None,
        }]
        with patch.object(JobDatabase, 'get_all_jobs', return_value=fake_jobs), \
             patch.object(JobDatabase, 'get_job_tasks', return_value=fake_tasks):
            jm = JobManager(MagicMock(), MagicMock())
        task = jm.jobs['legacy'].tasks[0]
        self.assertEqual(task.status, TaskStatus.DEFERRED)
        self.assertEqual(task.defer_reason, DEFER_REASON_RATE_LIMIT)

    def test_rate_limit_can_activate_job_pause_window(self):
        jm = self.make_manager()
        jm.client.fetch_running_trade.return_value = {
            "success": False,
            "error": "Rate limited (429)",
            "rate_limited": True,
            "retry_after_seconds": 12.0,
        }
        job = self.make_job(
            pause_on_rate_limit=True,
            tasks=[Task(ticker="A", date="2024-01-01")],
        )
        jm._process_task(job, job.tasks[0], "worker-1")
        self.assertEqual(job.tasks[0].status, TaskStatus.DEFERRED)
        self.assertIsNotNone(job.rate_limit_pause_until)

    def test_rate_limit_partial_progress_is_preserved_for_resume(self):
        jm = self.make_manager()
        jm.client.fetch_running_trade.return_value = {
            "success": False,
            "error": "Rate limited (429)",
            "rate_limited": True,
            "retry_after_seconds": 12.0,
            "count": 2100,
            "checkpoint_pages_fetched": 42,
            "checkpoint_records_fetched": 2100,
            "resume_trade_number": 2384900,
            "page1_fingerprint": "fp-1",
            "partial": True,
        }
        task = Task(ticker="A", date="2024-01-01")
        task.resume_trade_number = 2384900
        task.checkpoint_pages_fetched = 42
        task.checkpoint_records_fetched = 2100
        task.page1_fingerprint = "fp-1"
        job = self.make_job(tasks=[task])
        jm._process_task(job, job.tasks[0], "worker-1")
        self.assertEqual(job.tasks[0].status, TaskStatus.DEFERRED)
        self.assertEqual(job.tasks[0].resume_trade_number, 2384900)
        self.assertEqual(job.tasks[0].checkpoint_pages_fetched, 42)
        self.assertEqual(job.tasks[0].checkpoint_records_fetched, 2100)

    def test_regressed_completion_is_retried_not_completed(self):
        jm = self.make_manager()
        jm.client.fetch_running_trade.return_value = {
            "success": True,
            "count": 50,
            "pages_fetched": 1,
            "successful_pages_fetched": 1,
            "end_reason": FetchEndReason.OK_EMPTY_PAGE,
            "page1_fingerprint": "fp-1",
        }
        task = Task(ticker="A", date="2024-01-01")
        task.resume_trade_number = 123
        task.checkpoint_pages_fetched = 42
        task.checkpoint_records_fetched = 2100
        task.page1_fingerprint = "fp-1"
        job = self.make_job(tasks=[task])
        jm._process_task(job, job.tasks[0], "worker-1")
        self.assertEqual(job.tasks[0].status, TaskStatus.DEFERRED)
        self.assertIn("checkpoint 2100", job.tasks[0].error)
        self.assertEqual(job.tasks[0].defer_reason, "cursor_regression")
