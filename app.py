"""
Stockbit Running Trade Scraper - Flask Web Application
"""
from flask import Flask, render_template, request, jsonify, send_file
import logging
from logging.handlers import RotatingFileHandler
import platform

class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that handles Windows file locking during rotation.
    
    On Windows, os.rename() fails with PermissionError when another thread
    has the log file open. This subclass retries the rotation and silently
    skips if it still can't rename.
    """
    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            # On Windows, another thread may be holding the file open.
            # Skip this rotation attempt; it will succeed on the next one.
            pass
from datetime import datetime
from pathlib import Path
import json
import time

from config import (
    SECRET_KEY, DEBUG, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_MEMORY_LINES, DEFAULT_DELAY_SECONDS, DEFAULT_LIMIT, ORDERBOOK_DIR,
    ORDERBOOK_WATCHLIST_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    TELEGRAM_HEARTBEAT_MINUTES
)
from auth import TokenManager
from stockbit_client import StockbitClient
from storage import CSVStorage
from jobs import JobManager
from orderbook_manager import OrderbookManager
from orderbook_daemon import OrderbookDaemon
from perspective_server import start_perspective_server, get_perspective_server
from replay_engine import get_replay_engine

# Module-level globals for Telegram bot (set in __main__)
telegram_bot_instance = None

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
    file_handler = SafeRotatingFileHandler(
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

# ===== INDONESIAN MARKET HOURS LOGIC =====

def get_market_status():
    """
    Determine current Indonesian market status and calculate next open time
    
    Returns:
        Dict with:
            - status: 'open', 'closed', or 'break'
            - current_time: current WIB time
            - next_open: datetime of next session open (None if currently open)
            - next_close: datetime of next session close (None if currently closed)
            - time_until_next: seconds until next open (0 if open)
            - session: which session (1 or 2, None if closed)
    """
    from datetime import datetime, timedelta
    import pytz
    
    # Indonesia Western Time (WIB) is GMT+7
    wib_tz = pytz.timezone('Asia/Jakarta')
    now_wib = datetime.now(wib_tz)
    
    # Market is closed on weekends
    if now_wib.weekday() >= 5:  # Saturday=5, Sunday=6
        # Calculate next Monday 9:00 AM
        days_until_monday = 7 - now_wib.weekday()
        next_monday = now_wib.date() + timedelta(days=days_until_monday)
        next_open = wib_tz.localize(datetime.combine(next_monday, datetime.strptime('08:55', '%H:%M').time()))
        
        return {
            'status': 'closed',
            'current_time': now_wib.isoformat(),
            'next_open': next_open.isoformat(),
            'next_close': None,
            'time_until_next': int((next_open - now_wib).total_seconds()),
            'session': None,
            'reason': 'Weekend'
        }
    
    # Define market hours with 5-minute margins
    # Monday-Thursday schedule
    if now_wib.weekday() < 4:  # Monday=0 to Thursday=3
        session1_open = datetime.strptime('08:55', '%H:%M').time()  # 9:00 - 5min margin
        session1_close = datetime.strptime('12:05', '%H:%M').time()  # 12:00 + 5min margin
        session2_open = datetime.strptime('13:25', '%H:%M').time()  # 13:30 - 5min margin
        session2_close = datetime.strptime('15:54', '%H:%M').time()  # 15:49 + 5min margin
    else:  # Friday=4
        session1_open = datetime.strptime('08:55', '%H:%M').time()
        session1_close = datetime.strptime('11:35', '%H:%M').time()  # 11:30 + 5min margin
        session2_open = datetime.strptime('13:55', '%H:%M').time()  # 14:00 - 5min margin
        session2_close = datetime.strptime('15:54', '%H:%M').time()
    
    current_time = now_wib.time()
    today = now_wib.date()
    
    # Check if we're in session 1
    if session1_open <= current_time < session1_close:
        next_close = wib_tz.localize(datetime.combine(today, session1_close))
        return {
            'status': 'open',
            'current_time': now_wib.isoformat(),
            'next_open': None,
            'next_close': next_close.isoformat(),
            'time_until_next': 0,
            'session': 1,
            'reason': 'Session 1'
        }
    
    # Check if we're in session 2
    elif session2_open <= current_time < session2_close:
        next_close = wib_tz.localize(datetime.combine(today, session2_close))
        return {
            'status': 'open',
            'current_time': now_wib.isoformat(),
            'next_open': None,
            'next_close': next_close.isoformat(),
            'time_until_next': 0,
            'session': 2,
            'reason': 'Session 2'
        }
    
    # Check if we're in lunch break
    elif session1_close <= current_time < session2_open:
        next_open = wib_tz.localize(datetime.combine(today, session2_open))
        return {
            'status': 'break',
            'current_time': now_wib.isoformat(),
            'next_open': next_open.isoformat(),
            'next_close': None,
            'time_until_next': int((next_open - now_wib).total_seconds()),
            'session': None,
            'reason': 'Lunch Break'
        }
    
    # Before market opens today
    elif current_time < session1_open:
        next_open = wib_tz.localize(datetime.combine(today, session1_open))
        return {
            'status': 'closed',
            'current_time': now_wib.isoformat(),
            'next_open': next_open.isoformat(),
            'next_close': None,
            'time_until_next': int((next_open - now_wib).total_seconds()),
            'session': None,
            'reason': 'Pre-Market'
        }
    
    # After market closes today (after session 2)
    else:
        # Calculate next trading day
        if now_wib.weekday() == 4:  # Friday
            days_ahead = 3  # Next Monday
        else:
            days_ahead = 1  # Tomorrow
        
        next_day = today + timedelta(days=days_ahead)
        next_open = wib_tz.localize(datetime.combine(next_day, datetime.strptime('08:55', '%H:%M').time()))
        
        return {
            'status': 'closed',
            'current_time': now_wib.isoformat(),
            'next_open': next_open.isoformat(),
            'next_close': None,
            'time_until_next': int((next_open - now_wib).total_seconds()),
            'session': None,
            'reason': 'After Hours'
        }

# Initialize components
token_manager = TokenManager()
stockbit_client = StockbitClient(token_manager)
csv_storage = CSVStorage()
job_manager = JobManager(stockbit_client, csv_storage)
orderbook_manager = OrderbookManager(token_manager)
orderbook_daemon = OrderbookDaemon(token_manager, ORDERBOOK_WATCHLIST_FILE)

# Initialize Perspective server and replay engine
perspective_server = None
replay_engine = None

def init_perspective():
    """Initialize Perspective server and replay engine (call after app starts)"""
    global perspective_server, replay_engine
    if perspective_server is None:
        perspective_server = start_perspective_server(port=8888)
        table = perspective_server.get_table()
        replay_engine = get_replay_engine(table)
        logger.info("Perspective server and replay engine initialized")

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

@app.route('/replay/perspective')
def replay_perspective():
    """Advanced Perspective replay view"""
    return render_template('market_replay.html')

@app.route('/replay/debug')
def replay_debug_page():
    """Debug console for troubleshooting replay issues"""
    return render_template('replay_debug.html')

@app.route('/replay/test')
def replay_test_page():
    """Test Perspective CDN loading"""
    return render_template('test_perspective.html')

@app.route('/replay/orderbook')
def replay_orderbook():
    """Simple orderbook replay view (client-side)"""
    return render_template('simple_orderbook.html')

@app.route('/replay/workspace')
def replay_workspace():
    """Multi-panel workspace dashboard for orderbook analysis"""
    return render_template('workspace_replay.html')

@app.route('/replay')
def replay_index():
    """Landing page for replay views"""
    return render_template('replay_index.html')

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

# --- Auto Login ---
auto_auth_instance = None

def _get_auto_auth():
    """Lazy-load AutoAuth (pure Python, no external browser deps)."""
    global auto_auth_instance
    if auto_auth_instance is None:
        from auto_auth import AutoAuth
        auto_auth_instance = AutoAuth(token_manager)
    return auto_auth_instance

@app.route('/api/token/auto-login', methods=['POST'])
def api_token_auto_login():
    """Start automated login: 2Captcha v3 + direct HTTP POST."""
    auto_auth = _get_auto_auth()

    data = request.get_json() or {}
    email = data.get('email', '').strip() or None
    password = data.get('password', '').strip() or None

    result = auto_auth.start_login(email=email, password=password)
    return jsonify(result)

@app.route('/api/token/auto-login/status', methods=['GET'])
def api_token_auto_login_status():
    """Get auto-login progress and result."""
    auto_auth = _get_auto_auth()
    if not auto_auth:
        return jsonify({'running': False, 'status': 'unavailable', 'progress': [], 'result': None})
    return jsonify(auto_auth.get_status())

@app.route('/api/token/auto-login/config', methods=['GET'])
def api_token_auto_login_config():
    """Check if 2Captcha API key is configured."""
    from config import TWOCAPTCHA_API_KEY
    key = TWOCAPTCHA_API_KEY
    has_key = bool(key and key.strip())
    masked = key[:4] + '...' + key[-4:] if has_key and len(key) > 8 else ''
    return jsonify({
        'has_captcha_key': has_key,
        'captcha_key_masked': masked,
    })

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
    """Start a new orderbook streaming session with auto-reconnect"""
    data = request.get_json() or {}
    
    session_id = data.get('session_id', f"stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    max_retries = data.get('max_retries', None)  # None = infinite retries
    token = data.get('token')
    cookies = data.get('cookies')
    
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
    
    logger.info(f"Starting orderbook stream for {len(tickers)} tickers (max_retries={max_retries})" + 
                (" [TOKEN OVERRIDE]" if token else ""))
    
    result = orderbook_manager.start_stream(session_id, tickers, max_retries=max_retries, token=token, cookies=cookies)
    
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

@app.route('/api/orderbook/streams/<session_id>/refresh', methods=['POST'])
def api_orderbook_refresh_stream(session_id):
    """Refresh (restart) an orderbook streaming session"""
    result = orderbook_manager.refresh_stream(session_id)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/orderbook/market-status', methods=['GET'])
def api_orderbook_market_status():
    """Get current Indonesian market status and timing information"""
    try:
        status = get_market_status()
        return jsonify({
            'success': True,
            **status
        })
    except Exception as e:
        logger.error(f"Error getting market status: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# --- Orderbook Daemon ---

@app.route('/api/orderbook/daemon/status', methods=['GET'])
def api_orderbook_daemon_status():
    """Get full daemon status"""
    try:
        status = orderbook_daemon.get_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        logger.error(f"Error getting daemon status: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/orderbook/daemon/tickers', methods=['POST'])
def api_orderbook_daemon_set_tickers():
    """Set/update daemon watchlist tickers"""
    data = request.get_json() or {}
    action = data.get('action', 'set')  # set, add, remove
    
    tickers_input = data.get('tickers', [])
    if isinstance(tickers_input, str):
        tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
    else:
        tickers = [t.strip().upper() for t in tickers_input if t.strip()]
    
    if action == 'add':
        result = orderbook_daemon.add_tickers(tickers)
    elif action == 'remove':
        result = orderbook_daemon.remove_tickers(tickers)
    else:
        result = orderbook_daemon.set_tickers(tickers)
    
    return jsonify(result)

@app.route('/api/orderbook/daemon/pause', methods=['POST'])
def api_orderbook_daemon_pause():
    """Pause the daemon stream"""
    result = orderbook_daemon.pause()
    return jsonify(result)

@app.route('/api/orderbook/daemon/resume', methods=['POST'])
def api_orderbook_daemon_resume():
    """Resume the daemon stream"""
    result = orderbook_daemon.resume()
    return jsonify(result)

@app.route('/api/orderbook/daemon/reconnect', methods=['POST'])
def api_orderbook_daemon_reconnect():
    """Set token/cookies and reconnect the stream"""
    data = request.get_json() or {}
    token = data.get('token', '').strip()
    cookies = data.get('cookies', '').strip()
    
    if not token:
        return jsonify({'success': False, 'error': 'Token required'}), 400
    
    result = orderbook_daemon.set_token_and_reconnect(token, cookies if cookies else None)
    return jsonify(result)

@app.route('/api/orderbook/daemon/recap', methods=['GET'])
def api_orderbook_daemon_recap():
    """Get daily recap"""
    try:
        recap = orderbook_daemon.get_daily_recap()
        return jsonify({'success': True, **recap})
    except Exception as e:
        logger.error(f"Error getting daemon recap: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

# --- Market Replay ---

@app.route('/api/replay/files', methods=['GET'])
def api_replay_list_files():
    """List available orderbook CSV files for replay"""
    try:
        orderbook_dir = ORDERBOOK_DIR
        csv_files = sorted(orderbook_dir.glob('*.csv'), reverse=True)
        
        files = []
        for csv_file in csv_files:
            # Parse filename: YYYY-MM-DD_TICKER.csv
            parts = csv_file.stem.split('_')
            if len(parts) >= 2:
                date = parts[0]
                ticker = parts[1]
            else:
                date = 'unknown'
                ticker = csv_file.stem
            
            files.append({
                'filename': csv_file.name,
                'path': str(csv_file),
                'date': date,
                'ticker': ticker,
                'size_mb': round(csv_file.stat().st_size / 1024 / 1024, 2)
            })
        
        return jsonify({
            'success': True,
            'files': files
        })
    except Exception as e:
        logger.error(f"Error listing replay files: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/replay/metadata', methods=['POST'])
def api_replay_metadata():
    """Get file metadata without loading full file"""
    data = request.get_json() or {}
    csv_path = data.get('csv_path')
    
    if not csv_path:
        return jsonify({
            'success': False,
            'error': 'csv_path required'
        }), 400
    
    try:
        csv_file = Path(csv_path)
        if not csv_file.exists():
            return jsonify({
                'success': False,
                'error': 'File not found'
            }), 404
        
        # Quick line count without loading entire file
        import subprocess
        try:
            result = subprocess.run(
                ['wc', '-l', str(csv_file)],
                capture_output=True,
                text=True,
                timeout=5
            )
            line_count = int(result.stdout.split()[0]) - 1  # subtract header
        except Exception as e:
            logger.warning(f"Could not count lines: {e}")
            line_count = -1
        
        file_size_mb = csv_file.stat().st_size / 1024 / 1024
        
        return jsonify({
            'success': True,
            'file_size_mb': round(file_size_mb, 2),
            'estimated_rows': line_count,
            'filename': csv_file.name
        })
        
    except Exception as e:
        logger.error(f"Error getting metadata: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/replay/load', methods=['POST'])
def api_replay_load():
    """Load a CSV file for replay"""
    if not replay_engine:
        init_perspective()
    
    # Stop any running replay before loading new data
    if replay_engine and replay_engine.running:
        logger.info("Stopping existing replay before loading new file")
        replay_engine.stop()
        time.sleep(0.2)  # Brief delay to ensure thread stops
    
    data = request.get_json() or {}
    csv_path = data.get('csv_path')
    
    if not csv_path:
        return jsonify({
            'success': False,
            'error': 'csv_path required'
        }), 400
    
    result = replay_engine.load_csv(csv_path)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/start', methods=['POST'])
def api_replay_start():
    """Start replay playback"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    data = request.get_json() or {}
    speed_multiplier = float(data.get('speed_multiplier', 1.0))
    
    result = replay_engine.start(speed_multiplier=speed_multiplier)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/pause', methods=['POST'])
def api_replay_pause():
    """Pause replay playback"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    result = replay_engine.pause()
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/resume', methods=['POST'])
def api_replay_resume():
    """Resume replay playback"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    result = replay_engine.resume()
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/stop', methods=['POST'])
def api_replay_stop():
    """Stop replay playback"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    result = replay_engine.stop()
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/seek', methods=['POST'])
def api_replay_seek():
    """Seek to a specific position in the replay"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    data = request.get_json() or {}
    position = data.get('position')
    
    if position is None:
        return jsonify({
            'success': False,
            'error': 'position required'
        }), 400
    
    try:
        position = int(position)
    except ValueError:
        return jsonify({
            'success': False,
            'error': 'position must be an integer'
        }), 400
    
    result = replay_engine.seek(position)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/speed', methods=['POST'])
def api_replay_set_speed():
    """Set replay speed multiplier"""
    if not replay_engine:
        return jsonify({
            'success': False,
            'error': 'Replay engine not initialized'
        }), 400
    
    data = request.get_json() or {}
    multiplier = data.get('multiplier')
    
    if multiplier is None:
        return jsonify({
            'success': False,
            'error': 'multiplier required'
        }), 400
    
    try:
        multiplier = float(multiplier)
    except ValueError:
        return jsonify({
            'success': False,
            'error': 'multiplier must be a number'
        }), 400
    
    result = replay_engine.set_speed(multiplier)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400

@app.route('/api/replay/status', methods=['GET'])
def api_replay_status():
    """Get current replay status"""
    if not replay_engine:
        return jsonify({
            'success': True,
            'status': {
                'running': False,
                'csv_loaded': False,
                'message': 'Replay engine not initialized'
            }
        })
    
    status = replay_engine.get_status()
    
    return jsonify({
        'success': True,
        'status': status
    })

@app.route('/api/replay/data', methods=['GET'])
def api_replay_get_full_data():
    """Get ALL data rows for client-side replay"""
    if not replay_engine or not replay_engine.data_rows:
        return jsonify({
            'success': False,
            'error': 'No data loaded'
        }), 400
    
    # Optimization: return list of lists instead of dicts for smaller payload
    # Format: [timestamp_ts, price, freq, lot_size, side (0=bid, 1=offer)]
    rows = []
    for row in replay_engine.data_rows:
        rows.append([
            row['timestamp'].timestamp() * 1000, # ms timestamp
            row['price'],
            row['freq'],
            row['lot_size'],
            0 if row['side'] == 'BID' else 1
        ])
    
    return jsonify({
        'success': True,
        'rows': rows,
        'total_rows': len(rows),
        'ticker': replay_engine.get_status().get('csv_path', '').split('_')[-1]
    })

@app.route('/api/replay/data/chunked', methods=['GET'])
def api_replay_get_chunked_data():
    """Get data in chunks for large files"""
    if not replay_engine or not replay_engine.data_rows:
        return jsonify({
            'success': False,
            'error': 'No data loaded'
        }), 400
    
    try:
        chunk_size = int(request.args.get('chunk_size', 100000))
        offset = int(request.args.get('offset', 0))
        
        total = len(replay_engine.data_rows)
        end = min(offset + chunk_size, total)
        
        if offset >= total:
            return jsonify({
                'success': True,
                'rows': [],
                'offset': offset,
                'chunk_size': 0,
                'total_rows': total,
                'has_more': False
            })
        
        chunk = replay_engine.data_rows[offset:end]
        
        # Convert to compact format
        rows = []
        for row in chunk:
            rows.append([
                row['timestamp'].timestamp() * 1000,  # ms timestamp
                row['price'],
                row['freq'],
                row['lot_size'],
                0 if row['side'] == 'BID' else 1
            ])
        
        logger.info(f"Serving chunk: offset={offset}, size={len(rows)}, total={total}")
        
        return jsonify({
            'success': True,
            'rows': rows,
            'offset': offset,
            'chunk_size': len(rows),
            'total_rows': total,
            'has_more': end < total
        })
        
    except Exception as e:
        logger.error(f"Error in chunked data: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/replay/orderbook', methods=['GET'])
def api_get_current_orderbook():
    """Get current orderbook state for simple view"""
    if not replay_engine:
        logger.error("Replay engine not initialized")
        return jsonify({'success': False, 'error': 'Replay engine not initialized'}), 500
    
    # Get current orderbook state from replay engine
    state = replay_engine.state
    
    # Debug logging (first few calls only)
    if not hasattr(api_get_current_orderbook, 'call_count'):
        api_get_current_orderbook.call_count = 0
    api_get_current_orderbook.call_count += 1
    
    if api_get_current_orderbook.call_count <= 3:
        logger.info(f"[DEBUG] Orderbook API call #{api_get_current_orderbook.call_count}: state has {len(state)} entries, running={replay_engine.running}")
    
    if not state:
        return jsonify({
            'success': True,
            'bids': [],
            'offers': [],
            'total_levels': 0,
            'running': replay_engine.running,
            'index': replay_engine.current_index,
            'total_rows': replay_engine.total_rows
        })
    
    # Separate into bids and offers, sorted
    bids = []
    offers = []
    
    for (price, side), lots in state.items():
        if side == 'BID':
            bids.append({'price': price, 'lots': lots})
        else:
            offers.append({'price': price, 'lots': lots})
    
    # Sort: bids highest first, offers lowest first
    bids.sort(key=lambda x: x['price'], reverse=True)
    offers.sort(key=lambda x: x['price'])
    
    # Take top 20 of each
    bids = bids[:20]
    offers = offers[:20]
    
    # Debug log (first few times with data)
    if api_get_current_orderbook.call_count <= 5 and (bids or offers):
        logger.info(f"[DEBUG] Returning {len(bids)} bids, {len(offers)} offers from {len(state)} total levels")
    
    return jsonify({
        'success': True,
        'bids': bids,
        'offers': offers,
        'total_levels': len(state),
        'running': replay_engine.running,
        'index': replay_engine.current_index,
        'total_rows': replay_engine.total_rows,
        'current_timestamp': replay_engine.current_timestamp.isoformat() if replay_engine.current_timestamp else None,
        'last_bid_timestamp': replay_engine.last_bid_timestamp.isoformat() if replay_engine.last_bid_timestamp else None,
        'last_offer_timestamp': replay_engine.last_offer_timestamp.isoformat() if replay_engine.last_offer_timestamp else None
    })

# --- Telegram Bot ---

@app.route('/api/telegram/status', methods=['GET'])
def api_telegram_status():
    """Get Telegram bot status for UI"""
    if telegram_bot_instance:
        status = telegram_bot_instance.get_status()
        return jsonify({'success': True, **status})
    else:
        return jsonify({
            'success': True,
            'running': False,
            'token_configured': bool(TELEGRAM_BOT_TOKEN),
            'chat_id_configured': bool(TELEGRAM_CHAT_ID),
            'bot_info': None,
            'job_queue_available': False,
            'last_error': 'Bot not initialized (missing python-telegram-bot or token)',
        })

@app.route('/api/telegram/test', methods=['POST'])
def api_telegram_test():
    """Send a test message via Telegram bot"""
    if not telegram_bot_instance:
        return jsonify({'success': False, 'error': 'Telegram bot is not running'}), 400
    
    result = telegram_bot_instance.send_test_message()
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
    import os
    import atexit
    import signal
    
    logger.info("Starting Stockbit Running Trade Scraper")
    logger.info(f"Debug mode: {DEBUG}")
    
    def _cleanup():
        """Shared cleanup — registered with atexit so it runs even when
        Windows TerminateProcess bypasses finally blocks."""
        logger.info("Shutting down")
        job_manager.stop_worker()
        orderbook_daemon.stop()
        orderbook_manager.stop_all()
        if telegram_bot_instance:
            telegram_bot_instance.stop()
        if replay_engine:
            replay_engine.stop()
        if perspective_server:
            perspective_server.stop()
    
    # Only run background tasks in the reloader child (or if debug is off).
    # This prevents running two instances of the bot/daemon when using Flask reloader.
    if not DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        job_manager.start_worker()
        
        init_perspective()
        
        orderbook_daemon.start()
        logger.info("Orderbook daemon started")
        
        # -- optional Google Drive uploader --
        gdrive_uploader = None
        try:
            from config import (
                GDRIVE_SERVICE_ACCOUNT_FILE, GDRIVE_FOLDER_ID,
                GDRIVE_DELETE_AFTER_UPLOAD,
            )
            if GDRIVE_SERVICE_ACCOUNT_FILE and GDRIVE_FOLDER_ID:
                from pathlib import Path as _P
                sa_path = _P(GDRIVE_SERVICE_ACCOUNT_FILE)
                if sa_path.exists():
                    from gdrive_uploader import GDriveUploader
                    gdrive_uploader = GDriveUploader(
                        str(sa_path), GDRIVE_FOLDER_ID,
                        delete_after_upload=GDRIVE_DELETE_AFTER_UPLOAD,
                    )
                    logger.info("Google Drive uploader initialised")
                else:
                    logger.info(f"GDrive service account file not found ({sa_path}), uploads disabled")
        except (ImportError, AttributeError) as e:
            logger.info(f"Google Drive upload disabled: {e}")

        if TELEGRAM_BOT_TOKEN:
            try:
                from telegram_bot import TelegramBot
                telegram_bot_instance = TelegramBot(
                    token=TELEGRAM_BOT_TOKEN,
                    chat_id=TELEGRAM_CHAT_ID,
                    daemon=orderbook_daemon,
                    heartbeat_minutes=TELEGRAM_HEARTBEAT_MINUTES,
                    job_manager=job_manager,
                    gdrive_uploader=gdrive_uploader,
                    orderbook_dir=ORDERBOOK_DIR,
                )
                telegram_bot_instance.start()
                logger.info("Telegram bot started")
            except ImportError:
                logger.warning("python-telegram-bot not installed. Telegram bot disabled.")
            except Exception as e:
                logger.error(f"Failed to start Telegram bot: {e}", exc_info=True)
        else:
            logger.info("Telegram bot not configured (no TELEGRAM_BOT_TOKEN). Set it in .env to enable.")
        
        atexit.register(_cleanup)
    else:
        logger.info("Skipping background tasks in reloader monitor process")
    
    try:
        app.run(host='0.0.0.0', port=5151, debug=DEBUG)
    finally:
        _cleanup()



