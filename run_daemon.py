"""
Headless entry point for VPS deployment.
Starts the OrderbookDaemon, TelegramBot (with job control),
and Google Drive scheduled uploads — without Flask or web UI.

Usage:
    python run_daemon.py
"""
import os
import sys
import signal
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import (
    LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    ORDERBOOK_WATCHLIST_FILE, ORDERBOOK_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_HEARTBEAT_MINUTES,
)

PID_FILE = Path('config_data/daemon.pid')


class SafeRotatingFileHandler(RotatingFileHandler):
    """Handles file locking during log rotation on both Windows and Linux."""
    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            pass


def setup_logging():
    LOG_FILE.parent.mkdir(exist_ok=True)

    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    fh = SafeRotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES,
                                 backupCount=LOG_BACKUP_COUNT)
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(ch)


def write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    setup_logging()
    logger = logging.getLogger('run_daemon')
    logger.info("Starting headless orderbook daemon")
    write_pid()

    from auth import TokenManager
    from stockbit_client import StockbitClient
    from storage import CSVStorage
    from jobs import JobManager
    from orderbook_daemon import OrderbookDaemon

    token_manager = TokenManager()
    client = StockbitClient(token_manager)
    csv_storage = CSVStorage()
    job_manager = JobManager(client, csv_storage)
    orderbook_daemon = OrderbookDaemon(token_manager, ORDERBOOK_WATCHLIST_FILE)

    # -- optional Google Drive uploader --
    gdrive_uploader = None
    try:
        from config import (
            GDRIVE_SERVICE_ACCOUNT_FILE, GDRIVE_FOLDER_ID,
            GDRIVE_DELETE_AFTER_UPLOAD,
        )
        if GDRIVE_SERVICE_ACCOUNT_FILE and GDRIVE_FOLDER_ID:
            sa_path = Path(GDRIVE_SERVICE_ACCOUNT_FILE)
            if sa_path.exists():
                from gdrive_uploader import GDriveUploader
                gdrive_uploader = GDriveUploader(
                    str(sa_path), GDRIVE_FOLDER_ID,
                    delete_after_upload=GDRIVE_DELETE_AFTER_UPLOAD,
                )
                logger.info("Google Drive uploader initialised")
            else:
                logger.warning(f"Service account file not found: {sa_path}")
    except (ImportError, AttributeError) as e:
        logger.info(f"Google Drive upload disabled: {e}")

    # -- optional Telegram bot --
    telegram_bot = None
    if TELEGRAM_BOT_TOKEN:
        try:
            from telegram_bot import TelegramBot
            telegram_bot = TelegramBot(
                token=TELEGRAM_BOT_TOKEN,
                chat_id=TELEGRAM_CHAT_ID,
                daemon=orderbook_daemon,
                heartbeat_minutes=TELEGRAM_HEARTBEAT_MINUTES,
                job_manager=job_manager,
                gdrive_uploader=gdrive_uploader,
                orderbook_dir=ORDERBOOK_DIR,
            )
            telegram_bot.start()
            logger.info("Telegram bot started")
        except ImportError:
            logger.warning("python-telegram-bot not installed — Telegram disabled")
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}", exc_info=True)
    else:
        logger.info("Telegram not configured (no TELEGRAM_BOT_TOKEN in .env)")

    # start daemon and job worker
    job_manager.start_worker()
    orderbook_daemon.start()
    logger.info("Orderbook daemon and job worker running")

    # graceful shutdown on SIGTERM / SIGINT
    shutdown_event = False

    def _shutdown(signum, frame):
        nonlocal shutdown_event
        if shutdown_event:
            return
        shutdown_event = True
        logger.info(f"Received signal {signum}, shutting down...")
        orderbook_daemon.stop()
        job_manager.stop_worker()
        if telegram_bot:
            telegram_bot.stop()
        remove_pid()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not shutdown_event:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)

    logger.info("Daemon stopped")


if __name__ == '__main__':
    main()
