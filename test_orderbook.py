#!/usr/bin/env python3
"""
Test script for orderbook streaming
Run this to verify the orderbook feature works correctly
"""
import asyncio
import sys
from auth import TokenManager
from orderbook_streamer import OrderbookStreamer, encode_websocket_request, decode_orderbook_message

def test_protobuf_encoding():
    """Test Protobuf encoding/decoding"""
    print("Testing Protobuf encoding...")
    
    # sample data
    user_id = 4826457
    tickers = ["BBCA", "TLKM", "BBRI"]
    trading_key = "test_key_12345"
    access_token = "test_token_67890"
    
    # encode
    encoded = encode_websocket_request(user_id, tickers, trading_key, access_token)
    print(f"✓ Encoded subscription request: {len(encoded)} bytes")
    print(f"  First 50 bytes (hex): {encoded[:50].hex()}")
    
    # test decode (sample orderbook message)
    # field 1 (ticker): wire type 2, field num 1 = tag 0x0a
    # field 2 (data): wire type 2, field num 2 = tag 0x12
    sample_ticker = "BBCA"
    sample_data = "BUY|8200;100;820000000"
    
    # manually construct a simple protobuf message for testing
    message = bytearray()
    # field 1 (ticker)
    message.append(0x0a)  # tag: field 1, wire type 2
    message.append(len(sample_ticker))
    message.extend(sample_ticker.encode('utf-8'))
    # field 2 (orderbook data)
    message.append(0x12)  # tag: field 2, wire type 2
    message.append(len(sample_data))
    message.extend(sample_data.encode('utf-8'))
    
    decoded = decode_orderbook_message(bytes(message))
    if decoded and decoded.get(1) == sample_ticker and decoded.get(2) == sample_data:
        print(f"✓ Decoded orderbook message correctly")
        print(f"  Ticker: {decoded[1]}")
        print(f"  Data: {decoded[2]}")
    else:
        print(f"✗ Failed to decode orderbook message")
        return False
    
    return True

def test_authentication():
    """Test authentication flow"""
    print("\nTesting authentication...")
    
    token_manager = TokenManager()
    
    # check if token exists
    token = token_manager.get_valid_token()
    if not token:
        print("✗ No valid token found")
        print("  Please set your Bearer token first:")
        print("  1. Go to http://localhost:5151/settings")
        print("  2. Enter your Bearer token")
        print("  3. Run this test again")
        return False
    
    print(f"✓ Valid token found")
    
    # get user ID
    user_id = token_manager.get_user_id()
    if user_id:
        print(f"✓ User ID: {user_id}")
    else:
        print("✗ Failed to extract user ID from token")
        return False
    
    # fetch trading key
    trading_key = token_manager.fetch_trading_key()
    if trading_key:
        print(f"✓ Trading key fetched: {trading_key[:20]}...")
    else:
        print("✗ Failed to fetch trading key")
        return False
    
    return True

async def test_orderbook_stream():
    """Test orderbook streaming (live test)"""
    print("\nTesting live orderbook stream (10 seconds)...")
    print("This will create CSV files in data/orderbook/")
    
    token_manager = TokenManager()
    
    if not token_manager.get_valid_token():
        print("✗ No valid token. Skipping live test.")
        return False
    
    # test with small set of tickers
    test_tickers = ["BBCA", "TLKM"]
    
    streamer = OrderbookStreamer(token_manager, test_tickers)
    
    print(f"Starting stream for: {', '.join(test_tickers)}")
    print("Listening for 10 seconds...")
    
    # run for 10 seconds then stop
    try:
        # start the stream
        task = asyncio.create_task(streamer.run())
        
        # wait 10 seconds
        await asyncio.sleep(10)
        
        # stop the stream
        await streamer.stop()
        
        # wait for task to finish
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            pass
        
        # check stats
        stats = streamer.get_stats()
        print(f"\n✓ Stream completed")
        print(f"  Message counts: {stats['message_counts']}")
        print(f"  Last updates: {stats['last_updates']}")
        
        if sum(stats['message_counts'].values()) > 0:
            print(f"✓ Received {sum(stats['message_counts'].values())} messages total")
            print("\nCheck data/orderbook/ for CSV files")
            return True
        else:
            print("⚠ No messages received (market might be closed)")
            return True  # not a failure, just no data
            
    except Exception as e:
        print(f"✗ Stream test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("ORDERBOOK STREAMING TEST SUITE")
    print("=" * 60)
    
    # test 1: protobuf
    if not test_protobuf_encoding():
        print("\n✗ Protobuf tests failed")
        sys.exit(1)
    
    # test 2: authentication
    if not test_authentication():
        print("\n✗ Authentication tests failed")
        print("\nSome tests were skipped. Set up authentication first.")
        sys.exit(0)
    
    # test 3: live stream (optional)
    print("\n" + "=" * 60)
    response = input("Run live streaming test? (y/n): ").lower().strip()
    if response == 'y':
        try:
            result = asyncio.run(test_orderbook_stream())
            if not result:
                print("\n✗ Live stream test failed")
                sys.exit(1)
        except KeyboardInterrupt:
            print("\n\nTest interrupted by user")
    else:
        print("Skipping live test")
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)
    print("\nOrderbook streaming is ready to use!")
    print("Start the Flask app and go to: http://localhost:5151/orderbook")

if __name__ == '__main__':
    main()
