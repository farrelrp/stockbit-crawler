# How to Get Your Bearer Token from Stockbit

Since the app now uses manual token input, here's how to get your Bearer token from Stockbit:

## Step-by-Step Instructions

### 1. Login to Stockbit

Go to [https://stockbit.com/login](https://stockbit.com/login) and login with your credentials.

### 2. Open Developer Tools

- **Chrome/Edge**: Press `F12` or `Ctrl+Shift+I` (Windows) / `Cmd+Option+I` (Mac)
- **Firefox**: Press `F12` or `Ctrl+Shift+I` (Windows) / `Cmd+Option+I` (Mac)
- **Safari**: Enable developer tools in Preferences → Advanced, then press `Cmd+Option+I`

### 3. Go to the Network Tab

Click on the "Network" tab in the Developer Tools panel.

### 4. Make an API Request

While on the stockbit.com website (after logging in), navigate around or refresh the page to trigger some API requests.

### 5. Find an API Request

Look for requests to domains like:
- `exodus.stockbit.com`
- `api.stockbit.com`
- Any request with `stockbit` in the URL

### 6. Copy the Authorization Header

1. Click on one of the API requests
2. Look for the "Headers" section (or "Request Headers")
3. Find the line that says `Authorization: Bearer eyJ...`
4. Copy the entire token (everything after "Bearer ")

**Example:**
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c
```

You want to copy: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` (without the "Bearer " prefix)

### 7. Paste into the App

Go to Settings in the app and paste the token into the "Bearer Token" field.

## Token Expiration

JWT tokens typically expire after a certain time (usually a few hours to a few days). When your token expires, the app will show an error and you'll need to:

1. Go back to stockbit.com
2. Make sure you're still logged in (or login again)
3. Get a new token using the same steps above
4. Update the token in the Settings page

## Tips

- The token is a very long string (hundreds of characters)
- Make sure to copy the entire token
- Don't include "Bearer " when pasting
- Keep your token secure - it's like a password!

## Troubleshooting

**Can't find the Authorization header?**
- Make sure you're logged in to Stockbit
- Try navigating to different pages or refreshing
- Look for requests to `exodus.stockbit.com` specifically

**Token says "Invalid"?**
- Make sure you copied the entire token
- Make sure you didn't include "Bearer " at the start
- Make sure there are no extra spaces

**Token expired immediately?**
- The token might have already been expired when you copied it
- Try getting a fresh one by logging out and back in to Stockbit

