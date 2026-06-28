import asyncio
import json
import csv
import os

from datetime import datetime
from curl_cffi import AsyncSession
from tradingview_scraper.symbols.stream import Streamer, RealTimeData

# Import your local profile variables
from admin import EMAIL, PASSWORD, DOWNLOAD_DIR
LOGIN_URL = "https://www.tradingview.com/accounts/signin/"

##############################################################
# --------- PART 1: Retrieve Websocket Information --------- #
##############################################################

# Captcha Solution: https://github.com/2captcha/2captcha-python

async def get_websocket_auth_token():
    # Use curl_cffi to match standard browser TLS/WAF characteristics
    async with AsyncSession(impersonate="chrome") as s:
        
        # Mandatory: TradingView checks the Referer to prevent cross-site automation drops
        headers = {
            "Referer": "https://www.tradingview.com/",
            "Origin": "https://www.tradingview.com",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        payload = {
            "username": EMAIL, 
            "password": PASSWORD,
            "remember": "on"
        }
        
        print(f"Submitting session handshake request for: {EMAIL}...")
        response = await s.post(LOGIN_URL, data=payload, headers=headers)
        
        if response.status_code == 200:
            try:
                user_data = response.json()
                
                # TradingView returns the dynamic websocket authorization string inside 'user' -> 'auth_token'
                auth_token = user_data.get("user", {}).get("auth_token")
                
                if auth_token:
                    print("\n[SUCCESS] Retrieved TradingView WebSocket Auth Token:")
                    print(f"Token value: {auth_token}\n")
                    return auth_token
                else:
                    print("Login succeeded, but no 'auth_token' was found in the JSON response.")
                    print(f"Raw Response snippet: {response.text[:300]}")
            except json.JSONDecodeError:
                print("Failed to parse response matrix. TradingView might be challenging with a CAPTCHA.")
        else:
            print(f"Handshake rejected. Status code: {response.status_code}")
            print(response.text[:400])
            
    return None


###################################################
# --------- PART 2: Save Real-Time Data --------- #
###################################################

# Create an instance of the Streamer class
# streamer = Streamer(
#     export_result=False,
#     export_type='json',
#     websocket_jwt_token=asyncio.run(get_websocket_auth_token())
# )

# data_generator = streamer.stream(
#     exchange="NASDAQ",
#     symbol="NVDA",
#     timeframe="1s",
#     numb_price_candles=10,
# )

# for packet in data_generator:
#     print('-' * 50)
#     print(packet)

# The Streamer class expects exchange and symbol strings split apart
TARGET_EXCHANGE = "NASDAQ"
TARGET_SYMBOL = "NVDA"

# 1. Initialize the Streamer with your WebSocket JWT token
streamer = Streamer(
    export_result=False,          # Set to False so we can manually handle our custom CSV loop
    export_type='json',
    websocket_jwt_token=asyncio.run(get_websocket_auth_token())
)

# Define explicit column structure indexes for an OHLCV spreadsheet
csv_headers = ["local_timestamp", "symbol", "event_type", "open", "high", "low", "close", "volume"]

# Set up or open the CSV data spreadsheet container
file_exists = os.path.isfile(DOWNLOAD_DIR)
print(f"Opening {DOWNLOAD_DIR} and initializing the authorized OHLCV pipeline...")

with open(DOWNLOAD_DIR, mode="a", newline="", encoding="utf-8") as csv_file:
    writer = csv.DictWriter(csv_file, fieldnames=csv_headers)
    
    if not file_exists:
        writer.writeheader()
        csv_file.flush()

    # 2. Call the generator. To stream raw OHLCV only, leave the indicator parameters empty.
    data_generator = streamer.stream(
        exchange=TARGET_EXCHANGE,
        symbol=TARGET_SYMBOL,
        timeframe="1m",               # Timeframe interval choice (e.g., "1m", "5m", "1h")
        numb_price_candles=10         # Fetch the initial historical context backfill depth
    )

    try:
        for update in data_generator:
            # Example response matrix pattern:
            # {
            #   "status": "streaming", "symbol": "NASDAQ:NVDA", "event_type": "du",
            #   "raw_payload": [...]
            # }
            symbol = update.get("symbol")
            event_type = update.get("event_type")  # 'timescale_update' (historical backfill) or 'du' (live)
            payload = update.get("raw_payload", [])

            # TradingView packages the raw candlestick arrays into structured payload indices.
            # We target verified updates containing actionable numeric matrix updates:
            if isinstance(payload, list) and len(payload) > 0:
                
                # Filter down and pull from the internal data packets safely based on message formats
                try:
                    # Note: Structure can change slightly depending on historical vs live packets.
                    # This safely parses standard nested structural definitions:
                    data_block = payload[1] if len(payload) > 1 else payload[0]
                    
                    if isinstance(data_block, dict) and "series" in data_block:
                        # Extract the candle records array
                        candles = data_block["series"].get("s1", [])
                        
                        for candle in candles:
                            # Index positions map to: 0=Index, 1=Timestamp, 2=Open, 3=High, 4=Low, 5=Close, 6=Volume
                            v = candle.get("v", [])
                            if len(v) >= 7:
                                row_data = {
                                    "local_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                                    "symbol": symbol,
                                    "event_type": event_type,
                                    "open": v[2],
                                    "high": v[3],
                                    "low": v[4],
                                    "close": v[5],
                                    "volume": v[6]
                                }
                                
                                writer.writerow(row_data)
                                csv_file.flush()
                                print(f"[{row_data['local_timestamp']}] Saved {event_type.upper()} Bar for {symbol} | Close: ${v[5]:.2f}")
                
                except (IndexError, KeyError, TypeError):
                    # Silently bypass administrative socket layout updates that don't hold price arrays
                    continue

    except KeyboardInterrupt:
        print("\n[INFO] Real-time data pipeline disconnected safely.")