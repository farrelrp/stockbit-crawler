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
    # network/auth error after at least one good page — must NOT be saved as a complete day
    FETCH_INTERRUPTED = "fetch_interrupted"
    CANCELLED_PARTIAL = "cancelled_partial"
    # hit rate limit mid-run — jobs layer should cooldown, not burn retries
    RATE_LIMITED = "rate_limited"


# normal terminal reasons: day fetch reached a defined end, not an error
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
        
        Args:
            ticker: Stock symbol (e.g., 'BIRD')
            date: Date in YYYY-MM-DD format
            limit: Max records to fetch per page
            trade_number: For pagination - get trades before this trade_number
            retry_count: Number of retry attempts
        
        Returns:
            Dict with success status, data, and error info
        """
        # get valid token
        token = self.token_manager.get_valid_token()
        if not token:
            return {
                'success': False,
                'error': 'No valid token available. Please set your Bearer token.',
                'requires_login': True
            }
        
        # build query params
        params = {
            'sort': 'DESC',
            'limit': limit,
            'order_by': 'RUNNING_TRADE_ORDER_BY_TIME',
            'symbols[]': ticker,
            'date': date
        }
        
        # add trade_number for pagination if provided
        if trade_number is not None:
            params['trade_number'] = trade_number
        
        # build headers with auth
        headers = HEADERS_TEMPLATE.copy()
        headers['Authorization'] = f'Bearer {token}'
        
        # attempt request with retries
        for attempt in range(retry_count):
            try:
                response = requests.get(
                    STOCKBIT_RUNNING_TRADE_URL,
                    params=params,
                    headers=headers,
                    timeout=30
                )
                
                # handle unauthorized - token expired or invalid
                if response.status_code == 401:
                    self.token_manager.mark_token_invalid()
                    return {
                        'success': False,
                        'error': 'Token expired or invalid. Please enter a new token.',
                        'status_code': 401,
                        'requires_login': True
                    }
                
                # handle forbidden - might be captcha or other issue
                if response.status_code == 403:
                    return {
                        'success': False,
                        'error': 'Access forbidden. Token might need refresh.',
                        'status_code': 403,
                        'requires_login': True
                    }

                # rate limit — let the job manager backoff; don't lump with generic 4xx
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
                
                # handle other 4xx errors (don't retry)
                if 400 <= response.status_code < 500:
                    return {
                        'success': False,
                        'error': f'Client error: {response.status_code}',
                        'status_code': response.status_code,
                        'response_text': response.text[:500]  # first 500 chars for debugging
                    }
                
                # handle 5xx errors (retry with backoff)
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
                
                # success
                response.raise_for_status()
                data = response.json()
                
                # extract running_trade list
                running_trade = []
                if 'data' in data and isinstance(data['data'], dict):
                    running_trade = data['data'].get('running_trade', [])
                    is_open_market = data['data'].get('is_open_market', False)
                else:
                    # fallback if structure is different
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
    ) -> Dict[str, Any]:
        """
        Fetch ALL running trade data for a ticker on a specific date
        Paginates through all pages using trade_number
        
        Args:
            ticker: Stock symbol (e.g., 'BIRD')
            date: Date in YYYY-MM-DD format
            limit: Max records to fetch per page (default 50)
            retry_count: Number of retry attempts per page
            progress_callback: Optional callback(page, total_records) for progress updates
            cancel_check: If set, called before each page; return True to stop early (job paused/cancelled)
        
        Returns:
            Dict with success status, all data combined, and error info
        """
        logger.info(f"Fetching ALL trades for {ticker} on {date}")
        
        all_trades = []
        page = 1
        last_trade_number = None
        # set when we break out of a successful pagination path (not fetch_interrupted)
        end_reason: Optional[str] = None
        # a short page doesn't always mean "we're at the end" — if the follow-up with trade_number
        # is empty, then we're done; if it has rows, keep going
        short_page_awaiting_confirm = False

        while True:
            # bail if the job manager says we're done (pause/cancel) — checked before hitting the API
            if cancel_check and cancel_check():
                logger.info(
                    f"Fetch stopped by cancel_check for {ticker} {date} after {len(all_trades)} record(s)"
                )
                if all_trades:
                    return {
                        'success': True,
                        'cancelled': True,
                        'data': all_trades,
                        'count': len(all_trades),
                        'ticker': ticker,
                        'date': date,
                        'pages_fetched': max(0, page - 1),
                        'partial': True,
                        'end_reason': FetchEndReason.CANCELLED_PARTIAL,
                    }
                return {
                    'success': False,
                    'cancelled': True,
                    'error': 'Cancelled',
                    'ticker': ticker,
                    'date': date,
                    'end_reason': 'cancelled',
                }

            # report progress
            if progress_callback:
                progress_callback(page, len(all_trades))
            
            # fetch a page
            logger.info(f"Fetching page {page} for {ticker} {date} (last_trade_number: {last_trade_number})")
            
            result = self._fetch_page(
                ticker=ticker,
                date=date,
                limit=limit,
                trade_number=last_trade_number,
                retry_count=retry_count
            )
            
            # check for errors
            if not result.get('success'):
                # 429 — don't pretend it's a generic interrupted fetch; jobs use cooldown
                if result.get('rate_limited'):
                    err = result.get('error', 'Rate limited (429)')
                    retry_after = result.get('retry_after_seconds', RATE_LIMIT_FALLBACK_SECONDS)
                    if all_trades:
                        logger.warning(
                            f"429 on page {page} for {ticker} {date} after {len(all_trades)} trades — cooldown "
                            f"(retry_after={retry_after})"
                        )
                        return {
                            'success': False,
                            'partial': True,
                            'data': all_trades,
                            'count': len(all_trades),
                            'ticker': ticker,
                            'date': date,
                            'pages_fetched': max(0, page - 1),
                            'error': err,
                            'end_reason': FetchEndReason.RATE_LIMITED,
                            'rate_limited': True,
                            'retry_after_seconds': retry_after,
                            'status_code': result.get('status_code', 429),
                        }
                    return {
                        'success': False,
                        'error': err,
                        'ticker': ticker,
                        'date': date,
                        'rate_limited': True,
                        'retry_after_seconds': retry_after,
                        'status_code': result.get('status_code', 429),
                    }

                # if we already have some data, this is NOT a complete day — caller must retry, not mark COMPLETED
                if all_trades:
                    err = result.get('error', 'Unknown fetch error')
                    logger.warning(
                        f"Error on page {page} for {ticker} {date} after {len(all_trades)} trades in memory — "
                        f"{err} (end_reason={FetchEndReason.FETCH_INTERRUPTED})"
                    )
                    out = {
                        'success': False,
                        'partial': True,
                        'data': all_trades,
                        'count': len(all_trades),
                        'ticker': ticker,
                        'date': date,
                        'pages_fetched': max(0, page - 1),
                        'error': err,
                        'end_reason': FetchEndReason.FETCH_INTERRUPTED,
                    }
                    for key in (
                        'requires_login',
                        'captcha_required',
                        'status_code',
                    ):
                        if key in result:
                            out[key] = result[key]
                    return out
                # no data yet, return the error as-is
                return result
            
            # extract trades from this page
            page_trades = result.get('data', [])
            
            # no more data on this request
            if not page_trades:
                # n+1 probe after a short page: API returns [] when there is nothing older — that is a normal end
                if short_page_awaiting_confirm:
                    end_reason = FetchEndReason.OK_SHORT_PAGE
                    logger.info(
                        f"Probe page {page} after short page: empty — confirmed no more data ({end_reason}). "
                        f"Total collected: {len(all_trades)}"
                    )
                else:
                    end_reason = FetchEndReason.OK_EMPTY_PAGE
                    logger.info(
                        f"No more trades on page {page} ({end_reason}). Total collected: {len(all_trades)}"
                    )
                break

            # n+1 check returned more rows — day wasn't finished at the short page
            if short_page_awaiting_confirm:
                short_page_awaiting_confirm = False

            # add to our collection
            all_trades.extend(page_trades)

            # get earliest timestamp from this page for monitoring
            last_trade = page_trades[-1]
            earliest_time = last_trade.get('time', 'N/A')

            logger.info(
                f"Page {page}: got {len(page_trades)} trades. Total: {len(all_trades)} | Earliest: {earliest_time}"
            )

            # check if we've reached 09:00 - stop collecting before market open (applies to full or short pages)
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

            # fewer than limit might still have older trades — one more page will tell us
            if len(page_trades) < limit:
                short_page_awaiting_confirm = True
                logger.info(
                    f"Got {len(page_trades)} < {limit} — will probe one more page with "
                    f"trade_number {last_trade_number} before calling it done"
                )

            page += 1

            # small delay between pages to be nice to the API
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
            }

        logger.info(
            f"[OK] Completed fetching {ticker} {date}: {len(all_trades)} total trades, "
            f"pages_fetched={page}, end_reason={end_reason}"
        )

        return {
            'success': True,
            'data': all_trades,
            'count': len(all_trades),
            'ticker': ticker,
            'date': date,
            'pages_fetched': page,
            'end_reason': end_reason,
            'partial': False,
        }

