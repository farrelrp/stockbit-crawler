"""
Job scheduler and manager for fetching trade data
"""
import threading
import time
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, asdict, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from database import JobDatabase

logger = logging.getLogger(__name__)

class JobStatus(Enum):
    """Job status enum"""
    QUEUED = 'QUEUED'
    RUNNING = 'RUNNING'
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'

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
    records_fetched: int = 0
    pages_fetched: int = 0
    current_page: int = 0  # real-time page being fetched
    attempts: int = 0

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
    status: JobStatus = JobStatus.QUEUED
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    tasks: List[Task] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization"""
        data = asdict(self)
        data['status'] = self.status.value
        # convert task status enums
        for task_dict in data['tasks']:
            task_dict['status'] = task_dict['status'].value if isinstance(task_dict['status'], TaskStatus) else task_dict['status']
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
        
        # load persisted jobs on startup
        self._load_jobs_from_db()
    
    def _load_jobs_from_db(self):
        """Load persisted jobs from database on startup"""
        try:
            db_jobs = self.db.get_all_jobs(limit=50)
            loaded_count = 0
            for job_data in db_jobs:
                # only load jobs that aren't completed or failed
                if job_data['status'] not in ['COMPLETED', 'FAILED']:
                    # reconstruct job with tasks
                    from datetime import timedelta
                    start = datetime.strptime(job_data['from_date'], '%Y-%m-%d')
                    end = datetime.strptime(job_data['until_date'], '%Y-%m-%d')
                    
                    # generate dates
                    dates = []
                    current = start
                    while current <= end:
                        dates.append(current.strftime('%Y-%m-%d'))
                        current += timedelta(days=1)
                    
                    # create tasks
                    tasks = []
                    for ticker in job_data['tickers']:
                        for date in dates:
                            tasks.append(Task(ticker=ticker, date=date))
                    
                    job = Job(
                        job_id=job_data['job_id'],
                        tickers=job_data['tickers'],
                        from_date=job_data['from_date'],
                        until_date=job_data['until_date'],
                        delay_seconds=job_data['delay_seconds'],
                        limit=job_data['limit_per_request'],
                        status=JobStatus(job_data['status']),
                        created_at=job_data['created_at'],
                        tasks=tasks
                    )
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
        parallel_workers: int = 1
    ) -> str:
        """Create a new job with tasks for each ticker-date combination"""
        job_id = str(uuid.uuid4())
        
        # parse dates
        start = datetime.strptime(from_date, '%Y-%m-%d')
        end = datetime.strptime(until_date, '%Y-%m-%d')
        
        # generate all date strings in range
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
        
        # create tasks for each ticker-date combo
        tasks = []
        for ticker in tickers:
            for date in dates:
                tasks.append(Task(ticker=ticker, date=date))
        
        # create job
        job = Job(
            job_id=job_id,
            tickers=tickers,
            from_date=from_date,
            until_date=until_date,
            delay_seconds=delay_seconds,
            limit=limit,
            parallel_workers=parallel_workers,
            tasks=tasks
        )
        
        self.jobs[job_id] = job
        
        # persist to database
        self._persist_job(job)
        
        logger.info(f"Created job {job_id} with {len(tasks)} tasks")
        
        # start worker if not running
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
            job.status = JobStatus.QUEUED
            self.pause_flag.clear()
            self._persist_job(job)
            logger.info(f"Job {job_id} resumed")
            
            # restart worker if not running
            if not self.worker_thread or not self.worker_thread.is_alive():
                self.start_worker()
    
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
            job.status = JobStatus.FAILED
            self._persist_job(job)
            logger.info(f"Job {job_id} cancelled")
    
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
            # find next queued job
            next_job = None
            for job in self.jobs.values():
                if job.status == JobStatus.QUEUED:
                    next_job = job
                    break
            
            if next_job:
                self._process_job(next_job)
            else:
                # no jobs to process, sleep a bit
                time.sleep(1)
    
    def _process_job(self, job: Job):
        """Process all tasks in a job with optional parallelism"""
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now().isoformat()
        self.current_job_id = job.job_id
        
        workers = job.parallel_workers
        logger.info(f"Starting job {job.job_id} with {workers} parallel worker(s)")
        
        try:
            # get pending tasks
            pending_tasks = [t for t in job.tasks if t.status not in [TaskStatus.COMPLETED, TaskStatus.SKIPPED]]
            
            if workers == 1:
                # sequential processing (original behavior)
                for task in pending_tasks:
                    # check if paused or stopped
                    while self.pause_flag.is_set() and not self.stop_flag.is_set():
                        time.sleep(0.5)
                    
                    if self.stop_flag.is_set():
                        job.status = JobStatus.PAUSED
                        return
                    
                    self._process_task(job, task)
                    
                    # delay between requests
                    if job.delay_seconds > 0:
                        time.sleep(job.delay_seconds)
            
            else:
                # parallel processing with thread pool
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    # submit all tasks
                    future_to_task = {
                        executor.submit(self._process_task, job, task): task 
                        for task in pending_tasks
                    }
                    
                    # process as they complete
                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        
                        # check if paused or stopped
                        while self.pause_flag.is_set() and not self.stop_flag.is_set():
                            time.sleep(0.5)
                        
                        if self.stop_flag.is_set():
                            job.status = JobStatus.PAUSED
                            executor.shutdown(wait=False)
                            return
                        
                        try:
                            future.result()  # get result to catch exceptions
                        except Exception as e:
                            logger.error(f"Task {task.ticker} {task.date} raised exception: {e}")
                        
                        # small delay after each completed task
                        if job.delay_seconds > 0:
                            time.sleep(job.delay_seconds)
            
            # check if job should be paused (token expired)
            if job.status == JobStatus.PAUSED:
                logger.warning(f"Job {job.job_id} paused during execution")
                return
            
            # job completed
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now().isoformat()
            self._persist_job(job)
            logger.info(f"[OK] Job {job.job_id} completed")
            
        except Exception as e:
            logger.error(f"Job {job.job_id} failed with error: {e}")
            job.status = JobStatus.FAILED
            self._persist_job(job)
        
        finally:
            self.current_job_id = None
    
    def _process_task(self, job: Job, task: Task):
        """Process a single task (fetch data for one ticker-date)"""
        task.status = TaskStatus.RUNNING
        task.attempts += 1
        task.current_page = 0
        
        logger.info(f"Fetching {task.ticker} for {task.date}")
        
        # progress callback to update task in real-time
        def update_progress(page: int, total_records: int):
            task.current_page = page
            task.records_fetched = total_records
        
        try:
            # fetch data with progress tracking
            result = self.client.fetch_running_trade(
                ticker=task.ticker,
                date=task.date,
                limit=job.limit,
                progress_callback=update_progress
            )
            
            if result.get('success'):
                # save to CSV
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
                    logger.info(f"Saved {task.records_fetched} records ({task.pages_fetched} pages) for {task.ticker} {task.date}")
                    # persist progress every 5 tasks
                    progress = job.get_progress()
                    if progress['completed'] % 5 == 0:
                        self._persist_job(job)
                else:
                    task.status = TaskStatus.FAILED
                    task.error = save_result.get('error', 'Unknown save error')
                    logger.error(f"Failed to save {task.ticker} {task.date}: {task.error}")
            
            else:
                # fetch failed
                error = result.get('error', 'Unknown error')
                
                # handle special cases - pause job gracefully
                if result.get('requires_login'):
                    # token issue - pause job and reset task to pending
                    job.status = JobStatus.PAUSED
                    task.status = TaskStatus.PENDING  # will retry when resumed
                    task.error = 'Token expired - job paused'
                    task.current_page = 0
                    self._persist_job(job)
                    logger.warning(f"🔐 Job {job.job_id} PAUSED - Token expired. Set new token to resume.")
                    return
                
                elif result.get('captcha_required'):
                    # captcha - pause job
                    job.status = JobStatus.PAUSED
                    task.status = TaskStatus.PENDING
                    task.error = 'Captcha required'
                    task.current_page = 0
                    self._persist_job(job)
                    logger.warning(f"Job {job.job_id} paused due to captcha")
                    return
                
                else:
                    # other error - mark task failed and continue
                    task.status = TaskStatus.FAILED
                    task.error = error
                    logger.error(f"Task failed {task.ticker} {task.date}: {error}")
        
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.current_page = 0
            logger.error(f"Task exception {task.ticker} {task.date}: {e}")

