# 🔧 Troubleshooting Market Replay

## Your boss is watching - let's fix this fast!

### Issue 1: "Status shows running but nothing moves"

**Diagnosis Steps:**

1. **Open Debug Console**

   ```
   http://localhost:5151/replay/debug
   ```

   This will show:

   - ✓ WebSocket connection status
   - ✓ Table data flow
   - ✓ Update count
   - ✓ Engine status

2. **Check Browser Console (F12)**

   - Look for `[UPDATES] Received X table updates`
   - Should see updates every few seconds
   - If no updates → data isn't flowing

3. **Check Terminal Logs**
   - Look for: `Replay progress: X/Y`
   - Should appear every 100 rows
   - If missing → replay engine stuck

**Common Causes & Fixes:**

### A. Data not flowing to Perspective

**Symptom**: Engine running, but viewer empty

**Fix**:

```javascript
// In browser console (F12):
const ws = await perspective.websocket("ws://localhost:8888/websocket");
const table = await ws.open_table("orderbook");
const size = await table.size();
console.log("Table size:", size); // Should increase as replay runs
```

If size is 0 or not increasing:

1. Check Tornado server logs
2. Verify table is being updated (add print in replay_engine.py)
3. Restart application

### B. Viewer not refreshing

**Symptom**: Data in table, but viewer static

**Fix**:

1. Refresh the page (F5)
2. Check viewer configuration
3. Try opening `/replay/debug` first, then `/replay`

### C. WebSocket not connecting

**Symptom**: Console shows connection errors

**Fix**:

1. Verify Tornado running: Check logs for "Perspective Tornado server started on port 8888"
2. Check port 8888 not blocked by firewall
3. Try: `telnet localhost 8888` (should connect)

### Issue 2: "Where is the orderbook table with change column?"

**Current Configuration:**

The viewer should show:

```
        BID Column      |      OFFER Column
Price | Lots | Change  | Price | Lots | Change
------+------+---------+-------+------+--------
3850  | 153  | +50     | 3860  | 200  | -30
3840  | 303  | +100    | 3870  | 150  | +20
...
```

**If not showing correctly:**

1. **Check viewer config** (browser console):

```javascript
const viewer = document.getElementById("viewer");
const config = await viewer.save();
console.log(JSON.stringify(config, null, 2));
```

2. **Expected config**:

```json
{
  "plugin": "Datagrid",
  "columns": ["price", "lots", "change"],
  "split_by": ["side"],
  "sort": [["price", "desc"]]
}
```

3. **Manual fix** (in browser console):

```javascript
const viewer = document.getElementById("viewer");
await viewer.restore({
  plugin: "Datagrid",
  columns: ["price", "lots", "change"],
  split_by: ["side"],
  sort: [["price", "desc"]],
});
```

## 🚀 Quick Recovery Steps

### Option 1: Full Restart

```bash
# 1. Stop application (Ctrl+C)
# 2. Clear any stuck processes
taskkill /F /IM python.exe

# 3. Restart fresh
python app.py

# 4. Open debug console first
http://localhost:5151/replay/debug

# 5. Verify WebSocket connects
# 6. Then open main replay
http://localhost:5151/replay
```

### Option 2: Speed Up Replay

If replay is running but too slow:

1. **Increase speed multiplier**

   - Click `50x` button
   - Or type `100` in speed input

2. **Check actual speed**
   ```python
   # In terminal where app runs:
   # Look for log: "Replay started at index X, speed Yx"
   ```

### Option 3: Force Data Push

If everything connected but no data:

```python
# Add this endpoint to app.py for testing:

@app.route('/api/replay/test_push', methods=['POST'])
def api_replay_test_push():
    """Test endpoint to push sample data"""
    if replay_engine and replay_engine.table:
        test_data = [
            {'price': 3850.0, 'side': 'BID', 'lots': 500, 'change': 100},
            {'price': 3860.0, 'side': 'OFFER', 'lots': 300, 'change': -50}
        ]
        replay_engine.table.update(test_data)
        return jsonify({'success': True, 'pushed': len(test_data)})
    return jsonify({'success': False, 'error': 'No table'}), 400
```

Then test: `curl -X POST http://localhost:5151/api/replay/test_push`

## 📊 Show Your Boss

### Professional Demo Steps:

1. **Open two windows side by side:**

   - Left: `/replay` (main interface)
   - Right: `/replay/debug` (monitoring)

2. **Load data:**

   - Select `2026-02-04_BBRI.csv`
   - Click "Load File"
   - Watch debug console show ✓ checkmarks

3. **Start replay:**

   - Set speed to `10x` or `50x`
   - Click "Start"
   - Debug console shows update count increasing
   - Main window shows orderbook ladder updating

4. **Explain what's happening:**
   - "Left side shows BID orders (buyers)"
   - "Right side shows OFFER orders (sellers)"
   - "Change column shows order flow momentum"
   - "Green = volume added, Red = volume removed"
   - "Replaying 27,000 market updates at 50x speed"

## 🎯 Success Checklist

Before showing your boss:

- [ ] Application starts without errors
- [ ] Tornado server running (port 8888)
- [ ] Debug console shows ✓ WebSocket connected
- [ ] Debug console shows ✓ Table opened
- [ ] Load a file successfully
- [ ] Start replay - debug console shows updates increasing
- [ ] Main viewer shows BID|OFFER columns
- [ ] Progress bar moves
- [ ] Status shows "RUNNING ▶"

## 💡 Pro Tips

1. **Use 50x speed** for impressive live demo
2. **Point out the change column** - "See the order flow"
3. **Show the progress bar** - "27,000 market updates"
4. **Mention real-time** - "Preserves original market timing"
5. **Show pause/seek** - "Can jump to any moment"

## ⚠️ If Still Not Working

**Emergency fallback:**

1. Take screenshot of debug console showing:

   - ✓ WebSocket connected
   - ✓ Table opened
   - ✓ Update count increasing

2. Show terminal logs:

   - "Replay started"
   - "Replay progress" messages

3. Explain:
   - "Data is flowing (see update count)"
   - "Viewer config needs adjustment"
   - "Can export data or try different visualization"

## 📞 Need Help?

Check logs in this order:

1. Browser console (F12) - JavaScript errors
2. Terminal - Python errors
3. Debug console - Connection status

Most common issue: **Viewer not configured**

- Solution: Refresh page, or manually configure viewer

Good luck with your boss! 🚀
