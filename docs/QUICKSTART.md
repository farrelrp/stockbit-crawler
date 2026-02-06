# Quick Start Guide

Get up and running with the Stockbit Running Trade Scraper in 5 minutes!

## Step 1: Install Dependencies

```bash
cd "Saham Flask"
pip install -r requirements.txt
```

## Step 2: Start the Application

```bash
python app.py
```

The app will start on `http://localhost:5151`

## Step 3: Set Your Bearer Token

1. Open your browser to `http://localhost:5151`
2. Click on **Settings** in the navigation
3. Follow the instructions on the page to get your Bearer token from Stockbit:
   - Login to [stockbit.com](https://stockbit.com/login)
   - Open Developer Tools (F12)
   - Go to Network tab
   - Find an API request to `exodus.stockbit.com`
   - Copy the `Authorization` header token (without "Bearer ")
4. Paste the token into the app and click **Save Token**

## Step 4: Create Your First Job

1. Go to **Jobs** page
2. Enter stock tickers (one per line):
   ```
   BBRI
   BBCA
   TLKM
   ```
3. Select date range (e.g., from 2025-11-01 to 2025-11-11)
4. Click **Create Job**

The app will now fetch **ALL trades** for each ticker-date combination, paginating through the entire day's data automatically!

## Step 5: Monitor & Download

- Watch progress on the **Dashboard**
- Check job status on the **Jobs** page
- Download CSV files from the **Files** page

## Notes

- The app now fetches **all trades for the entire day**, not just the latest 50!
- It paginates automatically using the `trade_number` parameter
- Small delays (0.5s) are added between pages to be nice to the API
- Jobs are saved to a database - they'll survive server restarts!
- When your token expires, you'll see an error - just get a new one and update it in Settings

## Troubleshooting

**"No token set" error?**
- Go to Settings and enter your Bearer token

**"Token expired" error?**
- Your token has expired, get a new one from Stockbit and update it

**Job paused with "Token expired"?**
- Update your token in Settings, then the job will resume automatically

**Not getting all trades?**
- Check the logs on the Dashboard - you'll see how many pages were fetched
- The app automatically paginates until it gets all trades for each day

Enjoy! 🚀
