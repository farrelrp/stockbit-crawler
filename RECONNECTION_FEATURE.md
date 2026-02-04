# Auto-Reconnection Feature for Orderbook Streaming

## Overview
Added automatic reconnection capabilities with token refresh for the orderbook WebSocket streaming system. The system now automatically recovers from connection failures and displays detailed status information in the UI.

## Key Features

### 1. Automatic Reconnection with Exponential Backoff
- **Infinite Retries**: By default, streams will retry indefinitely until manually stopped
- **Smart Backoff**: Uses exponential backoff (5s, 10s, 20s, 40s, ...) capped at 5 minutes
- **Configurable**: Can set `max_retries` parameter to limit attempts or disable retries

### 2. Automatic Token Refresh
- Before each connection attempt, the system automatically refreshes the authentication token
- Ensures connections don't fail due to expired tokens
- Seamlessly handles token expiration during long-running sessions

### 3. Connection Status Tracking
New status indicators:
- `disconnected` - Initial state
- `connecting` - Attempting to connect
- `connected` - Successfully connected and receiving data
- `retrying (Xs)` - Waiting X seconds before retry
- `error` - Connection error occurred
- `stopped` - Manually stopped by user

### 4. Enhanced Statistics
The system now tracks:
- `connection_status` - Current connection state
- `retry_count` - Current retry attempt number
- `total_reconnects` - Total successful reconnections
- `last_error` - Most recent error message
- `last_disconnect_time` - When last disconnect occurred

## UI Changes

### Start Stream Form
- Added **"Enable Auto-Reconnect"** checkbox (checked by default)
- When enabled: Infinite retries with auto token refresh
- When disabled: No retries, stops on first failure

### Stream Status Display
Each active stream now shows:

1. **Connection Status Box**
   - Visual indicator with colored border (green=connected, yellow=connecting, orange=retrying, red=error)
   - Status message with icon
   - Current retry number (if retrying)
   - Error message display (if any)

2. **Enhanced Stats Grid**
   - Added "Reconnects" counter showing total successful reconnections
   - Shows connection uptime
   - Displays message counts per ticker

3. **Timestamps**
   - Started time
   - Stopped time (if applicable)
   - Last disconnect time (if any)

## API Changes

### Modified Endpoints

#### POST `/api/orderbook/streams`
**New Parameter:**
- `max_retries` (integer or null)
  - `null` (default): Infinite retries
  - `0`: No retries (fail on first error)
  - `N > 0`: Retry up to N times

**Example:**
```json
{
  "tickers": ["BBCA", "TLKM"],
  "max_retries": null,
  "session_id": "my_stream"
}
```

#### GET `/api/orderbook/streams/<session_id>`
**New Response Fields:**
```json
{
  "success": true,
  "stats": {
    "connection_status": "connected",
    "retry_count": 0,
    "total_reconnects": 2,
    "last_error": null,
    "last_disconnect_time": "2026-02-04T10:30:00",
    ...existing fields...
  }
}
```

## Implementation Details

### OrderbookStreamer Class
- Modified `__init__` to accept `max_retries` and `retry_delay` parameters
- Rewrote `run()` method with retry loop:
  - Calculates exponential backoff delays
  - Checks max retry limits
  - Logs retry attempts
  - Handles connection failures gracefully
- Enhanced `connect()` to refresh token before connecting
- Updated `get_stats()` to include new status fields

### OrderbookManager Class
- Updated `start_stream()` to accept and pass through `max_retries` parameter
- Sessions now track all reconnection metrics

### UI (orderbook.html)
- Added auto-reconnect checkbox to form
- Created `renderConnectionStatus()` function for status display
- Added CSS styles for different connection states
- Auto-refresh every 5 seconds to show real-time status

## Benefits

1. **Reliability**: Streams automatically recover from temporary network issues
2. **Visibility**: Users can see exactly what's happening with their connections
3. **Control**: Users can choose between infinite retries or manual control
4. **Token Management**: Automatic token refresh prevents auth-related failures
5. **Debugging**: Detailed error messages and retry counts help troubleshoot issues

## Usage Example

### Starting a Stream with Auto-Reconnect
1. Go to `/orderbook` page
2. Enter tickers (e.g., BBCA, TLKM, ANTM)
3. Keep "Enable Auto-Reconnect" checked (default)
4. Click "Start Stream"

The stream will:
- Connect to WebSocket
- If connection drops, automatically retry with exponential backoff
- Refresh authentication token before each retry
- Show status updates in real-time
- Continue until manually stopped

### Monitoring Connection Health
- Watch the connection status box for current state
- Green = healthy connection
- Orange = reconnecting (temporary issue)
- Red = error (check error message)
- Check "Reconnects" counter to see stability history

## Error Handling

The system handles:
- Network connectivity issues
- WebSocket connection failures
- Token expiration
- Server disconnections
- Invalid authentication

All errors are logged and displayed in the UI with timestamps.

## Performance Impact

- Minimal overhead: Only adds small delay between retries
- No impact on message processing speed
- Token refresh is only done before connection attempts
- UI updates every 5 seconds (configurable via JavaScript)

## Future Enhancements

Possible additions:
- Configurable retry delay and max delay
- Notification system for reconnection events
- Health check pings
- Connection quality metrics
- Auto-pause during market closed hours

