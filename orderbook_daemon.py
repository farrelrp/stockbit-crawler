"""
Always-On Orderbook Streaming Daemon
Runs 24/7, automatically starts/stops streaming based on Indonesian market hours.
Maintains a persistent watchlist of tickers.
"""
import asyncio
import threading
import logging
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
from enum import Enum

from orderbook_streamer import OrderbookStreamer

logger = logging.getLogger(__name__)


class DaemonState(str, Enum):
    WAITING_MARKET = "waiting_market"
    STREAMING = "streaming"
    PAUSED = "paused"
    ERROR = "error"
    MARKET_CLOSED = "market_closed"
    NO_TICKERS = "no_tickers"


class OrderbookDaemon:
    """
    Always-on orderbook streaming daemon.
    Manages a single persistent stream that auto-starts/stops with market hours.
    """

    def __init__(self, token_manager, watchlist_file: Path):
        self.token_manager = token_manager
        self.watchlist_file = watchlist_file

        # State
        self.state: DaemonState = DaemonState.WAITING_MARKET
        self.tickers: List[str] = []
        self.paused = False

        # Streaming
        self.streamer: Optional[OrderbookStreamer] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.loop_thread: Optional[threading.Thread] = None
        self.scheduler_thread: Optional[threading.Thread] = None
        self.running = False

        # Stats
        self.started_at: Optional[datetime] = None
        self.stream_started_at: Optional[datetime] = None
        self.last_state_change: Optional[datetime] = None
        self.consecutive_reconnects = 0
        self.total_reconnects_today = 0
        self.daily_stats: Dict = {}
        self._last_reconnect_count = 0

        # Callbacks for Telegram notifications
        self._on_reconnect_alert = None
        self._on_state_change = None

        # Load persisted watchlist
        self._load_watchlist()

        logger.info(f"OrderbookDaemon initialized with {len(self.tickers)} tickers")

    def _load_watchlist(self):
        """Load tickers from persistent file"""
        try:
            if self.watchlist_file.exists():
                with open(self.watchlist_file, 'r') as f:
                    data = json.load(f)
                    self.tickers = data.get('tickers', [])
                    self.daily_stats = data.get('daily_stats', {})
                    logger.info(f"Loaded watchlist: {self.tickers}")
        except Exception as e:
            logger.error(f"Failed to load watchlist: {e}")
            self.tickers = []

    def _save_watchlist(self):
        """Save tickers to persistent file"""
        try:
            data = {
                'tickers': self.tickers,
                'daily_stats': self.daily_stats,
                'updated_at': datetime.now().isoformat()
            }
            with open(self.watchlist_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save watchlist: {e}")

    def _get_market_status(self):
        """Get current Indonesian market status"""
        # Import here to avoid circular imports
        import pytz

        wib_tz = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(wib_tz)

        # Market is closed on weekends
        if now_wib.weekday() >= 5:
            days_until_monday = 7 - now_wib.weekday()
            next_monday = now_wib.date() + timedelta(days=days_until_monday)
            next_open = wib_tz.localize(datetime.combine(next_monday, datetime.strptime('08:55', '%H:%M').time()))
            return {
                'is_open': False,
                'status': 'closed',
                'reason': 'Weekend',
                'current_time': now_wib,
                'next_open': next_open,
                'time_until_next': int((next_open - now_wib).total_seconds())
            }

        # Define market hours with 5-minute margins
        if now_wib.weekday() < 4:  # Monday-Thursday
            session1_open = datetime.strptime('08:55', '%H:%M').time()
            session1_close = datetime.strptime('12:05', '%H:%M').time()
            session2_open = datetime.strptime('13:25', '%H:%M').time()
            session2_close = datetime.strptime('15:54', '%H:%M').time()
        else:  # Friday
            session1_open = datetime.strptime('08:55', '%H:%M').time()
            session1_close = datetime.strptime('11:35', '%H:%M').time()
            session2_open = datetime.strptime('13:55', '%H:%M').time()
            session2_close = datetime.strptime('15:54', '%H:%M').time()

        current_time = now_wib.time()
        today = now_wib.date()

        # Session 1
        if session1_open <= current_time < session1_close:
            return {
                'is_open': True,
                'status': 'open',
                'reason': 'Session 1',
                'current_time': now_wib,
                'session': 1,
                'next_close': wib_tz.localize(datetime.combine(today, session1_close)),
                'time_until_next': 0
            }

        # Session 2
        if session2_open <= current_time < session2_close:
            return {
                'is_open': True,
                'status': 'open',
                'reason': 'Session 2',
                'current_time': now_wib,
                'session': 2,
                'next_close': wib_tz.localize(datetime.combine(today, session2_close)),
                'time_until_next': 0
            }

        # Lunch break
        if session1_close <= current_time < session2_open:
            next_open = wib_tz.localize(datetime.combine(today, session2_open))
            return {
                'is_open': False,  # Not actively trading during break
                'status': 'break',
                'reason': 'Lunch Break',
                'current_time': now_wib,
                'next_open': next_open,
                'time_until_next': int((next_open - now_wib).total_seconds())
            }

        # Before market opens
        if current_time < session1_open:
            next_open = wib_tz.localize(datetime.combine(today, session1_open))
            return {
                'is_open': False,
                'status': 'closed',
                'reason': 'Pre-Market',
                'current_time': now_wib,
                'next_open': next_open,
                'time_until_next': int((next_open - now_wib).total_seconds())
            }

        # After market closes
        if now_wib.weekday() == 4:  # Friday
            days_ahead = 3
        else:
            days_ahead = 1
        next_day = today + timedelta(days=days_ahead)
        next_open = wib_tz.localize(datetime.combine(next_day, datetime.strptime('08:55', '%H:%M').time()))
        return {
            'is_open': False,
            'status': 'closed',
            'reason': 'After Hours',
            'current_time': now_wib,
            'next_open': next_open,
            'time_until_next': int((next_open - now_wib).total_seconds())
        }

    def _ensure_event_loop(self):
        """Ensure we have an event loop running in background thread"""
        if self.loop is None or not self.loop.is_running():
            self.loop = asyncio.new_event_loop()

            def run_loop():
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()

            self.loop_thread = threading.Thread(target=run_loop, daemon=True, name="orderbook-daemon-loop")
            self.loop_thread.start()
            logger.info("Started background event loop for daemon")

    def _start_stream(self):
        """Start the orderbook stream for current tickers"""
        if not self.tickers:
            self._set_state(DaemonState.NO_TICKERS)
            return False

        token = self.token_manager.get_valid_token()
        if not token:
            logger.error("No valid token available to start stream")
            self._set_state(DaemonState.ERROR)
            return False

        try:
            self._ensure_event_loop()

            self.streamer = OrderbookStreamer(
                self.token_manager,
                self.tickers,
                max_retries=None,  # infinite retries
            )

            asyncio.run_coroutine_threadsafe(self.streamer.run(), self.loop)
            self.stream_started_at = datetime.now()
            self._last_reconnect_count = 0
            self._set_state(DaemonState.STREAMING)
            logger.info(f"Daemon started streaming: {self.tickers}")
            return True

        except Exception as e:
            logger.error(f"Failed to start daemon stream: {e}", exc_info=True)
            self._set_state(DaemonState.ERROR)
            return False

    def _stop_stream(self):
        """Stop the current stream"""
        if self.streamer:
            try:
                if self.loop and self.loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(self.streamer.stop(), self.loop)
                    future.result(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping stream: {e}")
            finally:
                # Save daily stats before clearing
                self._save_daily_stats()
                self.streamer = None
                self.stream_started_at = None

    def _restart_stream(self):
        """Stop the current stream and immediately start a fresh one"""
        logger.info("Restarting stream (clean stop + fresh start)...")
        self._stop_stream()
        return self._start_stream()

    def _is_stream_healthy(self) -> bool:
        """Check if the current stream is actually receiving data (not stuck retrying)"""
        if not self.streamer:
            return False
        stats = self.streamer.get_stats()
        status = stats.get('connection_status', 'unknown')
        # Healthy = connected and running
        if status == 'connected' and stats.get('running', False):
            return True
        # Retrying or error = not healthy
        if 'retrying' in str(status) or status in ('error', 'stopped', 'disconnected'):
            return False
        return True

    def _save_daily_stats(self):
        """Save current stream stats for daily recap"""
        if self.streamer:
            stats = self.streamer.get_stats()
            today = datetime.now().strftime('%Y-%m-%d')
            self.daily_stats[today] = {
                'message_counts': stats.get('message_counts', {}),
                'total_reconnects': stats.get('total_reconnects', 0),
                'uptime_seconds': stats.get('uptime_seconds', 0),
                'tickers': self.tickers.copy(),
                'saved_at': datetime.now().isoformat()
            }
            self._save_watchlist()

    def _set_state(self, new_state: DaemonState):
        """Update daemon state with timestamp"""
        old_state = self.state
        self.state = new_state
        self.last_state_change = datetime.now()
        if old_state != new_state:
            logger.info(f"Daemon state: {old_state.value} → {new_state.value}")
            if self._on_state_change:
                try:
                    self._on_state_change(old_state, new_state)
                except Exception as e:
                    logger.error(f"State change callback error: {e}")

    def _check_reconnects(self):
        """Check for consecutive reconnections and alert if needed"""
        if not self.streamer:
            return

        stats = self.streamer.get_stats()
        current_reconnects = stats.get('total_reconnects', 0)

        if current_reconnects > self._last_reconnect_count:
            diff = current_reconnects - self._last_reconnect_count
            self.consecutive_reconnects += diff
            self.total_reconnects_today += diff
            self._last_reconnect_count = current_reconnects

            if self.consecutive_reconnects > 1 and self._on_reconnect_alert:
                try:
                    self._on_reconnect_alert(self.consecutive_reconnects)
                except Exception as e:
                    logger.error(f"Reconnect alert callback error: {e}")
        else:
            # No new reconnects, reset consecutive counter
            if self.consecutive_reconnects > 0:
                self.consecutive_reconnects = 0

    def _scheduler_loop(self):
        """Main scheduler loop — runs every 30 seconds"""
        logger.info("Daemon scheduler started")
        while self.running:
            try:
                if self.paused:
                    time.sleep(5)
                    continue

                market = self._get_market_status()
                market_status = market.get('status')  # 'open', 'break', 'closed'

                if market_status == 'open':
                    # Market session is active — ensure stream is running and healthy
                    if self.state != DaemonState.STREAMING:
                        if self.tickers:
                            session = market.get('session', '?')
                            logger.info(f"Market Session {session} is open, starting stream")
                            self._start_stream()
                        else:
                            self._set_state(DaemonState.NO_TICKERS)
                    else:
                        # Stream should be running — verify it's actually healthy
                        if not self._is_stream_healthy():
                            logger.warning("Stream unhealthy (stuck retrying or disconnected), restarting...")
                            self._restart_stream()
                        else:
                            self._check_reconnects()

                elif market_status == 'break':
                    # Lunch break — proactively stop the stream
                    # The WebSocket server drops connections during the break,
                    # so keeping the stream alive just wastes retries.
                    if self.state == DaemonState.STREAMING:
                        logger.info("Lunch break started, stopping stream to avoid stale retries")
                        self._stop_stream()
                        self._set_state(DaemonState.WAITING_MARKET)
                    elif self.state not in (DaemonState.WAITING_MARKET, DaemonState.NO_TICKERS):
                        if self.tickers:
                            self._set_state(DaemonState.WAITING_MARKET)
                        else:
                            self._set_state(DaemonState.NO_TICKERS)

                else:
                    # Market is closed (pre-market, after hours, weekend)
                    if self.state == DaemonState.STREAMING:
                        logger.info(f"Market closed ({market['reason']}), stopping stream")
                        self._stop_stream()
                        self._set_state(DaemonState.MARKET_CLOSED)
                    elif self.state not in (DaemonState.MARKET_CLOSED, DaemonState.WAITING_MARKET, DaemonState.NO_TICKERS):
                        if self.tickers:
                            self._set_state(DaemonState.WAITING_MARKET)
                        else:
                            self._set_state(DaemonState.NO_TICKERS)

            except Exception as e:
                logger.error(f"Daemon scheduler error: {e}", exc_info=True)

            time.sleep(30)  # Check every 30 seconds

    # ===== Public API =====

    def start(self):
        """Start the daemon scheduler"""
        if self.running:
            return

        self.running = True
        self.started_at = datetime.now()

        market = self._get_market_status()
        if not self.tickers:
            self._set_state(DaemonState.NO_TICKERS)
        elif market['is_open']:
            self._set_state(DaemonState.WAITING_MARKET)  # Will start on next scheduler tick
        else:
            self._set_state(DaemonState.WAITING_MARKET)

        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="orderbook-daemon-scheduler")
        self.scheduler_thread.start()

        logger.info("OrderbookDaemon started")

    def stop(self):
        """Stop the daemon"""
        self.running = False
        self._stop_stream()

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        logger.info("OrderbookDaemon stopped")

    def set_tickers(self, tickers: List[str]) -> Dict:
        """Set the watchlist tickers"""
        old_tickers = self.tickers.copy()
        self.tickers = [t.strip().upper() for t in tickers if t.strip()]
        self._save_watchlist()

        logger.info(f"Tickers updated: {old_tickers} → {self.tickers}")

        # If currently streaming and tickers changed, restart stream
        if self.state == DaemonState.STREAMING and old_tickers != self.tickers:
            if self.tickers:
                logger.info("Tickers changed while streaming, restarting stream")
                self._stop_stream()
                time.sleep(0.5)
                self._start_stream()
            else:
                self._stop_stream()
                self._set_state(DaemonState.NO_TICKERS)
        elif self.state == DaemonState.NO_TICKERS and self.tickers:
            self._set_state(DaemonState.WAITING_MARKET)

        return {
            'success': True,
            'tickers': self.tickers,
            'old_tickers': old_tickers
        }

    def add_tickers(self, tickers: List[str]) -> Dict:
        """Add tickers to the watchlist"""
        new_tickers = [t.strip().upper() for t in tickers if t.strip()]
        added = []
        for t in new_tickers:
            if t not in self.tickers:
                self.tickers.append(t)
                added.append(t)

        if added:
            self._save_watchlist()
            # Restart stream if currently streaming
            if self.state == DaemonState.STREAMING:
                self._stop_stream()
                time.sleep(0.5)
                self._start_stream()
            elif self.state == DaemonState.NO_TICKERS:
                self._set_state(DaemonState.WAITING_MARKET)

        return {
            'success': True,
            'added': added,
            'tickers': self.tickers
        }

    def remove_tickers(self, tickers: List[str]) -> Dict:
        """Remove tickers from the watchlist"""
        to_remove = [t.strip().upper() for t in tickers if t.strip()]
        removed = []
        for t in to_remove:
            if t in self.tickers:
                self.tickers.remove(t)
                removed.append(t)

        if removed:
            self._save_watchlist()
            if self.state == DaemonState.STREAMING:
                if self.tickers:
                    self._stop_stream()
                    time.sleep(0.5)
                    self._start_stream()
                else:
                    self._stop_stream()
                    self._set_state(DaemonState.NO_TICKERS)

        return {
            'success': True,
            'removed': removed,
            'tickers': self.tickers
        }

    def pause(self) -> Dict:
        """Pause the daemon (stop streaming but keep scheduler)"""
        self.paused = True
        if self.state == DaemonState.STREAMING:
            self._stop_stream()
        self._set_state(DaemonState.PAUSED)
        return {'success': True, 'state': self.state.value}

    def resume(self) -> Dict:
        """Resume the daemon"""
        self.paused = False
        market = self._get_market_status()
        if market['status'] == 'open' and self.tickers:
            self._set_state(DaemonState.WAITING_MARKET)  # Scheduler will start stream
        elif not self.tickers:
            self._set_state(DaemonState.NO_TICKERS)
        else:
            self._set_state(DaemonState.WAITING_MARKET)
        return {'success': True, 'state': self.state.value}

    def set_token_and_reconnect(self, token: str, cookies: str = None) -> Dict:
        """Set new token/cookies and reconnect the stream"""
        result = self.token_manager.set_token(token, cookies)
        if not result.get('success'):
            return result

        # Reconnect if currently streaming
        if self.state == DaemonState.STREAMING:
            logger.info("Token updated, reconnecting stream")
            self._stop_stream()
            time.sleep(0.5)
            self._start_stream()
            return {
                'success': True,
                'message': 'Token updated and stream reconnected'
            }
        elif self.state == DaemonState.ERROR:
            # Try to recover from error state
            market = self._get_market_status()
            if market['is_open'] and self.tickers:
                self._start_stream()
                return {
                    'success': True,
                    'message': 'Token updated and stream started (recovered from error)'
                }

        return {
            'success': True,
            'message': 'Token updated. Stream will use new token when market opens.'
        }

    def get_status(self) -> Dict:
        """Get full daemon status"""
        market = self._get_market_status()

        status = {
            'state': self.state.value,
            'paused': self.paused,
            'tickers': self.tickers,
            'market': {
                'is_open': market['is_open'],
                'status': market['status'],
                'reason': market['reason'],
                'current_time': market['current_time'].isoformat(),
                'next_open': market.get('next_open', market.get('next_close')),
                'time_until_next': market.get('time_until_next', 0),
            },
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'stream_started_at': self.stream_started_at.isoformat() if self.stream_started_at else None,
            'last_state_change': self.last_state_change.isoformat() if self.last_state_change else None,
            'consecutive_reconnects': self.consecutive_reconnects,
            'total_reconnects_today': self.total_reconnects_today,
        }

        # Add streamer stats if streaming
        if self.streamer and self.state == DaemonState.STREAMING:
            stats = self.streamer.get_stats()
            status['stream'] = {
                'running': stats.get('running', False),
                'connected': stats.get('connected', False),
                'connection_status': stats.get('connection_status', 'unknown'),
                'message_counts': stats.get('message_counts', {}),
                'total_reconnects': stats.get('total_reconnects', 0),
                'uptime_seconds': stats.get('uptime_seconds', 0),
                'retry_count': stats.get('retry_count', 0),
                'last_error': stats.get('last_error'),
                'last_disconnect_time': stats.get('last_disconnect_time'),
            }
        else:
            status['stream'] = None

        # If next_open is a datetime, convert to ISO
        if status['market'].get('next_open') and hasattr(status['market']['next_open'], 'isoformat'):
            status['market']['next_open'] = status['market']['next_open'].isoformat()

        return status

    def get_daily_recap(self) -> Dict:
        """Get recap for today's trading"""
        today = datetime.now().strftime('%Y-%m-%d')

        # Current stream stats
        current_stats = {}
        if self.streamer:
            stats = self.streamer.get_stats()
            current_stats = {
                'message_counts': stats.get('message_counts', {}),
                'total_reconnects': stats.get('total_reconnects', 0),
                'uptime_seconds': stats.get('uptime_seconds', 0),
            }

        # Saved stats from earlier today
        saved = self.daily_stats.get(today, {})

        # Merge
        recap = {
            'date': today,
            'tickers': self.tickers,
            'message_counts': current_stats.get('message_counts', saved.get('message_counts', {})),
            'total_reconnects': self.total_reconnects_today,
            'total_messages': sum(current_stats.get('message_counts', saved.get('message_counts', {})).values()),
        }

        # Next market open
        market = self._get_market_status()
        if not market['is_open'] and market.get('next_open'):
            next_open = market['next_open']
            if hasattr(next_open, 'isoformat'):
                recap['next_open'] = next_open.isoformat()
            else:
                recap['next_open'] = str(next_open)

        return recap

    def set_reconnect_callback(self, callback):
        """Set callback for reconnect alerts"""
        self._on_reconnect_alert = callback

    def set_state_change_callback(self, callback):
        """Set callback for state changes"""
        self._on_state_change = callback
