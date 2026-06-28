import asyncio
import json
from datetime import datetime
from curl_cffi import AsyncSession

# Assuming these variables are properly exported by your local admin.py
from admin import DOWNLOAD_DIR, USERNAME, EMAIL, PASSWORD, COOKIES_FILE

TRADING_VIEW_URL = "https://www.tradingview.com/"
LOGIN_URL = "https://www.tradingview.com/accounts/signin/"

async def tv_login_and_scrape():
    async with AsyncSession(impersonate="chrome") as s:
        
        # --- STEP 1: INITIALIZE SESSION STATE ---
        print("Visiting homepage to capture CSRF configurations and cookies...")
        init_response = await s.get(TRADING_VIEW_URL)
        
        if init_response.status_code != 200:
            print(f"Initialization blocked by firewall. Status: {init_response.status_code}")
            return

        # --- STEP 2: EXECUTE AUTHENTICATION PIPELINE ---
        print(f"Transmitting credentials for profile: {EMAIL}...")
        login_res = await s.post(
            LOGIN_URL, 
            data={"username": EMAIL, "password": PASSWORD},
        )
        
        if login_res.status_code == 200:
            print("Login completely successful. Persistent cookies bound to active AsyncSession.")
        else:
            print("Authentication skipped/rejected. Proceeding as public scraper...")

        # --- STEP 3: INTERACT WITH INTERNAL SCREENER PIPELINE ---
        screener_api_url = "https://scanner.tradingview.com/america/scan"
        
        # Added "open_time" to the columns layout to extract the underlying bar timestamp
        screener_payload = {
            "filter": [
                {"left": "market_cap_basic", "operation": "nempty"},
                {"left": "type", "operation": "in_range", "right": ["stock", "dr", "bdr"]}
            ],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": [
                "base_currency", 
                "logoid", 
                "name", 
                "close", 
                "change", 
                "volume", 
                "description",
                "time"  # <--- Added to pull the UNIX timestamp (seconds) of the last data update
            ],
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [0, 10]  # Scrape top 10 positions
        }

        print("Querying backend screener matrix...")
        headers = {
            "Referer": "https://www.tradingview.com/",
            "Content-Type": "application/json"
        }
        
        response = await s.post(screener_api_url, json=screener_payload, headers=headers)
        
        if response.status_code == 200:
            try:
                data = response.json()
                stock_rows = data.get("data", [])
                
                print(f"\n--- Market Overview Metrics (Retrieved {len(stock_rows)} items) ---")
                for stock in stock_rows:
                    metrics = stock.get("d", [])
                    
                    # Mapping based explicitly on index tracking in the "columns" array:
                    ticker = metrics[2] if len(metrics) > 2 else "N/A"
                    price = metrics[3] if len(metrics) > 3 else 0.0
                    volume = metrics[5] if len(metrics) > 5 else 0
                    desc = metrics[6] if len(metrics) > 6 else "N/A"
                    
                    # Extract and parse timestamp field (Index 7)
                    raw_timestamp = metrics[7] if len(metrics) > 7 else None
                    human_time = "N/A"
                    
                    if raw_timestamp:
                        try:
                            # TradingView's open_time is returned as a standard UNIX integer timestamp
                            human_time = datetime.fromtimestamp(int(raw_timestamp)).strftime('%Y-%m-%d %H:%M:%S')
                        except (ValueError, TypeError):
                            human_time = f"Raw: {raw_timestamp}"

                    print(f"[{human_time}] Ticker: {ticker:<6} | Price: ${price:>8.2f} | Volume: {volume:>10,}")
                    
            except json.JSONDecodeError:
                print("Failed to parse data matrix.")
        else:
            print(f"Screener connection dropped. Status: {response.status_code}")

if __name__ == "__main__":
    asyncio.run(tv_login_and_scrape())