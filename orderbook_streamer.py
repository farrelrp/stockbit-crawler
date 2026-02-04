"""
Orderbook (Level 2) WebSocket streaming with Protobuf support
Handles multi-stock orderbook data in a single WebSocket connection
"""
import asyncio
import websockets
import logging
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional, Dict, Set
import csv
from collections import defaultdict
from config import STOCKBIT_WEBSOCKET_URL, ORDERBOOK_DIR

logger = logging.getLogger(__name__)

# Protobuf encoding/decoding helpers
def encode_websocket_request(user_id: int, tickers: List[str], trading_key: str, access_token: str) -> bytes:
    """
    Encode WebSocket subscription request as Protobuf
    Based on decoded structure:
    - Field 1: userId (string)
    - Field 2: (nested container with repeated Field 2 entries for each ticker variation)
    - Field 3: Trading key (string)
    - Field 5: JWT token (string)
    """
    logger.info(f"Encoding Protobuf subscription:")
    logger.info(f"  Field 1 (userId): {user_id}")
    logger.info(f"  Field 2: Nested container with {len(tickers)} tickers in 4 formats")
    logger.info(f"  Field 3 (trading_key): {trading_key[:20]}...")
    logger.info(f"  Field 5 (JWT token): {access_token[:50]}...")
    
    # Build Field 2 nested content
    field2_inner = bytearray()
    
    # Add each plain ticker as separate Field 2
    for ticker in tickers:
        field2_inner.extend(_encode_field_string(2, ticker))
    
    # Add each numbered ticker (2BBCA, 2TLKM, ...)
    for ticker in tickers:
        field2_inner.extend(_encode_field_string(2, f"2{ticker}"))
    
    # Add each colon-prefixed ticker (:BBCA, :TLKM, ...)
    for ticker in tickers:
        field2_inner.extend(_encode_field_string(2, f":{ticker}"))
    
    # Add each J-prefixed ticker (JBBCA, JTLKM, ...)
    for ticker in tickers:
        field2_inner.extend(_encode_field_string(2, f"J{ticker}"))
    
    logger.info(f"  Field 2 inner content: {len(field2_inner)} bytes")
    
    # Build final message
    message = bytearray()
    
    # Field 1: userId as STRING
    user_id_str = str(user_id)
    message.extend(_encode_field_string(1, user_id_str))
    
    # Field 2: Nested container with all ticker variations (as raw bytes)
    field2_tag = (2 << 3) | 2  # field 2, wire type 2
    message.append(field2_tag)
    message.extend(_encode_varint(len(field2_inner)))
    message.extend(field2_inner)
    
    # Field 3: Trading key
    message.extend(_encode_field_string(3, trading_key))
    
    # Field 5: JWT token
    message.extend(_encode_field_string(5, access_token))
    
    result = bytes(message)
    logger.info(f"  Encoded {len(result)} bytes total")
    logger.info(f"  Hex (first 100 bytes): {result[:100].hex()}...")
    
    return result


def _encode_field_varint(field_number: int, value: int) -> bytes:
    """Encode protobuf varint field"""
    # wire type 0 for varint
    tag = (field_number << 3) | 0
    result = bytearray()
    result.extend(_encode_varint(tag))
    result.extend(_encode_varint(value))
    return bytes(result)


def _encode_field_string(field_number: int, value: str) -> bytes:
    """Encode protobuf string field (length-delimited)"""
    # wire type 2 for length-delimited
    tag = (field_number << 3) | 2
    value_bytes = value.encode('utf-8')
    result = bytearray()
    result.extend(_encode_varint(tag))
    result.extend(_encode_varint(len(value_bytes)))
    result.extend(value_bytes)
    return bytes(result)


def _encode_varint(value: int) -> bytes:
    """Encode integer as protobuf varint"""
    result = bytearray()
    while value > 127:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def decode_orderbook_message(data: bytes) -> Optional[Dict]:
    """
    Decode incoming orderbook Protobuf message
    The data is in Field 10, which contains nested Protobuf:
      Sub-field 1: Ticker Symbol (string)
      Sub-field 2: Orderbook data in format #O|TICKER|SIDE|PRICE;LOTS;VALUE|...
      Other fields: timestamps, etc.
    """
    try:
        fields = {}
        pos = 0
        
        while pos < len(data):
            # read tag (field number + wire type)
            tag, pos = _decode_varint(data, pos)
            field_number = tag >> 3
            wire_type = tag & 0x7
            
            if wire_type == 0:  # varint
                value, pos = _decode_varint(data, pos)
                fields[field_number] = value
            elif wire_type == 2:  # length-delimited (string/bytes)
                length, pos = _decode_varint(data, pos)
                value = data[pos:pos + length]
                
                # Field 10 contains nested orderbook data
                if field_number == 10:
                    try:
                        nested = decode_nested_orderbook(value)
                        return nested  # return the nested data directly
                    except:
                        fields[field_number] = value
                else:
                    try:
                        fields[field_number] = value.decode('utf-8')
                    except:
                        fields[field_number] = value
                pos += length
            else:
                # skip unknown wire types
                logger.warning(f"Unknown wire type {wire_type} for field {field_number}")
                break
        
        return fields
    except Exception as e:
        logger.error(f"Failed to decode orderbook message: {e}")
        return None


def decode_nested_orderbook(data: bytes) -> Optional[Dict]:
    """
    Decode nested orderbook data from Field 10
    Sub-field 1: Ticker
    Sub-field 2: Orderbook string
    """
    try:
        fields = {}
        pos = 0
        
        while pos < len(data):
            tag, pos = _decode_varint(data, pos)
            field_number = tag >> 3
            wire_type = tag & 0x7
            
            if wire_type == 0:  # varint
                value, pos = _decode_varint(data, pos)
                fields[field_number] = value
            elif wire_type == 2:  # length-delimited
                length, pos = _decode_varint(data, pos)
                value = data[pos:pos + length]
                try:
                    fields[field_number] = value.decode('utf-8')
                except:
                    fields[field_number] = value
                pos += length
        
        # Map to expected fields
        # Sub-field 1 -> ticker, Sub-field 2 -> orderbook data
        return {
            1: fields.get(1, ''),  # ticker
            2: fields.get(2, ''),  # orderbook data
            'raw_fields': fields
        }
    except Exception as e:
        logger.error(f"Failed to decode nested orderbook: {e}")
        return None


def _decode_varint(data: bytes, pos: int) -> tuple:
    """Decode protobuf varint from bytes at position"""
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Invalid varint")


class OrderbookCSVStorage:
    """Handles daily CSV storage for orderbook data per ticker"""
    
    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or ORDERBOOK_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # track open file handles by ticker
        self.file_handles: Dict[str, tuple] = {}  # ticker -> (file, writer, current_date)
        
        logger.info(f"OrderbookCSVStorage initialized: {self.output_dir}")
    
    def _get_filename(self, ticker: str, target_date: date) -> str:
        """Generate filename for ticker and date"""
        return f"{target_date.strftime('%Y-%m-%d')}_{ticker}.csv"
    
    def _get_or_create_writer(self, ticker: str) -> tuple:
        """Get CSV writer for ticker, create new file if date changed"""
        current_date = date.today()
        
        # check if we already have an open file for this ticker
        if ticker in self.file_handles:
            file_obj, writer, file_date = self.file_handles[ticker]
            
            # if date changed, close old file and create new one
            if file_date != current_date:
                logger.info(f"Date changed for {ticker}, rotating file")
                file_obj.close()
                del self.file_handles[ticker]
            else:
                return file_obj, writer
        
        # create new file
        filename = self._get_filename(ticker, current_date)
        filepath = self.output_dir / filename
        
        # check if file exists to determine if we need headers
        file_exists = filepath.exists()
        
        file_obj = open(filepath, 'a', newline='', encoding='utf-8')
        writer = csv.writer(file_obj)
        
        # write headers if new file
        if not file_exists:
            writer.writerow(['timestamp', 'price', 'lots', 'total_value', 'side'])
            logger.info(f"Created new orderbook CSV: {filename}")
        
        self.file_handles[ticker] = (file_obj, writer, current_date)
        return file_obj, writer
    
    def write_orderbook_level(self, ticker: str, timestamp: str, price: float, 
                               lots: int, total_value: float, side: str):
        """Write a single orderbook level to CSV"""
        try:
            file_obj, writer = self._get_or_create_writer(ticker)
            writer.writerow([timestamp, price, lots, total_value, side])
            file_obj.flush()
        except Exception as e:
            logger.error(f"Failed to write orderbook level for {ticker}: {e}")
    
    def close_all(self):
        """Close all open file handles"""
        for ticker, (file_obj, _, _) in self.file_handles.items():
            try:
                file_obj.close()
                logger.info(f"Closed orderbook CSV for {ticker}")
            except Exception as e:
                logger.error(f"Error closing file for {ticker}: {e}")
        
        self.file_handles.clear()


class OrderbookStreamer:
    """WebSocket streamer for orderbook data across multiple stocks"""
    
    def __init__(self, token_manager, tickers: List[str]):
        self.token_manager = token_manager
        self.tickers = [t.upper() for t in tickers]
        self.csv_storage = OrderbookCSVStorage()
        
        self.websocket = None
        self.running = False
        self.task = None
        
        # stats tracking
        self.message_count = defaultdict(int)
        self.last_update = {}
        self.connection_time = None
        
        logger.info(f"OrderbookStreamer initialized for {len(self.tickers)} tickers")
    
    async def connect(self):
        """Establish WebSocket connection and subscribe to orderbook"""
        # fetch required auth data
        user_id = self.token_manager.get_user_id()
        trading_key = self.token_manager.fetch_trading_key()
        access_token = self.token_manager.get_valid_token()
        
        if not user_id or not trading_key or not access_token:
            raise Exception("Failed to get authentication data for WebSocket")
        
        logger.info(f"Connecting to WebSocket with userId={user_id}, tickers={self.tickers}")
        logger.info(f"Trading key: {trading_key[:20]}...")
        
        # connect to websocket with extra headers (including cookies if available)
        extra_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Origin': 'https://stockbit.com',
            'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits'
        }
        
        # Add cookies if available (important for session persistence!)
        cookies = self.token_manager.get_cookies()
        if cookies:
            extra_headers['Cookie'] = cookies
            logger.info(f"Using session cookies (length: {len(cookies)} chars)")
        
        try:
            # Match Postman behavior: NO client-initiated pings
            # Server will send pings if needed, websockets library auto-responds
            self.websocket = await websockets.connect(
                STOCKBIT_WEBSOCKET_URL,
                extra_headers=extra_headers,
                max_size=10 * 1024 * 1024,  # 10MB max message size
                ping_interval=None,  # DON'T send pings (like Postman)
                ping_timeout=None,   # no timeout on pongs
                close_timeout=10,    # wait 10 seconds for clean close
                compression=None     # no compression
            )
            logger.info("✅ WebSocket connected (passive mode, no client pings)")
        except Exception as e:
            logger.error(f"❌ Failed to connect to WebSocket: {e}")
            raise
        
        # send subscription request
        subscription_msg = encode_websocket_request(
            user_id=user_id,
            tickers=self.tickers,
            trading_key=trading_key,
            access_token=access_token
        )
        
        logger.info(f"Sending subscription message ({len(subscription_msg)} bytes)")
        logger.debug(f"Subscription message (hex): {subscription_msg[:100].hex()}...")
        
        try:
            await self.websocket.send(subscription_msg)
            logger.info(f"✅ Sent subscription for {len(self.tickers)} tickers: {', '.join(self.tickers)}")
        except Exception as e:
            logger.error(f"❌ Failed to send subscription: {e}")
            raise
        
        # Don't wait for immediate response - let the receive loop handle it
        logger.info("Subscription sent, waiting for data in receive loop...")
        
        self.connection_time = datetime.now()
        logger.info(f"🎯 Connection established and ready to receive data")
    
    async def handle_message(self, raw_data: bytes):
        """Process incoming orderbook message"""
        try:
            # log first few bytes for debugging
            logger.debug(f"Processing message: {raw_data[:50].hex()}...")
            
            fields = decode_orderbook_message(raw_data)
            if not fields:
                logger.warning(f"⚠️ Could not decode message (got None)")
                return
            
            logger.debug(f"Decoded fields: {list(fields.keys())}")
            
            # extract ticker symbol (field 1)
            ticker = fields.get(1, '').strip().upper()
            if not ticker:
                logger.warning(f"⚠️ Received message without ticker symbol. Fields: {fields}")
                return
            
            # extract orderbook data (field 2)
            orderbook_raw = fields.get(2, '')
            if not orderbook_raw:
                logger.warning(f"⚠️ No orderbook data in field 2 for {ticker}")
                return
            
            # timestamps (field 5 and 9)
            timestamp = fields.get(5) or fields.get(9) or datetime.now().isoformat()
            
            logger.info(f"📊 {ticker}: Processing orderbook data ({len(orderbook_raw)} chars)")
            
            # parse orderbook levels: SIDE|PRICE;LOTS;VALUE|...
            self._parse_and_store_orderbook(ticker, orderbook_raw, timestamp)
            
            # update stats
            self.message_count[ticker] += 1
            self.last_update[ticker] = datetime.now()
            
        except Exception as e:
            logger.error(f"❌ Error handling orderbook message: {e}", exc_info=True)
            logger.error(f"Raw data (first 200 bytes): {raw_data[:200]}")
    
    def _parse_and_store_orderbook(self, ticker: str, raw_data: str, timestamp):
        """Parse orderbook string and store to CSV"""
        try:
            # format: #O|TICKER|SIDE|PRICE;LOTS;VALUE|PRICE;LOTS;VALUE|...
            parts = raw_data.split('|')
            
            if len(parts) < 4:
                logger.warning(f"Invalid orderbook format for {ticker}: {raw_data[:100]}")
                return
            
            # parts[0] = "#O" (prefix)
            # parts[1] = ticker name
            # parts[2] = side (BID or OFFER)
            # parts[3+] = price levels
            
            side = parts[2].strip()  # BID or OFFER
            
            # process each price level (starting from index 3)
            for i in range(3, len(parts)):
                level = parts[i].strip()
                if not level:
                    continue
                
                # split by semicolon: PRICE;LOTS;VALUE
                level_parts = level.split(';')
                if len(level_parts) >= 3:
                    try:
                        price = float(level_parts[0])
                        lots = int(level_parts[1])
                        total_value = float(level_parts[2])
                        
                        self.csv_storage.write_orderbook_level(
                            ticker=ticker,
                            timestamp=str(timestamp),
                            price=price,
                            lots=lots,
                            total_value=total_value,
                            side=side
                        )
                    except ValueError as e:
                        logger.warning(f"Failed to parse level data: {level} - {e}")
                        
        except Exception as e:
            logger.error(f"Error parsing orderbook for {ticker}: {e}")
    
    async def heartbeat(self):
        """Monitor connection status - passive mode, no active pinging"""
        # In Postman mode: we don't send pings, just monitor
        # Server sends pings if needed, websockets library auto-responds
        while self.running:
            await asyncio.sleep(30)
            
            if self.websocket and not self.websocket.closed:
                logger.debug("💓 Connection alive (passive monitoring)")
            else:
                logger.warning("💔 Heartbeat detected closed connection")
                break
    
    async def receive_loop(self):
        """Main loop to receive and process messages"""
        logger.info("📡 Starting receive loop...")
        message_counter = 0
        last_message_time = datetime.now()
        
        try:
            async for message in self.websocket:
                message_counter += 1
                last_message_time = datetime.now()
                
                if isinstance(message, bytes):
                    # Log first bytes to see message type
                    logger.debug(f"📨 Message #{message_counter} ({len(message)} bytes) - first 20 bytes: {message[:20].hex()}")
                    await self.handle_message(message)
                else:
                    logger.info(f"📨 Received text message #{message_counter}: {message}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            time_since_last = (datetime.now() - last_message_time).total_seconds()
            if message_counter == 0:
                logger.error(f"❌ Connection closed without receiving any data! code={e.code}")
            else:
                logger.info(f"⚠️ WebSocket connection closed: code={e.code}, reason={e.reason}, last message {time_since_last}s ago")
        except Exception as e:
            logger.error(f"❌ Error in receive loop: {e}", exc_info=True)
        finally:
            logger.info(f"📡 Receive loop ended after {message_counter} messages")
    
    async def run(self):
        """Start the orderbook streaming session"""
        self.running = True
        
        try:
            await self.connect()
            
            # run heartbeat and receive loop concurrently
            heartbeat_task = asyncio.create_task(self.heartbeat())
            receive_task = asyncio.create_task(self.receive_loop())
            
            # wait for either to finish (or error)
            await asyncio.gather(heartbeat_task, receive_task, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Orderbook streamer error: {e}", exc_info=True)
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop streaming and cleanup"""
        self.running = False
        
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        
        self.csv_storage.close_all()
        logger.info("OrderbookStreamer stopped")
    
    def get_stats(self) -> Dict:
        """Get current streaming statistics"""
        return {
            'running': self.running,
            'tickers': self.tickers,
            'message_counts': dict(self.message_count),
            'last_updates': {
                ticker: ts.isoformat() 
                for ticker, ts in self.last_update.items()
            },
            'connection_time': self.connection_time.isoformat() if self.connection_time else None,
            'uptime_seconds': (datetime.now() - self.connection_time).total_seconds() 
                if self.connection_time else 0
        }
