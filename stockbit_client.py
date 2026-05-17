"""
Stockbit API client for fetching running trade data
"""
from typing import Dict, List, Any, Optional, Callable
import datetime as dt
import requests
import time
import logging
from email.utils import parsedate_to_datetime

from config import (
    STOCKBIT_RUNNING_TRADE_URL, HEADERS_TEMPLATE,
    DEFAULT_LIMIT, DEFAULT_RETRY_COUNT, DEFAULT_RETRY_BACKOFF,
    RATE_LIMIT_FALLBACK_SECONDS,
)

logger = logging.getLogger(__name__)


class FetchEndReason:
    """Why pagination stopped — used by jobs layer to avoid treating mid-fetch errors as a finished day."""

    OK_EMPTY_PAGE = "ok_empty_page"
    OK_SHORT_PAGE = "ok_short_page"
    OK_BEFORE_MARKET_OPEN = "ok_before_market_open"
    OK_NO_TRADE_NUMBER = "ok_no_trade_number"
    FETCH_INTERRUPTED = "fetch_interrupted"
    CANCELLED_PARTIAL = "cancelled_partial"
    RATE_LIMITED = "rate_limited"


NORMAL_FETCH_END_REASONS = frozenset(
    {
        FetchEndReason.OK_EMPTY_PAGE,
        FetchEndReason.OK_SHORT_PAGE,
        FetchEndReason.OK_BEFORE_MARKET_OPEN,
        FetchEndReason.OK_NO_TRADE_NUMBER,
    }
)


def parse_retry_after_header(value: Optional[str], fallback: float) -> float:
    """Turn Retry-After into seconds. Servers send an int or an HTTP-date; junk -> fallback."""
    if value is None or not str(value).strip():
        return float(fallback)
    raw = str(value).strip()
    if raw.isdigit():
        return float(int(raw))
    try:
        n = float(raw)
        if n >= 0:
            return n
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is None:
            return float(fallback)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        wait = (parsed - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if wait > 0:
            return wait
    except Exception:
        pass
    return float(fallback)


def build_page_fingerprint(trades: List[Dict[str, Any]]) -> Optional[str]:
    """Fingerprint page 1 so retries can detect unstable top-page payloads."""
    if not trades:
        return None
    first = trades[0]
    last = trades[-1]
    return "|".join([
        str(len(trades)),
        str(first.get('trade_number', '')),
        str(first.get('time', '')),
        str(last.get('trade_number', '')),
        str(last.get('time', '')),
    ])


class StockbitClient:
    """Client for Stockbit Running Trade API"""

    def __init__(self, token_manager):
        self.token_manager = token_manager

    def _fetch_page(
        self,
        ticker: str,
        date: str,
        limit: int = DEFAULT_LIMIT,
        trade_number: Optional[int] = None,
        retry_count: int = DEFAULT_RETRY_COUNT
    ) -> Dict[str, Any]:
        """
        Fetch a single page of running trade data
        """
        token = self.token_manager.get_valid_token()
        if not token:
            return {
                'success': False,
                'error': 'No valid token available. Please set your Bearer token.',
                'requires_login': True
            }

        params = {
            'sort': 'DESC',
            'limit': limit,
            'order_by': 'RUNNING_TRADE_ORDER_BY_TIME',
            'symbols[]': ticker,
            'date': date
        }
        if trade_number is not None:
            params['trade_number'] = trade_number

        headers = HEADERS_TEMPLATE.copy()
        headers['Authorization'] = f'Bearer {token}'

        for attempt in range(retry_count):
            try:
                response = requests.get(
                    STOCKBIT_RUNNING_TRADE_URL,
                    params=params,
                    headers=headers,
                    timeout=30
                )

                if response.status_code == 401:
                    self.token_manager.mark_token_invalid()
                    return {
                        'success': False,
                        'error': 'Token expired or invalid. Please enter a new token.',
                        'status_code': 401,
                        'requires_login': True
                    }

                if response.status_code == 403:
                    return {
                        'success': False,
                        'error': 'Access forbidden. Token might need refresh.',
                        'status_code': 403,
                        'requires_login': True
                    }

                if response.status_code == 429:
                    retry_after = parse_retry_after_header(
                        response.headers.get('Retry-After'),
                        RATE_LIMIT_FALLBACK_SECONDS,
                    )
                    return {
                        'success': False,
                        'error': 'Rate limited (429)',
                        'status_code': 429,
                        'rate_limited': True,
                        'retry_after_seconds': retry_after,
                        'response_text': response.text[:500],
                    }

                if 400 <= response.status_code < 500:
                    return {
                        'success': False,
                        'error': f'Client error: {response.status_code}',
                        'status_code': response.status_code,
                        'response_text': response.text[:500]
                    }

                if response.status_code >= 500:
                    if attempt < retry_count - 1:
                        wait_time = DEFAULT_RETRY_BACKOFF ** attempt
                        time.sleep(wait_time)
                        continue
                    return {
                        'success': False,
                        'error': f'Server error after {retry_count} attempts',
                        'status_code': response.status_code
                    }

                response.raise_for_status()
                data = response.json()

                running_trade = []
                if 'data' in data and isinstance(data['data'], dict):
                    running_trade = data['data'].get('running_trade', [])
                    is_open_market = data['data'].get('is_open_market', False)
                else:
                    running_trade = data.get('running_trade', [])
                    is_open_market = data.get('is_open_market', False)

                return {
                    'success': True,
                    'data': running_trade,
                    'is_open_market': is_open_market,
                    'count': len(running_trade),
                    'ticker': ticker,
                    'date': date
                }

            except requests.Timeout:
                if attempt < retry_count - 1:
                    time.sleep(DEFAULT_RETRY_BACKOFF ** attempt)
                    continue
                return {
                    'success': False,
                    'error': f'Request timeout after {retry_count} attempts'
                }

            except requests.RequestException as e:
                if attempt < retry_count - 1:
                    time.sleep(DEFAULT_RETRY_BACKOFF ** attempt)
                    continue
                return {
                    'success': False,
                    'error': f'Request failed: {str(e)}'
                }

        return {
            'success': False,
            'error': 'Unknown error after all retry attempts'
        }

    def fetch_running_trade(
        self,
        ticker: str,
        date: str,
        limit: int = DEFAULT_LIMIT,
        retry_count: int = DEFAULT_RETRY_COUNT,
        progress_callback: Optional[callable] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        resume_trade_number: Optional[int] = None,
        initial_records: int = 0,
        initial_pages: int = 0,
        page_callback: Optional[Callable[[List[Dict[str, Any]], Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch all running trade data for a ticker on a specific date.
        """
        logger.info(f"Fetching ALL trades for {ticker} on {date}")

        all_trades: List[Dict[str, Any]] = []
        total_records = max(0, int(initial_records or 0))
        successful_pages = max(0, int(initial_pages or 0))
        page = max(1, successful_pages + 1)
        last_trade_number = resume_trade_number
        end_reason: Optional[str] = None
        short_page_awaiting_confirm = False
        page1_fingerprint: Optional[str] = None
        last_checkpoint: Dict[str, Any] = {
            'resume_trade_number': resume_trade_number,
            'checkpoint_pages_fetched': successful_pages,
            'checkpoint_records_fetched': total_records,
            'checkpoint_first_trade_number': None,
            'checkpoint_last_trade_number': resume_trade_number,
            'checkpoint_first_time': None,
            'checkpoint_last_time': None,
            'page1_fingerprint': None,
        }

        while True:
            if cancel_check and cancel_check():
                logger.info(
                    f"Fetch stopped by cancel_check for {ticker} {date} after {total_records} record(s)"
                )
                if successful_pages > int(initial_pages or 0):
                    return {
                        'success': True,
                        'cancelled': True,
                        'data': all_trades,
                        'count': total_records,
                        'ticker': ticker,
                        'date': date,
                        'pages_fetched': successful_pages,
                        'successful_pages_fetched': successful_pages,
                        'partial': True,
                        'end_reason': FetchEndReason.CANCELLED_PARTIAL,
                        **last_checkpoint,
                    }
                return {
                    'success': False,
                    'cancelled': True,
                    'error': 'Cancelled',
                    'ticker': ticker,
                    'date': date,
                    'end_reason': 'cancelled',
                    **last_checkpoint,
                }

            if progress_callback:
                progress_callback(page, total_records)

            logger.info(f"Fetching page {page} for {ticker} {date} (last_trade_number: {last_trade_number})")

            result = self._fetch_page(
                ticker=ticker,
                date=date,
                limit=limit,
                trade_number=last_trade_number,
                retry_count=retry_count
            )

            if not result.get('success'):
                if result.get('rate_limited'):
                    err = result.get('error', 'Rate limited (429)')
                    retry_after = result.get('retry_after_seconds', RATE_LIMIT_FALLBACK_SECONDS)
                    if successful_pages > int(initial_pages or 0):
                        logger.warning(
                            f"429 on page {page} for {ticker} {date} after {total_records} trades — per-task wait "
                            f"(retry_after={retry_after})"
                        )
                        return {
                            'success': False,
                            'partial': True,
                            'data': all_trades,
                            'count': total_records,
                            'ticker': ticker,
                            'date': date,
                            'pages_fetched': successful_pages,
                            'successful_pages_fetched': successful_pages,
                            'error': err,
                            'end_reason': FetchEndReason.RATE_LIMITED,
                            'rate_limited': True,
                            'retry_after_seconds': retry_after,
                            'status_code': result.get('status_code', 429),
                            **last_checkpoint,
                        }
                    return {
                        'success': False,
                        'error': err,
                        'ticker': ticker,
                        'date': date,
                        'rate_limited': True,
                        'retry_after_seconds': retry_after,
                        'status_code': result.get('status_code', 429),
                        **last_checkpoint,
                    }

                if successful_pages > int(initial_pages or 0):
                    err = result.get('error', 'Unknown fetch error')
                    logger.warning(
                        f"Error on page {page} for {ticker} {date} after {total_records} trades in memory — "
                        f"{err} (end_reason={FetchEndReason.FETCH_INTERRUPTED})"
                    )
                    out = {
                        'success': False,
                        'partial': True,
                        'data': all_trades,
                        'count': total_records,
                        'ticker': ticker,
                        'date': date,
                        'pages_fetched': successful_pages,
                        'successful_pages_fetched': successful_pages,
                        'error': err,
                        'end_reason': FetchEndReason.FETCH_INTERRUPTED,
                        **last_checkpoint,
                    }
                    for key in ('requires_login', 'captcha_required', 'status_code'):
                        if key in result:
                            out[key] = result[key]
                    return out
                out = result.copy()
                out.update(last_checkpoint)
                return out

            page_trades = result.get('data', [])

            if not page_trades:
                if short_page_awaiting_confirm:
                    end_reason = FetchEndReason.OK_SHORT_PAGE
                    logger.info(
                        f"Probe page {page} after short page: empty — confirmed no more data ({end_reason}). "
                        f"Total collected: {total_records}"
                    )
                else:
                    end_reason = FetchEndReason.OK_EMPTY_PAGE
                    logger.info(
                        f"No more trades on page {page} ({end_reason}). Total collected: {total_records}"
                    )
                break

            if short_page_awaiting_confirm:
                short_page_awaiting_confirm = False

            all_trades.extend(page_trades)
            total_records += len(page_trades)
            successful_pages += 1

            if page1_fingerprint is None:
                page1_fingerprint = build_page_fingerprint(page_trades)

            first_trade = page_trades[0]
            last_trade = page_trades[-1]
            earliest_time = last_trade.get('time', 'N/A')

            logger.info(
                f"Page {page}: got {len(page_trades)} trades. Total: {total_records} | Earliest: {earliest_time}"
            )

            last_checkpoint = {
                'resume_trade_number': last_trade.get('trade_number'),
                'checkpoint_pages_fetched': successful_pages,
                'checkpoint_records_fetched': total_records,
                'checkpoint_first_trade_number': first_trade.get('trade_number'),
                'checkpoint_last_trade_number': last_trade.get('trade_number'),
                'checkpoint_first_time': first_trade.get('time'),
                'checkpoint_last_time': last_trade.get('time'),
                'page1_fingerprint': page1_fingerprint,
            }

            if page_callback:
                page_callback(page_trades, last_checkpoint.copy())

            trade_time = last_trade.get('time', '')
            if trade_time and trade_time <= '09:00:00':
                end_reason = FetchEndReason.OK_BEFORE_MARKET_OPEN
                logger.info(
                    f"Reached trade at {trade_time} (before 09:00). Stopping pagination ({end_reason})."
                )
                break

            if 'trade_number' not in last_trade:
                end_reason = FetchEndReason.OK_NO_TRADE_NUMBER
                logger.warning(
                    f"No trade_number field in response. Stopping pagination ({end_reason})."
                )
                break

            last_trade_number = last_trade['trade_number']
            logger.info(f"Next pagination will use trade_number: {last_trade_number}")

            if len(page_trades) < limit:
                short_page_awaiting_confirm = True
                logger.info(
                    f"Got {len(page_trades)} < {limit} — will probe one more page with "
                    f"trade_number {last_trade_number} before calling it done"
                )

            page += 1
            time.sleep(0.5)

        if end_reason is None:
            logger.error(
                f"Internal bug: pagination loop exited for {ticker} {date} but end_reason was not set"
            )
            return {
                "success": False,
                "error": "Internal error: could not determine why pagination ended",
                "ticker": ticker,
                "date": date,
                "end_reason": "internal_bug_no_end_reason",
                **last_checkpoint,
            }

        logger.info(
            f"[OK] Completed fetching {ticker} {date}: {total_records} total trades, "
            f"pages_fetched={successful_pages}, end_reason={end_reason}"
        )

        return {
            'success': True,
            'data': all_trades,
            'count': total_records,
            'ticker': ticker,
            'date': date,
            'pages_fetched': successful_pages,
            'successful_pages_fetched': successful_pages,
            'end_reason': end_reason,
            'partial': False,
            **last_checkpoint,
        }
