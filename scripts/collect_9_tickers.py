import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import sys
import os

# Ensure we can import from the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ibkr.backfill import backfill_ticker
from ibkr.ibkr_main import run_pipeline_ibkr

TICKERS = [
    "NVDA", "AAPL", "MSFT",  # Mega
    "GME", "AMC", "PLTR",    # Micro (Using as volatile small/mid proxies)
    "PENN", "CHWY", "RUM"    # Nano (Using as micro/nano proxies)
]

async def collect_all(tickers, days: int, backfill_only: bool = False):
    end = datetime.now(ZoneInfo("America/New_York"))
    start = end - timedelta(days=days)

    for ticker in tickers:
        logging.info(f"Starting backfill for {ticker}...")
        try:
            await backfill_ticker(ticker, start, end, port=7497, client_id=11)
            logging.info(f"✅ Finished backfill for {ticker}.")
        except Exception as e:
            logging.error(f"❌ Failed backfill for {ticker}: {e}")

    if not backfill_only:
        logging.info(f"🚀 Starting LIVE Tick Collectors for {len(tickers)} ticker(s): {tickers}")
        await run_pipeline_ibkr(tickers, 7497, 20, 0)
            
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=5,
                        help="How many days back to backfill 1-sec bars (default: 5)")
    parser.add_argument("--tickers", nargs="+", default=TICKERS,
                        help="Tickers to process (default: the 9-ticker universe). For LIVE "
                             "collection pass 1 per tier to stay under tick-line limits, "
                             "e.g. --tickers NVDA PLTR RUM")
    parser.add_argument("--backfill-only", action="store_true",
                        help="Only run backfill, do not start live collectors")
    args = parser.parse_args()

    asyncio.run(collect_all(args.tickers, args.days, args.backfill_only))
