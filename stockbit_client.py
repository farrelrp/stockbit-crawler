"""
Stockbit API client for fetching running trade data
"""
from typing import Dict, List, Any, Optional
import requests
import time
import logging
from config import (
    STOCKBIT_RUNNING_TRADE_URL, HEADERS_TEMPLATE,
    DEFAULT_LIMIT, DEFAULT_RETRY_COUNT, DEFAULT_RETRY_BACKOFF
)

logger = logging.getLogger(__name__)

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
        progress_callback: Optional[callable] = None
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
        
        Returns:
            Dict with success status, all data combined, and error info
        """
        logger.info(f"Fetching ALL trades for {ticker} on {date}")
        
        all_trades = []
        page = 1
        last_trade_number = None
        
        while True:
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
                # if we already have some data, return what we got
                if all_trades:
                    logger.warning(f"Error on page {page}, but returning {len(all_trades)} trades collected so far")
                    return {
                        'success': True,
                        'data': all_trades,
                        'count': len(all_trades),
                        'ticker': ticker,
                        'date': date,
                        'pages_fetched': page - 1,
                        'partial': True
                    }
                else:
                    # no data yet, return the error
                    return result
            
            # extract trades from this page
            page_trades = result.get('data', [])
            
            # no more data? we're done
            if not page_trades:
                logger.info(f"No more trades on page {page}. Total collected: {len(all_trades)}")
                break
            
            # add to our collection
            all_trades.extend(page_trades)
            
            # get earliest timestamp from this page for monitoring
            earliest_time = 'N/A'
            if page_trades:
                last_trade = page_trades[-1]
                earliest_time = last_trade.get('time', 'N/A')
            
            logger.info(f"Page {page}: got {len(page_trades)} trades. Total: {len(all_trades)} | Earliest: {earliest_time}")
            
            # if we got fewer than the limit, we've reached the end
            if len(page_trades) < limit:
                logger.info(f"Got {len(page_trades)} < {limit}, reached end of data")
                break
            
            # get the last trade_number from this page for pagination
            # since we're sorting DESC, the last item is the earliest trade
            if page_trades:
                last_trade = page_trades[-1]
                
                # check if we've reached 09:00 - stop collecting before market open
                trade_time = last_trade.get('time', '')
                if trade_time and trade_time <= '09:00:00':
                    logger.info(f"Reached trade at {trade_time} (before 09:00). Stopping pagination.")
                    break
                
                # check if trade_number exists
                if 'trade_number' in last_trade:
                    last_trade_number = last_trade['trade_number']
                    logger.info(f"Next pagination will use trade_number: {last_trade_number}")
                else:
                    logger.warning(f"No trade_number field in response. Stopping pagination.")
                    break
            
            page += 1
            
            # small delay between pages to be nice to the API
            time.sleep(0.5)
        
        logger.info(f"[OK] Completed fetching {ticker} {date}: {len(all_trades)} total trades across {page} pages")
        
        return {
            'success': True,
            'data': all_trades,
            'count': len(all_trades),
            'ticker': ticker,
            'date': date,
            'pages_fetched': page
        }

