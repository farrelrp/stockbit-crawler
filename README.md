# Stockbit Running Trade Scraper

A Flask-based web application that automates retrieval of running trade data and real-time orderbook data for multiple Indonesian stock tickers from the Stockbit API.

## Features

### Running Trade (Historical Data)
- 🔐 **Manual Token Authentication**: Simple Bearer token input - no automation needed!
- 💾 **Job Persistence**: Jobs are saved to database - survive server restarts!
- 📊 **Automated Data Collection**: Fetch running trade data for multiple tickers across date ranges
- 📈 **Job Management**: Create, monitor, pause, and resume data collection jobs
- 📁 **CSV Export**: Automatic export of trade data to CSV files
- 🔄 **Live Dashboard**: Real-time progress monitoring and logs
- ⚡ **Token Expiry Detection**: Automatically detects when your token expires

### Orderbook Level 2 Streaming (Real-Time)
- 📡 **WebSocket Streaming**: Real-time orderbook data via persistent connection
- 🎯 **Multi-Stock Support**: Subscribe to multiple tickers in a single WebSocket connection
- 🗂️ **Daily CSV Files**: Automatic daily file rotation per ticker (e.g., `2026-02-04_BBCA.csv`)
- 🔄 **Session Management**: Start/stop multiple streaming sessions independently
- 📊 **Live Statistics**: Real-time message counts and uptime monitoring
- 🔐 **Protobuf Protocol**: Binary protocol for efficient data transmission

## Installation

1. **Clone or download this project**

2. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

   Dependencies include:
   - Flask (web framework)
   - requests (API calls)
   - pandas (CSV handling)
   - websockets (orderbook streaming)

3. **Run the application**:
```bash
python app.py
```

4. **Open your browser** and navigate to:
```
http://localhost:5151
```

## Usage

### 1. Set Your Bearer Token

1. Go to **Settings** page
2. Follow the instructions to get your Bearer token from Stockbit (see `HOW_TO_GET_TOKEN.md`)
3. Paste the token and click **Save Token**

### 2. Create a Data Collection Job

1. Go to **Jobs** page
2. Enter stock tickers (one per line), for example:
   ```
   BIRD
   BBCA
   TLKM
   ```
3. Select date range (from date and until date)
4. Configure request delay (recommended: 3-5 seconds to avoid rate limiting)
5. Click **Create Job**

### 3. Monitor Progress

- View real-time progress on the **Dashboard**
- Check detailed job status on the **Jobs** page
- View recent activity in the logs section
- **Token status** is shown in the top-right corner

### 4. Download CSV Files

1. Go to **Files** page
2. View all generated CSV files
3. Click **Download** to get the data

### 5. Stream Real-Time Orderbook Data (NEW!)

1. Go to **Orderbook** page
2. Enter stock tickers (one per line)
3. Optionally provide a session ID (auto-generated if blank)
4. Click **Start Stream**
5. Monitor real-time statistics:
   - Message counts per ticker
   - Stream uptime
   - Last update times
6. CSV files automatically created in `data/orderbook/` with format: `YYYY-MM-DD_TICKER.csv`
7. Click **Stop** when done

**Note**: Orderbook streaming uses WebSocket with binary Protobuf protocol for efficient real-time data transmission.

See [`ORDERBOOK_GUIDE.md`](ORDERBOOK_GUIDE.md) for detailed documentation.

## Project Structure

```
Saham Flask/
├── app.py                          # Main Flask application
├── config.py                       # Configuration settings
├── auth.py                         # Authentication and token management
├── stockbit_client.py              # Stockbit API client (REST)
├── orderbook_streamer.py           # Orderbook WebSocket streaming (NEW)
├── orderbook_manager.py            # Orderbook session manager (NEW)
├── storage.py                      # CSV data storage
├── jobs.py                         # Job scheduler and manager
├── database.py                     # SQLite database for job persistence
├── requirements.txt                # Python dependencies
├── test_orderbook.py               # Orderbook testing script (NEW)
├── HOW_TO_GET_TOKEN.md             # Guide for getting Bearer token
├── ORDERBOOK_GUIDE.md              # Orderbook streaming documentation (NEW)
├── ORDERBOOK_IMPLEMENTATION.md     # Technical implementation details (NEW)
├── templates/                      # HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── settings.html
│   ├── jobs.html
│   ├── orderbook.html              # Orderbook streaming UI (NEW)
│   ├── captcha.html
│   ├── files.html
│   ├── 404.html
│   └── 500.html
├── static/                         # CSS and JavaScript
│   ├── style.css
│   └── script.js
├── data/                           # Output CSV files (auto-created)
│   ├── running_trade/              # Historical trade data
│   └── orderbook/                  # Real-time orderbook data (NEW)
├── logs/                           # Application logs (auto-created)
└── config_data/                    # Saved credentials (auto-created)
```

## API Endpoints

### Token Management
- `POST /api/token/set` - Set Bearer token manually
- `GET /api/token/status` - Get current token status
- `POST /api/token/refresh` - Manually refresh token
- `GET /api/credentials/check` - Check if credentials are saved

### Captcha
- `GET /api/captcha/status` - Get current captcha challenge
- `POST /api/captcha/solve` - Submit captcha solution

### Jobs
- `GET /api/jobs` - List all jobs
- `GET /api/jobs/<job_id>` - Get specific job details
- `POST /api/jobs/create` - Create new job
- `POST /api/jobs/<job_id>/pause` - Pause a job
- `POST /api/jobs/<job_id>/resume` - Resume a paused job
- `POST /api/jobs/<job_id>/cancel` - Cancel a job

### Orderbook Streaming
- `GET /api/orderbook/streams` - List all streaming sessions
- `POST /api/orderbook/streams` - Start new orderbook stream
- `GET /api/orderbook/streams/<session_id>` - Get session statistics
- `POST /api/orderbook/streams/<session_id>/stop` - Stop a stream

### Logs & Files
- `GET /api/logs` - Get recent log entries
- `GET /api/files` - List output CSV files
- `GET /api/files/download/<filename>` - Download CSV file

## Configuration

Edit `config.py` to customize:

- **Request delays**: `DEFAULT_DELAY_SECONDS`
- **Retry settings**: `DEFAULT_RETRY_COUNT`, `DEFAULT_RETRY_BACKOFF`
- **Token warning threshold**: `TOKEN_WARNING_THRESHOLD`
- **Log settings**: `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`
- **CSV settings**: `CSV_APPEND_MODE`, `CSV_COLUMNS`

## Data Format

CSV files contain the following columns:
- `id` - Trade ID
- `date` - Trading date (YYYY-MM-DD)
- `time` - Trade time (HH:MM:SS)
- `action` - Buy or sell
- `code` - Stock ticker
- `price` - Trade price
- `change` - Price change percentage
- `lot` - Number of lots traded
- `buyer` - Buyer broker code
- `seller` - Seller broker code
- `trade_number` - Trade number
- `buyer_type` - Buyer type classification
- `seller_type` - Seller type classification
- `market_board` - Market board type

## Important Notes

### No Authentication Required! 🎉

Great news! The Stockbit running trade endpoint is **publicly accessible** - no login, tokens, or authentication needed!

**What this means:**
- ✅ No Selenium browser automation
- ✅ No reCAPTCHA challenges
- ✅ No credential management
- ✅ Just works instantly!

The app simply fetches data from:
```
https://exodus.stockbit.com/order-trade/running-trade
```

~~All authentication code has been disabled~~ (still in codebase but commented out).

### Rate Limiting
- Use appropriate request delays (3-5 seconds recommended)
- The app includes retry logic with exponential backoff
- Respect Stockbit's terms of service and rate limits

### Security
- Credentials are stored in plain text in `config_data/credentials.json`
- For production use, implement proper encryption
- Never commit credentials to version control
- Add `config_data/` to `.gitignore`

### Captcha
- Captcha detection and solving is semi-automated
- User must manually solve captcha when prompted
- The app will pause jobs when captcha is required

## Troubleshooting

### Token expires frequently
- Check token expiration time in the token modal
- Ensure credentials are saved for automatic refresh
- Verify login credentials are correct

### Jobs fail with 401 errors
- Token has expired or is invalid
- Go to Settings and test login
- Check if captcha is required

### No data in CSV files
- Check if the ticker symbols are correct
- Verify the date range includes trading days
- Check logs for error messages
- Ensure token is valid

### Rate limiting / Too many requests
- Increase request delay in job settings
- Reduce number of concurrent jobs
- Wait before creating new jobs

## Development

To modify or extend the application:

1. **Backend logic**: Edit Python modules (`auth.py`, `stockbit_client.py`, etc.)
2. **API endpoints**: Add routes in `app.py`
3. **Frontend**: Edit HTML templates in `templates/` and CSS in `static/style.css`
4. **Job processing**: Modify `jobs.py` for custom task handling

## License

This is an educational project for coursework. Use responsibly and in accordance with Stockbit's terms of service.

## Disclaimer

This tool is for educational purposes only. Users are responsible for:
- Complying with Stockbit's terms of service
- Respecting rate limits and API usage policies
- Not attempting to bypass security controls
- Proper use of obtained data

The authors are not responsible for any misuse of this application.

