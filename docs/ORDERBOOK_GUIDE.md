# Orderbook Level 2 Streaming Guide

## Overview

The orderbook streaming feature allows you to capture real-time Level 2 market data (bid/ask orderbook) for multiple stocks simultaneously through a single WebSocket connection. Data is automatically stored in daily CSV files per ticker.

## Features

- **Multi-Stock Streaming**: Subscribe to multiple tickers in one WebSocket connection
- **Real-Time Data**: Receive orderbook updates as they happen
- **Automatic CSV Storage**: Data saved to daily CSV files (e.g., `2026-02-04_BBCA.csv`)
- **Smart File Rotation**: New files created automatically when the day changes
- **Binary Protocol**: Uses Protobuf for efficient data transmission
- **Session Management**: Start/stop multiple streaming sessions independently

## Architecture

### Authentication Flow

1. **JWT Token**: Uses your existing Bearer token (same as Running Trade)
2. **Trading Key**: Automatically fetched from `https://exodus.stockbit.com/auth/websocket/key`
3. **User ID**: Extracted from JWT token payload (`data.uid`)

### WebSocket Connection

- **Endpoint**: `wss://wss-jkt.trading.stockbit.com/ws`
- **Protocol**: Binary Protobuf messages
- **Heartbeat**: Automatic ping/pong every 30 seconds

### Subscription Request (Protobuf)

When subscribing, the client sends a binary message with these fields:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| 1 | int | User ID from JWT | 4826457 |
| 2 | string | Concatenated tickers (no separator) | "BBCATLKMBBRI" |
| 3 | string | Colon-separated tickers | "BBCA:TLKM:BBRI" |
| 4 | string | J-prefixed tickers | "JBBCAJTLKMJBBRI" |
| 5 | string | Trading key from REST endpoint | "vq3bj6Zxv6e..." |
| 6 | string | JWT Bearer token | "eyJhbGci..." |

### Incoming Orderbook Messages (Protobuf)

Server sends binary messages with these fields:

| Field | Type | Description | Format |
|-------|------|-------------|--------|
| 1 | string | Ticker symbol | "BBCA" |
| 2 | string | Orderbook data | "BUY\|8200;100;820000000\|8150;50;407500000" |
| 5 | int/string | Server timestamp 1 | Unix timestamp or ISO format |
| 9 | int/string | Server timestamp 2 | Unix timestamp or ISO format |

### Orderbook Data Format

Field 2 contains the orderbook levels in this format:

```
SIDE|PRICE;LOTS;VALUE|PRICE;LOTS;VALUE|...
```

Example:
```
BUY|8200;100;820000000|8150;50;407500000
```

Parsed as:
- **Side**: BUY
- **Level 1**: Price=8200, Lots=100, Value=820000000
- **Level 2**: Price=8150, Lots=50, Value=407500000

## Usage

### Web Interface

1. Navigate to the **Orderbook** page in the web UI
2. Enter your desired tickers (one per line)
3. Optionally provide a session ID (auto-generated if blank)
4. Click "Start Stream"

The page will show:
- Active streams
- Message counts per ticker
- Uptime
- Real-time statistics

### API Endpoints

#### Start a Stream

```bash
POST /api/orderbook/streams
Content-Type: application/json

{
  "session_id": "my_stream_1",  # optional
  "tickers": ["BBCA", "TLKM", "BBRI"]
}
```

Response:
```json
{
  "success": true,
  "session_id": "my_stream_1",
  "tickers": ["BBCA", "TLKM", "BBRI"],
  "started_at": "2026-02-04T10:30:00"
}
```

#### List All Streams

```bash
GET /api/orderbook/streams
```

Response:
```json
{
  "success": true,
  "sessions": [
    {
      "session_id": "my_stream_1",
      "status": "running",
      "tickers": ["BBCA", "TLKM", "BBRI"],
      "message_counts": {
        "BBCA": 1234,
        "TLKM": 987,
        "BBRI": 1500
      },
      "uptime_seconds": 3600,
      "started_at": "2026-02-04T10:30:00"
    }
  ]
}
```

#### Get Stream Statistics

```bash
GET /api/orderbook/streams/{session_id}
```

#### Stop a Stream

```bash
POST /api/orderbook/streams/{session_id}/stop
```

### Python Usage

```python
from orderbook_manager import OrderbookManager
from auth import TokenManager

# Initialize
token_manager = TokenManager()
token_manager.set_token("your_bearer_token_here")

orderbook_manager = OrderbookManager(token_manager)

# Start streaming
result = orderbook_manager.start_stream(
    session_id="my_stream",
    tickers=["BBCA", "TLKM", "BBRI"]
)

# Get stats
stats = orderbook_manager.get_session_stats("my_stream")
print(f"Messages received: {stats['message_counts']}")

# Stop streaming
orderbook_manager.stop_stream("my_stream")
```

## Output Files

### File Naming

Files are stored in `data/orderbook/` with the format:
```
YYYY-MM-DD_TICKER.csv
```

Examples:
- `2026-02-04_BBCA.csv`
- `2026-02-04_TLKM.csv`

### CSV Format

Each CSV contains these columns:

| Column | Type | Description |
|--------|------|-------------|
| timestamp | string | ISO timestamp or Unix timestamp |
| price | float | Price level |
| lots | int | Number of lots |
| total_value | float | Total value at this level |
| side | string | "BUY" or "SELL" |

Example:
```csv
timestamp,price,lots,total_value,side
2026-02-04T10:30:15,8200,100,820000000,BUY
2026-02-04T10:30:15,8150,50,407500000,BUY
2026-02-04T10:30:16,8250,75,618750000,SELL
```

### File Rotation

- Files are rotated automatically at midnight
- If a stream runs across midnight, a new file is created for the new day
- Old files remain intact

## Implementation Details

### Classes

#### `OrderbookStreamer`
Main WebSocket client that:
- Establishes connection
- Handles authentication
- Receives and parses messages
- Routes data to CSV storage
- Manages heartbeat

#### `OrderbookCSVStorage`
Manages file operations:
- Creates daily CSV files
- Handles file rotation
- Writes orderbook levels
- Maintains file handles

#### `OrderbookManager`
High-level manager:
- Runs streams in background thread
- Manages multiple sessions
- Provides statistics
- Handles start/stop operations

### Protobuf Encoding

The implementation uses manual Protobuf wire format encoding:
- **Varint encoding** for integers (field 1)
- **Length-delimited encoding** for strings (fields 2-6)
- No `.proto` files needed - direct binary manipulation

Key functions:
- `encode_websocket_request()`: Creates subscription message
- `decode_orderbook_message()`: Parses incoming updates
- `_encode_varint()`: Protobuf varint encoding
- `_decode_varint()`: Protobuf varint decoding

## Troubleshooting

### Connection Issues

**Problem**: WebSocket connection fails

**Solution**:
1. Verify your Bearer token is valid
2. Check token expiry time
3. Ensure trading key fetch is successful
4. Check network connectivity

### No Data Received

**Problem**: Stream starts but no messages

**Possible causes**:
1. Market is closed
2. Tickers are invalid
3. Subscription message format incorrect

**Debug**:
- Check logs for Protobuf encoding errors
- Verify ticker symbols are correct
- Ensure userId is extracted from JWT

### CSV Files Not Created

**Problem**: Stream running but no CSV files

**Solution**:
1. Check `data/orderbook/` directory exists
2. Verify write permissions
3. Look for parsing errors in logs

### Token Expired During Stream

**Problem**: Stream stops with auth error

**Solution**:
- Update your Bearer token in Settings
- Restart the stream
- Consider implementing auto-refresh (future enhancement)

## Best Practices

1. **Monitor Session Stats**: Check message counts to ensure data flow
2. **Reasonable Ticker Count**: Don't subscribe to too many tickers at once (start with 5-10)
3. **File Management**: Periodically clean up old CSV files
4. **Token Validity**: Ensure token has sufficient time before expiry
5. **Error Handling**: Check logs if streams stop unexpectedly

## Differences from Running Trade

| Feature | Running Trade | Orderbook Streaming |
|---------|---------------|---------------------|
| **Connection** | REST API (HTTP) | WebSocket (persistent) |
| **Data Type** | Historical trades | Real-time orderbook |
| **Protocol** | JSON | Binary Protobuf |
| **Pagination** | Manual (trade_number) | Continuous stream |
| **Storage** | Batch CSV writes | Real-time CSV append |
| **Rate Limiting** | Per-request delays | WebSocket flow control |

## Future Enhancements

Potential improvements:
- Database storage option (SQLite, PostgreSQL)
- Data compression for CSV files
- Aggregation/OHLC calculation
- Market depth visualization
- Alert system for price levels
- Automatic reconnection with exponential backoff
- Token auto-refresh before expiry

## Notes

- This implementation is based on reverse-engineering the Stockbit WebSocket protocol
- Field mappings may change if Stockbit updates their protocol
- Always test with a small set of tickers first
- Monitor logs for protocol changes or errors
