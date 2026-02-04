#!/usr/bin/env python3
"""
Compare our generated hex with the working hex
"""
from auth import TokenManager
from orderbook_streamer import encode_websocket_request

# Get our current auth
token_manager = TokenManager()
user_id = token_manager.get_user_id()
trading_key = token_manager.fetch_trading_key()
access_token = token_manager.get_valid_token()

# Generate for same tickers as working example
tickers = ["BBCA", "TLKM", "HMSP", "BBRI", "UNVR", "ASII", "BMRI", "BBNI", "UNTR", "GGRM", "ICBP", "TPIA"]

our_bytes = encode_websocket_request(user_id, tickers, trading_key, access_token)

print("="*60)
print("OUR HEX (first 200 chars):")
print(our_bytes[:100].hex())
print()
print("WORKING HEX (first 200 chars):")
print("0a073438323634353712a0021204424243411204544c4b4d1204484d53501204424252491204554e56521204415349491204424d5249120442424e491204554e545212044747524d1204494342501204545049413204424243413204544c4b4d3204")
print()

# Check field by field
print("Comparison:")
print(f"Field 1 (userId): {str(user_id)}")
print(f"  Our hex starts: {our_bytes[:20].hex()}")
print(f"  Expected: 0a073438323634353712...")
print()

# Check if structure matches
if our_bytes[:9].hex() == "0a07343832363435":
    print("✓ Field 1 matches!")
else:
    print("✗ Field 1 DIFFERENT")
    
print(f"\nOur message is {len(our_bytes)} bytes")
print(f"Working example is ~1100+ bytes")
