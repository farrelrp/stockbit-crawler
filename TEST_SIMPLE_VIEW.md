# 🔍 Testing Simple View - Fixed Issues

## What I Fixed

### Issue 1: Update counter always incrementing ✅

**Problem:** Counter went up every 100ms even with no data
**Fix:** Now only increments when there's actual bid/offer data to display

### Issue 2: "No data yet" stays forever ✅

**Problem:** Tables never showed data even after loading and starting
**Fix:** Added debug logging throughout the data flow to track where data goes

## New Features Added

1. **Better Status Display**

   - Progress: Shows `current_index/total_rows`
   - Levels: Shows total orderbook levels in state
   - All info updates in real-time

2. **Debug Logging**

   - Browser console shows detailed logs for:
     - File loading
     - Replay starting
     - Data updates
     - API responses
   - First 5 updates logged in detail

3. **Server-Side Logging**
   - First 3 API calls logged to see state
   - Shows how many state entries exist
   - Shows running status

## Testing Steps

### Step 1: Restart Flask App

```bash
# Stop if running (Ctrl+C)
cd "D:\Data\Flask Saham"
python app.py
```

Wait for:

```
2026-02-04 XX:XX:XX - perspective_server - INFO - Perspective Tornado server started on port 8888
2026-02-04 XX:XX:XX - replay_engine - INFO - ReplayEngine initialized
```

### Step 2: Open Simple View

```
http://localhost:5151/replay/simple
```

### Step 3: Open Browser Console

Press **F12** → Go to **Console** tab

You should see:

```
[LOAD] Loading file: ...
```

### Step 4: Load File

1. Select `2026-02-04 - BBRI` from dropdown
2. Click "Load"
3. **Check console** - should see:
   ```
   [LOAD] Loading file: D:\Data\Flask Saham\data\orderbook\2026-02-04_BBRI.csv
   [LOAD] Response: {success: true, total_rows: 27019, ...}
   [LOAD] File loaded successfully
   ```
4. **Check alert** - "Loaded 27,019 rows - Ready to replay!"
5. **Check status bar** - Progress: `0/27019`

### Step 5: Start Replay

1. Set speed to `50` or `100`
2. Click "▶ Start"
3. **Check console** - should see:
   ```
   [START] Starting replay with speed 50x
   [START] Response: {success: true, message: "Replay started..."}
   [START] Replay started successfully
   [DEBUG] Update #1: X bids, Y offers, Z total levels, running=true, progress=...
   [DEBUG] Update #2: ...
   ```

### Step 6: Watch The Tables

**Expected behavior:**

✅ **BID table (left, green)** fills with prices like:

```
Price    | Lots      | Change
3850     | 153       | +153
3840     | 303       | +303
3830     | 299       | +299
...
```

✅ **OFFER table (right, red)** fills with prices like:

```
Price    | Lots      | Change
3860     | 145       | +145
3870     | 220       | +220
3880     | 178       | +178
...
```

✅ **Status bar shows:**

- Status: RUNNING
- Updates: 1, 2, 3, 4... (incrementing only when data appears)
- Progress: 150/27019, 200/27019, etc.
- Levels: 50, 75, 100... (growing as orderbook builds)

✅ **Change column:**

- First update: All green +numbers (new data)
- Later updates: Mix of green/red/0 as lots change

## What To Check

### In Browser Console:

1. **File loads successfully?**

   ```
   ✓ [LOAD] File loaded successfully
   ```

2. **Replay starts?**

   ```
   ✓ [START] Replay started successfully
   ```

3. **Updates coming in?**

   ```
   ✓ [DEBUG] Update #1: 15 bids, 18 offers, 33 total levels
   ✓ [DEBUG] Update #2: 20 bids, 20 offers, 45 total levels
   ```

4. **Any errors?**
   ```
   ✗ Look for red [ERROR] messages
   ```

### In Flask Terminal:

1. **Replay starts?**

   ```
   ✓ INFO - Starting replay loop: 27019 rows, speed=50x
   ```

2. **Progress logs?**

   ```
   ✓ DEBUG - Replay progress: 0/27019 - Last: BID @ 3850.0 x 153
   ✓ DEBUG - Replay progress: 100/27019 - Last: OFFER @ 3900.0 x 89
   ```

3. **Orderbook API calls?**
   ```
   ✓ INFO - [DEBUG] Orderbook API call #1: state has 0 entries, running=False
   ✓ INFO - [DEBUG] Orderbook API call #2: state has 33 entries, running=True
   ✓ INFO - [DEBUG] Returning 15 bids, 18 offers from 33 total levels
   ```

## If Still Not Working

### Scenario A: Update counter increments, but no data in tables

**Check console for:**

```
[DEBUG] Update #1: 0 bids, 0 offers, 0 total levels, running=true, progress=0/27019
```

**This means:**

- Replay IS running (running=true)
- But state is empty (0 total levels)
- Data not being added to replay_engine.state

**Debug:**

1. Check Flask terminal for "Starting replay loop" message
2. Check for "Replay progress: X/27019" debug logs
3. If no logs → replay thread not starting
4. If logs exist → table.update() might be failing silently

### Scenario B: Everything starts, but tables stay "Waiting for data..."

**Check console for:**

```
[DEBUG] Update #1: 0 bids, 0 offers, 0 total levels, running=true
```

**This means:**

- API is being called
- But state is empty

**Debug:**

1. In Flask terminal, check for `_calculate_change` or `table.update()` errors
2. Add breakpoint in `replay_engine.py` line 158 (`change = self._calculate_change(...)`)
3. Verify `self.data_rows` is populated after load

### Scenario C: Nothing happens when clicking Start

**Check console for:**

```
[START] Response: {success: false, error: "..."}
```

**This means:**

- API endpoint returned error
- Check the error message

**Common errors:**

- "Replay already running" → Stop first, then start
- "No data loaded" → Load file first
- "Replay engine not initialized" → Restart Flask app

## Report Back

After testing, tell me:

1. **What do you see in browser console?**

   - Copy the first 10 lines

2. **What do you see in tables?**

   - Still "No data yet"? OR
   - Data appearing? If so, how many rows?

3. **What's in Flask terminal?**

   - Any "Replay progress" logs?
   - Any "[DEBUG] Returning X bids, Y offers" logs?

4. **Status bar values:**
   - Updates: ?
   - Progress: ?
   - Levels: ?

This will help me pinpoint exactly where the data flow is breaking! 🔍
