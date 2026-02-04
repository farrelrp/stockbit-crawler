#!/usr/bin/env python3
"""
Quick WebSocket debug script
"""
import asyncio
import logging
from auth import TokenManager
from orderbook_streamer import OrderbookStreamer

# setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def main():
    print("=" * 60)
    print("WebSocket Debug Test")
    print("=" * 60)
    
    token_manager = TokenManager()
    
    if not token_manager.get_valid_token():
        print("ERROR: No valid token. Set token first in web UI.")
        return
    
    print(f"✓ Token OK")
    print(f"✓ User ID: {token_manager.get_user_id()}")
    
    # test with multiple tickers like the working example
    streamer = OrderbookStreamer(token_manager, ["BBCA", "TLKM", "BBRI"])
    
    print(f"\nStarting 5-second test...")
    
    # create stream task
    stream_task = asyncio.create_task(streamer.run())
    
    # wait 5 seconds
    await asyncio.sleep(5)
    
    # stop
    await streamer.stop()
    
    # wait for task to complete
    try:
        await asyncio.wait_for(stream_task, timeout=2)
    except asyncio.TimeoutError:
        pass
    
    # show stats
    stats = streamer.get_stats()
    print(f"\n" + "=" * 60)
    print(f"Results:")
    print(f"  Messages: {stats['message_counts']}")
    print(f"  Last updates: {stats['last_updates']}")
    print("=" * 60)

if __name__ == '__main__':
    asyncio.run(main())
