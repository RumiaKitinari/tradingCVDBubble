"""
alpaca/alpaca_backfill.py — Step 4: Historical backfill via Alpaca.

Retrieves historical trade + quote ticks from the Alpaca IEX free feed,
classifies each trade's aggressor direction using the nearest prior quote
(same quote-based logic as alpaca_collector.py), and saves to MongoDB in
one or both of two modes:

  --mode 1sec  (default) : aggregate to 1-second OHLCV bars (timeframe='1sec',
                           source='alpaca_tick'). Used by calculator/visualizer.
  --mode tick            : store every raw trade tick as-is (timeframe='tick',
                           source='alpaca_tick'). Useful for re-aggregating at
                           any granularity or for Level-2 research later.
  --mode both            : save both simultaneously.

Tick documents have microsecond-precision timestamps (ET-naive) and include
the matched bid/ask at the time of the trade.

Unlike ibkr/backfill.py (which gets OHLCV bars only → wick decomposition),
this module uses tick-level bid/ask for REAL aggressor classification even
for historical data.

Limitations:
    - IEX free feed: ~2.5% of total market volume; bid/ask is IEX-only (not NBBO).
    - Retention period for tick-level history varies (typically a few years).
    - 15-min delay on free tier (data up to 15 min ago is available).

Usage:
    python -m alpaca_mkt.alpaca_backfill --ticker NVDA --days 1
    python -m alpaca_mkt.alpaca_backfill --ticker NVDA --days 1 --mode tick
    python -m alpaca_mkt.alpaca_backfill --ticker NVDA --days 1 --mode both
    python -m alpaca_mkt.alpaca_backfill --ticker NVDA --start 2026-06-01 --end 2026-06-29
    python -m alpaca_mkt.alpaca_backfill --ticker NVDA AAPL --days 5
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pymongo import MongoClient, UpdateOne
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest
from alpaca.data.enums import DataFeed

from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"


# ─────────────────────────────────────────
# Fetch helpers
# ─────────────────────────────────────────

def _to_et_naive(ts: datetime) -> datetime:
    """Convert any tz-aware timestamp to ET-naive (for consistent MongoDB storage)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(ET).replace(tzinfo=None)


def _fetch_trades(client: StockHistoricalDataClient, ticker: str,
                  start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch historical trade ticks for `ticker` from `start` to `end`.
    Returns a DataFrame indexed by ET-naive timestamp with columns: price, size.
    Returns empty DataFrame if no data.
    """
    try:
        req = StockTradesRequest(
            symbol_or_symbols=ticker,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        result = client.get_stock_trades(req)
    except Exception as e:
        logging.error(f"  [Trades] Fetch error for {ticker}: {e}")
        return pd.DataFrame()

    if not result.data:
        return pd.DataFrame()

    df = result.df.reset_index()
    df = df[df["symbol"] == ticker][["timestamp", "price", "size"]].copy()
    df["timestamp"] = df["timestamp"].apply(_to_et_naive)
    return df.set_index("timestamp").sort_index()


def _fetch_quotes(client: StockHistoricalDataClient, ticker: str,
                  start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch historical quote ticks for `ticker` from `start` to `end`.
    Returns a DataFrame indexed by ET-naive timestamp with columns: bid_price, ask_price.
    Returns empty DataFrame if no data.
    """
    try:
        req = StockQuotesRequest(
            symbol_or_symbols=ticker,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        result = client.get_stock_quotes(req)
    except Exception as e:
        logging.error(f"  [Quotes] Fetch error for {ticker}: {e}")
        return pd.DataFrame()

    if not result.data:
        return pd.DataFrame()

    df = result.df.reset_index()
    df = df[df["symbol"] == ticker][["timestamp", "bid_price", "ask_price"]].copy()
    df["timestamp"] = df["timestamp"].apply(_to_et_naive)
    return df.set_index("timestamp").sort_index()


# ─────────────────────────────────────────
# Aggressor classification (vectorized)
# ─────────────────────────────────────────

def _classify_vectorized(price: np.ndarray, size: np.ndarray,
                         bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """
    Vectorized aggressor classification.  Same logic as alpaca_collector.classify_aggressor.

    Priority:
      1. Quote-based: if bid < ask and price >= ask → buy (+size); <= bid → sell (-size).
      2. Tick-rule fallback for mid-market trades: uptick → buy, downtick → sell.
      3. Zero for indeterminate.
    """
    delta = np.zeros(len(price))
    prev = np.empty(len(price))
    prev[0] = np.nan
    prev[1:] = price[:-1]

    bid_nan = np.isnan(bid)
    ask_nan = np.isnan(ask)
    has_spread = ~bid_nan & ~ask_nan & (bid < ask)

    # Quote-based
    buy_q  = has_spread & (price >= ask)
    sell_q = has_spread & (price <= bid)
    delta[buy_q]  =  size[buy_q]
    delta[sell_q] = -size[sell_q]

    # Tick-rule for mid-market or quotes absent
    prev_valid = ~np.isnan(prev)
    needs_tick = ~buy_q & ~sell_q
    uptick   = needs_tick & prev_valid & (price > prev)
    downtick = needs_tick & prev_valid & (price < prev)
    delta[uptick]   =  size[uptick]
    delta[downtick] = -size[downtick]

    return delta


# ─────────────────────────────────────────
# Aggregate one day's ticks → 1-sec bars
# ─────────────────────────────────────────

def _aggregate_to_1sec_from_merged(merged: pd.DataFrame, ticker: str) -> list[dict]:
    """
    Aggregate a classified+merged trade DataFrame to 1-second OHLCV bars.
    Expects columns: ts, price, size, buying_volume, selling_volume, delta.
    Returns a list of MongoDB documents (timeframe='1sec').
    """
    merged = merged.copy()
    merged["second"] = merged["ts"].dt.floor("s")

    docs = []
    for second, group in merged.groupby("second"):
        bv = float(group["buying_volume"].sum())
        sv = float(group["selling_volume"].sum())
        docs.append({
            "ticker":          ticker,
            "timeframe":       "1sec",
            "date":            second.to_pydatetime(),
            "open":            float(group["price"].iloc[0]),
            "high":            float(group["price"].max()),
            "low":             float(group["price"].min()),
            "close":           float(group["price"].iloc[-1]),
            "volume":          float(group["size"].sum()),
            "buying_volume":   bv,
            "selling_volume":  sv,
            "delta":           bv - sv,
            "source":          "alpaca_tick",
        })
    return docs


# ─────────────────────────────────────────
# Main backfill function
# ─────────────────────────────────────────

# ─────────────────────────────────────────
# Raw tick documents
# ─────────────────────────────────────────

def _build_tick_docs(merged: pd.DataFrame, ticker: str) -> list[dict]:
    """
    Build one MongoDB document per trade tick from a classified+merged DataFrame.
    timeframe='tick', microsecond-precision ET-naive date.

    conditions list is preserved so odd-lot / out-of-sequence trades can be
    filtered later (e.g. condition 'I' = odd lot, 'Z' = out of sequence).
    """
    docs = []
    for row in merged.itertuples(index=False):
        docs.append({
            "ticker":     ticker,
            "timeframe":  "tick",
            "date":       row.ts,            # ET-naive, microsecond precision
            "price":      float(row.price),
            "size":       float(row.size),
            "bid":        float(row.bid_price) if not pd.isna(row.bid_price) else None,
            "ask":        float(row.ask_price) if not pd.isna(row.ask_price) else None,
            "delta":      float(row.delta),
            "source":     "alpaca_tick",
        })
    return docs


# ─────────────────────────────────────────
# Main backfill function
# ─────────────────────────────────────────

def backfill_ticker(ticker: str, start: datetime, end: datetime, mode: str = "1sec"):
    """
    Backfill Alpaca data for `ticker` from `start` to `end`.
    Processes one calendar day at a time to keep memory usage bounded.

    Args:
        ticker : Stock symbol (e.g. 'NVDA').
        start  : Start UTC-aware datetime.
        end    : End UTC-aware datetime.
        mode   : '1sec' | 'tick' | 'both'
                 '1sec' — aggregate to 1-sec OHLCV bars (timeframe='1sec')
                 'tick' — store raw trade ticks (timeframe='tick')
                 'both' — save both simultaneously
    """
    save_1sec = mode in ("1sec", "both")
    save_tick = mode in ("tick", "both")

    client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

    mongo = MongoClient(MONGO_URI)
    col      = mongo[DB_NAME]["candles"]   # 1-sec bars (unique index)
    tick_col = mongo[DB_NAME]["ticks"]     # raw tick archive (separate collection)

    if save_1sec:
        col.create_index(
            [("ticker", 1), ("timeframe", 1), ("date", 1)],
            unique=True, name="ticker_tf_date", background=True,
        )
    if save_tick:
        # Non-unique: many ticks per second. Index for range queries by ticker+date.
        tick_col.create_index(
            [("ticker", 1), ("date", 1)],
            name="ticker_date", background=True,
        )

    logging.info(f"[Backfill] {ticker}: {start.date()} → {end.date()}  mode={mode}")
    total_1sec = 0
    total_tick = 0
    current = start

    while current < end:
        chunk_end = min(current + timedelta(days=1), end)
        logging.info(f"  Day {current.date()} …")

        trades_df = _fetch_trades(client, ticker, current, chunk_end)
        quotes_df = _fetch_quotes(client, ticker, current, chunk_end)
        logging.info(f"    {len(trades_df)} trades, {len(quotes_df)} quotes")

        if trades_df.empty:
            logging.info("    No trades (market closed or no IEX data).")
            current = chunk_end
            continue

        # Build the classified+merged DataFrame once; reuse for both modes.
        trades = trades_df.reset_index().rename(columns={"timestamp": "ts"})
        if not quotes_df.empty:
            quotes = quotes_df.reset_index().rename(columns={"timestamp": "ts"})
            merged = pd.merge_asof(
                trades.sort_values("ts"),
                quotes[["ts", "bid_price", "ask_price"]].sort_values("ts"),
                on="ts", direction="backward",
            )
        else:
            trades["bid_price"] = np.nan
            trades["ask_price"] = np.nan
            merged = trades.copy()

        price = merged["price"].to_numpy(dtype=float)
        size  = merged["size"].to_numpy(dtype=float)
        bid   = pd.to_numeric(merged["bid_price"], errors="coerce").to_numpy(dtype=float)
        ask   = pd.to_numeric(merged["ask_price"], errors="coerce").to_numpy(dtype=float)
        delta = _classify_vectorized(price, size, bid, ask)
        merged["delta"]          = delta
        merged["buying_volume"]  = np.where(delta > 0, delta, 0.0)
        merged["selling_volume"] = np.where(delta < 0, -delta, 0.0)

        # ── Save 1-sec bars ──────────────────────────────────────────────────
        if save_1sec:
            docs_1sec = _aggregate_to_1sec_from_merged(merged, ticker)
            if docs_1sec:
                ops = [
                    UpdateOne(
                        {"ticker": d["ticker"], "timeframe": d["timeframe"], "date": d["date"]},
                        {"$set": d}, upsert=True,
                    )
                    for d in docs_1sec
                ]
                r = col.bulk_write(ops)
                saved = r.upserted_count + r.modified_count
                total_1sec += saved
                logging.info(f"    [1sec] Saved {saved} bars. Total: {total_1sec}")

        # ── Save raw ticks (separate collection: finviz_db.ticks) ───────────
        if save_tick:
            docs_tick = _build_tick_docs(merged, ticker)
            if docs_tick:
                tick_col.insert_many(docs_tick, ordered=False)
                total_tick += len(docs_tick)
                logging.info(f"    [tick] Saved {len(docs_tick)} ticks. Total: {total_tick}")

        current = chunk_end

    logging.info(
        f"[Backfill] Done. {ticker}: "
        f"{total_1sec} 1-sec bars, {total_tick} ticks saved."
    )


# ─────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Alpaca historical backfill → MongoDB (source='alpaca_tick')"
    )
    parser.add_argument("--ticker", nargs="+", default=["NVDA"],
                        help="Ticker(s) to backfill (space-separated)")
    parser.add_argument("--days", type=int, default=1,
                        help="Calendar days to backfill ending now (default: 1)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD UTC")
    parser.add_argument("--end",   default=None, help="End date   YYYY-MM-DD UTC")
    parser.add_argument(
        "--mode", default="1sec", choices=["1sec", "tick", "both"],
        help=(
            "1sec: aggregate to 1-sec OHLCV bars (default). "
            "tick: store every raw trade tick. "
            "both: save both simultaneously."
        ),
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    end_dt = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end else now
    )
    start_dt = (
        datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.start else (now - timedelta(days=args.days))
    )

    for ticker in args.ticker:
        backfill_ticker(ticker.upper(), start_dt, end_dt, mode=args.mode)


if __name__ == "__main__":
    main()
