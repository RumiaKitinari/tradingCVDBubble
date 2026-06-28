import os
import time
import pandas as pd
from tradingview_screener import Query, Column
import rookiepy

from admin import DOWNLOAD_DIR, INTERVAL

################################################
# --------- PART 1: Helper Functions --------- #
################################################

cookies = None

def create_data_dir():
    try:
        os.mkdir(DOWNLOAD_DIR)
        print(f"Directory '{DOWNLOAD_DIR}' created successfully.")
    except FileExistsError:
        print(f"Directory '{DOWNLOAD_DIR}' already exists.")
    except PermissionError:
        print(f"Permission denied: Unable to create '{DOWNLOAD_DIR}'.")
    except Exception as e:
        print(f"An error occurred: {e}")

def retrieve_cookies():
    print("[ACTIVITY] Extracting active session cookies from browser")
    
    try: # (rookiepy.chrome -> .firefox / .edge)
        cookies = rookiepy.to_cookiejar(rookiepy.chrome(['.tradingview.com']))
    except Exception as e:
        print(f"[ERROR] Could not load cookies automatically: {e}. Falling back to unauthenticated public request.")
        cookies = None

def scrape_TV_stocks():
    # 1.) Query -> Stock market
    q = (
        Query()
        .set_markets('america')
    )

    # 2.) Select specific columns (data points)
    q = q.select(
        'name',                     # Ticker
        'description',              # Company
        'close',                    # Price
        'volume',                   # Volume (now)
        'market_cap_basic',         # Market Cap
        'pe_ratio',                 # P/E Ratio
        'relative_volume_10d_calc'  # (EX: Relative Volume compared to 10-day average)
    )

    # 3.) Screening filters
    # q = q.where(
    #     Column('market_cap_basic') > 1_000_000_000,
    #     Column('volume') > 500_000
    # )

    # 4.) Sort the results (e.g., by highest volume descending)
    q = q.order_by('volume', ascending=False)

    # 5. Limit fetches (e.g., first 100 matching rows)
    q = q.offset(0).limit(100)

    try:
        # 6.) Execute the query
        print("[PING] TradingView scanner API")
        total_count, df = q.get_scanner_data(cookies=cookies)
        
        print(f"[QUERY] 1.) Stocks = {total_count} \t2.) Limit = {len(df)} records.")
        
        # 7.) Renaming columns
        # df.columns = ['Ticker', 'Company Name', 'Price', 'Volume', 'Market Cap', 'P/E Ratio', 'Relative Volume']
        
        # 8.) Snippet of the data
        # print("\nTop 5 Results:")
        # print(df.head())
        
        # 9.) Save to local CSV file
        output_file = f"tradingview_data_{int(time.time())}.csv"
        df.to_csv(os.path.join(DOWNLOAD_DIR, output_file), index=False)        
        print(f"[SAVE] Data successfully saved to '{output_file}'\n")

    except Exception as e:
        print(f"[ERROR] {e}")

###############################################################
# --------- PART 2: Scraping Stock Data Per Interval -------- #
###############################################################

if __name__ == "__main__":
    # Example: Run every 60 seconds indefinitely
    create_data_dir()
    retrieve_cookies()
    while True:
        scrape_TV_stocks()
        print(f"Waiting {INTERVAL} seconds for the next pull...\n")
        time.sleep(INTERVAL)