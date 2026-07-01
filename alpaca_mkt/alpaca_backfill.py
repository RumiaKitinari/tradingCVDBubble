"""
alpaca_mkt/alpaca_backfill.py — Step 4: Historical backfill via Alpaca.

Retrieves historical trade + quote ticks from the Alpaca IEX free feed,
classifies each trade's aggressor direction using the nearest prior quote
(shared logic in cvd/aggressor.py), and saves to MongoDB in one or both of
two modes:

  --mode 1sec  (default) : aggregate to 1-second OHLCV bars (timeframe='1sec',
                           source='alpaca_tick'). Used by calculator/visualizer.
  --mode tick            : store every raw trade tick as-is with trade id and
                           condition codes. Useful for re-aggregating at any
                           granularity or for Level-2 research later.
  --mode both            : save both simultaneously.

Data is fetched in hourly chunks (memory-bounded: a full day of IEX quotes can
run into millions of rows). Classification state (previous price / tick
direction / last quote) carries across chunk boundaries, so the result is
identical to processing the whole range in one pass.

Tick documents have microsecond-precision timestamps (ET-naive) and include
the matched bid/ask, the Alpaca trade id and the condition codes. The unique
(ticker, date, id) index makes re-running a range idempotent (duplicates are
skipped, not re-inserted).

Unlike ibkr/backfill.py (which gets OHLCV bars only → wick decomposition),
this module uses tick-level bid/ask for REAL aggressor classification even
for historical data.

Limitations:
    - IEX free feed: ~2.5% of total market volume; bid/ask is IEX-only (not NBBO).
    - Retention period for tick-level history varies (typically a few years).
    - The most recent ~15 minutes are unavailable on the free tier.

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

from cvd.aggressor import classify_vectorized
from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY
from .alpaca_collector import _ensure_tick_index, _insert_ticks

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"

# A trade is only matched to a quote at most this old; older quotes are
# considered stale (e.g. the previous session's last quote before a
# pre-market trade) and the tick rule takes over instead.
QUOTE_TOLERANCE = pd.Timedelta("5s")

# Fetch window per API call. Bounds memory: a full day of IEX quotes can be
# millions of rows, an hour stays in the hundreds of thousands.
CHUNK = timedelta(hours=1)


# ─────────────────────────────────────────
# Fetch helpers
# ─────────────────────────────────────────

def _to_et_naive_col(ts: pd.Series) -> pd.Series:
    """Vectorized: tz-aware timestamp column → ET-naive."""
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    return ts.dt.tz_convert("America/New_York").dt.tz_localize(None)


def _fetch_trades(client: StockHistoricalDataClient, ticker: str,
                  start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch historical trade ticks for `ticker` from `start` to `end`.
    Returns a DataFrame with columns: ts, price, size (+id, conditions when
    provided by the API), sorted by ts. Empty DataFrame if no data.
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
    df = df[df["symbol"] == ticker]
    keep = [c for c in ("timestamp", "price", "size", "id", "conditions") if c in df.columns]
    df = df[keep].rename(columns={"timestamp": "ts"}).copy()
    df["ts"] = _to_et_naive_col(df["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def _fetch_quotes(client: StockHistoricalDataClient, ticker: str,
                  start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch historical quote ticks for `ticker` from `start` to `end`.
    Returns a DataFrame with columns: ts, bid_price, ask_price, sorted by ts.
    Empty DataFrame if no data.
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
    df = df.rename(columns={"timestamp": "ts"})
    df["ts"] = _to_et_naive_col(df["ts"])
    return df.sort_values("ts").reset_index(drop=True)


# ─────────────────────────────────────────
# Aggregate classified ticks → 1-sec bars
# ─────────────────────────────────────────

def _aggregate_to_1sec_from_merged(merged: pd.DataFrame, ticker: str) -> list[dict]:
    """
    Aggregate a classified+merged trade DataFrame to 1-second OHLCV bars.
    Expects columns: ts, price, size, buying_volume, selling_volume.
    Returns a list of MongoDB documents (timeframe='1sec').
    """
    agg = (
        merged.assign(second=merged["ts"].dt.floor("s"))
        .groupby("second")
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("size", "sum"),
            buying_volume=("buying_volume", "sum"),
            selling_volume=("selling_volume", "sum"),
        )
    )

    docs = []
    for row in agg.itertuples():
        bv, sv = float(row.buying_volume), float(row.selling_volume)
        docs.append({
            "ticker":          ticker,
            "timeframe":       "1sec",
            "date":            row.Index.to_pydatetime(),
            "open":            float(row.open),
            "high":            float(row.high),
            "low":             float(row.low),
            "close":           float(row.close),
            "volume":          float(row.volume),
            "buying_volume":   bv,
            "selling_volume":  sv,
            "delta":           bv - sv,
            "source":          "alpaca_tick",
        })
    return docs


# ─────────────────────────────────────────
# Raw tick documents
# ─────────────────────────────────────────

def _build_tick_docs(merged: pd.DataFrame, ticker: str) -> list[dict]:
    """
    Build one MongoDB document per trade tick from a classified+merged DataFrame.
    Microsecond-precision ET-naive date, plus the Alpaca trade id and condition
    codes so odd-lot / out-of-sequence trades can be filtered later
    (e.g. condition 'I' = odd lot, 'Z' = out of sequence).
    """
    has_id   = "id" in merged.columns
    has_cond = "conditions" in merged.columns

    docs = []
    for row in merged.itertuples(index=False):
        docs.append({
            "ticker":     ticker,
            "date":       row.ts,            # ET-naive, microsecond precision
            "price":      float(row.price),
            "size":       float(row.size),
            "bid":        float(row.bid_price) if not pd.isna(row.bid_price) else None,
            "ask":        float(row.ask_price) if not pd.isna(row.ask_price) else None,
            "delta":      float(row.delta),
            "id":         getattr(row, "id", None) if has_id else None,
            "conditions": list(row.conditions) if has_cond and row.conditions is not None else None,
            "source":     "alpaca_tick",
        })
    return docs


# ─────────────────────────────────────────
# Main backfill function
# ─────────────────────────────────────────

def backfill_ticker(ticker: str, start: datetime, end: datetime, mode: str = "1sec"):
    """
    Backfill Alpaca data for `ticker` from `start` to `end` (tz-aware datetimes).
    Fetches in hourly chunks; classification state carries across chunks.

    Args:
        ticker : Stock symbol (e.g. 'NVDA').
        start  : Start tz-aware datetime.
        end    : End tz-aware datetime.
        mode   : '1sec' | 'tick' | 'both'
                 '1sec' — aggregate to 1-sec OHLCV bars (timeframe='1sec')
                 'tick' — store raw trade ticks (finviz_db.ticks)
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
        _ensure_tick_index(tick_col)

    logging.info(f"[Backfill] {ticker}: {start} → {end}  mode={mode}")
    total_1sec = 0
    total_tick = 0
    current = start

    # Classification state carried across hourly chunks
    prev_price: float | None = None
    prev_dir: float = 0.0
    last_quote: pd.DataFrame | None = None   # 1-row df: last quote of the previous chunk

    while current < end:
        chunk_end = min(current + CHUNK, end)

        trades = _fetch_trades(client, ticker, current, chunk_end)
        quotes = _fetch_quotes(client, ticker, current, chunk_end)

        if trades.empty:
            current = chunk_end
            continue
        logging.info(f"  {current:%Y-%m-%d %H:%M %Z}: {len(trades)} trades, {len(quotes)} quotes")

        # Prepend the previous chunk's last quote so trades at the start of
        # this chunk still match a recent quote (subject to QUOTE_TOLERANCE).
        if last_quote is not None:
            quotes = pd.concat([last_quote, quotes], ignore_index=True)

        if not quotes.empty:
            merged = pd.merge_asof(
                trades,
                quotes[["ts", "bid_price", "ask_price"]],
                on="ts", direction="backward",
                tolerance=QUOTE_TOLERANCE,
            )
            last_quote = quotes.iloc[[-1]][["ts", "bid_price", "ask_price"]]
        else:
            merged = trades.copy()
            merged["bid_price"] = np.nan
            merged["ask_price"] = np.nan

        price = merged["price"].to_numpy(dtype=float)
        size  = merged["size"].to_numpy(dtype=float)
        bid   = pd.to_numeric(merged["bid_price"], errors="coerce").to_numpy(dtype=float)
        ask   = pd.to_numeric(merged["ask_price"], errors="coerce").to_numpy(dtype=float)
        delta, prev_price, prev_dir = classify_vectorized(
            price, size, bid, ask, prev_price, prev_dir
        )
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
                total_1sec += r.upserted_count + r.modified_count

        # ── Save raw ticks (separate collection: finviz_db.ticks) ───────────
        if save_tick:
            docs_tick = _build_tick_docs(merged, ticker)
            total_tick += _insert_ticks(tick_col, docs_tick, f"{ticker} {current:%Y-%m-%d %H}h")

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
    parser.add_argument("--start", default=None,
                        help="Start date YYYY-MM-DD (ET, midnight)")
    parser.add_argument("--end", default=None,
                        help="End date YYYY-MM-DD (ET, inclusive — covers that whole day)")
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

    def _parse_et(s: str) -> datetime:
        # Trading days are ET days; parse CLI dates as ET midnight.
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=ET)

    end_dt = (
        min(_parse_et(args.end) + timedelta(days=1), now)   # inclusive end day
        if args.end else now
    )
    start_dt = (
        _parse_et(args.start)
        if args.start else (now - timedelta(days=args.days))
    )

    for ticker in args.ticker:
        backfill_ticker(ticker.upper(), start_dt, end_dt, mode=args.mode)


if __name__ == "__main__":
    main()
