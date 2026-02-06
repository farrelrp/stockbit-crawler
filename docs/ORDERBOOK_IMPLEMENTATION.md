# Orderbook Level 2 Streaming - Implementation Summary

## What Was Built

A complete orderbook streaming system that subscribes to multiple stocks via a single WebSocket connection and stores Level 2 market data to daily CSV files.

## Files Created

### 1. `orderbook_streamer.py` (373 lines)
Core streaming implementation with:
- **Protobuf encoding/decoding** functions for binary WebSocket protocol
- **OrderbookCSVStorage** class: Manages daily CSV files per ticker with automatic rotation
- **OrderbookStreamer** class: Handles WebSocket connection, message processing, and data routing

Key functions:
- `encode_websocket_request()` - Creates subscription message with all 6 required fields
- `decode_orderbook_message()` - Parses incoming binary orderbook updates
- `_parse_and_store_orderbook()` - Parses "SIDE|PRICE;LOTS;VALUE|..." format
- Automatic heartbeat (ping/pong every 30s)
- Per-ticker message routing and statistics

### 2. `orderbook_manager.py` (172 lines)
High-level session management:
- **OrderbookManager** class: Runs streams in background event loop
- Multi-session support (run multiple streams simultaneously)
- Session statistics and monitoring
- Graceful start/stop operations
- Thread-safe async event loop management

### 3. `test_orderbook.py` (206 lines)
Comprehensive test suite:
- Protobuf encoding/decoding verification
- Authentication flow testing
- Optional live streaming test (10 seconds)
- Helpful error messages and setup guidance

### 4. `ORDERBOOK_GUIDE.md` (423 lines)
Complete user documentation covering:
- Feature overview and architecture
- Authentication flow details
- Protobuf message formats
- API usage examples
- CSV output format
- Troubleshooting guide
- Best practices

### 5. `ORDERBOOK_IMPLEMENTATION.md` (this file)
Technical implementation summary

### 6. `templates/orderbook.html` (260 lines)
Web UI for managing streams:
- Start new streaming sessions
- View active streams and statistics
- Real-time message counts per ticker
- Auto-refresh every 5 seconds
- Stop streams with confirmation

## Files Modified

### 1. `auth.py`
Added methods to TokenManager:
- `get_user_id()` - Extracts userId from JWT payload (data.uid)
- `fetch_trading_key()` - Fetches trading key from REST endpoint
- Added imports: `requests`, `logging`

### 2. `app.py`
- Imported `OrderbookManager`
- Initialized `orderbook_manager` instance
- Added `/orderbook` route for UI page
- Added 4 API endpoints:
  - `GET /api/orderbook/streams` - List all sessions
  - `POST /api/orderbook/streams` - Start new stream
  - `GET /api/orderbook/streams/<id>` - Get session stats
  - `POST /api/orderbook/streams/<id>/stop` - Stop stream
- Added cleanup call to `orderbook_manager.stop_all()` on shutdown

### 3. `templates/base.html`
- Added "Orderbook" link to navigation menu

### 4. `requirements.txt`
- Added `websockets==12.0` dependency

## How It Works

### 1. Authentication Flow
```
User Token (JWT) → TokenManager
                 ↓
          Extract userId (data.uid)
                 ↓
          Fetch tradingKey from REST API
                 ↓
          Build subscription message with:
          - userId
          - tickers (3 formats)
          - tradingKey
          - accessToken
```

### 2. WebSocket Subscription
```
Connect to wss://wss-jkt.trading.stockbit.com/ws
         ↓
Send binary Protobuf subscription request
         ↓
Receive binary Protobuf orderbook updates
         ↓
Parse: Field 1 = ticker, Field 2 = orderbook data
         ↓
Route to correct CSV file based on ticker
```

### 3. Data Storage
```
Orderbook update received
         ↓
Parse: SIDE|PRICE;LOTS;VALUE|...
         ↓
For each level:
  - Extract price, lots, value, side
  - Get/create CSV writer for ticker
  - Write row: timestamp, price, lots, total_value, side
  - Flush to disk
         ↓
At midnight: Close old file, create new one
```

### 4. Session Management
```
User starts stream via API/UI
         ↓
OrderbookManager creates OrderbookStreamer
         ↓
Streamer runs in background async event loop
         ↓
Manager tracks session stats
         ↓
User can stop stream anytime
         ↓
CSV files closed, resources cleaned up
```

## Key Technical Decisions

### 1. Manual Protobuf Encoding
**Why**: No `.proto` schema files available, only reverse-engineered field mappings
**How**: Direct wire format encoding using varint and length-delimited types
**Benefit**: No protoc compilation needed, full control over binary format

### 2. Daily CSV Files Per Ticker
**Why**: Easy to analyze, simple rotation, no database overhead
**Format**: `YYYY-MM-DD_TICKER.csv`
**Benefit**: One file per ticker per day, automatic rotation at midnight

### 3. Background Event Loop
**Why**: Flask is synchronous, WebSocket needs async
**How**: Dedicated thread running asyncio event loop
**Benefit**: Multiple concurrent streams without blocking Flask

### 4. Session-Based Management
**Why**: Support multiple independent streams
**How**: Each stream has unique session_id, tracked in dictionary
**Benefit**: Start/stop streams independently, get per-session stats

### 5. Ticker-Based Routing
**Why**: One WebSocket receives updates for all subscribed tickers
**How**: Parse Field 1 (ticker symbol) to route data to correct CSV
**Benefit**: Efficient - single connection for multiple stocks

## Data Flow Diagram

```
┌─────────────┐
│   Browser   │
│     UI      │
└──────┬──────┘
       │ HTTP POST /api/orderbook/streams
       │ {tickers: ["BBCA", "TLKM"]}
       ↓
┌─────────────────┐
│  Flask App      │
│ OrderbookMgr    │
└──────┬──────────┘
       │ Start stream
       ↓
┌─────────────────┐
│  Async Thread   │
│  Event Loop     │
└──────┬──────────┘
       │
       ↓
┌─────────────────┐         ┌──────────────────┐
│ OrderbookStream │────────▶│  TokenManager    │
│                 │  Auth   │  - userId        │
│  WebSocket      │  data   │  - tradingKey    │
│  wss://...      │         │  - accessToken   │
└──────┬──────────┘         └──────────────────┘
       │
       │ Binary Protobuf messages
       ↓
┌─────────────────┐
│ Message Parser  │
│ Field 1: Ticker │
│ Field 2: Data   │
└──────┬──────────┘
       │
       ├──── BBCA update ───▶ data/orderbook/2026-02-04_BBCA.csv
       │
       └──── TLKM update ───▶ data/orderbook/2026-02-04_TLKM.csv
```

## Testing

Run the test suite:
```bash
python3 test_orderbook.py
```

Tests verify:
1. Protobuf encoding/decoding works
2. Authentication flow (userId, tradingKey)
3. Optional: Live streaming test (requires valid token)

## Usage Examples

### Web UI
1. Start Flask: `python3 app.py`
2. Navigate to: `http://localhost:5151/orderbook`
3. Enter tickers (one per line)
4. Click "Start Stream"
5. Monitor stats in real-time
6. Check CSV files in `data/orderbook/`

### API
```bash
# Start a stream
curl -X POST http://localhost:5151/api/orderbook/streams \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["BBCA", "TLKM", "BBRI"]}'

# Get stats
curl http://localhost:5151/api/orderbook/streams/stream_20260204_103000

# Stop stream
curl -X POST http://localhost:5151/api/orderbook/streams/stream_20260204_103000/stop
```

### Python
```python
from orderbook_manager import OrderbookManager
from auth import TokenManager

token_manager = TokenManager()
token_manager.set_token("your_jwt_token")

orderbook_manager = OrderbookManager(token_manager)
orderbook_manager.start_stream("my_stream", ["BBCA", "TLKM"])

# ... later ...
orderbook_manager.stop_stream("my_stream")
```

## CSV Output Example

File: `data/orderbook/2026-02-04_BBCA.csv`
```csv
timestamp,price,lots,total_value,side
2026-02-04T10:30:15.123456,8200,100,820000000,BUY
2026-02-04T10:30:15.123456,8150,50,407500000,BUY
2026-02-04T10:30:16.234567,8250,75,618750000,SELL
2026-02-04T10:30:16.234567,8300,25,207500000,SELL
```

## Error Handling

- **Token expired**: Manager checks token validity before connecting
- **Connection lost**: Stream stops gracefully, CSV files closed properly
- **Invalid tickers**: Data routed correctly even if some tickers have no updates
- **Parsing errors**: Logged but don't crash the stream
- **File I/O errors**: Caught and logged per-ticker

## Performance Considerations

- **Memory**: One file handle per active ticker
- **CPU**: Protobuf parsing is fast (binary format)
- **I/O**: CSV writes flushed immediately for data integrity
- **Network**: Single WebSocket connection for all tickers
- **Threading**: One background thread for all streams

## Future Enhancements

Possible additions:
- Database storage (SQLite/PostgreSQL)
- Data compression
- Aggregation (OHLC candles)
- Alert system for price levels
- Auto-reconnection with backoff
- Token auto-refresh
- Market depth visualization

## Notes

- Based on reverse-engineered Stockbit WebSocket protocol
- Field mappings confirmed from your research examples
- No external dependencies on .proto files
- Reuses existing authentication from Running Trade feature
- Compatible with existing Flask app structure
