"""
Timezone helpers — all data timestamps should be in WIB (Asia/Jakarta, UTC+7)
so that CSV files and filenames are consistent regardless of the server's
system clock timezone.
"""
import pytz
from datetime import datetime, date

WIB = pytz.timezone('Asia/Jakarta')


def now_wib() -> datetime:
    """Current datetime in WIB (timezone-aware)."""
    return datetime.now(WIB)


def today_wib() -> date:
    """Current date in WIB."""
    return datetime.now(WIB).date()
