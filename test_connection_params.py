#!/usr/bin/env python3
"""
Test different WebSocket connection parameters to match Postman behavior
"""
import asyncio
import websockets
from auth import TokenManager
from orderbook_streamer import encode_websocket_request
from config import STOCKBIT_WEBSOCKET_URL
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_connection():
    token_manager = TokenManager()
    
    user_id = token_manager.get_user_id()
    trading_key = token_manager.fetch_trading_key()
    access_token = token_manager.get_valid_token()
    
    tickers = ["BBCA"]
    
    # Try different connection parameters
    configs = [
        {
            "name": "Default",
            "params": {}
        },
        {
            "name": "With max_size increased",
            "params": {"max_size": 10 * 1024 * 1024}  # 10MB
        },
        {
            "name": "With ping_interval",
            "params": {"ping_interval": 20, "ping_timeout": 10}
        },
        {
            "name": "With close_timeout",
            "params": {"close_timeout": None, "ping_interval": None}
        },
        {
            "name": "All combined",
            "params": {
                "max_size": 10 * 1024 * 1024,
                "ping_interval": 30,
                "ping_timeout": 10,
                "close_timeout": 10
            }
        }
    ]
    
    extra_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Origin': 'https://stockbit.com'
    }
    
    for config in configs:
        print(f"\n{'='*60}")
        print(f"Testing: {config['name']}")
        print(f"Params: {config['params']}")
        print(f"{'='*60}")
        
        try:
            async with websockets.connect(
                STOCKBIT_WEBSOCKET_URL,
                extra_headers=extra_headers,
                **config['params']
            ) as ws:
                print("✓ Connected")
                
                # Send subscription
                sub_msg = encode_websocket_request(user_id, tickers, trading_key, access_token)
                await ws.send(sub_msg)
                print("✓ Subscription sent")
                
                # Receive messages for 15 seconds
                msg_count = 0
                start_time = asyncio.get_event_loop().time()
                
                try:
                    while True:
                        current_time = asyncio.get_event_loop().time()
                        elapsed = current_time - start_time
                        
                        if elapsed > 15:
                            print(f"\n✓ Reached 15 seconds, stopping test")
                            break
                        
                        # Wait for message with timeout
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        msg_count += 1
                        
                        if msg_count % 5 == 0:
                            print(f"  {elapsed:.1f}s: {msg_count} messages received")
                        
                except asyncio.TimeoutError:
                    print(f"\n⚠ No message for 2 seconds at {elapsed:.1f}s")
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"\n✗ Connection closed at {elapsed:.1f}s: code={e.code}, reason={e.reason}")
                
                print(f"Total messages: {msg_count}")
                
        except Exception as e:
            print(f"✗ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_connection())
