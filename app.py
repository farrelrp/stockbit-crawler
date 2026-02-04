"""
Stockbit Running Trade Scraper - Flask Web Application
"""
from flask import Flask, render_template, request, jsonify, send_file
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
import json

from config import (
    SECRET_KEY, DEBUG, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_MEMORY_LINES, DEFAULT_DELAY_SECONDS, DEFAULT_LIMIT
)
from auth import TokenManager
from stockbit_client import StockbitClient
from storage import CSVStorage
from jobs import JobManager
from orderbook_manager import OrderbookManager

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['DEBUG'] = DEBUG

# Setup logging
def setup_logging():
    """Configure application logging"""
    # create logs directory if needed
    LOG_FILE.parent.mkdir(exist_ok=True)
    
    # file handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    
    # console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(file_formatter)
    
    # configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # also configure app logger
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger(__name__)

# Initialize components
token_manager = TokenManager()
stockbit_client = StockbitClient(token_manager)
csv_storage = CSVStorage()
job_manager = JobManager(stockbit_client, csv_storage)
orderbook_manager = OrderbookManager(token_manager)

# In-memory log storage for UI
log_buffer = []

class LogBufferHandler(logging.Handler):
    """Custom handler to store logs in memory for UI"""
    def emit(self, record):
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'message': record.getMessage()
        }
        log_buffer.append(log_entry)
        # keep only last N entries
        if len(log_buffer) > LOG_MEMORY_LINES:
            log_buffer.pop(0)

# add buffer handler to root logger
log_buffer_handler = LogBufferHandler()
logging.getLogger().addHandler(log_buffer_handler)

# ===== WEB ROUTES =====

@app.route('/')
def index():
    """Dashboard home page"""
    return render_template('dashboard.html')

@app.route('/settings')
def settings():
    """Settings page"""
    return render_template('settings.html')

@app.route('/jobs')
def jobs_page():
    """Jobs management page"""
    return render_template('jobs.html')

@app.route('/captcha')
def captcha_page():
    """Captcha solving page"""
    return render_template('captcha.html')

@app.route('/files')
def files_page():
    """Output files listing page"""
    return render_template('files.html')

@app.route('/orderbook')
def orderbook_page():
    """Orderbook streaming page"""
    return render_template('orderbook.html')

# ===== API ENDPOINTS =====

# --- Authentication & Token ---

@app.route('/api/token/status', methods=['GET'])
def api_token_status():
    """Get current token status"""
    status = token_manager.get_status()
    return jsonify(status)

@app.route('/api/token/set', methods=['POST'])
def api_token_set():
    """Manually set Bearer token and optionally cookies"""
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    cookies = data.get('cookies', '').strip()  # Optional cookies
    
    if not token:
        return jsonify({
            'success': False,
            'error': 'Token required'
        })
    
    logger.info("Manual token set requested" + (" (with cookies)" if cookies else ""))
    
    result = token_manager.set_token(token, cookies if cookies else None)
    
    if result.get('success'):
        logger.info("Token set successfully")
        
        # auto-resume any paused jobs
        resumed = job_manager.auto_resume_paused_jobs()
        
        return jsonify({
            'success': True,
            'message': result.get('message'),
            'status': token_manager.get_status(),
            'resumed_jobs': resumed
        })
    else:
        logger.error(f"Failed to set token: {result.get('error')}")
        return jsonify(result)

# --- Jobs ---

@app.route('/api/jobs', methods=['GET'])
def api_jobs_list():
    """List all jobs"""
    jobs = job_manager.list_jobs()
    
    # add progress info to each job
    for job_dict in jobs:
        job = job_manager.get_job(job_dict['job_id'])
        if job:
            job_dict['progress'] = job.get_progress()
    
    return jsonify({
        'jobs': jobs,
        'current_job_id': job_manager.current_job_id
    })

@app.route('/api/jobs/<job_id>', methods=['GET'])
def api_job_get(job_id):
    """Get specific job details"""
    job = job_manager.get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    job_dict = job.to_dict()
    job_dict['progress'] = job.get_progress()
    
    return jsonify(job_dict)

@app.route('/api/jobs/create', methods=['POST'])
def api_job_create():
    """Create a new job"""
    data = request.get_json() or {}
    
    # parse tickers (can be newline-separated string or array)
    tickers_input = data.get('tickers', [])
    if isinstance(tickers_input, str):
        tickers = [t.strip().upper() for t in tickers_input.split('\n') if t.strip()]
    else:
        tickers = [t.strip().upper() for t in tickers_input if t.strip()]
    
    from_date = data.get('from_date')
    until_date = data.get('until_date')
    delay_seconds = float(data.get('delay_seconds', DEFAULT_DELAY_SECONDS))
    limit = int(data.get('limit', DEFAULT_LIMIT))
    parallel_workers = int(data.get('parallel_workers', 1))
    
    # validation
    if not tickers:
        return jsonify({'error': 'At least one ticker required'}), 400
    
    if not from_date or not until_date:
        return jsonify({'error': 'from_date and until_date required'}), 400
    
    if parallel_workers < 1 or parallel_workers > 10:
        return jsonify({'error': 'Parallel workers must be between 1 and 10'}), 400
    
    try:
        # validate date format
        datetime.strptime(from_date, '%Y-%m-%d')
        datetime.strptime(until_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format (use YYYY-MM-DD)'}), 400
    
    logger.info(f"Creating job: {len(tickers)} tickers, {from_date} to {until_date}, {parallel_workers} workers")
    
    try:
        job_id = job_manager.create_job(
            tickers=tickers,
            from_date=from_date,
            until_date=until_date,
            delay_seconds=delay_seconds,
            limit=limit,
            parallel_workers=parallel_workers
        )
        
        job = job_manager.get_job(job_id)
        job_dict = job.to_dict()
        job_dict['progress'] = job.get_progress()
        
        logger.info(f"Job {job_id} created with {len(job.tasks)} tasks")
        
        return jsonify({
            'success': True,
            'job_id': job_id,
            'job': job_dict
        })
        
    except Exception as e:
        logger.error(f"Failed to create job: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/jobs/<job_id>/pause', methods=['POST'])
def api_job_pause(job_id):
    """Pause a job"""
    job_manager.pause_job(job_id)
    return jsonify({'success': True})

@app.route('/api/jobs/<job_id>/resume', methods=['POST'])
def api_job_resume(job_id):
    """Resume a paused job"""
    job_manager.resume_job(job_id)
    return jsonify({'success': True})

@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def api_job_cancel(job_id):
    """Cancel a job"""
    job_manager.cancel_job(job_id)
    return jsonify({'success': True})

# --- Orderbook Streaming ---

@app.route('/api/orderbook/streams', methods=['GET'])
def api_orderbook_list_streams():
    """List all orderbook streaming sessions"""
    sessions = orderbook_manager.list_sessions()
    return jsonify({
        'success': True,
        'sessions': sessions
    })

@app.route('/api/orderbook/streams', methods=['POST'])
def api_orderbook_start_stream():
    """Start a new orderbook streaming session"""
    data = request.get_json() or {}
    
    session_id = data.get('session_id', f"stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    
    # parse tickers
    tickers_input = data.get('tickers', [])
    if isinstance(tickers_input, str):
        tickers = [t.strip().upper() for t in tickers_input.split('\n') if t.strip()]
    else:
        tickers = [t.strip().upper() for t in tickers_input if t.strip()]
    
    if not tickers:
        return jsonify({
            'success': False,
            'error': 'At least one ticker required'
        }), 400
    
    logger.info(f"Starting orderbook stream for {len(tickers)} tickers")
    
    result = orderbook_manager.start_stream(session_id, tickers)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/orderbook/streams/<session_id>', methods=['GET'])
def api_orderbook_get_stats(session_id):
    """Get statistics for an orderbook streaming session"""
    stats = orderbook_manager.get_session_stats(session_id)
    
    if stats:
        return jsonify({
            'success': True,
            'stats': stats
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Session not found'
        }), 404

@app.route('/api/orderbook/streams/<session_id>/stop', methods=['POST'])
def api_orderbook_stop_stream(session_id):
    """Stop an orderbook streaming session"""
    result = orderbook_manager.stop_stream(session_id)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

# --- Logs ---

@app.route('/api/logs', methods=['GET'])
def api_logs():
    """Get recent log entries"""
    limit = request.args.get('limit', LOG_MEMORY_LINES, type=int)
    return jsonify({
        'logs': log_buffer[-limit:]
    })

# --- Files ---

@app.route('/api/files', methods=['GET'])
def api_files_list():
    """List output CSV files"""
    files = csv_storage.list_output_files()
    return jsonify({'files': files})

@app.route('/api/files/download/<filename>', methods=['GET'])
def api_file_download(filename):
    """Download a CSV file"""
    filepath = csv_storage.get_file_path(filename)
    
    if not filepath.exists():
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename
    )

# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(e):
    """404 handler"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    """500 handler"""
    logger.error(f"Server error: {e}")
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('500.html'), 500

# ===== MAIN =====

if __name__ == '__main__':
    logger.info("Starting Stockbit Running Trade Scraper")
    logger.info(f"Debug mode: {DEBUG}")
    
    # start job worker
    job_manager.start_worker()
    
    try:
        app.run(host='0.0.0.0', port=5151, debug=DEBUG)
    finally:
        # cleanup on shutdown
        logger.info("Shutting down")
        job_manager.stop_worker()
        orderbook_manager.stop_all()



