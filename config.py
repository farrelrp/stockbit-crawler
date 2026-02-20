"""
Configuration for Stockbit Running Trade Scraper
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file into environment
load_dotenv()


# Base directory
BASE_DIR = Path(__file__).parent

# Flask config
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'

# Data directories
DATA_DIR = BASE_DIR / 'data'
ORDERBOOK_DIR = DATA_DIR / 'orderbook'
LOGS_DIR = BASE_DIR / 'logs'
CONFIG_DIR = BASE_DIR / 'config_data'

# Create directories if they don't exist
DATA_DIR.mkdir(exist_ok=True)
ORDERBOOK_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

# Stockbit API config
STOCKBIT_API_BASE = 'https://exodus.stockbit.com'
STOCKBIT_LOGIN_URL = 'https://stockbit.com/api/login/email'
STOCKBIT_RUNNING_TRADE_URL = f'{STOCKBIT_API_BASE}/order-trade/running-trade'
STOCKBIT_WEBSOCKET_URL = 'wss://wss-jkt.trading.stockbit.com/ws'

# reCAPTCHA config (for login) — Stockbit uses v3 (score-based, invisible)
# Verified from login JS bundle: reCaptchaKey:"6LeBXZYqAAAAAIAqBYdAV5HuBc6i0YeVziSYrXAZ"
RECAPTCHA_SITE_KEY = '6LeBXZYqAAAAAIAqBYdAV5HuBc6i0YeVziSYrXAZ'
RECAPTCHA_VERSION = 'RECAPTCHA_VERSION_3'

# Request headers template
HEADERS_TEMPLATE = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://stockbit.com/',
    'Origin': 'https://stockbit.com',
    'DNT': '1',
    'Connection': 'keep-alive',
}

# Login-specific headers (includes Content-Type)
LOGIN_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.5',
    'Content-Type': 'application/json',
    'Referer': 'https://stockbit.com/login',
    'Origin': 'https://stockbit.com',
    'DNT': '1',
    'Sec-GPC': '1',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}

# Default job settings
DEFAULT_DELAY_SECONDS = 3
DEFAULT_LIMIT = 50
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_BACKOFF = 2  # seconds

# Token settings
TOKEN_WARNING_THRESHOLD = 600  # seconds (10 minutes)

# Logging
LOG_FILE = LOGS_DIR / 'app.log'
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5
LOG_MEMORY_LINES = 200  # keep last N lines in memory for UI

# CSV settings
CSV_APPEND_MODE = True
CSV_COLUMNS = [
    'id', 'date', 'time', 'action', 'code', 'price', 'change',
    'lot', 'buyer', 'seller', 'trade_number', 'buyer_type',
    'seller_type', 'market_board'
]

# Credentials file (encrypted would be better but this works for class project)
CREDENTIALS_FILE = CONFIG_DIR / 'credentials.json'

# Orderbook daemon watchlist
ORDERBOOK_WATCHLIST_FILE = CONFIG_DIR / 'orderbook_watchlist.json'

# Telegram bot settings (set via environment variables or .env file)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
TELEGRAM_HEARTBEAT_MINUTES = int(os.environ.get('TELEGRAM_HEARTBEAT_MINUTES', '15'))

# 2Captcha settings (for reCAPTCHA v3 solving during auto-login)
TWOCAPTCHA_API_KEY = os.environ.get('TWOCAPTCHA_API_KEY', '')

# Google Drive upload settings (service account)
GDRIVE_SERVICE_ACCOUNT_FILE = os.environ.get(
    'GDRIVE_SERVICE_ACCOUNT_FILE', str(CONFIG_DIR / 'gdrive-service-account.json')
)
GDRIVE_FOLDER_ID = os.environ.get('GDRIVE_FOLDER_ID', '')
GDRIVE_DELETE_AFTER_UPLOAD = os.environ.get(
    'GDRIVE_DELETE_AFTER_UPLOAD', 'false'
).lower() == 'true'
