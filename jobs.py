"""
Job scheduler and manager for fetching trade data
"""
import json
import os
import threading
import time
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, asdict, field, fields
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, Future
import logging
from database import JobDatabase
from tz import now_wib
from stockbit_client import FetchEndReason, NORMAL_FETCH_END_REASONS
from config import (
    RATE_LIMIT_FALLBACK_SECONDS,
    RATE_LIMIT_MIN_SECONDS,
    RATE_LIMIT_MAX_SECONDS,
)

logger = logging.getLogger(__name__)

# clamp for max_backoff_seconds from API/FE — still lets you go pretty patient
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


# loaded once — restart picks up file edits
_HOLIDAYS: Dict[str, str] = _load_holidays()


def _get_skip_reason(date_str: str) -> Optional[str]:
    """Non-trading day label: weekend first, else exchange holiday from JSON."""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    if d.weekday() >= 5:  # Sat/Sun
        return 'Weekend'
    return _HOLIDAYS.get(date_str)


class JobStatus(Enum):
    """Job status enum"""
    QUEUED = 'QUEUED'
    RUNNING = 'RUNNING'
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


class TaskStatus(Enum):
    """Individual task status"""
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'


@dataclass
class Task:
    """Individual fetch task for one ticker on one date"""
    ticker: str
    date: str
    status: TaskStatus = TaskStatus.PENDING
    error: Optional[str] = None
    # why the task stopped (from StockbitClient), e.g. ok_short_page vs fetch_interrupted
    end_reason: Optional[str] = None
    records_fetched: int = 0
    pages_fetched: int = 0
    current_page: int = 0  # real-time page being fetched
    attempts: int = 0
    # weekend / holiday label for SKIPPED tasks — not in SQLite, re-derived on load
    skip_reason: Optional[str] = None
    # after a failed attempt, don't run again until this monotonic time (parallel-friendly)
    retry_after_monotonic: Optional[float] = field(default=None, repr=False)


@dataclass
class Job:
    """Job containing multiple tasks"""
    job_id: str
    tickers: List[str]
    from_date: str
    until_date: str
    delay_seconds: float
    limit: int
    parallel_workers: int = 1  # number of stocks to process in parallel
    # cap for exponential-ish backoff between retries (seconds); tasks retry forever otherwise
    max_backoff_seconds: float = 180.0
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: now_wib().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None  # job-level error message if the job itself fails
    tasks: List[Task] = field(default_factory=list)
    # runtime only — HTTP 429 backoff (not persisted; monotonic clock)
    cooldown_until_monotonic: Optional[float] = field(default=None, repr=False)
    cooldown_reason: Optional[str] = field(default=None, repr=False)
    # Telegram milestone spam guard (25/50/75%) — must be thread-safe in parallel mode
    last_milestone_pct: int = 0
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization (skip non-serializable lock + monotonic fields)."""
        skip = frozenset({
            'cooldown_until_monotonic', 'cooldown_reason', 'progress_lock',
            'last_milestone_pct',  # internal Telegram milestone guard only
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
                    td.pop('retry_after_monotonic', None)
                    data['tasks'].append(td)
                continue
            v = getattr(self, f.name)
            if f.name == 'status':
                data['status'] = v.value if isinstance(v, JobStatus) else v
            else:
                data[f.name] = v
        # so the UI can show "waiting on rate limit" without digging logs
        if self.cooldown_until_monotonic is not None:
            rem = self.cooldown_until_monotonic - time.monotonic()
            if rem > 0:
                data['cooldown_seconds_remaining'] = round(rem, 1)
                data['cooldown_reason'] = self.cooldown_reason
        return data

    def get_progress(self) -> Dict[str, Any]:
        """Calculate job progress"""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.status in [TaskStatus.COMPLETED, TaskStatus.SKIPPED])
        failed = sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)
        running = sum(1 for t in self.tasks if t.status == TaskStatus.RUNNING)

        return {
            'total': total,
            'completed': completed,
            'failed': failed,
            'running': running,
            'pending': total - completed - failed - running,
            'percentage': round((completed / total * 100) if total > 0 else 0, 1)
        }


class JobManager:
    """Manages job queue and execution"""

    def __init__(self, stockbit_client, csv_storage):
        self.client = stockbit_client
        self.storage = csv_storage
        self.jobs: Dict[str, Job] = {}
        self.current_job_id: Optional[str] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.db = JobDatabase()
        self._next_job_id: Optional[str] = None  # prioritized job set by play_queued_job
        self._cooldown_lock = threading.Lock()

        # external notification callback — set by TelegramBot or others
        # signature: callback(event: str, data: dict)
        self._notification_callback = None

        # load persisted jobs on startup
        self._load_jobs_from_db()

    def set_notification_callback(self, callback):
        """Register a callback for job lifecycle events.

        Events emitted:
            job_started, job_progress, job_completed, job_failed, job_paused
        """
        self._notification_callback = callback

    def _notify(self, event: str, data: Dict[str, Any]):
        """Fire the notification callback if one is registered."""
        if self._notification_callback:
            try:
                self._notification_callback(event, data)
            except Exception as e:
                logger.error(f"Notification callback error ({event}): {e}")

    def _is_job_cancelled(self, job: Job) -> bool:
        # stop workers when user cancelled or the job hit a hard failure
        return job.status in (JobStatus.CANCELLED, JobStatus.FAILED)

    @staticmethod
    def _task_ready_to_run(task: Task) -> bool:
        if task.status != TaskStatus.PENDING:
            return False
        ra = task.retry_after_monotonic
        if ra is not None and time.monotonic() < ra:
            return False
        return True

    @staticmethod
    def _job_has_pending_tasks_any(job: Job) -> bool:
        return any(t.status == TaskStatus.PENDING for t in job.tasks)

    def _pick_next_queued_job(self) -> Optional[Job]:
        """FIFO by created_at among QUEUED jobs (dict order alone isn't enough long-term)."""
        queued = [j for j in self.jobs.values() if j.status == JobStatus.QUEUED]
        if not queued:
            return None
        queued.sort(key=lambda j: j.created_at)
        return queued[0]

    def _extend_job_cooldown(
        self,
        job: Job,
        retry_after_seconds: Optional[float],
        reason: str,
    ) -> None:
        """Lengthen job-wide cooldown after 429; several workers can race — take the latest end time."""
        try:
            sec = float(retry_after_seconds)
        except (TypeError, ValueError):
            sec = float(RATE_LIMIT_FALLBACK_SECONDS)
        sec = max(RATE_LIMIT_MIN_SECONDS, min(sec, RATE_LIMIT_MAX_SECONDS))
        with self._cooldown_lock:
            new_until = time.monotonic() + sec
            prev = job.cooldown_until_monotonic
            if prev is None or new_until > prev:
                job.cooldown_until_monotonic = new_until
            job.cooldown_reason = reason
        logger.warning(
            "Job %s rate-limit cooldown ~%.0fs — %s",
            job.job_id,
            sec,
            reason,
        )

    def _wait_cooldown_if_needed(self, job: Job) -> None:
        """Block until 429 cooldown ends, but still respect pause/cancel/stop."""
        while True:
            if self.stop_flag.is_set():
                return
            if self._is_job_cancelled(job):
                return
            while (
                not self.stop_flag.is_set()
                and job.status == JobStatus.PAUSED
            ):
                time.sleep(0.5)
            if self._is_job_cancelled(job):
                return

            with self._cooldown_lock:
                until = job.cooldown_until_monotonic

            if until is None or time.monotonic() >= until:
                with self._cooldown_lock:
                    if (
                        job.cooldown_until_monotonic is not None
                        and time.monotonic() >= job.cooldown_until_monotonic
                    ):
                        job.cooldown_until_monotonic = None
                        job.cooldown_reason = None
                return

            time.sleep(0.5)

    def _pause_job_notify_once(
        self,
        job: Job,
        *,
        telegram_reason: str,
        task: Task,
        task_error_msg: str,
        log_msg: str,
    ) -> None:
        """Parallel workers can all see 401 at once — only the first transition should Telegram spam."""
        should_notify = False
        with job.progress_lock:
            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.PAUSED
                should_notify = True
        task.status = TaskStatus.PENDING
        task.error = task_error_msg
        task.current_page = 0
        task.retry_after_monotonic = None
        self._persist_job(job)
        logger.warning(log_msg)
        if should_notify:
            self._notify('job_paused', {
                'job_id': job.job_id,
                'tickers': job.tickers,
                'reason': telegram_reason,
            })

    def _schedule_task_retry_backoff(self, job: Job, task: Task) -> None:
        """Mark PENDING and set monotonic retry time — no sleep (keeps parallel slots free)."""
        extra_delay = max(0, task.attempts - 3) * 30
        raw = 2.0 * task.attempts + extra_delay
        cap = float(job.max_backoff_seconds)
        backoff = min(raw, cap)
        task.status = TaskStatus.PENDING
        task.retry_after_monotonic = time.monotonic() + backoff
        task.current_page = 0
        # tack the retry note onto the existing error so the UI shows it's not stuck
        retry_note = f"Retrying in {backoff:.0f}s (attempt {task.attempts + 1})..."
        if task.error:
            task.error = f"{task.error} — {retry_note}"
        else:
            task.error = retry_note
        logger.info(
            "Back-off %.1fs (cap %.1fs) before retry attempt %s for %s %s",
            backoff,
            cap,
            task.attempts + 1,
            task.ticker,
            task.date,
        )
        self._save_task_row(job, task)

    def _save_task_row(self, job: Job, task: Task) -> None:
        """Persist one task so a crash doesn't redo finished days."""
        self.db.save_task(job.job_id, {
            'ticker': task.ticker,
            'date': task.date,
            'status': task.status.value,
            'error': task.error,
            'records_fetched': task.records_fetched,
            'attempts': task.attempts,
        })

    def _maybe_fire_progress_milestones(self, job: Job) -> None:
        # can cross several thresholds in one tick (e.g. small job) — fire each once
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

    def _load_jobs_from_db(self):
        """Load persisted jobs from database on startup"""
        try:
            db_jobs = self.db.get_all_jobs(limit=50)
            loaded_count = 0
            skip_statuses = {'COMPLETED', 'FAILED', 'CANCELLED'}
            for job_data in db_jobs:
                if job_data['status'] in skip_statuses:
                    continue
                start = datetime.strptime(job_data['from_date'], '%Y-%m-%d')
                end = datetime.strptime(job_data['until_date'], '%Y-%m-%d')

                dates = []
                current = start
                while current <= end:
                    dates.append(current.strftime('%Y-%m-%d'))
                    current += timedelta(days=1)

                tasks = []
                for ticker in job_data['tickers']:
                    for d in dates:
                        sr = _get_skip_reason(d)
                        if sr:
                            tasks.append(
                                Task(
                                    ticker=ticker,
                                    date=d,
                                    status=TaskStatus.SKIPPED,
                                    skip_reason=sr,
                                )
                            )
                        else:
                            tasks.append(Task(ticker=ticker, date=d))

                pw = int(job_data.get('parallel_workers', 1) or 1)
                mb = float(job_data.get('max_backoff_seconds', 180) or 180)

                job = Job(
                    job_id=job_data['job_id'],
                    tickers=job_data['tickers'],
                    from_date=job_data['from_date'],
                    until_date=job_data['until_date'],
                    delay_seconds=job_data['delay_seconds'],
                    limit=job_data['limit_per_request'],
                    parallel_workers=pw,
                    max_backoff_seconds=mb,
                    status=JobStatus(job_data['status']),
                    created_at=job_data['created_at'],
                    started_at=job_data.get('start_time'),
                    completed_at=job_data.get('end_time'),
                    error=job_data.get('error'),
                    tasks=tasks,
                )

                saved_tasks = self.db.get_job_tasks(job.job_id)
                if saved_tasks:
                    key_to_row = {(r['ticker'], r['date']): r for r in saved_tasks}
                    for t in job.tasks:
                        row = key_to_row.get((t.ticker, t.date))
                        if not row:
                            continue
                        try:
                            t.status = TaskStatus(row['status'])
                        except ValueError:
                            t.status = TaskStatus.PENDING
                        t.error = row.get('error')
                        t.records_fetched = row.get('records_fetched') or 0
                        t.attempts = row.get('attempts') or 0

                # refresh skip labels from calendar; bump stale PENDING non-trading rows to SKIPPED
                for t in job.tasks:
                    sr = _get_skip_reason(t.date)
                    t.skip_reason = sr
                    if sr and t.status == TaskStatus.PENDING:
                        t.status = TaskStatus.SKIPPED

                self.jobs[job.job_id] = job
                loaded_count += 1

            logger.info(f"Loaded {loaded_count} pending jobs from database")
        except Exception as e:
            logger.error(f"Failed to load jobs from database: {e}")

    def _persist_job(self, job: Job):
        """Save job to database"""
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
                'status': job.status.value,
                'created_at': job.created_at,
                'start_time': job.started_at,
                'end_time': job.completed_at,
                'total_tasks': progress['total'],
                'completed_tasks': progress['completed'],
                'failed_tasks': progress['failed'],
                'total_records': sum(t.records_fetched for t in job.tasks)
            }
            self.db.save_job(job_data)
        except Exception as e:
            logger.error(f"Failed to persist job {job.job_id}: {e}")

    def create_job(
        self,
        tickers: List[str],
        from_date: str,
        until_date: str,
        delay_seconds: float = 3.0,
        limit: int = 50,
        parallel_workers: int = 1,
        max_backoff_seconds: float = 180.0,
    ) -> str:
        """Create a new job with tasks for each ticker-date combination"""
        job_id = str(uuid.uuid4())
        mb = float(max_backoff_seconds)
        mb = max(_MIN_BACKOFF_CAP, min(mb, _MAX_BACKOFF_CAP))

        start = datetime.strptime(from_date, '%Y-%m-%d')
        end = datetime.strptime(until_date, '%Y-%m-%d')

        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        tasks = []
        for ticker in tickers:
            for date in dates:
                sr = _get_skip_reason(date)
                if sr:
                    tasks.append(
                        Task(
                            ticker=ticker,
                            date=date,
                            status=TaskStatus.SKIPPED,
                            skip_reason=sr,
                        )
                    )
                else:
                    tasks.append(Task(ticker=ticker, date=date))

        job = Job(
            job_id=job_id,
            tickers=tickers,
            from_date=from_date,
            until_date=until_date,
            delay_seconds=delay_seconds,
            limit=limit,
            parallel_workers=parallel_workers,
            max_backoff_seconds=mb,
            tasks=tasks
        )

        self.jobs[job_id] = job

        self._persist_job(job)
        for t in tasks:
            self._save_task_row(job, t)

        logger.info(f"Created job {job_id} with {len(tasks)} tasks")

        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.start_worker()

        return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID"""
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[Dict[str, Any]]:
        """List all jobs"""
        return [job.to_dict() for job in self.jobs.values()]

    def pause_job(self, job_id: str):
        """Pause a job"""
        job = self.jobs.get(job_id)
        if job and job.status == JobStatus.RUNNING:
            job.status = JobStatus.PAUSED
            self.pause_flag.set()
            logger.info(f"Job {job_id} paused")

    def resume_job(self, job_id: str):
        """Resume a paused job"""
        job = self.jobs.get(job_id)
        if job and job.status == JobStatus.PAUSED:
            if self.current_job_id == job_id:
                job.status = JobStatus.RUNNING
            else:
                job.status = JobStatus.QUEUED
            self.pause_flag.clear()
            self._persist_job(job)
            logger.info(f"Job {job_id} resumed")

            if not self.worker_thread or not self.worker_thread.is_alive():
                self.start_worker()

    def play_queued_job(self, job_id: str) -> bool:
        """Kick a queued job so worker starts processing it next."""
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.QUEUED:
            return False

        self._next_job_id = job_id

        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()
        return True

    def auto_resume_paused_jobs(self):
        """Auto-resume all paused jobs (call when token is refreshed)"""
        resumed_count = 0
        for job in self.jobs.values():
            if job.status == JobStatus.PAUSED:
                self.resume_job(job.job_id)
                resumed_count += 1

        if resumed_count > 0:
            logger.info(f"[OK] Auto-resumed {resumed_count} paused job(s) after token refresh")

        return resumed_count

    def cancel_job(self, job_id: str):
        """Cancel a job"""
        job = self.jobs.get(job_id)
        if job:
            job.status = JobStatus.CANCELLED
            job.error = 'Cancelled by user'
            self._persist_job(job)
            self.pause_flag.set()
            if not any(j.status == JobStatus.PAUSED for j in self.jobs.values()):
                self.pause_flag.clear()
            logger.info(f"Job {job_id} cancelled")

    def delete_job(self, job_id: str) -> bool:
        """Delete a job if it's failed, cancelled, or paused."""
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
        logger.info(f"Deleted job {job_id}")
        return True

    def retry_job(self, job_id: str) -> bool:
        """Reset a FAILED job back to QUEUED so it can be re-processed."""
        job = self.jobs.get(job_id)
        if not job or job.status != JobStatus.FAILED:
            return False

        for task in job.tasks:
            if task.status == TaskStatus.FAILED:
                task.status = TaskStatus.PENDING
                task.error = None
                task.attempts = 0
                task.records_fetched = 0
                task.pages_fetched = 0
                task.current_page = 0
                task.retry_after_monotonic = None
                self._save_task_row(job, task)

        job.status = JobStatus.QUEUED
        job.error = None
        job.completed_at = None
        job.last_milestone_pct = 0
        job.cooldown_until_monotonic = None
        job.cooldown_reason = None
        self._persist_job(job)
        logger.info(f"Job {job_id} re-queued for retry")

        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()

        return True

    def retry_task(self, job_id: str, ticker: str, date: str) -> bool:
        """Retry one FAILED task in a job."""
        job = self.jobs.get(job_id)
        if not job:
            return False

        task_to_retry = None
        for task in job.tasks:
            if task.ticker == ticker and task.date == date:
                task_to_retry = task
                break

        if not task_to_retry or task_to_retry.status != TaskStatus.FAILED:
            return False

        task_to_retry.status = TaskStatus.PENDING
        task_to_retry.error = None
        task_to_retry.attempts = 0
        task_to_retry.records_fetched = 0
        task_to_retry.pages_fetched = 0
        task_to_retry.current_page = 0
        task_to_retry.retry_after_monotonic = None
        self._save_task_row(job, task_to_retry)

        if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
            job.status = JobStatus.QUEUED
            job.completed_at = None
            job.error = None

        self._persist_job(job)
        logger.info(f"Task {ticker} {date} in job {job_id} re-queued for retry")

        if not self.worker_thread or not self.worker_thread.is_alive():
            self.start_worker()

        return True

    def start_worker(self):
        """Start background worker thread"""
        self.stop_flag.clear()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Job worker started")

    def stop_worker(self):
        """Stop background worker"""
        self.stop_flag.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        logger.info("Job worker stopped")

    def _worker_loop(self):
        """Main worker loop that processes jobs"""
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
                logger.critical(
                    "Job worker loop crashed (will retry in 5s): %s",
                    e,
                    exc_info=True,
                )
                time.sleep(5)

    def _process_job(self, job: Job):
        """Process all tasks in a job with optional parallelism"""
        job.status = JobStatus.RUNNING
        job.started_at = now_wib().isoformat()
        self.current_job_id = job.job_id
        job.last_milestone_pct = 0

        workers = job.parallel_workers
        logger.info(f"Starting job {job.job_id} with {workers} parallel worker(s)")

        self._notify('job_started', {
            'job_id': job.job_id,
            'tickers': job.tickers,
            'from_date': job.from_date,
            'until_date': job.until_date,
            'total_tasks': len(job.tasks),
            'parallel_workers': workers,
        })

        try:
            if workers == 1:
                while self._job_has_pending_tasks_any(job):
                    while (
                        not self.stop_flag.is_set()
                        and job.status == JobStatus.PAUSED
                    ):
                        # another job is waiting to run — yield so the worker can pick it up
                        if self._next_job_id and self._next_job_id != job.job_id:
                            self._persist_job(job)
                            return
                        time.sleep(0.5)

                    if self.stop_flag.is_set():
                        job.status = JobStatus.PAUSED
                        return

                    if self._is_job_cancelled(job):
                        self._persist_job(job)
                        return

                    self._wait_cooldown_if_needed(job)

                    if self.stop_flag.is_set():
                        job.status = JobStatus.PAUSED
                        return

                    if self._is_job_cancelled(job):
                        self._persist_job(job)
                        return

                    next_task = None
                    for t in job.tasks:
                        if self._task_ready_to_run(t):
                            next_task = t
                            break

                    if not next_task:
                        soonest = None
                        now_m = time.monotonic()
                        for t in job.tasks:
                            if t.status != TaskStatus.PENDING:
                                continue
                            ra = t.retry_after_monotonic
                            if ra is not None and ra > now_m:
                                soonest = ra if soonest is None else min(soonest, ra)
                        if soonest:
                            time.sleep(min(soonest - now_m, 0.5))
                        continue

                    self._process_task(job, next_task)

                    if self._is_job_cancelled(job):
                        self._persist_job(job)
                        return

                    if job.delay_seconds > 0 and self._job_has_pending_tasks_any(job):
                        time.sleep(job.delay_seconds)

            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures: Dict[Future, Task] = {}

                    def _submit_pending_slots() -> None:
                        for t in job.tasks:
                            if self.stop_flag.is_set() or self._is_job_cancelled(job):
                                return
                            if not self._task_ready_to_run(t):
                                continue
                            if t in futures.values():
                                continue
                            if len(futures) >= workers:
                                break
                            fut = executor.submit(self._process_task, job, t)
                            futures[fut] = t

                    while futures or self._job_has_pending_tasks_any(job):
                        while (
                            not self.stop_flag.is_set()
                            and job.status == JobStatus.PAUSED
                        ):
                            # another job is waiting to run — yield so the worker can pick it up
                            if self._next_job_id and self._next_job_id != job.job_id:
                                self._persist_job(job)
                                executor.shutdown(wait=False)
                                return
                            time.sleep(0.5)

                        if self.stop_flag.is_set():
                            job.status = JobStatus.PAUSED
                            executor.shutdown(wait=False)
                            return

                        if self._is_job_cancelled(job):
                            executor.shutdown(wait=False)
                            self._persist_job(job)
                            return

                        self._wait_cooldown_if_needed(job)

                        if self.stop_flag.is_set():
                            job.status = JobStatus.PAUSED
                            executor.shutdown(wait=False)
                            return

                        if self._is_job_cancelled(job):
                            executor.shutdown(wait=False)
                            self._persist_job(job)
                            return

                        _submit_pending_slots()

                        if not futures:
                            if self._job_has_pending_tasks_any(job):
                                time.sleep(0.05)
                            continue

                        done, _ = wait(
                            list(futures.keys()),
                            return_when=FIRST_COMPLETED,
                        )

                        for future in done:
                            task = futures.pop(future)
                            try:
                                future.result()
                            except Exception as e:
                                logger.error(
                                    f"Task {task.ticker} {task.date} raised exception: {e}"
                                )

                            while (
                                not self.stop_flag.is_set()
                                and job.status == JobStatus.PAUSED
                            ):
                                # another job is waiting to run — yield so the worker can pick it up
                                if self._next_job_id and self._next_job_id != job.job_id:
                                    self._persist_job(job)
                                    executor.shutdown(wait=False)
                                    return
                                time.sleep(0.5)

                            if self.stop_flag.is_set():
                                job.status = JobStatus.PAUSED
                                executor.shutdown(wait=False)
                                return

                            if self._is_job_cancelled(job):
                                executor.shutdown(wait=False)
                                self._persist_job(job)
                                return

                            if job.delay_seconds > 0 and (futures or self._job_has_pending_tasks_any(job)):
                                time.sleep(job.delay_seconds)

                            self._wait_cooldown_if_needed(job)

                            if self._is_job_cancelled(job):
                                executor.shutdown(wait=False)
                                self._persist_job(job)
                                return

            if job.status == JobStatus.PAUSED:
                logger.warning(f"Job {job.job_id} paused during execution")
                return

            if self._is_job_cancelled(job):
                logger.warning(f"Job {job.job_id} stopped (cancelled or failed)")
                return

            if job.status == JobStatus.RUNNING:
                job.status = JobStatus.COMPLETED
                job.completed_at = now_wib().isoformat()
                self._persist_job(job)
                logger.info(f"[OK] Job {job.job_id} completed")

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
            logger.error(f"Job {job.job_id} failed with error: {e}")
            job.status = JobStatus.FAILED
            job.error = str(e)
            self._persist_job(job)

            self._notify('job_failed', {
                'job_id': job.job_id,
                'tickers': job.tickers,
                'error': str(e),
            })

        finally:
            self.current_job_id = None

    def _process_task(self, job: Job, task: Task):
        """Process a single task — one fetch attempt per call; backoff is scheduled, not slept here."""
        if self._is_job_cancelled(job):
            task.status = TaskStatus.PENDING
            task.current_page = 0
            return

        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.current_page = 0
        task.end_reason = None
        # wipe old failure text + "Retrying in…" note — this attempt is live now, not in backoff
        task.error = None
        task.retry_after_monotonic = None
        self._save_task_row(job, task)

        if task.attempts > 1:
            logger.info(f"Retrying {task.ticker} {task.date} (attempt {task.attempts})")
        else:
            logger.info(f"Fetching {task.ticker} for {task.date}")

        def update_progress(page: int, total_records: int):
            task.current_page = page
            task.records_fetched = total_records

        try:
            result = self.client.fetch_running_trade(
                ticker=task.ticker,
                date=task.date,
                limit=job.limit,
                progress_callback=update_progress,
                cancel_check=lambda: job.status != JobStatus.RUNNING,
            )

            if result.get('cancelled'):
                task.status = TaskStatus.PENDING
                task.current_page = 0
                task.error = None
                task.end_reason = None
                logger.info(f"Fetch aborted for {task.ticker} {task.date} (job stopped)")
                return

            if result.get('success'):
                er = result.get('end_reason')
                if er not in NORMAL_FETCH_END_REASONS:
                    task.error = (
                        f"Internal fetch end state: {er!r} (expected one of {sorted(NORMAL_FETCH_END_REASONS)})"
                    )
                    task.end_reason = er
                    logger.error(
                        f"Refusing to mark complete for {task.ticker} {task.date}: bad end_reason={er!r}"
                    )
                    self._schedule_task_retry_backoff(job, task)
                    return

                trades = result.get('data', [])
                filename = self.storage.get_filename(
                    task.ticker,
                    job.from_date,
                    job.until_date
                )

                save_result = self.storage.save_trades(
                    ticker=task.ticker,
                    date=task.date,
                    trades=trades,
                    filename=filename
                )

                if save_result.get('success'):
                    task.status = TaskStatus.COMPLETED
                    task.records_fetched = result.get('count', 0)
                    task.pages_fetched = result.get('pages_fetched', 1)
                    task.end_reason = er
                    task.error = None
                    logger.info(
                        f"Saved {task.records_fetched} records ({task.pages_fetched} pages) for "
                        f"{task.ticker} {task.date} (end_reason={er})"
                    )
                    self._save_task_row(job, task)
                    progress = job.get_progress()
                    if progress['completed'] % 5 == 0:
                        self._persist_job(job)
                    self._maybe_fire_progress_milestones(job)
                    return

                task.error = save_result.get('error', 'Unknown save error')
                logger.error(
                    f"Failed to save {task.ticker} {task.date} (attempt {task.attempts}): {task.error}"
                )
                self._schedule_task_retry_backoff(job, task)
                return

            error = result.get('error', 'Unknown error')

            if result.get('rate_limited'):
                task.status = TaskStatus.PENDING
                if task.attempts > 0:
                    task.attempts -= 1
                task.current_page = 0
                task.end_reason = None
                raw_wait = result.get('retry_after_seconds')
                try:
                    wait_secs = max(RATE_LIMIT_MIN_SECONDS, min(float(raw_wait), RATE_LIMIT_MAX_SECONDS))
                except (TypeError, ValueError):
                    wait_secs = float(RATE_LIMIT_FALLBACK_SECONDS)
                task.error = f"Rate limited — retrying in {wait_secs:.0f}s..."
                self._extend_job_cooldown(
                    job,
                    raw_wait,
                    f"HTTP 429 {task.ticker} {task.date}",
                )
                logger.info(
                    f"Rate limited {task.ticker} {task.date} — leaving task PENDING "
                    f"(retry_after={raw_wait!r})"
                )
                return

            if result.get('requires_login'):
                self._pause_job_notify_once(
                    job,
                    telegram_reason='Token expired',
                    task=task,
                    task_error_msg='Token expired - job paused',
                    log_msg=f"Job {job.job_id} PAUSED - Token expired. Set new token to resume.",
                )
                return

            if result.get('captcha_required'):
                self._pause_job_notify_once(
                    job,
                    telegram_reason='Captcha required',
                    task=task,
                    task_error_msg='Captcha required',
                    log_msg=f"Job {job.job_id} paused due to captcha",
                )
                return

            if result.get('partial') and result.get('end_reason') == FetchEndReason.FETCH_INTERRUPTED:
                n = result.get('count', 0)
                task.end_reason = FetchEndReason.FETCH_INTERRUPTED
                task.error = (
                    f"Fetch cut short mid-page — {n} row(s) collected but not saved. Cause: {error}"
                )
                logger.error(
                    f"Fetch interrupted for {task.ticker} {task.date} (attempt {task.attempts}): {error} "
                    f"(end_reason={FetchEndReason.FETCH_INTERRUPTED})"
                )
            else:
                task.end_reason = None
                task.error = error
                logger.error(
                    f"Task failed {task.ticker} {task.date} (attempt {task.attempts}): {error}"
                )

            self._schedule_task_retry_backoff(job, task)

        except Exception as e:
            task.error = str(e)
            task.current_page = 0
            logger.error(f"Task exception {task.ticker} {task.date} (attempt {task.attempts}): {e}")
            self._schedule_task_retry_backoff(job, task)
