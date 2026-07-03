import os
import sys
import time

# Ensure we can import from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from finviz.finviz_curl import login, get_token, update_api_keys
from finviz.new_finviz import fetch_and_save, FinvizTokenError

# ─────────────────────────────────────────
# Ticker Lists
# ─────────────────────────────────────────
LARGE_CAP = ["NVDA", "AAPL", "TSLA", "MSFT", "JPM", "XOM", "UNH", "WMT", "CAT", "DIS"]
MID_CAP   = ["RBLX", "DKNG", "AFRM", "CVNA", "CROX", "SOFI", "CHWY", "RIVN", "PLTR", "U"]
SMALL_CAP = ["GME", "AMC", "BYND", "UPST", "FUBO", "CLOV", "WKHS", "MULN", "SIRI", "RUM"]

ALL_TICKERS = LARGE_CAP + MID_CAP + SMALL_CAP


def refresh_token():
    print("\n[Token] Regenerating FinViz API token...")
    try:
        session = login()
        token = get_token(session)
        update_api_keys(token)
        print("[Token] ✅ Token updated.")
    except Exception as e:
        print(f"[Token] ❌ Token regeneration failed: {e}")


def main():
    print(f"Starting Multi-Ticker Data Harvest for {len(ALL_TICKERS)} tickers...")
    refresh_token()
    
    success_count = 0
    fail_count = 0
    failed_tickers = []
    
    for i, ticker in enumerate(ALL_TICKERS, 1):
        print(f"\n[{i}/{len(ALL_TICKERS)}] Fetching 1-min data for {ticker}...")
        try:
            candles = fetch_and_save(ticker, timeframe="i1")
            print(f"✅ {ticker}: Fetched {len(candles)} candles.")
            success_count += 1
            # Sleep slightly to avoid hammering the FinViz API too aggressively
            time.sleep(1.5)
        except FinvizTokenError:
            print(f"⚠️ Token error on {ticker}. Refreshing token and retrying...")
            refresh_token()
            try:
                candles = fetch_and_save(ticker, timeframe="i1")
                print(f"✅ {ticker}: Fetched {len(candles)} candles after retry.")
                success_count += 1
                time.sleep(1.5)
            except Exception as e:
                print(f"❌ {ticker}: Failed even after token refresh: {e}")
                fail_count += 1
                failed_tickers.append(ticker)
        except Exception as e:
            print(f"❌ {ticker}: Failed with error: {e}")
            fail_count += 1
            failed_tickers.append(ticker)
            
    print("\n" + "="*50)
    print(f"Harvest Complete. Success: {success_count} | Fail: {fail_count}")
    if failed_tickers:
        print(f"Failed tickers: {', '.join(failed_tickers)}")
    print("="*50)

if __name__ == "__main__":
    main()
