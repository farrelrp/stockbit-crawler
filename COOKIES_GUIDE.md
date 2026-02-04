# Adding Cookies for Stable WebSocket Connections

## Why Cookies Matter

The Stockbit WebSocket server validates session cookies to keep connections alive for hours. Without proper cookies, the connection may close after ~30-40 messages.

**With cookies**: Connection stays open for hours ✅  
**Without cookies**: Connection may close after 1-2 minutes ⚠️

## How to Get Your Cookies

### Option 1: From Postman (Easiest)

1. Open Postman with your working WebSocket connection
2. Look at the Headers section
3. Find the **Cookie** header
4. Copy the entire value (it will look like):
   ```
   G_ENABLED_IDPS=google; crisp-client%2Fsession%2F...=session_...; AWSALB=...; AWSALBCORS=...
   ```

### Option 2: From Browser DevTools

1. Login to [Stockbit.com](https://stockbit.com) in your browser
2. Open Developer Tools (F12 or Right-click → Inspect)
3. Go to the **Network** tab
4. Click on any request to `exodus.stockbit.com` or `wss-jkt.trading.stockbit.com`
5. Look at **Request Headers**
6. Find **Cookie:** and copy the entire value

### Option 3: From Browser Console

1. Login to Stockbit.com
2. Open Developer Tools Console (F12)
3. Type: `document.cookie`
4. Press Enter
5. Copy the result

## How to Add Cookies to the App

### Step 1: Go to Settings

Open: `http://localhost:5151/settings`

### Step 2: Enter Token and Cookies

1. **Bearer Token** field: Paste your JWT token (required)
2. **Cookies** field: Paste your entire Cookie header (recommended for orderbook)
3. Click **"Save Token & Cookies"**

You should see: "Token set successfully (with cookies)"

### Step 3: Start Orderbook Streaming

1. Go to: `http://localhost:5151/orderbook`
2. Enter tickers
3. Click "Start Stream"
4. Connection should now stay open for hours!

## What Cookies Look Like

Your cookie string should contain session identifiers like:

```
G_ENABLED_IDPS=google; crisp-client^%^2Fsession^%^2F75898fc6-9f95-4d0c-b385-fb8dfd630835=session_f1941556-cb2a-4715-913b-c2298b0cba95; AWSALB=qPnFTPW9AR0Yq8lt904PKunu90kaGvNvBWoQxIdqDapOYzkQAlx7ZJnDHJmOfrXXqvKongn+TVge/UfXo3/7LGbXJCYLPKFzZMMAg82LocLGJzIMskrAtvGxeYZR; AWSALBCORS=qPnFTPW9AR0Yq8lt904PKunu90kaGvNvBWoQxIdqDapOYzkQAlx7ZJnDHJmOfrXXqvKongn+TVge/UfXo3/7LGbXJCYLPKFzZMMAg82LocLGJzIMskrAtvGxeYZR
```

**Important cookies**:
- `AWSALB` / `AWSALBCORS` - AWS load balancer session
- `crisp-client` - Session identifiers
- `G_ENABLED_IDPS` - Google auth

## Testing

After adding cookies, test the connection:

```bash
cd "/Users/reksa/Projects/Saham Flask"
python3 << 'TEST'
from auth import TokenManager

tm = TokenManager()
print(f"Token: {'✅ Set' if tm.get_valid_token() else '❌ Missing'}")
print(f"Cookies: {'✅ Set' if tm.get_cookies() else '❌ Missing'}")

if tm.get_cookies():
    print(f"Cookie length: {len(tm.get_cookies())} chars")
TEST
```

## Troubleshooting

### Issue: Connection still closes quickly

**Possible causes**:
1. Cookies are expired/invalid
2. Cookies were copied incorrectly (missing parts)
3. Session expired on Stockbit's side

**Solution**:
- Get fresh cookies from a new login session
- Make sure you copied the ENTIRE cookie string
- Include all cookies, don't pick and choose

### Issue: "Cookies saved" but connection unstable

**Check**:
```bash
# Verify cookies are stored
cat config_data/token.json | python3 -m json.tool
```

You should see a `"cookies"` field with your cookie string.

### Issue: Cookies contain special characters

This is normal! Cookies may contain:
- URL-encoded characters (`%2F`, `%5E`, etc.)
- Semicolons (`;`) as separators
- Special symbols (`^`, `=`, `/`)

**Don't modify** the cookie string - paste it exactly as-is!

## Cookie Expiration

**Important**: Cookies expire when:
- You logout from Stockbit
- Your browser session ends
- Stockbit's session timeout is reached

**Solution**: When your orderbook connections start closing quickly again, get fresh cookies and update them in Settings.

## Security Note

**Cookies are sensitive!** They provide session access to your Stockbit account.

- ✅ Stored locally in `config_data/token.json`
- ✅ Not transmitted except to Stockbit's WebSocket
- ⚠️ Don't share your `token.json` file
- ⚠️ Don't commit cookies to git repositories

## Summary

1. **Get cookies** from Postman or Browser DevTools
2. **Add to Settings** page in the "Cookies" field
3. **Save** with your Bearer token
4. **Start streaming** - connection should stay open for hours
5. **Refresh cookies** when connections start closing again

With proper cookies, your orderbook WebSocket connections should match the stability you see in Postman! 🎉
