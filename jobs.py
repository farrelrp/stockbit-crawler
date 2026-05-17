"""
Job scheduler and manager for fetching trade data.
"""
import json
import os
import threading
import time
import uuid
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, asdict, field, fields
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, Future
import logging

from database import JobDatabase
from tz import now_wib
from stockbit_client import FetchEndReason, NORMAL_FETCH_END_REASONS
from config import (
    DEFAULT_LIMIT,
    RATE_LIMIT_FALLBACK_SECONDS,
    RATE_LIMIT_MIN_SECONDS,
    RATE_LIMIT_MAX_SECONDS,
)

logger = logging.getLogger(__name__)

DEFER_REASON_RATE_LIMIT = 'rate_limit'
DEFER_REASON_ERROR_BACKOFF = 'error_backoff'
DEFER_REASON_MANUAL_DELAY = 'manual_delay'

BLOCKED_REASON_TOKEN_REFRESH = 'token_refresh'
BLOCKED_REASON_CAPTCHA_REQUIRED = 'captcha_required'
BLOCKED_REASON_MANUAL_PAUSE = 'manual_pause'
DEFER_REASON_CURSOR_REGRESSION = 'cursor_regression'

_MIN_BACKOFF_CAP = 5.0
_MAX_BACKOFF_CAP = 24 * 3600.0


def _load_holidays() -> Dict[str, str]:
    """Map YYYY-MM-DD -> label from holiday.json (same folder as this module)."""
    path = os.path.join(os.path.dirname(__file__), 'holiday.json')
    try:
        with open(path, encoding='utf-8') as f:
            return {h['date']: h['reason'] for h in json.load(f)}
    except Exception as e:
        logger.warning("Could not load holiday.json: %s", e)
        return {}


_HOLIDAYS: Dict[str, str] = _load_holidays()


def _get_skip_reason(date_str: str) -> Optional[str]:
    d = datetime.strptime(date_str, '%Y-%m-%d')
    if d.weekday() >= 5:
        return 'Weekend'
    return _HOLIDAYS.get(date_str)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class JobStatus(Enum):
    QUEUED = 'QUEUED'
    RUNNING = 'RUNNING'
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


class TaskStatus(Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'
    DEFERRED = 'DEFERRED'
    BLOCKED = 'BLOCKED'


@dataclass
class Task:
    ticker: str
    date: str
    status: TaskStatus = TaskStatus.PENDING
    error: Optional[str] = None
    end_reason: Optional[str] = None
    records_fetched: int = 0
    pages_fetched: int = 0
    current_page: int = 0
    attempts: int = 0
    skip_reason: Optional[str] = None
    retry_after_at: Optional[str] = None
    defer_reason: Optional[str] = None
    blocked_reason: Optional[str] = None
    rate_limit_count: int = 0
    last_error_at: Optional[str] = None
    updated_at: Optional[str] = None
    active_worker_id: Optional[str] = None
    resume_trade_number: Optional[int] = None
    checkpoint_pages_fetched: int = 0
    checkpoint_records_fetched: int = 0
    checkpoint_first_trade_number: Optional[int] = None
    checkpoint_last_trade_number: Optional[int] = None
    checkpoint_first_time: Optional[str] = None
    checkpoint_last_time: Optional[str] = None
    page1_fingerprint: Optional[str] = None


@dataclass
class Job:
    job_id: str
    tickers: List[str]
    from_date: str
    until_date: str
    delay_seconds: float
    limit: int
    parallel_workers: int = 1
    max_backoff_seconds: float = 180.0
    pause_on_rate_limit: bool = False
    rate_limit_pause_until: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: now_wib().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    tasks: List[Task] = field(default_factory=list)
    last_milestone_pct: int = 0
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    active_workers: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)
    refresh_requested: bool = field(default=False, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        skip = frozenset({
            'progress_lock',
            'last_milestone_pct',
            'refresh_requested',
        })
        data: Dict[str, Any] = {}
        for f in fields(self):
            if f.name in skip:
                continue
            if f.name == 'tasks':
                data['tasks'] = []
                for t in self.tasks:
                    td = asdict(t)
                    td['status'] = t.status.value
                    if t.retry_after_at:
                        retry_after = _parse_iso(t.retry_after_at)
                        if retry_after:
                            remaining = (retry_after - now_wib()).total_seconds()
                            if remaining > 0:
                                td['retry_after_seconds_remaining'] = round(remaining, 1)
                    data['tasks'].append(td)
                continue
            if f.name == 'active_workers':
                workers = list(self.active_workers.values())
                workers.sort(key=lambda item: item.get('worker_id', ''))
                data['active_workers'] = workers
                continue
            value = getattr(self, f.name)
            if f.name == 'status':
                data['status'] = value.value if isinstance(value, JobStatus) else value
            elif f.name == 'rate_limit_pause_until':
                data[f.name] = value
                if value:
                    pause_until = _parse_iso(value)
                    if pause_until:
                        remaining = (pause_until - now_wib()).total_seconds()
                        if remaining > 0:
                            data['rate_limit_pause_seconds_remaining'] = round(remaining, 1)
            else:
                data[f.name] = value
        return data

    def get_progress(self) -> Dict[str, Any]:
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
        failed = sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)
        running = sum(1 for t in self.tasks if t.status == TaskStatus.RUNNING)
        deferred = sum(1 for t in self.tasks if t.status == TaskStatus.DEFERRED)
        blocked = sum(1 for t in self.tasks if t.status == TaskStatus.BLOCKED)
        pending = sum(1 for t in self.tasks if t.status == TaskStatus.PENDING)
        return {
            'total': total,
            'completed': completed,
            'failed': failed,
            'running': running,
            'deferred': deferred,
            'blocked': blocked,
            'pending': pending,
            'percentage': round((completed / total * 100) if total > 0 else 0, 1),
        }


class JobManager:
    """Manages job queue and execution."""

    def __init__(self, stockbit_client, csv_storage):
        self.client = stockbit_client
        self.storage = csv_storage
        self.jobs: Dict[str, Job] = {}
        self.current_job_id: Optional[str] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.db = JobDatabase()
        self._next_job_id: Optional[str] = None
        self._notification_callback = None
        self._auto_refresh_callback: Optional[Callable[[str, str, str], Any]] = None
        self._job_rate_limit_streaks: Dict[str, int] = {}
        self._load_jobs_from_db()

    def set_notification_callback(self, callback):
        self._notification_callback = callback

    def set_auto_refresh_callback(self, callback: Callable[[str, str, str], Any]):
        self._auto_refresh_callback = callback

    def _notify(self, event: str, data: Dict[str, Any]):
        if self._notification_callback:
            try:
                self._notification_callback(event, data)
            except Exception as e:
                logger.error("Notification callback error (%s): %s", event, e)

    def _load_jobs_from_db(self):
        try:
            db_jobs = self.db.get_all_jobs(limit=100)
            loaded_count = 0
            skip_statuses = {'COMPLETED', 'CANCELLED'}
            for job_data in db_jobs:
                if job_data['status'] in skip_statuses:
                    continue
                tasks = self._build_tasks_for_job(job_data['tickers'], job_data['from_date'], job_data['until_date'])
                job = Job(
                    job_id=job_data['job_id'],
                    tickers=job_data['tickers'],
                    from_date=job_data['from_date'],
                    until_date=job_data['until_date'],
                    delay_seconds=job_data['delay_seconds'],
                    limit=DEFAULT_LIMIT,
                    parallel_workers=int(job_data.get('parallel_workers', 1) or 1),
                    max_backoff_seconds=float(job_data.get('max_backoff_seconds', 180) or 180),
                    pause_on_rate_limit=bool(
                        job_data.get('pause_on_rate_limit', 0)
                    ) or (job_data.get('rate_limit_policy') == 'strict_fifo'),
                    rate_limit_pause_until=job_data.get('rate_limit_pause_until'),
                    status=JobStatus(job_data['status']) if job_data['status'] in JobStatus._value2member_map_ else JobStatus.QUEUED,
                    created_at=job_data['created_at'],
                    started_at=job_data.get('start_time'),
                    completed_at=job_data.get('end_time'),
                    error=job_data.get('error'),
                    tasks=tasks,
                )

                saved_tasks = self.db.get_job_tasks(job.job_id)
                if saved_tasks:
                    key_to_row = {(row['ticker'], row['date']): row for row in saved_tasks}
                    for task in job.tasks:
                        row = key_to_row.get((task.ticker, task.date))
                        if not row:
                            continue
                        status = row.get('status') or 'PENDING'
                        if status == 'WAITING_RETRY':
                            status = 'DEFERRED'
                        if status not in TaskStatus._value2member_map_:
                            logger.warning(
                                "Unknown persisted task status %r for %s %s in job %s; keeping as DEFERRED",
                                status,
                                task.ticker,
                                task.date,
                                job.job_id,
                            )
                            status = 'DEFERRED'
                        task.status = TaskStatus(status)
                        task.error = row.get('error')
                        task.records_fetched = row.get('records_fetched') or 0
                        task.attempts = row.get('attempts') or 0
                        task.pages_fetched = row.get('pages_fetched') or 0
                        task.current_page = row.get('current_page') or 0
                        task.end_reason = row.get('end_reason')
                        task.skip_reason = task.skip_reason or row.get('skip_reason')
                        task.retry_after_at = row.get('retry_after_at')
                        task.defer_reason = row.get('defer_reason') or self._infer_defer_reason(row.get('error'))
                        task.blocked_reason = row.get('blocked_reason')
                        task.rate_limit_count = row.get('rate_limit_count') or 0
                        task.last_error_at = row.get('last_error_at')
                        task.updated_at = row.get('updated_at')
                        task.active_worker_id = None
                        task.resume_trade_number = row.get('resume_trade_number')
                        task.checkpoint_pages_fetched = row.get('checkpoint_pages_fetched') or 0
                        task.checkpoint_records_fetched = row.get('checkpoint_records_fetched') or 0
                        task.checkpoint_first_trade_number = row.get('checkpoint_first_trade_number')
                        task.checkpoint_last_trade_number = row.get('checkpoint_last_trade_number')
                        task.checkpoint_first_time = row.get('checkpoint_first_time')
                        task.checkpoint_last_time = row.get('checkpoint_last_time')
                        task.page1_fingerprint = row.get('page1_fingerprint')

                for task in job.tasks:
                    skip_reason = _get_skip_reason(task.date)
                    if skip_reason:
                        task.skip_reason = skip_reason if task.status == TaskStatus.SKIPPED else task.skip_reason
                        if task.status == TaskStatus.PENDING:
                            task.status = TaskStatus.SKIPPED
                            task.skip_reason = skip_reason

                if job.status == JobStatus.RUNNING:
                    job.status = JobStatus.PAUSED
                    for task in job.tasks:
                        if task.status == TaskStatus.RUNNING:
                            task.status = TaskStatus.BLOCKED
                            task.blocked_reason = BLOCKED_REASON_MANUAL_PAUSE
                            task.active_worker_id = None
                    self._persist_job(job)

                self.jobs[job.job_id] = job
                loaded_count += 1

            logger.info("Loaded %s pending jobs from database", loaded_count)
        except Exception as e:
            logger.error("Failed to load jobs from database: %s", e)

    def _build_tasks_for_job(self, tickers: List[str], from_date: str, until_date: str) -> List[Task]:
        start = datetime.strptime(from_date, '%Y-%m-%d')
        end = datetime.strptime(until_date, '%Y-%m-%d')
        dates: List[str] = []
        current = start
        while current <= end:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        tasks: List[Task] = []
        for ticker in tickers:
            for date in dates:
                skip_reason = _get_skip_reason(date)
                if skip_reason:
                    tasks.append(Task(
                        ticker=ticker,
                        date=date,
                        status=TaskStatus.SKIPPED,
                        skip_reason=skip_reason,
                    ))
                else:
                    tasks.append(Task(ticker=ticker, date=date))
        return tasks

    def _persist_job(self, job: Job):
        try:
            progress = job.get_progress()
            job_data = {
                'job_id': job.job_id,
                'tickers': job.tickers,
                'from_date': job.from_date,
                'until_date': job.until_date,
                'delay_seconds': job.delay_seconds,
                'limit': job.limit,
                'parallel_workers': job.parallel_workers,
                'max_backoff_seconds': job.max_backoff_seconds,
                'pause_on_rate_limit': job.pause_on_rate_limit,
                'rate_limit_pause_until': job.rate_limit_pause_until,
                'status': job.status.value,
                'created_at': job.created_at,
                'start_time': job.started_at,
                'end_time': job.completed_at,
                'total_tasks': progress['total'],
                'completed_tasks': progress['completed'],
                'failed_tasks': progress['failed'],
                'total_records': sum(t.records_fetched for t in job.tasks),
                'error': job.error,
            }
            self.db.save_job(job_data)
        except Exception as e:
            logger.error("Failed to persist job %s: %s", job.job_id, e)

    def _save_task_row(self, job: Job, task: Task) -> None:
        task.updated_at = now_wib().isoformat()
        self.db.save_task(job.job_id, {
            'ticker': task.ticker,
            'date': task.date,
            'status': task.status.value,
            'error': task.error,
            'records_fetched': task.records_fetched,
            'attempts': task.attempts,
            'pages_fetched': task.pages_fetched,
            'current_page': task.current_page,
            'end_reason': task.end_reason,
            'skip_reason': task.skip_reason,
            'retry_after_at': task.retry_after_at,
            'defer_reason': task.defer_reason,
            'blocked_reason': task.blocked_reason,
            'rate_limit_count': task.rate_limit_count,
            'last_error_at': task.last_error_at,
            'updated_at': task.updated_at,
            'active_worker_id': task.active_worker_id,
            'resume_trade_number': task.resume_trade_number,
            'checkpoint_pages_fetched': task.checkpoint_pages_fetched,
            'checkpoint_records_fetched': task.checkpoint_records_fetched,
            'checkpoint_first_trade_number': task.checkpoint_first_trade_number,
            'checkpoint_last_trade_number': task.checkpoint_last_trade_number,
            'checkpoint_first_time': task.checkpoint_first_time,
            'checkpoint_last_time': task.checkpoint_last_time,
            'page1_fingerprint': task.page1_fingerprint,
        })

    def _infer_defer_reason(self, error: Optional[str]) -> Optional[str]:
        if not error:
            return None
        lower = error.lower()
        if 'rate limit' in lower or '429' in lower:
            return DEFER_REASON_RATE_LIMIT
        if 'regressed' in lower or 'checkpoint' in lower:
            return DEFER_REASON_CURSOR_REGRESSION
        return DEFER_REASON_ERROR_BACKOFF

    def _mark_task_pending(self, job: Job, task: Task) -> None:
        task.status = TaskStatus.PENDING
        task.retry_after_at = None
        task.defer_reason = None
        task.blocked_reason = None
        task.current_page = 0
        task.active_worker_id = None
        self._save_task_row(job, task)

    @staticmethod
    def _clear_task_checkpoint(task: Task) -> None:
        task.resume_trade_number = None
        task.checkpoint_pages_fetched = 0
        task.checkpoint_records_fetched = 0
        task.checkpoint_first_trade_number = None
        task.checkpoint_last_trade_number = None
        task.checkpoint_first_time = None
        task.checkpoint_last_time = None
        task.page1_fingerprint = None

    def _reset_task_progress(self, job: Job, task: Task) -> None:
        task.records_fetched = 0
        task.pages_fetched = 0
        task.current_page = 0
        task.end_reason = None
        self._clear_task_checkpoint(task)
        self.storage.discard_task_stage(job.job_id, task.ticker, task.date)

    def _mark_task_deferred(
        self,
        job: Job,
        task: Task,
        *,
        delay_seconds: float,
        reason: str,
        error_message: str,
        count_rate_limit: bool = False,
    ) -> None:
        if count_rate_limit:
            task.rate_limit_count += 1
        task.status = TaskStatus.DEFERRED
        task.defer_reason = reason
        task.blocked_reason = None
        task.current_page = 0
        task.active_worker_id = None
        task.retry_after_at = (now_wib() + timedelta(seconds=delay_seconds)).isoformat()
        task.error = error_message
        task.last_error_at = now_wib().isoformat()
        self._save_task_row(job, task)

    def _mark_task_blocked(self, job: Job, task: Task, *, reason: str, error_message: str) -> None:
        task.status = TaskStatus.BLOCKED
        task.blocked_reason = reason
        task.defer_reason = None
        task.retry_after_at = None
        task.current_page = 0
        task.active_worker_id = None
        task.error = error_message
        task.last_error_at = now_wib().isoformat()
        self._save_task_row(job, task)

    def _promote_due_tasks(self, job: Job) -> None:
        now_dt = now_wib()
        for task in job.tasks:
            if task.status != TaskStatus.DEFERRED or not task.retry_after_at:
                continue
            retry_at = _parse_iso(task.retry_after_at)
            if retry_at and retry_at <= now_dt:
                task.status = TaskStatus.PENDING
                task.retry_after_at = None
                task.active_worker_id = None
                self._save_task_row(job, task)

    def _task_ready_to_run(self, task: Task) -> bool:
        return task.status == TaskStatus.PENDING

    def _task_ready_to_resume(self, task: Task) -> bool:
        return task.status == TaskStatus.RUNNING and not task.active_worker_id

    def _job_has_open_tasks(self, job: Job) -> bool:
        return any(
            task.status in (
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.DEFERRED,
                TaskStatus.BLOCKED,
            )
            for task in job.tasks
        )

    def _job_has_pending_tasks_any(self, job: Job) -> bool:
        return any(task.status == TaskStatus.PENDING for task in job.tasks)

    def _next_retry_deadline(self, job: Job) -> Optional[datetime]:
        retry_times = [
            _parse_iso(task.retry_after_at)
            for task in job.tasks
            if task.status == TaskStatus.DEFERRED and task.retry_after_at
        ]
        retry_times = [item for item in retry_times if item is not None]
        return min(retry_times) if retry_times else None

    def _current_rate_limit_pause_until(self, job: Job) -> Optional[datetime]:
        if not job.rate_limit_pause_until:
            return None
        pause_until = _parse_iso(job.rate_limit_pause_until)
        if not pause_until:
            return None
        if pause_until <= now_wib():
            job.rate_limit_pause_until = None
            self._persist_job(job)
            return None
        return pause_until

    def _dispatchable_tasks(
        self,
        job: Job,
        inflight: Dict[Future, Task],
        capacity: int,
    ) -> List[Task]:
        self._promote_due_tasks(job)
        if capacity <= 0:
            return []
        if self._current_rate_limit_pause_until(job):
            return []

        inflight_tasks = list(inflight.values())
        resumable = [
            task for task in job.tasks
            if self._task_ready_to_resume(task) and task not in inflight_tasks
        ]
        if resumable:
            return resumable[:capacity]

        chosen: List[Task] = []
        for task in job.tasks:
            if len(chosen) >= capacity:
                break
            if not self._task_ready_to_run(task):
                continue
            if task in inflight_tasks or task in chosen:
                continue
            chosen.append(task)
        return chosen

    def _maybe_fire_progress_milestones(self, job: Job) -> None:
        with job.progress_lock:
            progress = job.get_progress()
            pct = progress['percentage']
            for milestone in (25, 50, 75):
                if pct >= milestone and job.last_milestone_pct < milestone:
                    job.last_milestone_pct = milestone
                    self._notify('job_progress', {
                        'job_id': job.job_id,
                        'tickers': job.tickers,
                        'percentage': pct,
                        'completed': progress['completed'],
                        'total': progress['total'],
                        'failed': progress['failed'],
                    })

    def _is_job_cancelled(self, job: Job) -> bool:
        return job.status in (JobStatus.CANCELLED, JobStatus.FAILED)

    def _pick_next_queued_job(self) -> Optional[Job]:
        queued = [j for j in self.jobs.values() if j.status == JobStatus.QUEUED]
        if not queued:
            return None
        queued.sort(key=lambda j: j.created_at)
        return queued[0]

    def create_job(
        self,
        tickers: List[str],
        from_date: str,
        until_date: str,
        delay_seconds: float = 3.0,
        limit: int = DEFAULT_LIMIT,
        parallel_workers: int = 1,
        max_backoff_seconds: float = 180.0,
        pause_on_rate_limit: bool = False,
    ) -> str:
        job_id = str(uuid.uuid4())
        mb = max(_MIN_BACKOFF_CAP, min(float(max_backoff_seconds), _MAX_BACKOFF_CAP))
        job = Job(
            job_id=job_id,
            tickers=tickers,
            from_date=from_date,
            until_date=until_date,
            delay_seconds=delay_seconds,
            limit=DEFAULT_LIMIT,
            parallel_workers=parallel_workers,
            max_backoff_seconds=mb,
            pause_on_rate_limit=bool(pause_on_rate_limit),
            tasks=self._build_tasks_for_job(tickers, from_date, until_date),
        )
        self.jobs[job_id] = job
        self._persist_job(job)
        for task in job.tasks:
            self._save_task_row(job, task)
        logger.info("Created job %s with %s tasks", job_id, len(job.tasks))
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.start_worker()
        return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
        return [job.to_dict() for job in jobs]

    def pause_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if job and job.status == JobStatus.RUNNING:
            job.status = JobStatus.PAUSED
            self.pause_flag.set()
            logger.info("Job %s paused", job_id)

    def resume_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.PAUSED:
            return
        if self.current_job_id == job_id:
            job.status = JobStatus.RUNNING
        else:
            job.status = JobStatus.QUEUED
        job.refresh_requested = False
        for task in job.tasks:
            if task.status == TaskStatus.BLOCKED:
                self._mark_task_pending(job, task)
        self.pause_flag.clear()
        self._persist_job(job)
        logger.info("Job %s resumed", job_id)
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()

    def play_queued_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.QUEUED:
            return False
        self._next_job_id = job_id
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()
        return True

    def auto_resume_paused_jobs(self):
        resumed_count = 0
        for job in self.jobs.values():
            if job.status != JobStatus.PAUSED:
                continue
            has_auth_block = any(
                task.status == TaskStatus.BLOCKED and task.blocked_reason == BLOCKED_REASON_TOKEN_REFRESH
                for task in job.tasks
            )
            if not has_auth_block:
                continue
            self.resume_job(job.job_id)
            resumed_count += 1
        if resumed_count:
            logger.info("[OK] Auto-resumed %s paused job(s) after token refresh", resumed_count)
        return resumed_count

    def cancel_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if not job:
            return
        job.status = JobStatus.CANCELLED
        job.error = 'Cancelled by user'
        self._persist_job(job)
        self.pause_flag.set()
        if not any(j.status == JobStatus.PAUSED for j in self.jobs.values()):
            self.pause_flag.clear()
        logger.info("Job %s cancelled", job_id)

    def delete_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status not in [JobStatus.FAILED, JobStatus.PAUSED, JobStatus.CANCELLED]:
            return False
        deleted = self.db.delete_job(job_id)
        if not deleted:
            return False
        self.jobs.pop(job_id, None)
        if self.current_job_id == job_id:
            self.current_job_id = None
        logger.info("Deleted job %s", job_id)
        return True

    def retry_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.FAILED:
            return False
        for task in job.tasks:
            if task.status in (TaskStatus.FAILED, TaskStatus.DEFERRED, TaskStatus.BLOCKED):
                self._reset_task_progress(job, task)
                task.error = None
                self._mark_task_pending(job, task)
        job.status = JobStatus.QUEUED
        job.error = None
        job.completed_at = None
        job.last_milestone_pct = 0
        self._persist_job(job)
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()
        return True

    def _find_task(self, job: Job, ticker: str, date: str) -> Optional[Task]:
        for task in job.tasks:
            if task.ticker == ticker and task.date == date:
                return task
        return None

    def task_action(
        self,
        job_id: str,
        ticker: str,
        date: str,
        action: str,
        delay_seconds: Optional[float] = None,
    ) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        task = self._find_task(job, ticker, date)
        if not task:
            return False
        if action == 'retry_now':
            self._reset_task_progress(job, task)
            task.error = None
            self._mark_task_pending(job, task)
        elif action == 'retry_after':
            if delay_seconds is None:
                return False
            self._mark_task_deferred(
                job,
                task,
                delay_seconds=max(1.0, float(delay_seconds)),
                reason=DEFER_REASON_MANUAL_DELAY,
                error_message=f"Manual retry scheduled in {float(delay_seconds):.0f}s...",
            )
        elif action == 'skip':
            self._reset_task_progress(job, task)
            task.status = TaskStatus.SKIPPED
            task.skip_reason = 'User skipped'
            task.blocked_reason = None
            task.defer_reason = None
            task.retry_after_at = None
            task.active_worker_id = None
            self._save_task_row(job, task)
        else:
            return False

        if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
            job.status = JobStatus.QUEUED
            job.completed_at = None
            job.error = None
        self._persist_job(job)
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()
        return True

    def retry_task(self, job_id: str, ticker: str, date: str) -> bool:
        return self.task_action(job_id, ticker, date, 'retry_now')

    def start_worker(self):
        self.stop_flag.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Job worker started")

    def stop_worker(self):
        self.stop_flag.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        logger.info("Job worker stopped")

    def _worker_loop(self):
        while not self.stop_flag.is_set():
            try:
                next_job = None
                if self._next_job_id:
                    prioritized = self.jobs.get(self._next_job_id)
                    if prioritized and prioritized.status == JobStatus.QUEUED:
                        next_job = prioritized
                    self._next_job_id = None
                if not next_job:
                    next_job = self._pick_next_queued_job()
                if next_job:
                    self._process_job(next_job)
                else:
                    time.sleep(1)
            except Exception as e:
                logger.critical("Job worker loop crashed (will retry in 5s): %s", e, exc_info=True)
                time.sleep(5)

    def _assign_worker(self, job: Job, worker_id: str, task: Task) -> None:
        started_at = now_wib().isoformat()
        with job.progress_lock:
            job.active_workers[worker_id] = {
                'worker_id': worker_id,
                'ticker': task.ticker,
                'date': task.date,
                'attempt': task.attempts,
                'current_page': task.current_page,
                'started_at': started_at,
            }
        task.active_worker_id = worker_id

    def _release_worker(self, job: Job, worker_id: str) -> None:
        with job.progress_lock:
            job.active_workers.pop(worker_id, None)

    def _touch_worker_page(self, job: Job, worker_id: str, page: int, task: Task) -> None:
        with job.progress_lock:
            info = job.active_workers.get(worker_id)
            if info:
                info['current_page'] = page
                info['attempt'] = task.attempts

    @staticmethod
    def _allocate_worker_id(total_workers: int, reserved_worker_ids: List[str]) -> str:
        """Pick the lowest free worker slot from the configured pool."""
        reserved = set(reserved_worker_ids)
        for index in range(1, total_workers + 1):
            worker_id = f"worker-{index}"
            if worker_id not in reserved:
                return worker_id
        # Should not happen when capacity is enforced, but keep a deterministic fallback.
        return f"worker-{total_workers}"

    def _pause_job_notify_once(
        self,
        job: Job,
        *,
        telegram_reason: str,
        task: Task,
        task_error_msg: str,
        blocked_reason: str,
        log_msg: str,
        trigger_refresh: bool = False,
    ) -> None:
        should_notify = False
        with job.progress_lock:
            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.PAUSED
                should_notify = True
        self._mark_task_blocked(job, task, reason=blocked_reason, error_message=task_error_msg)
        self._persist_job(job)
        logger.warning(log_msg)
        if should_notify:
            self._notify('job_paused', {
                'job_id': job.job_id,
                'tickers': job.tickers,
                'reason': telegram_reason,
            })
        if trigger_refresh and self._auto_refresh_callback and not job.refresh_requested:
            job.refresh_requested = True
            try:
                self._auto_refresh_callback(job.job_id, task.ticker, task.date)
            except Exception as e:
                logger.error("Auto-refresh callback failed: %s", e, exc_info=True)

    def _schedule_task_retry_backoff(
        self,
        job: Job,
        task: Task,
        *,
        reason: str = DEFER_REASON_ERROR_BACKOFF,
    ) -> None:
        extra_delay = max(0, task.attempts - 3) * 30
        raw = 2.0 * task.attempts + extra_delay
        backoff = min(raw, float(job.max_backoff_seconds))
        retry_note = f"Retrying in {backoff:.0f}s (attempt {task.attempts + 1})..."
        message = f"{task.error} — {retry_note}" if task.error else retry_note
        self._mark_task_deferred(
            job,
            task,
            delay_seconds=backoff,
            reason=reason,
            error_message=message,
        )
        logger.info(
            "Back-off %.1fs (cap %.1fs) before retry attempt %s for %s %s",
            backoff,
            float(job.max_backoff_seconds),
            task.attempts + 1,
            task.ticker,
            task.date,
        )

    def _reset_rate_limit_streak(self, job_id: str) -> None:
        self._job_rate_limit_streaks[job_id] = 0

    def _current_rate_limit_streak(self, job_id: str) -> int:
        return max(0, int(self._job_rate_limit_streaks.get(job_id, 0)))

    def _scaled_rate_limit_wait(self, job: Job, raw_wait: Any) -> float:
        try:
            base_wait = max(RATE_LIMIT_MIN_SECONDS, min(float(raw_wait), RATE_LIMIT_MAX_SECONDS))
        except (TypeError, ValueError):
            base_wait = float(RATE_LIMIT_FALLBACK_SECONDS)
        multiplier = self._current_rate_limit_streak(job.job_id) + 1
        return min(float(RATE_LIMIT_MAX_SECONDS), base_wait * multiplier)

    def _activate_rate_limit_pause(self, job: Job, wait_seconds: float) -> None:
        new_deadline = now_wib() + timedelta(seconds=wait_seconds)
        current_deadline = _parse_iso(job.rate_limit_pause_until)
        if current_deadline and current_deadline > new_deadline:
            new_deadline = current_deadline
        job.rate_limit_pause_until = new_deadline.isoformat()
        self._persist_job(job)

    def _park_task_for_rate_limit(self, job: Job, task: Task, wait_seconds: float, *, count_rate_limit: bool) -> None:
        if count_rate_limit:
            task.rate_limit_count += 1
            self._job_rate_limit_streaks[job.job_id] = self._current_rate_limit_streak(job.job_id) + 1
        task.status = TaskStatus.RUNNING
        task.defer_reason = None
        task.blocked_reason = None
        task.retry_after_at = None
        task.current_page = 0
        task.active_worker_id = None
        task.error = (
            f"Rate limited — pausing job for {wait_seconds:.0f}s, then resuming from checkpoint..."
        )
        task.last_error_at = now_wib().isoformat()
        self._save_task_row(job, task)

    @staticmethod
    def _apply_checkpoint_from_result(task: Task, result: Dict[str, Any]) -> None:
        task.resume_trade_number = result.get('resume_trade_number', task.resume_trade_number)
        task.checkpoint_pages_fetched = result.get('checkpoint_pages_fetched', task.checkpoint_pages_fetched) or 0
        task.checkpoint_records_fetched = result.get('checkpoint_records_fetched', task.checkpoint_records_fetched) or 0
        task.checkpoint_first_trade_number = result.get(
            'checkpoint_first_trade_number', task.checkpoint_first_trade_number
        )
        task.checkpoint_last_trade_number = result.get(
            'checkpoint_last_trade_number', task.checkpoint_last_trade_number
        )
        task.checkpoint_first_time = result.get('checkpoint_first_time', task.checkpoint_first_time)
        task.checkpoint_last_time = result.get('checkpoint_last_time', task.checkpoint_last_time)
        task.page1_fingerprint = result.get('page1_fingerprint', task.page1_fingerprint)

    def _sleep_until_next_event(self, job: Job) -> None:
        deadlines = []
        next_retry = self._next_retry_deadline(job)
        if next_retry:
            deadlines.append(next_retry)
        pause_until = self._current_rate_limit_pause_until(job)
        if pause_until:
            deadlines.append(pause_until)
        if deadlines:
            soonest = min(deadlines)
            seconds = max(0.05, min((soonest - now_wib()).total_seconds(), 0.5))
            time.sleep(seconds)
            return
        time.sleep(0.1)

    def _process_job(self, job: Job):
        job.status = JobStatus.RUNNING
        job.started_at = job.started_at or now_wib().isoformat()
        job.completed_at = None
        self.current_job_id = job.job_id
        job.last_milestone_pct = 0
        workers = max(1, job.parallel_workers)
        logger.info(
            "Starting job %s with %s parallel worker(s) pause_on_rate_limit=%s",
            job.job_id,
            workers,
            job.pause_on_rate_limit,
        )

        self._notify('job_started', {
            'job_id': job.job_id,
            'tickers': job.tickers,
            'from_date': job.from_date,
            'until_date': job.until_date,
            'total_tasks': len(job.tasks),
            'parallel_workers': workers,
            'pause_on_rate_limit': job.pause_on_rate_limit,
        })

        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures: Dict[Future, Task] = {}
                future_workers: Dict[Future, str] = {}
                while futures or self._job_has_open_tasks(job):
                    while not self.stop_flag.is_set() and job.status == JobStatus.PAUSED:
                        if self._next_job_id and self._next_job_id != job.job_id:
                            self._persist_job(job)
                            executor.shutdown(wait=False)
                            return
                        time.sleep(0.25)

                    if self.stop_flag.is_set():
                        job.status = JobStatus.PAUSED
                        executor.shutdown(wait=False)
                        return
                    if self._is_job_cancelled(job):
                        executor.shutdown(wait=False)
                        self._persist_job(job)
                        return

                    capacity = workers - len(futures)
                    for task in self._dispatchable_tasks(job, futures, capacity):
                        worker_id = self._allocate_worker_id(workers, list(future_workers.values()))
                        future = executor.submit(self._process_task, job, task, worker_id)
                        futures[future] = task
                        future_workers[future] = worker_id

                    if not futures:
                        if self._current_rate_limit_pause_until(job) or not self._job_has_pending_tasks_any(job):
                            self._sleep_until_next_event(job)
                        else:
                            time.sleep(0.05)
                        continue

                    done, _ = wait(list(futures.keys()), return_when=FIRST_COMPLETED)
                    for future in done:
                        task = futures.pop(future)
                        future_workers.pop(future, None)
                        try:
                            future.result()
                        except Exception as e:
                            logger.error("Task %s %s raised exception: %s", task.ticker, task.date, e)
                        if job.delay_seconds > 0 and (futures or self._job_has_pending_tasks_any(job)):
                            time.sleep(job.delay_seconds)

            if job.status == JobStatus.PAUSED:
                logger.warning("Job %s paused during execution", job.job_id)
                return
            if self._is_job_cancelled(job):
                logger.warning("Job %s stopped (cancelled or failed)", job.job_id)
                return
            if job.status == JobStatus.RUNNING:
                remaining_open = [
                    task for task in job.tasks
                    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.DEFERRED, TaskStatus.BLOCKED)
                ]
                if remaining_open:
                    job.status = JobStatus.PAUSED
                    job.error = "Job paused with unfinished tasks"
                    self._persist_job(job)
                    return
                job.status = JobStatus.COMPLETED
                job.completed_at = now_wib().isoformat()
                job.error = None
                self._persist_job(job)
                progress = job.get_progress()
                self._notify('job_completed', {
                    'job_id': job.job_id,
                    'tickers': job.tickers,
                    'from_date': job.from_date,
                    'until_date': job.until_date,
                    'total_tasks': progress['total'],
                    'completed_tasks': progress['completed'],
                    'failed_tasks': progress['failed'],
                    'total_records': sum(t.records_fetched for t in job.tasks),
                    'started_at': job.started_at,
                    'completed_at': job.completed_at,
                })
        except Exception as e:
            logger.error("Job %s failed with error: %s", job.job_id, e, exc_info=True)
            job.status = JobStatus.FAILED
            job.error = str(e)
            self._persist_job(job)
            self._notify('job_failed', {
                'job_id': job.job_id,
                'tickers': job.tickers,
                'error': str(e),
            })
        finally:
            job.active_workers.clear()
            self.current_job_id = None

    def _process_task(self, job: Job, task: Task, worker_id: str):
        if self._is_job_cancelled(job):
            return

        continuing_task = (
            task.status == TaskStatus.RUNNING
            or task.resume_trade_number is not None
            or task.checkpoint_pages_fetched > 0
            or task.checkpoint_records_fetched > 0
        )
        resume_trade_number = task.resume_trade_number
        checkpoint_records = task.checkpoint_records_fetched
        checkpoint_pages = task.checkpoint_pages_fetched
        resuming = bool(resume_trade_number is not None and checkpoint_pages > 0)

        task.status = TaskStatus.RUNNING
        if not continuing_task:
            task.attempts += 1
        elif task.attempts <= 0:
            task.attempts = 1
        task.current_page = 0
        task.end_reason = None
        task.error = None
        task.retry_after_at = None
        task.defer_reason = None
        task.blocked_reason = None
        self._assign_worker(job, worker_id, task)
        self._save_task_row(job, task)

        if not continuing_task:
            self._clear_task_checkpoint(task)
            self.storage.reset_task_stage(job.job_id, task.ticker, task.date)
            checkpoint_records = 0
            checkpoint_pages = 0

        def update_progress(page: int, total_records: int):
            task.current_page = page
            task.records_fetched = total_records
            self._touch_worker_page(job, worker_id, page, task)

        def persist_page(trades: List[Dict[str, Any]], checkpoint: Dict[str, Any]):
            save_result = self.storage.append_task_trades(job.job_id, task.ticker, task.date, trades)
            if not save_result.get('success'):
                raise RuntimeError(save_result.get('error', 'Failed to persist checkpoint page'))
            self._reset_rate_limit_streak(job.job_id)
            task.resume_trade_number = checkpoint.get('resume_trade_number')
            task.checkpoint_pages_fetched = checkpoint.get('checkpoint_pages_fetched', 0) or 0
            task.checkpoint_records_fetched = checkpoint.get('checkpoint_records_fetched', 0) or 0
            task.checkpoint_first_trade_number = checkpoint.get('checkpoint_first_trade_number')
            task.checkpoint_last_trade_number = checkpoint.get('checkpoint_last_trade_number')
            task.checkpoint_first_time = checkpoint.get('checkpoint_first_time')
            task.checkpoint_last_time = checkpoint.get('checkpoint_last_time')
            if checkpoint.get('page1_fingerprint'):
                task.page1_fingerprint = checkpoint.get('page1_fingerprint')
            task.records_fetched = task.checkpoint_records_fetched
            task.pages_fetched = task.checkpoint_pages_fetched
            self._save_task_row(job, task)

        try:
            result = self.client.fetch_running_trade(
                ticker=task.ticker,
                date=task.date,
                limit=job.limit,
                progress_callback=update_progress,
                cancel_check=lambda: (
                    job.status != JobStatus.RUNNING
                    or self._current_rate_limit_pause_until(job) is not None
                ),
                resume_trade_number=resume_trade_number if resuming else None,
                initial_records=checkpoint_records if resuming else 0,
                initial_pages=checkpoint_pages if resuming else 0,
                page_callback=persist_page,
            )

            if result.get('cancelled'):
                if job.status == JobStatus.PAUSED:
                    self._mark_task_blocked(
                        job,
                        task,
                        reason=BLOCKED_REASON_MANUAL_PAUSE,
                        error_message='Paused by user',
                    )
                elif self._current_rate_limit_pause_until(job):
                    pause_until = self._current_rate_limit_pause_until(job)
                    remaining = 0.0
                    if pause_until:
                        remaining = max(0.0, (pause_until - now_wib()).total_seconds())
                    self._park_task_for_rate_limit(
                        job,
                        task,
                        max(RATE_LIMIT_MIN_SECONDS, remaining or RATE_LIMIT_FALLBACK_SECONDS),
                        count_rate_limit=False,
                    )
                else:
                    task.status = TaskStatus.PENDING
                    task.current_page = 0
                    task.active_worker_id = None
                    self._save_task_row(job, task)
                return

            if result.get('success'):
                end_reason = result.get('end_reason')
                if end_reason not in NORMAL_FETCH_END_REASONS:
                    task.end_reason = end_reason
                    task.error = (
                        f"Internal fetch end state: {end_reason!r} "
                        f"(expected one of {sorted(NORMAL_FETCH_END_REASONS)})"
                    )
                    self._schedule_task_retry_backoff(job, task)
                    return

                if checkpoint_records and result.get('count', 0) < checkpoint_records:
                    task.error = (
                        f"Refusing to complete regressed result for {task.ticker} {task.date}: "
                        f"{result.get('count', 0)} row(s) < checkpoint {checkpoint_records}"
                    )
                    task.last_error_at = now_wib().isoformat()
                    self._schedule_task_retry_backoff(job, task, reason=DEFER_REASON_CURSOR_REGRESSION)
                    return

                if checkpoint_pages and result.get('successful_pages_fetched', result.get('pages_fetched', 0)) < checkpoint_pages:
                    task.error = (
                        f"Refusing to complete regressed pagination for {task.ticker} {task.date}: "
                        f"{result.get('successful_pages_fetched', result.get('pages_fetched', 0))} page(s) "
                        f"< checkpoint {checkpoint_pages}"
                    )
                    task.last_error_at = now_wib().isoformat()
                    self._schedule_task_retry_backoff(job, task, reason=DEFER_REASON_CURSOR_REGRESSION)
                    return

                filename = self.storage.get_filename(task.ticker, job.from_date, job.until_date)
                save_result = self.storage.finalize_task_trades(
                    job.job_id,
                    task.ticker,
                    task.date,
                    filename,
                )
                if save_result.get('success'):
                    self._reset_rate_limit_streak(job.job_id)
                    task.status = TaskStatus.COMPLETED
                    task.records_fetched = result.get('count', 0)
                    task.pages_fetched = result.get('successful_pages_fetched', result.get('pages_fetched', 0))
                    task.end_reason = end_reason
                    task.error = None
                    task.current_page = 0
                    task.active_worker_id = None
                    self._clear_task_checkpoint(task)
                    self._save_task_row(job, task)
                    if job.get_progress()['completed'] % 5 == 0:
                        self._persist_job(job)
                    self._maybe_fire_progress_milestones(job)
                    return
                task.error = save_result.get('error', 'Unknown save error')
                task.last_error_at = now_wib().isoformat()
                self._schedule_task_retry_backoff(job, task)
                return

            error = result.get('error', 'Unknown error')

            if result.get('rate_limited'):
                self._apply_checkpoint_from_result(task, result)
                wait_secs = self._scaled_rate_limit_wait(job, result.get('retry_after_seconds'))
                self._activate_rate_limit_pause(job, wait_secs)
                self._park_task_for_rate_limit(job, task, wait_secs, count_rate_limit=True)
                logger.warning(
                    "Task %s %s rate-limited (HTTP 429) — pausing job for ~%.0fs (job %s)",
                    task.ticker,
                    task.date,
                    wait_secs,
                    job.job_id,
                )
                return

            if result.get('requires_login'):
                self._pause_job_notify_once(
                    job,
                    telegram_reason='Token expired',
                    task=task,
                    task_error_msg='Token expired - waiting for refresh',
                    blocked_reason=BLOCKED_REASON_TOKEN_REFRESH,
                    log_msg=f"Job {job.job_id} PAUSED - Token expired. Refreshing token.",
                    trigger_refresh=True,
                )
                return

            if result.get('captcha_required'):
                self._pause_job_notify_once(
                    job,
                    telegram_reason='Captcha required',
                    task=task,
                    task_error_msg='Captcha required',
                    blocked_reason=BLOCKED_REASON_CAPTCHA_REQUIRED,
                    log_msg=f"Job {job.job_id} paused due to captcha",
                )
                return

            if result.get('partial') and result.get('end_reason') == FetchEndReason.FETCH_INTERRUPTED:
                task.end_reason = FetchEndReason.FETCH_INTERRUPTED
                task.error = (
                    f"Fetch cut short mid-page — {result.get('count', 0)} row(s) collected but not saved. "
                    f"Cause: {error}"
                )
            else:
                task.end_reason = None
                task.error = error
            task.last_error_at = now_wib().isoformat()
            self._schedule_task_retry_backoff(job, task)
        except Exception as e:
            task.error = str(e)
            task.last_error_at = now_wib().isoformat()
            logger.error("Task exception %s %s (attempt %s): %s", task.ticker, task.date, task.attempts, e)
            self._schedule_task_retry_backoff(job, task)
        finally:
            task.active_worker_id = None
            self._release_worker(job, worker_id)
