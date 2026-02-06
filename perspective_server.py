"""
Perspective Tornado WebSocket Server
Runs in a separate thread to handle real-time orderbook visualization
"""
import logging
import threading
import pandas as pd
from tornado import ioloop, web
from perspective import Server
from perspective.handlers.tornado import PerspectiveTornadoHandler

logger = logging.getLogger(__name__)


class PerspectiveServer:
    """Manages the Perspective Tornado server in a background thread"""
    
    def __init__(self, port=8888):
        self.port = port
        self.server = Server()
        self.client = self.server.new_local_client()
        self.table = None
        self.io_loop = None
        self.thread = None
        self.app = None
        
        logger.info("PerspectiveServer initialized")
    
    def create_table(self, name="orderbook"):
        """Create a Perspective table with orderbook schema"""
        # Create empty DataFrame with correct schema
        # Use index to ensure updates overwrite rather than append
        # This creates a "ladder" effect where each price level is unique
        df = pd.DataFrame({
            "timestamp": pd.Series([], dtype=object), # datetime as object/string
            "price": pd.Series([], dtype=float),
            "side": pd.Series([], dtype=str),
            "freq": pd.Series([], dtype='Int64'),
            "lot_size": pd.Series([], dtype='Int64'),
            "change": pd.Series([], dtype='Int64')
        })
        
        # Create table with index on price using client
        self.table = self.client.table(df, index="price", name=name)
        logger.info(f"Created Perspective table '{name}' with index on price")
        return self.table
    
    def get_table(self):
        """Get the current table instance"""
        return self.table
    
    def clear_table(self):
        """Clear all data from the table"""
        if self.table:
            # Clear by removing all rows
            self.table.remove(self.table.view().to_records())
            logger.info("Cleared Perspective table")
    
    def _make_app(self):
        """Create Tornado application with Perspective handler"""
        return web.Application([
            (
                r"/websocket",
                PerspectiveTornadoHandler,
                {"perspective_server": self.server, "check_origin": True}
            ),
        ])
    
    def _run_server(self):
        """Run Tornado server in background thread (blocking call)"""
        # Create new event loop for this thread
        self.io_loop = ioloop.IOLoop.current()
        
        # Create and start the application
        self.app = self._make_app()
        self.app.listen(self.port)
        
        logger.info(f"Perspective Tornado server started on port {self.port}")
        
        # Start the event loop (blocks until stopped)
        self.io_loop.start()
        
        logger.info("Perspective Tornado server stopped")
    
    def start(self):
        """Start the Perspective server in a background thread"""
        if self.thread and self.thread.is_alive():
            logger.warning("Perspective server already running")
            return
        
        # Create the table before starting server
        self.create_table()
        
        # Start server in daemon thread
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
        
        logger.info(f"Perspective server thread started (port {self.port})")
    
    def stop(self):
        """Stop the Perspective server"""
        if self.io_loop:
            self.io_loop.add_callback(self.io_loop.stop)
            logger.info("Stopping Perspective server...")
    
    def is_running(self):
        """Check if server is running"""
        return self.thread and self.thread.is_alive()


# Global singleton instance
_perspective_server = None


def get_perspective_server(port=8888):
    """Get or create the global Perspective server instance"""
    global _perspective_server
    if _perspective_server is None:
        _perspective_server = PerspectiveServer(port=port)
    return _perspective_server


def start_perspective_server(port=8888):
    """Start the global Perspective server"""
    server = get_perspective_server(port)
    if not server.is_running():
        server.start()
    return server
