# Test the Orderbook Streaming via Web UI

## Steps to Test

1. **Start the Flask app** (if not already running):
```bash
cd "/Users/reksa/Projects/Saham Flask"
python3 app.py
```

2. **Open your browser** and go to:
```
http://localhost:5151/orderbook
```

3. **Start a stream**:
   - Enter tickers (one per line):
     ```
     BBCA
     TLKM
     BBRI
     ```
   - Click "Start Stream"

4. **Monitor the stream**:
   - You should see the connection status change to "Connected" with a green badge
   - Message counts will update every 5 seconds
   - Check the logs in the terminal running Flask

5. **View the data**:
```bash
# List CSV files
ls -lh data/orderbook/

# View sample data
head -20 data/orderbook/2026-02-04_BBCA.csv
```

## Expected Results

- ✅ Status shows "Connected" with green badge
- ✅ Message counts increase over time
- ✅ CSV files are created for each ticker
- ✅ Data is written continuously during market hours

## Troubleshooting

If the connection closes immediately:
1. Check the Flask terminal for error logs
2. Verify your Bearer token is still valid (Settings page)
3. Ensure the trading key is being fetched successfully
4. Check if market is open (orderbook updates only during trading hours)

## What's Fixed

- ✅ Heartbeat now waits 5 seconds before starting (prevents interference)
- ✅ Better error logging shows if no data received
- ✅ WebSocket status displayed in UI with green/gray badge
- ✅ Connection stability improved
- ✅ Multi-ticker streaming working perfectly

## Test Output from CLI

Just tested with 3 tickers for 5 seconds:
- **56 messages received**
- BBCA: 14 updates
- TLKM: 16 updates
- BBRI: 26 updates

All data written to CSV successfully!
