# 🚀 Quick Start - Market Replay

## Installation (2 minutes)

```bash
# Install new dependencies
pip install perspective-python tornado

# Or use requirements.txt
pip install -r requirements.txt
```

## Start the Application

```bash
python app.py
```

Expected output:
```
INFO - Perspective Tornado server started on port 8888
INFO - Perspective server and replay engine initialized
INFO - Starting Stockbit Running Trade Scraper
 * Running on http://0.0.0.0:5151
```

## Access the UI

Open browser: **http://localhost:5151/replay**

## Basic Usage (30 seconds)

1. **Select a CSV file** from dropdown (e.g., `2026-02-04_BBRI.csv`)
2. **Click "Load File"** - Wait for confirmation
3. **Click "Start"** - Replay begins at 1x speed
4. **Watch the orderbook ladder** update in real-time

## Try These Actions

### Change Speed
- Click preset buttons: `2x`, `5x`, `10x`, `50x`
- Or enter custom speed and press Enter

### Seek to Position
- **Click anywhere on progress bar** to jump instantly
- Useful for finding interesting market events

### Pause & Resume
- **Pause** - Freeze current state
- **Resume** - Continue from where you paused

### Stop
- **Stop** - End replay completely
- Can restart or seek to new position

## Understanding the View

### Columns
- **Price**: Order price level
- **Lots**: Current volume at this price
- **Change**: Δ lots (positive = added, negative = removed)

### Sides
- **BID**: Buy orders (left column)
- **OFFER**: Sell orders (right column)

### Sorting
- Prices sorted descending (highest first)
- Best ask at top, best bid below

## The "Change" Column Magic

**First time price appears**: `change = lots` (new level)
**Volume increases**: `change > 0` (🟢 buyers adding)
**Volume decreases**: `change < 0` (🔴 orders cancelled)
**No change**: `change = 0` (⚪ static level)

## Performance Tips

### Fast Playback
- Use `10x` or `50x` for quick overview
- Skip boring periods

### Detailed Analysis
- Use `0.5x` for slow motion
- Pause at critical moments
- Seek back and forth

### Large Files
- Files load entirely into memory (fast seeking)
- 27K rows ≈ 5MB RAM (very manageable)

## Common Workflows

### 1. Morning Review
```
1. Load yesterday's file
2. Set speed to 10x
3. Watch full day in ~30 minutes
4. Pause at anomalies
5. Seek back for details
```

### 2. Event Analysis
```
1. Load event day file
2. Seek to event time (use progress bar)
3. Set speed to 0.5x (slow motion)
4. Watch orderbook behavior
5. Note change patterns
```

### 3. Pattern Recognition
```
1. Load multiple days sequentially
2. Use 50x speed for overview
3. Pause when pattern detected
4. Seek for precise timing
5. Compare across days
```

## Troubleshooting

**Problem**: Viewer is blank
- **Solution**: Refresh page, check browser console (F12)

**Problem**: "Connection refused" error
- **Solution**: Verify port 8888 is available, restart app

**Problem**: Speed not changing
- **Solution**: Speed affects future intervals, wait a few updates

**Problem**: Seek not working
- **Solution**: Ensure file is loaded first

## Architecture Quick Reference

```
Flask (5151) ─┬─> Serves UI
              └─> API endpoints (/api/replay/*)

Tornado (8888) ──> WebSocket for Perspective
                   (ws://localhost:8888/websocket)

Replay Engine ──> Background thread
                  Reads CSV & pushes to Perspective

Browser ──────> <perspective-viewer>
                Connects to Tornado via WebSocket
```

## Key Files

```
perspective_server.py    - Tornado + Perspective setup
replay_engine.py         - CSV replay logic
templates/market_replay.html - UI
app.py                   - API endpoints (lines 345-500)
```

## API Examples

### Check Status
```bash
curl http://localhost:5151/api/replay/status
```

### Load File
```bash
curl -X POST http://localhost:5151/api/replay/load \
  -H "Content-Type: application/json" \
  -d '{"csv_path": "/path/to/file.csv"}'
```

### Start Replay at 10x Speed
```bash
curl -X POST http://localhost:5151/api/replay/start \
  -H "Content-Type: application/json" \
  -d '{"speed_multiplier": 10.0}'
```

### Seek to Row 5000
```bash
curl -X POST http://localhost:5151/api/replay/seek \
  -H "Content-Type: application/json" \
  -d '{"position": 5000}'
```

## Next Steps

1. **Read Full Guide**: See `MARKET_REPLAY_GUIDE.md` for details
2. **Explore Perspective**: Check https://perspective.finos.org/
3. **Customize View**: Modify viewer config in `market_replay.html`
4. **Extend Functionality**: Add custom metrics, annotations, etc.

## Tips for Success

✓ Start with small files (< 10K rows) to learn
✓ Use high speeds (10x-50x) for initial exploration
✓ Pause frequently to understand patterns
✓ Seek is instant - use it liberally
✓ Watch the "Change" column for momentum shifts

## Got Questions?

Check the comprehensive guide: `MARKET_REPLAY_GUIDE.md`

Happy replaying! 📊✨
