"""
Database manager for persisting jobs
"""
import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta
from config import CONFIG_DIR

logger = logging.getLogger(__name__)

DB_FILE = CONFIG_DIR / 'jobs.db'

class JobDatabase:
    """Simple SQLite database for job persistence"""
    
    def __init__(self):
        self.db_path = DB_FILE
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # jobs table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        tickers TEXT NOT NULL,
                        from_date TEXT NOT NULL,
                        until_date TEXT NOT NULL,
                        delay_seconds REAL DEFAULT 3.0,
                        limit_per_request INTEGER DEFAULT 50,
                        status TEXT DEFAULT 'QUEUED',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        total_tasks INTEGER DEFAULT 0,
                        completed_tasks INTEGER DEFAULT 0,
                        failed_tasks INTEGER DEFAULT 0,
                        error TEXT,
                        start_time TEXT,
                        end_time TEXT,
                        total_records INTEGER DEFAULT 0
                    )
                ''')
                
                # tasks table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        status TEXT DEFAULT 'PENDING',
                        error TEXT,
                        records_fetched INTEGER DEFAULT 0,
                        attempts INTEGER DEFAULT 0,
                        FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                    )
                ''')
                
                conn.commit()
                logger.info(f"Database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    def save_job(self, job_data: Dict[str, Any]) -> bool:
        """Save or update a job"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # convert tickers list to JSON string
                tickers_json = json.dumps(job_data.get('tickers', []))
                
                cursor.execute('''
                    INSERT OR REPLACE INTO jobs (
                        job_id, tickers, from_date, until_date, delay_seconds,
                        limit_per_request, status, created_at, updated_at,
                        total_tasks, completed_tasks, failed_tasks, error,
                        start_time, end_time, total_records
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    job_data['job_id'],
                    tickers_json,
                    job_data['from_date'],
                    job_data['until_date'],
                    job_data.get('delay_seconds', 3.0),
                    job_data.get('limit', 50),
                    job_data.get('status', 'QUEUED'),
                    job_data.get('created_at', datetime.now().isoformat()),
                    datetime.now().isoformat(),
                    job_data.get('total_tasks', 0),
                    job_data.get('completed_tasks', 0),
                    job_data.get('failed_tasks', 0),
                    job_data.get('error'),
                    job_data.get('start_time'),
                    job_data.get('end_time'),
                    job_data.get('total_records', 0)
                ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save job {job_data.get('job_id')}: {e}")
            return False
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a job by ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
                row = cursor.fetchone()
                
                if row:
                    job = dict(row)
                    job['tickers'] = json.loads(job['tickers'])
                    return job
                return None
        except Exception as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None
    
    def get_all_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all jobs, most recent first"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT * FROM jobs 
                    ORDER BY created_at DESC 
                    LIMIT ?
                ''', (limit,))
                
                jobs = []
                for row in cursor.fetchall():
                    job = dict(row)
                    job['tickers'] = json.loads(job['tickers'])
                    jobs.append(job)
                
                return jobs
        except Exception as e:
            logger.error(f"Failed to get all jobs: {e}")
            return []
    
    def delete_job(self, job_id: str) -> bool:
        """Delete a job and its tasks"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM jobs WHERE job_id = ?', (job_id,))
                cursor.execute('DELETE FROM tasks WHERE job_id = ?', (job_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to delete job {job_id}: {e}")
            return False
    
    def save_task(self, job_id: str, task_data: Dict[str, Any]) -> bool:
        """Save or update a task"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO tasks (
                        job_id, ticker, date, status, error, records_fetched, attempts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    job_id,
                    task_data['ticker'],
                    task_data['date'],
                    task_data.get('status', 'PENDING'),
                    task_data.get('error'),
                    task_data.get('records_fetched', 0),
                    task_data.get('attempts', 0)
                ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save task for job {job_id}: {e}")
            return False
    
    def get_job_tasks(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all tasks for a job"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('''
                    SELECT * FROM tasks 
                    WHERE job_id = ?
                    ORDER BY date, ticker
                ''', (job_id,))
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get tasks for job {job_id}: {e}")
            return []
    
    def clear_old_jobs(self, days: int = 30) -> int:
        """Clear jobs older than N days"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                
                cursor.execute('''
                    DELETE FROM jobs 
                    WHERE created_at < ? AND status IN ('COMPLETED', 'FAILED')
                ''', (cutoff,))
                
                deleted = cursor.rowcount
                conn.commit()
                logger.info(f"Cleared {deleted} old jobs")
                return deleted
        except Exception as e:
            logger.error(f"Failed to clear old jobs: {e}")
            return 0

