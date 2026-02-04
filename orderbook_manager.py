"""
Manager for orderbook streaming sessions
Handles multiple concurrent orderbook streams in the background
"""
import asyncio
import threading
import logging
from typing import Dict, List, Optional
from datetime import datetime
from orderbook_streamer import OrderbookStreamer

logger = logging.getLogger(__name__)


class OrderbookManager:
    """Manages orderbook streaming sessions"""
    
    def __init__(self, token_manager):
        self.token_manager = token_manager
        self.sessions: Dict[str, Dict] = {}  # session_id -> session_info
        self.loop = None
        self.thread = None
        
        logger.info("OrderbookManager initialized")
    
    def _ensure_event_loop(self):
        """Ensure we have an event loop running in background thread"""
        if self.loop is None or not self.loop.is_running():
            self.loop = asyncio.new_event_loop()
            
            def run_loop():
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()
            
            self.thread = threading.Thread(target=run_loop, daemon=True)
            self.thread.start()
            logger.info("Started background event loop for orderbook streaming")
    
    def start_stream(self, session_id: str, tickers: List[str], max_retries: int = None) -> Dict:
        """
        Start a new orderbook streaming session with auto-reconnect
        
        Args:
            session_id: Unique identifier for this session
            tickers: List of stock symbols to stream
            max_retries: Maximum reconnection attempts (None = infinite)
        
        Returns:
            Dict with success status and session info
        """
        try:
            # check if session already exists
            if session_id in self.sessions:
                return {
                    'success': False,
                    'error': f'Session {session_id} already exists'
                }
            
            # validate token
            if not self.token_manager.get_valid_token():
                return {
                    'success': False,
                    'error': 'No valid token available. Please set your Bearer token.',
                    'requires_login': True
                }
            
            # ensure event loop is running
            self._ensure_event_loop()
            
            # create streamer with retry capability
            streamer = OrderbookStreamer(self.token_manager, tickers, max_retries=max_retries)
            
            # schedule the streamer on the event loop
            future = asyncio.run_coroutine_threadsafe(streamer.run(), self.loop)
            
            # store session info
            self.sessions[session_id] = {
                'session_id': session_id,
                'tickers': tickers,
                'streamer': streamer,
                'future': future,
                'started_at': datetime.now(),
                'status': 'running'
            }
            
            logger.info(f"Started orderbook stream '{session_id}' for {len(tickers)} tickers")
            
            return {
                'success': True,
                'session_id': session_id,
                'tickers': tickers,
                'started_at': self.sessions[session_id]['started_at'].isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to start orderbook stream: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def stop_stream(self, session_id: str) -> Dict:
        """
        Stop an orderbook streaming session
        
        Args:
            session_id: Session identifier to stop
        
        Returns:
            Dict with success status
        """
        try:
            if session_id not in self.sessions:
                return {
                    'success': False,
                    'error': f'Session {session_id} not found'
                }
            
            session = self.sessions[session_id]
            streamer = session['streamer']
            
            # schedule stop on event loop
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(streamer.stop(), self.loop)
            
            # update status
            session['status'] = 'stopped'
            session['stopped_at'] = datetime.now()
            
            logger.info(f"Stopped orderbook stream '{session_id}'")
            
            return {
                'success': True,
                'session_id': session_id,
                'stopped_at': session['stopped_at'].isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to stop orderbook stream: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_session_stats(self, session_id: str) -> Optional[Dict]:
        """Get statistics for a specific session"""
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        streamer = session['streamer']
        
        # get stats from streamer
        stats = streamer.get_stats()
        
        # add session info
        stats['session_id'] = session_id
        stats['started_at'] = session['started_at'].isoformat()
        stats['status'] = session['status']
        
        if 'stopped_at' in session:
            stats['stopped_at'] = session['stopped_at'].isoformat()
        
        return stats
    
    def list_sessions(self) -> List[Dict]:
        """List all orderbook streaming sessions"""
        sessions_list = []
        
        for session_id, session in self.sessions.items():
            stats = self.get_session_stats(session_id)
            if stats:
                sessions_list.append(stats)
        
        return sessions_list
    
    def stop_all(self):
        """Stop all running orderbook streams"""
        for session_id in list(self.sessions.keys()):
            self.stop_stream(session_id)
        
        # stop event loop
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        logger.info("Stopped all orderbook streams")
