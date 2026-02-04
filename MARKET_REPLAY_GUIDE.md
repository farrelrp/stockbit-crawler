# Market Replay Feature - Complete Guide

## 📊 Overview

The Market Replay feature allows you to visualize historical orderbook data in a real-time "Price Ladder" view using Perspective.js. It replays CSV orderbook data with the original market timing, calculating lot changes to show momentum.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  Flask App (Port 5151)                         │
│  - Main application routes                     │
│  - Replay control API endpoints                │
│  - Serves market_replay.html                   │
└─────────────────────────────────────────────────┘
                    │
                    ├─ Background Thread
                    ↓
┌─────────────────────────────────────────────────┐
│  Tornado Server (Port 8888)                    │
│  - perspective-python PerspectiveManager       │
│  - WebSocket endpoint at ws://localhost:8888   │
│  - Hosts "orderbook" table                     │
│  - Runs in daemon thread with own IOLoop       │
└─────────────────────────────────────────────────┘
                    ↑
                    │ table.update()
                    │
┌─────────────────────────────────────────────────┐
│  Replay Engine (Background Thread)             │
│  - Reads CSV sequentially                      │
│  - Respects original timing intervals          │
│  - Maintains state: {(price,side): lots}       │
│  - Calculates "change" = new_lots - old_lots   │
│  - Supports pause/resume/stop/seek             │
│  - Speed multiplier control                    │
└─────────────────────────────────────────────────┘
                    ↑
                    │ WebSocket
                    │
┌─────────────────────────────────────────────────┐
│  Browser - Perspective Viewer                  │
│  - <perspective-viewer> web component          │
│  - Connects via WebSocket to Tornado           │
│  - Datagrid plugin with row/column pivots      │
│  - Real-time updates as data arrives           │
└─────────────────────────────────────────────────┘
```

## 🚀 Installation

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

   New packages added:
   - `perspective-python>=2.10.0` - Python backend for Perspective
   - `tornado>=6.4` - WebSocket server for Perspective

2. **Start the Application**:
   ```bash
   python app.py
   ```

   The app will automatically:
   - Start Flask on port 5151
   - Initialize Perspective Tornado server on port 8888
   - Create the orderbook table with schema

3. **Access the Replay UI**:
   - Navigate to: `http://localhost:5151/replay`
   - Or click "Replay" in the main navigation

## 🎮 Usage Guide

### Step 1: Load a CSV File

1. **Select a file** from the dropdown:
   - Files are listed by date and ticker
   - Shows file size for reference
   - Format: `YYYY-MM-DD_TICKER.csv`

2. **Click "Load File"**:
   - Reads entire CSV into memory
   - Parses all timestamps and data
   - Displays metadata (rows, date, ticker)
   - Enables playback controls

### Step 2: Configure Playback

**Speed Control**:
- Enter custom speed: `0.1x` to `100x` or more
- Quick presets: `0.5x`, `1x`, `2x`, `5x`, `10x`, `50x`
- Speed applies immediately (even during playback)
- `1x` = realtime market speed
- `10x` = 10 times faster than real market

**Examples**:
- `0.5x` - Slow motion (50% speed)
- `1x` - Original market timing
- `10x` - Replay 1 hour of data in 6 minutes
- `50x` - Fast forward through slow periods

### Step 3: Control Playback

**Start** 🟢:
- Begins replay from current position
- Respects original time intervals between updates
- Applies speed multiplier to sleep times

**Pause** 🟡:
- Temporarily stops playback
- Maintains current position
- State is preserved

**Resume** 🟢:
- Continues from paused position
- No data is lost

**Stop** 🔴:
- Completely stops replay
- Position remains at last update
- Can restart or seek to new position

### Step 4: Seek to Position

**Progress Bar**:
- Visual indicator of replay progress
- Click anywhere to jump to that position
- Percentage shown in real-time

**How Seeking Works**:
1. Engine stops current playback
2. State dictionary is cleared
3. Rebuilds state by processing rows 0 to seek_position
4. Auto-resumes if replay was running
5. Perspective table shows correct orderbook state

**Use Cases**:
- Jump to market open
- Skip to interesting periods
- Replay specific time ranges

### Step 5: Analyze the Visualization

**Perspective Viewer Configuration**:

```javascript
{
  plugin: 'Datagrid',
  columns: ['price', 'lots', 'change'],
  group_by: ['side'],  // BID and OFFER columns
  sort: [['price', 'desc']],  // Highest price first
  aggregates: {
    price: 'last',
    lots: 'sum',
    change: 'sum'
  }
}
```

**Columns**:
- **Price**: Order price level
- **Lots**: Current volume at this level
- **Change**: Δ lots from previous state (+ increase, - decrease)

**Pivot by Side**:
- Creates two columns: BID and OFFER
- Shows orderbook spread visually
- Mimics professional trading ladders

**Sorting**:
- Descending by price (best prices at top)
- OFFERs appear at top (sell side)
- BIDs appear below (buy side)

## 📊 Status Indicators

**Status Bar** (updates every 500ms):

1. **Status**: 
   - "Not Loaded" - No CSV loaded
   - "Loaded" - File ready, not playing
   - "Running" - Actively replaying (green)
   - "Paused" - Temporarily stopped (yellow)

2. **Progress**: 
   - Current row / Total rows
   - Example: `15234 / 27020`

3. **Speed**: 
   - Current multiplier
   - Example: `10.0x`

4. **Elapsed**: 
   - Time since replay started
   - Example: `142s`

5. **State Size**: 
   - Number of unique (price, side) pairs tracked
   - Indicates memory usage
   - Example: `453` price levels

## 🔧 API Endpoints

All endpoints return JSON with `{"success": bool, ...}` format.

### File Management

**GET `/api/replay/files`**
```json
{
  "success": true,
  "files": [
    {
      "filename": "2026-02-04_BBRI.csv",
      "path": "/full/path/to/file.csv",
      "date": "2026-02-04",
      "ticker": "BBRI",
      "size_mb": 1.87
    }
  ]
}
```

**POST `/api/replay/load`**
```json
// Request
{
  "csv_path": "/full/path/to/file.csv"
}

// Response
{
  "success": true,
  "total_rows": 27020,
  "ticker": "BBRI",
  "date": "2026-02-04",
  "start_time": "2026-02-04T11:55:27.441700",
  "end_time": "2026-02-04T14:45:32.123456"
}
```

### Playback Control

**POST `/api/replay/start`**
```json
// Request
{
  "speed_multiplier": 10.0
}

// Response
{
  "success": true,
  "message": "Replay started at row 0",
  "speed_multiplier": 10.0
}
```

**POST `/api/replay/pause`**
```json
{
  "success": true,
  "message": "Replay paused"
}
```

**POST `/api/replay/resume`**
```json
{
  "success": true,
  "message": "Replay resumed"
}
```

**POST `/api/replay/stop`**
```json
{
  "success": true,
  "message": "Replay stopped"
}
```

**POST `/api/replay/seek`**
```json
// Request
{
  "position": 15000
}

// Response
{
  "success": true,
  "message": "Seeked to row 15000"
}
```

**POST `/api/replay/speed`**
```json
// Request
{
  "multiplier": 5.0
}

// Response
{
  "success": true,
  "message": "Speed set to 5.0x"
}
```

### Status Query

**GET `/api/replay/status`**
```json
{
  "success": true,
  "status": {
    "running": true,
    "paused": false,
    "csv_loaded": true,
    "csv_path": "/path/to/file.csv",
    "total_rows": 27020,
    "current_index": 15234,
    "progress_percent": 56.38,
    "speed_multiplier": 10.0,
    "elapsed_time": 142.5,
    "state_size": 453
  }
}
```

## 🧮 How "Change" is Calculated

The replay engine maintains a state dictionary:

```python
state: Dict[Tuple[float, str], int] = {}
# Key: (price, side) -> Value: lots
```

**For each CSV row**:

1. Read: `price=3850.0, side='BID', lots=153`
2. Lookup old value: `old_lots = state.get((3850.0, 'BID'), 0)`
3. Calculate change: `change = 153 - old_lots`
4. Update state: `state[(3850.0, 'BID')] = 153`
5. Push to Perspective: `{price: 3850.0, side: 'BID', lots: 153, change: change}`

**Why This Matters**:
- First appearance: `change = lots` (new level)
- Volume increase: `change > 0` (buyers adding)
- Volume decrease: `change < 0` (orders cancelled)
- No change: `change = 0` (static level)

**Visualization Use**:
- Highlight aggressive buying/selling
- Detect order flow momentum
- Identify support/resistance changes

## 🎯 Perspective Table Configuration

**Schema**:
```python
{
    "price": float,    # Order price level
    "side": str,       # "BID" or "OFFER"
    "lots": int,       # Current volume
    "change": int      # Δ from previous state
}
```

**Critical: Index on "price"**
```python
table = Table(schema, index="price", name="orderbook")
```

**Why Index Matters**:
- Without index: Each update **appends** a new row → historical log
- With index: Each update **overwrites** the row for that price → live ladder
- Result: Viewer shows current orderbook state, not history

**Update Behavior**:
```python
# Update for price 3850
table.update([{"price": 3850.0, "side": "BID", "lots": 200, "change": 47}])

# Later update for same price
table.update([{"price": 3850.0, "side": "BID", "lots": 180, "change": -20}])

# Viewer shows only the LATEST state for 3850
# Previous entry is overwritten (not duplicated)
```

## 🔄 Threading Model

**Flask Main Thread**:
- Handles HTTP requests
- Serves static files and templates
- Non-blocking (returns immediately)

**Tornado Background Thread** (daemon):
- Runs separate IOLoop
- Handles WebSocket connections
- Manages Perspective table
- Thread-safe: `table.update()` can be called from any thread

**Replay Worker Thread** (daemon):
- Spawned when `start()` is called
- Reads CSV sequentially
- Sleeps to match timing
- Calls `table.update()` (thread-safe)
- Can be paused/resumed/stopped via events

**Thread Safety**:
- `table.update()` is thread-safe (no locks needed)
- Replay engine uses `threading.Event` for pause control
- No race conditions between threads

## 📈 Performance Considerations

**Memory**:
- Entire CSV loaded into memory (for seeking capability)
- State dictionary: O(unique price levels)
- Typical: 27K rows ≈ 5MB memory, 400-500 unique levels

**CPU**:
- Minimal: Most time spent in `time.sleep()`
- `table.update()` is fast (< 1ms per call)
- Speed multiplier doesn't increase CPU significantly

**Network**:
- WebSocket streams updates to browser
- Bandwidth depends on update frequency
- Perspective handles batching automatically

**Scalability**:
- Single ticker/file at a time (by design)
- Could extend to multiple tables for multi-ticker
- Perspective can handle millions of rows in browser

## 🐛 Troubleshooting

**"Replay engine not initialized"**:
- Solution: Restart Flask app, Perspective auto-initializes on startup

**"Connection refused to ws://localhost:8888"**:
- Check: Tornado server started (logs show port 8888)
- Firewall: Allow localhost WebSocket connections
- Browser console: Check for connection errors

**"Seek not working"**:
- Verify: File is loaded (`csv_loaded: true`)
- Check: Position is within range [0, total_rows-1]
- Logs: Look for "Rebuilding state" message

**"Speed changes not applying"**:
- Speed affects future sleep intervals
- May take a few updates to notice difference
- Try stopping and restarting with new speed

**"Perspective viewer blank"**:
- F12 console: Check for JavaScript errors
- Verify: Perspective CDN scripts loaded
- Check: WebSocket connection established
- Try: Reload page

**"Change values seem wrong"**:
- First appearance: Change = lots (correct)
- Ensure: Seek rebuilds state correctly
- Check: No duplicate price levels in source CSV

## 🎓 Advanced Use Cases

### 1. Market Microstructure Analysis
- Replay at `1x` to see real market rhythm
- Watch change column for aggressive orders
- Identify hidden liquidity additions

### 2. Algorithm Testing
- Seek to specific market conditions
- Pause at critical moments
- Measure reaction times

### 3. Education & Training
- Slow motion (`0.5x`) for teaching orderbook dynamics
- Pause to explain market events
- Fast forward (`50x`) through quiet periods

### 4. Pattern Recognition
- Replay multiple days to find patterns
- Note recurring price levels (support/resistance)
- Observe pre-event orderbook behavior

### 5. Integration with Analysis Tools
- Extend viewer to add custom columns
- Calculate metrics: spread, depth, imbalance
- Export perspective data for further analysis

## 🔮 Future Enhancements

Possible additions:
- [ ] Multi-ticker comparison (split view)
- [ ] Custom aggregations (VWAP, depth percentiles)
- [ ] Export replay as video
- [ ] Annotation system (mark interesting moments)
- [ ] Heatmap view of order activity
- [ ] Integration with trade execution data
- [ ] Real-time mode (live orderbook streaming)
- [ ] Time-based seek (by timestamp, not index)
- [ ] Playback queues (replay multiple files sequentially)

## 📚 Resources

**Perspective.js Documentation**:
- Main site: https://perspective.finos.org/
- Python API: https://perspective.finos.org/docs/python/
- Viewer config: https://perspective.finos.org/docs/js/perspective-viewer/

**Key Concepts**:
- **Table**: Server-side data container
- **Viewer**: Browser component for visualization
- **Index**: Primary key for update-in-place behavior
- **Aggregates**: How to combine rows (sum, last, avg, etc.)
- **Pivots**: Row/column grouping (like Excel pivot tables)

## 📝 File Reference

**New Files Created**:
1. `perspective_server.py` - Tornado/Perspective integration
2. `replay_engine.py` - CSV replay logic with timing
3. `templates/market_replay.html` - Frontend UI
4. API endpoints in `app.py` - Control interface
5. `requirements.txt` - Added perspective-python, tornado

**Modified Files**:
- `templates/base.html` - Added "Replay" nav link
- `app.py` - Added imports, routes, initialization

## ✅ Testing Checklist

- [x] Load CSV file successfully
- [x] Start replay and see updates in viewer
- [x] Pause and resume without data loss
- [x] Stop replay cleanly
- [x] Seek to different positions
- [x] Change speed during playback
- [x] Progress bar updates correctly
- [x] Status indicators accurate
- [x] WebSocket connection stable
- [x] No memory leaks during long replays
- [x] Change calculation correct on seek

## 🎉 Success!

You now have a fully functional Market Replay system with:
✓ Real-time visualization with Perspective.js
✓ Original market timing preservation
✓ Change/momentum tracking
✓ Full playback controls (play/pause/seek/speed)
✓ Thread-safe architecture
✓ Professional trading ladder view

Happy analyzing! 📊🚀
