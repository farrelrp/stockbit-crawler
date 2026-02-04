# Orderbook Streaming - Quick Start Guide

## Prerequisites

1. Valid Bearer token from Stockbit (same as Running Trade)
2. Python dependencies installed: `pip install -r requirements.txt`

## Step-by-Step Setup

### 1. Install New Dependency

```bash
pip install websockets==12.0
```

Or reinstall all dependencies:
```bash
pip install -r requirements.txt
```

### 2. Start the Flask App

```bash
python3 app.py
```

You should see:
```
Starting Stockbit Running Trade Scraper
 * Running on http://0.0.0.0:5151
```

### 3. Set Your Token (if not already set)

1. Open browser: `http://localhost:5151/settings`
2. Paste your Bearer token
3. Click "Save Token"
4. Verify token shows as "Valid" in the top-right corner

### 4. Test the Feature

Run the test script to verify everything works:

```bash
python3 test_orderbook.py
```

This will:
- Test Protobuf encoding/decoding
- Verify authentication (userId, tradingKey)
- Optionally run a live 10-second streaming test

### 5. Start Streaming

#### Option A: Web UI (Recommended)

1. Navigate to: `http://localhost:5151/orderbook`
2. Enter tickers (one per line):
   ```
   BBCA
   TLKM
   BBRI
   ```
3. Click "Start Stream"
4. Watch real-time statistics update every 5 seconds

#### Option B: API

```bash
curl -X POST http://localhost:5151/api/orderbook/streams \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "my_stream",
    "tickers": ["BBCA", "TLKM", "BBRI"]
  }'
```

### 6. View Data

Check the output directory:

```bash
ls -lh data/orderbook/

# Example output:
# 2026-02-04_BBCA.csv
# 2026-02-04_TLKM.csv
# 2026-02-04_BBRI.csv
```

View a file:
```bash
head data/orderbook/2026-02-04_BBCA.csv

# timestamp,price,lots,total_value,side
# 2026-02-04T10:30:15.123456,8200,100,820000000,BUY
# 2026-02-04T10:30:15.123456,8150,50,407500000,BUY
```

### 7. Monitor Stream

Get statistics:
```bash
curl http://localhost:5151/api/orderbook/streams/my_stream
```

Response:
```json
{
  "success": true,
  "stats": {
    "session_id": "my_stream",
    "status": "running",
    "tickers": ["BBCA", "TLKM", "BBRI"],
    "message_counts": {
      "BBCA": 1234,
      "TLKM": 987,
      "BBRI": 1500
    },
    "uptime_seconds": 3600
  }
}
```

### 8. Stop Stream

#### Web UI:
Click "Stop" button next to the stream

#### API:
```bash
curl -X POST http://localhost:5151/api/orderbook/streams/my_stream/stop
```

## Common Issues

### Issue: "No valid token available"

**Solution**: Set your Bearer token in Settings page first

### Issue: "Failed to fetch trading key"

**Causes**:
- Token expired
- Network connection issue

**Solution**: Get a fresh token from Stockbit and update in Settings

### Issue: Stream starts but no data received

**Causes**:
- Market is closed (check trading hours)
- Invalid ticker symbols
- Connection established but no orderbook updates

**Solution**: 
- Check if market is open
- Verify ticker symbols are correct
- Check logs: `tail -f logs/app.log`

### Issue: CSV files not created

**Solution**:
1. Check directory exists: `ls -la data/orderbook/`
2. Check write permissions
3. Look for errors in logs

## Data Format

Each CSV file contains orderbook levels:

| Column | Description | Example |
|--------|-------------|---------|
| timestamp | ISO timestamp | 2026-02-04T10:30:15.123456 |
| price | Price level | 8200 |
| lots | Number of lots | 100 |
| total_value | Total value | 820000000 |
| side | BUY or SELL | BUY |

## Tips

1. **Start Small**: Test with 2-3 tickers first
2. **Monitor Logs**: Check `logs/app.log` for issues
3. **Check Stats**: Verify message counts are increasing
4. **Market Hours**: Best results during trading hours (09:00-16:00 WIB)
5. **Token Expiry**: Keep an eye on token expiry time

## Next Steps

- Read full documentation: [`ORDERBOOK_GUIDE.md`](ORDERBOOK_GUIDE.md)
- Technical details: [`ORDERBOOK_IMPLEMENTATION.md`](ORDERBOOK_IMPLEMENTATION.md)
- Run tests: `python3 test_orderbook.py`

## Example Python Usage

```python
from orderbook_manager import OrderbookManager
from auth import TokenManager

# Setup
token_manager = TokenManager()
orderbook_manager = OrderbookManager(token_manager)

# Start streaming
result = orderbook_manager.start_stream(
    session_id="my_stream",
    tickers=["BBCA", "TLKM", "BBRI"]
)

if result['success']:
    print(f"Stream started: {result['session_id']}")
    
    # Get stats
    import time
    time.sleep(30)  # wait 30 seconds
    stats = orderbook_manager.get_session_stats("my_stream")
    print(f"Messages: {stats['message_counts']}")
    
    # Stop
    orderbook_manager.stop_stream("my_stream")
else:
    print(f"Error: {result['error']}")
```

## Architecture Overview

```
Browser → Flask App → OrderbookManager
                              ↓
                      OrderbookStreamer
                              ↓
                      WebSocket (wss://)
                              ↓
                   Stockbit Server (Protobuf)
                              ↓
                   OrderbookCSVStorage
                              ↓
                   data/orderbook/*.csv
```

## Performance

- **Memory**: ~1-2MB per active ticker
- **CPU**: Low (<5% for 10 tickers)
- **Network**: ~10-50KB/s per ticker
- **Disk**: ~1-10MB per ticker per day

## Support

If you encounter issues:
1. Check logs: `logs/app.log`
2. Run test script: `python3 test_orderbook.py`
3. Verify token status in UI
4. Review the troubleshooting section in [`ORDERBOOK_GUIDE.md`](ORDERBOOK_GUIDE.md)
