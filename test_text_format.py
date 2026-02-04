#!/usr/bin/env python3
"""
Test if WebSocket uses text format instead of Protobuf
"""
import asyncio
import websockets
import logging
from auth import TokenManager
from config import STOCKBIT_WEBSOCKET_URL

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

async def test_text_subscription():
    token_manager = TokenManager()
    
    user_id = token_manager.get_user_id()
    trading_key = token_manager.fetch_trading_key()
    access_token = token_manager.get_valid_token()
    
    if not all([user_id, trading_key, access_token]):
        print("ERROR: Missing auth data")
        return
    
    print(f"User ID: {user_id}")
    print(f"Trading Key: {trading_key[:30]}...")
    print(f"Token: {access_token[:50]}...")
    
    # Try different text formats based on your example
    tickers = ["BBCA", "TLKM"]
    
    # Format 1: Space-separated fields (like your example shows)
    concatenated = ''.join(tickers)
    numbered = ''.join([f"2{t}" for t in tickers])  # the "2BBCA2TLKM" pattern
    colon_sep = ':'.join(tickers)
    j_prefixed = 'J' + 'J'.join(tickers)
    
    message_format1 = f"{user_id} {concatenated}{numbered}{colon_sep}{j_prefixed},{trading_key}*{access_token}"
    
    # Format 2: JSON
    import json
    message_format2 = json.dumps({
        "userId": user_id,
        "tickers": tickers,
        "key": trading_key,
        "token": access_token
    })
    
    # Format 3: Simple delimited
    message_format3 = f"{user_id}|{','.join(tickers)}|{trading_key}|{access_token}"
    
    extra_headers = {
        'User-Agent': 'Mozilla/5.0',
        'Origin': 'https://stockbit.com'
    }
    
    for i, msg in enumerate([message_format1, message_format2, message_format3], 1):
        print(f"\n{'='*60}")
        print(f"Testing Format {i}:")
        print(f"Message (first 100 chars): {msg[:100]}...")
        print(f"Length: {len(msg)} chars")
        
        try:
            async with websockets.connect(STOCKBIT_WEBSOCKET_URL, extra_headers=extra_headers) as ws:
                print("✓ Connected")
                
                # send as text
                await ws.send(msg)
                print("✓ Sent subscription")
                
                # wait for response
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    print(f"✓ Got response: {response[:200]}")
                    return True  # success!
                except asyncio.TimeoutError:
                    print("✗ No response (timeout)")
                    
        except websockets.exceptions.ConnectionClosed as e:
            print(f"✗ Connection closed: code={e.code}, reason={e.reason}")
        except Exception as e:
            print(f"✗ Error: {e}")
    
    return False

if __name__ == '__main__':
    asyncio.run(test_text_subscription())
