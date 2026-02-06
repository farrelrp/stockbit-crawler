"""
Market Replay Engine
Reads orderbook CSV and replays with original timing, calculating changes
"""
import csv
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class ReplayEngine:
    """
    Replays orderbook data from CSV with timing control and change calculation
    """
    
    def __init__(self, perspective_table):
        self.table = perspective_table
        self.thread = None
        self.running = False
        self.paused = False
        
        # Replay state
        self.csv_path = None
        self.data_rows = []
        self.current_index = 0
        self.speed_multiplier = 1.0
        
        # State tracking for change calculation
        # Key: (price, side) -> Value: lots
        self.state: Dict[Tuple[float, str], int] = {}
        
        # Timestamp tracking (last update time per side)
        self.last_bid_timestamp = None
        self.last_offer_timestamp = None
        self.current_timestamp = None
        
        # Stats
        self.total_rows = 0
        self.start_time = None
        self.elapsed_time = 0.0
        
        # Thread control
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused by default
        
        logger.info("ReplayEngine initialized")
    
    def load_csv(self, csv_path: str) -> Dict:
        """
        Load and parse CSV file
        Returns dict with metadata
        """
        try:
            csv_path = Path(csv_path)
            if not csv_path.exists():
                return {
                    'success': False,
                    'error': f'File not found: {csv_path}'
                }
            
            self.csv_path = csv_path
            self.data_rows = []
            
            # Read entire CSV into memory
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Parse timestamp
                    timestamp_str = row['timestamp']
                    timestamp = datetime.fromisoformat(timestamp_str)
                    
                    self.data_rows.append({
                        'timestamp': timestamp,
                        'price': float(row['price']),
                        'freq': int(row['lots']),  # lots column = frequency
                        'lot_size': int(float(row['total_value']) / 100),  # total_value / 100 = lot_size
                        'side': row['side']
                    })
            
            self.total_rows = len(self.data_rows)
            self.current_index = 0
            
            # Extract metadata
            ticker = csv_path.stem.split('_')[-1] if '_' in csv_path.stem else 'UNKNOWN'
            date = self.data_rows[0]['timestamp'].date() if self.data_rows else None
            
            logger.info(f"Loaded {self.total_rows} rows from {csv_path.name}")
            
            return {
                'success': True,
                'total_rows': self.total_rows,
                'ticker': ticker,
                'date': str(date),
                'start_time': self.data_rows[0]['timestamp'].isoformat() if self.data_rows else None,
                'end_time': self.data_rows[-1]['timestamp'].isoformat() if self.data_rows else None
            }
            
        except Exception as e:
            logger.error(f"Failed to load CSV: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    def _calculate_change(self, price: float, side: str, new_lots: int) -> int:
        """
        Calculate change in lots from previous state
        Returns: change (positive or negative)
        """
        key = (price, side)
        old_lots = self.state.get(key, 0)
        change = new_lots - old_lots
        
        # Update state
        self.state[key] = new_lots
        
        return change
    
    def _replay_loop(self):
        """
        Main replay loop that reads CSV and pushes to Perspective
        Respects original timing with speed multiplier
        """
        logger.info(f"Starting replay loop: {self.total_rows} rows, speed={self.speed_multiplier}x")
        
        self.running = True
        self.start_time = time.time()
        self.elapsed_time = 0.0
        
        # Clear state
        self.state.clear()
        self.last_bid_timestamp = None
        self.last_offer_timestamp = None
        self.current_timestamp = None
        
        # Clear table before starting
        if self.table:
            try:
                # Remove all existing data
                view = self.table.view()
                records = view.to_records()
                if records:
                    self.table.remove(records)
            except:
                pass
        
        try:
            while self.current_index < self.total_rows and not self._stop_event.is_set():
                # Wait if paused
                self._pause_event.wait()
                
                # Check if stopped while paused
                if self._stop_event.is_set():
                    break
                
                current_row = self.data_rows[self.current_index]
                
                # Update timestamp tracking
                self.current_timestamp = current_row['timestamp']
                if current_row['side'] == 'BID':
                    self.last_bid_timestamp = current_row['timestamp']
                else:
                    self.last_offer_timestamp = current_row['timestamp']
                
                # Calculate change
                change = self._calculate_change(
                    current_row['price'],
                    current_row['side'],
                    current_row['lots']
                )
                
                # Prepare update for Perspective
                update_data = {
                    'price': current_row['price'],
                    'side': current_row['side'],
                    'lots': current_row['lots'],
                    'change': change
                }
                
                # Push to Perspective table (thread-safe)
                if self.table:
                    self.table.update([update_data])
                    
                    # Log every 100th update for debugging
                    if self.current_index % 100 == 0:
                        logger.debug(f"Replay progress: {self.current_index}/{self.total_rows} - Last: {current_row['side']} @ {current_row['price']} x {current_row['lots']}")
                
                # Calculate sleep time if not the last row
                if self.current_index < self.total_rows - 1:
                    next_row = self.data_rows[self.current_index + 1]
                    time_delta = (next_row['timestamp'] - current_row['timestamp']).total_seconds()
                    
                    # Apply speed multiplier (faster replay with higher multiplier)
                    sleep_time = time_delta / self.speed_multiplier
                    
                    # Sleep if positive (handle same-timestamp batches)
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                
                self.current_index += 1
                
                # Update elapsed time
                self.elapsed_time = time.time() - self.start_time
            
            # Finished
            if self.current_index >= self.total_rows:
                logger.info(f"Replay completed: {self.current_index}/{self.total_rows} rows")
            else:
                logger.info(f"Replay stopped at: {self.current_index}/{self.total_rows} rows")
                
        except Exception as e:
            logger.error(f"Error in replay loop: {e}", exc_info=True)
        finally:
            self.running = False
            self._stop_event.clear()
    
    def start(self, speed_multiplier: float = 1.0) -> Dict:
        """
        Start replay from current position
        """
        if self.running:
            return {
                'success': False,
                'error': 'Replay already running'
            }
        
        if not self.data_rows:
            return {
                'success': False,
                'error': 'No data loaded. Call load_csv() first.'
            }
        
        self.speed_multiplier = speed_multiplier
        self._stop_event.clear()
        self._pause_event.set()  # ensure not paused
        
        # Start replay thread
        self.thread = threading.Thread(target=self._replay_loop, daemon=True)
        self.thread.start()
        
        logger.info(f"Replay started at index {self.current_index}, speed {speed_multiplier}x")
        
        return {
            'success': True,
            'message': f'Replay started at row {self.current_index}',
            'speed_multiplier': speed_multiplier
        }
    
    def pause(self) -> Dict:
        """Pause the replay"""
        if not self.running:
            return {'success': False, 'error': 'Replay not running'}
        
        if self.paused:
            return {'success': False, 'error': 'Already paused'}
        
        self._pause_event.clear()
        self.paused = True
        logger.info("Replay paused")
        
        return {'success': True, 'message': 'Replay paused'}
    
    def resume(self) -> Dict:
        """Resume the replay"""
        if not self.running:
            return {'success': False, 'error': 'Replay not running'}
        
        if not self.paused:
            return {'success': False, 'error': 'Not paused'}
        
        self._pause_event.set()
        self.paused = False
        logger.info("Replay resumed")
        
        return {'success': True, 'message': 'Replay resumed'}
    
    def stop(self) -> Dict:
        """Stop the replay"""
        if not self.running:
            return {'success': False, 'error': 'Replay not running'}
        
        self._stop_event.set()
        self._pause_event.set()  # unpause if paused
        
        # Wait for thread to finish (with timeout)
        if self.thread:
            self.thread.join(timeout=2.0)
        
        self.paused = False
        logger.info("Replay stopped")
        
        return {'success': True, 'message': 'Replay stopped'}
    
    def seek(self, position: int) -> Dict:
        """
        Seek to a specific row index
        If running, will pause, seek, and auto-resume
        """
        if position < 0 or position >= self.total_rows:
            return {
                'success': False,
                'error': f'Position {position} out of range [0, {self.total_rows-1}]'
            }
        
        was_running = self.running
        
        # Stop if running
        if was_running:
            self.stop()
            time.sleep(0.1)  # brief delay to ensure stop
        
        # Optimization: Forward seek
        if position > self.current_index and self.state:
            logger.info(f"Seeking forward from {self.current_index} to {position}...")
            for i in range(self.current_index, position):
                if i < len(self.data_rows):
                    row = self.data_rows[i]
                    key = (row['price'], row['side'])
                    self.state[key] = row['lots']
            self.current_index = position
            
            # Update timestamps from the last row processed
            if position > 0:
                last_row = self.data_rows[position - 1]
                self.current_timestamp = last_row['timestamp']
                # Note: We can't easily retrieve last_bid/offer_timestamp without full scan,
                # but we can at least update current_timestamp which is most important.
                # For exact precision on side-specific timestamps, full scan is needed,
                # but for simple scrubbing, this tradeoff is acceptable for performance.
                if last_row['side'] == 'BID':
                    self.last_bid_timestamp = last_row['timestamp']
                else:
                    self.last_offer_timestamp = last_row['timestamp']

        else:
            # Backward seek or initial load: Full rebuild
            logger.info(f"Rebuilding state up to position {position}...")
            
            # Reset state
            self.state.clear()
            self.current_index = position
            self.last_bid_timestamp = None
            self.last_offer_timestamp = None
            self.current_timestamp = None
            
            # Rebuild state
            for i in range(position):
                row = self.data_rows[i]
                key = (row['price'], row['side'])
                self.state[key] = row['lots']
                
                # Update timestamps
                if row['side'] == 'BID':
                    self.last_bid_timestamp = row['timestamp']
                else:
                    self.last_offer_timestamp = row['timestamp']
            
            if position > 0:
                self.current_timestamp = self.data_rows[position-1]['timestamp']
        
        logger.info(f"Seeked to position {position}")
        
        # Auto-resume if was running
        if was_running:
            return self.start(self.speed_multiplier)
        
        return {
            'success': True,
            'message': f'Seeked to row {position}'
        }
    
    def set_speed(self, multiplier: float) -> Dict:
        """Change playback speed (applies immediately)"""
        if multiplier <= 0:
            return {'success': False, 'error': 'Speed multiplier must be positive'}
        
        self.speed_multiplier = multiplier
        logger.info(f"Speed multiplier set to {multiplier}x")
        
        return {
            'success': True,
            'message': f'Speed set to {multiplier}x'
        }
    
    def get_status(self) -> Dict:
        """Get current replay status"""
        return {
            'running': self.running,
            'paused': self.paused,
            'csv_loaded': self.csv_path is not None,
            'csv_path': str(self.csv_path) if self.csv_path else None,
            'total_rows': self.total_rows,
            'current_index': self.current_index,
            'progress_percent': (self.current_index / self.total_rows * 100) if self.total_rows > 0 else 0,
            'speed_multiplier': self.speed_multiplier,
            'elapsed_time': self.elapsed_time,
            'state_size': len(self.state),
            'current_timestamp': self.current_timestamp.isoformat() if self.current_timestamp else None,
            'last_bid_timestamp': self.last_bid_timestamp.isoformat() if self.last_bid_timestamp else None,
            'last_offer_timestamp': self.last_offer_timestamp.isoformat() if self.last_offer_timestamp else None
        }


# Global singleton instance
_replay_engine = None


def get_replay_engine(perspective_table=None):
    """Get or create the global replay engine instance"""
    global _replay_engine
    if _replay_engine is None:
        if perspective_table is None:
            raise ValueError("perspective_table required for first initialization")
        _replay_engine = ReplayEngine(perspective_table)
    return _replay_engine
