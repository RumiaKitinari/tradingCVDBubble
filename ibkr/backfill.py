"""
ibkr/backfill.py — Step 4: Historical 1-second bar backfill.

Uses reqHistoricalData (barSizeSetting='1 secs', whatToShow='TRADES') to pull
up to 6 months of 1-second bars from IBKR and insert them into MongoDB.

Limitations vs. live tick data:
  - No tick-level bid/ask available → aggressor classification is impossible.
  - Bars are tagged source='ibkr_hist'; calculator.py will apply wick
    decomposition (same as FinViz data) to get buy/sell volume estimates.
  - IBKR maximum lookback: 6 months for 1-sec bars.

For data older than 6 months, FinViz data (source='finviz_wick') is used and
remains in MongoDB alongside IBKR data; the chart labels them differently.

Pacing rules (IBKR):
  - Max 60 historical data requests per 10 minutes.
  - We wait PACING_DELAY seconds between requests (default 11 s → ~54/10min).
  - On pacing-violation error (code 162), we wait PACING_BACKOFF seconds.

Each request retrieves CHUNK_SECONDS = 1800 s (30 minutes) of 1-sec bars.

Usage:
    python -m ibkr.backfill --ticker NVDA --days 1
    python -m ibkr.backfill --ticker NVDA --days 5
    python -m ibkr.backfill --ticker NVDA --start 20260601 --end 20260628
    python -m ibkr.backfill --ticker NVDA --port 7496   # live TWS
"""

import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pymongo import MongoClient, UpdateOne
from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"

CHUNK_SECONDS = 1800    # seconds per historical request (30 min = 1800 1-sec bars)
PACING_DELAY = 11.0     # wait between requests: 11 s → ≈54 req/10 min (< 60 limit)
PACING_BACKOFF = 60.0   # wait after a pacing-violation error


def _parse_bar_time(raw_time) -> datetime:
    """Convert IBKR bar time (datetime or 'YYYYMMDD  HH:MM:SS' string) to ET naive datetime."""
    if isinstance(raw_time, datetime):
        if raw_time.tzinfo:
            return raw_time.astimezone(ET).replace(tzinfo=None)
        return raw_time
    # String format from IBKR: "20260628  09:30:00"
    return datetime.strptime(str(raw_time).strip(), "%Y%m%d  %H:%M:%S")


def _bars_to_docs(ticker: str, bars) -> list[dict]:
    """Convert a list of IB BarData objects to MongoDB documents."""
    docs = []
    for bar in bars:
        try:
            ts = _parse_bar_time(bar.date)
            docs.append({
                "ticker":    ticker.upper(),
                "timeframe": "1sec",
                "date":      ts,
                "open":      float(bar.open),
                "high":      float(bar.high),
                "low":       float(bar.low),
                "close":     float(bar.close),
                "volume":    float(bar.volume),
                "source":    "ibkr_hist",
                # buying_volume/selling_volume/delta are NOT set here;
                # calculator.add_cvd_columns() will apply wick decomp for ibkr_hist rows.
            })
        except Exception as e:
            logging.warning(f"Skipping malformed bar {bar}: {e}")
    return docs


async def backfill_ticker(
    ticker: str,
    start: datetime,
    end: datetime,
    port: int = 7497,
    client_id: int = 11,
):
    """
    Backfill 1-sec bars for `ticker` from `start` to `end` (ET naive datetimes).
    Iterates backwards in CHUNK_SECONDS windows; pacing-aware.
    """
    logging.info(
        f"[Backfill] {ticker}: {start.strftime('%Y-%m-%d %H:%M')} → "
        f"{end.strftime('%Y-%m-%d %H:%M')} ET"
    )

    ib = IB()
    try:
        await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    except Exception as e:
        logging.error(f"[Backfill] Connection failed: {e}")
        return

    contract = Stock(ticker.upper(), "SMART", "USD")
    await ib.qualifyContractsAsync(contract)

    mongo = MongoClient(MONGO_URI)
    col = mongo[DB_NAME]["candles"]
    col.create_index(
        [("ticker", 1), ("timeframe", 1), ("date", 1)],
        unique=True, name="ticker_tf_date", background=True,
    )

    total_saved = 0
    current_end = end
    request_count = 0

    try:
        while current_end > start:
            chunk_start = max(current_end - timedelta(seconds=CHUNK_SECONDS), start)
            actual_secs = int((current_end - chunk_start).total_seconds())
            if actual_secs <= 0:
                break

            end_dt_aware = current_end.replace(tzinfo=ET)
            logging.info(
                f"  Request {request_count+1}: "
                f"{chunk_start.strftime('%H:%M:%S')} – {current_end.strftime('%H:%M:%S')} "
                f"({actual_secs} s)..."
            )

            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime=end_dt_aware,
                    durationStr=f"{actual_secs} S",
                    barSizeSetting="1 secs",
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                )
                request_count += 1
            except Exception as e:
                err = str(e)
                if "162" in err or "pacing" in err.lower():
                    logging.warning(
                        f"  Pacing violation — waiting {PACING_BACKOFF}s..."
                    )
                    await asyncio.sleep(PACING_BACKOFF)
                    continue
                logging.error(f"  Request error: {e}")
                await asyncio.sleep(10)
                continue

            if bars:
                docs = _bars_to_docs(ticker, bars)
                if docs:
                    ops = [
                        UpdateOne(
                            {"ticker": d["ticker"], "timeframe": d["timeframe"], "date": d["date"]},
                            {"$set": d},
                            upsert=True,
                        )
                        for d in docs
                    ]
                    result = col.bulk_write(ops)
                    saved = result.upserted_count + result.modified_count
                    total_saved += saved
                    logging.info(f"    Saved {saved} bars (fetched {len(docs)}). Total: {total_saved}")
            else:
                logging.info("    No bars returned (outside market hours or no trades).")

            current_end = chunk_start

            if current_end > start:
                await asyncio.sleep(PACING_DELAY)

    finally:
        ib.disconnect()
        logging.info(f"[Backfill] Done. Total saved: {total_saved} bars for {ticker}.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill IBKR 1-sec historical bars → MongoDB (source='ibkr_hist')"
    )
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument(
        "--days", type=int, default=1,
        help="Number of calendar days to backfill ending now (default: 1)",
    )
    parser.add_argument(
        "--start", default=None,
        help="Start date YYYYMMDD ET (overrides --days, e.g. 20260601)",
    )
    parser.add_argument(
        "--end", default=None,
        help="End date YYYYMMDD ET (default: now, e.g. 20260628)",
    )
    parser.add_argument(
        "--port", type=int, default=7497,
        help="IB Gateway port (default: 7497 paper TWS)",
    )
    parser.add_argument(
        "--client-id", type=int, default=11, dest="client_id",
        help="clientId for this connection (must differ from tick_collector's)",
    )
    args = parser.parse_args()

    now = datetime.now(tz=ET).replace(tzinfo=None)
    end_dt = (
        datetime.strptime(args.end, "%Y%m%d").replace(hour=23, minute=59, second=59)
        if args.end else now
    )
    start_dt = (
        datetime.strptime(args.start, "%Y%m%d")
        if args.start else (now - timedelta(days=args.days))
    )

    asyncio.run(backfill_ticker(args.ticker, start_dt, end_dt, args.port, args.client_id))


if __name__ == "__main__":
    main()
