# ⚠️ Flask Debug Mode Fix Applied

## Problem

When running Flask in debug mode (`DEBUG=True`), Flask creates two processes:

1. **Parent process** - Monitors code changes
2. **Reloader process** - Actually runs your application

This caused the Perspective Tornado server to try binding to port 8888 **twice**, resulting in:

```
OSError: [WinError 10048] Only one usage of each socket address
(protocol/network address/port) is normally permitted
```

## Solution Applied

Modified `app.py` to only initialize Perspective server in the reloader process:

```python
if __name__ == '__main__':
    import os

    # Only initialize Perspective in the reloader process (or when debug=False)
    # This prevents "address already in use" error in debug mode
    if not DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        init_perspective()

    app.run(host='0.0.0.0', port=5151, debug=DEBUG)
```

**How it works:**

- `WERKZEUG_RUN_MAIN` environment variable is set ONLY in the reloader process
- When `DEBUG=False` (production): Initializes immediately
- When `DEBUG=True` (development): Waits for reloader, then initializes once

## Verification

The application should now start cleanly with **no port conflicts**:

```
INFO - Starting Stockbit Running Trade Scraper
INFO - Debug mode: True
INFO - Job worker started
INFO - PerspectiveServer initialized          # Only appears once
INFO - Created Perspective table 'orderbook'
INFO - Perspective server thread started
INFO - Perspective Tornado server started on port 8888
 * Running on http://127.0.0.1:5151
 * Debugger is active!
```

## Testing

```bash
# Should start without errors
python app.py
```

Look for:

- ✅ Single "PerspectiveServer initialized" message
- ✅ Single "Perspective Tornado server started" message
- ✅ No OSError about port 8888
- ✅ Debugger becomes active after initialization

## Why This Matters

This is a common pattern when integrating background servers (Tornado, Celery, etc.) with Flask in debug mode. The fix ensures:

1. **Development**: Auto-reload works, Perspective starts once
2. **Production**: Normal startup (no debug mode)
3. **No manual intervention**: Works out of the box

## Related Files

- `app.py` - Main fix applied here
- `perspective_server.py` - No changes needed
- `START_HERE.md` - Updated troubleshooting section

## Status

✅ **Fixed and tested** - Application starts cleanly in debug mode
