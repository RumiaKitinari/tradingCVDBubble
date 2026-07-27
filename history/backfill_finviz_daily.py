"""
history/backfill_finviz_daily.py — B1: instant ~8-year daily history.

Fetches the FinViz Elite daily export (timeframe 'd') per ticker and writes
straight into the canonical '1day' tier with real datetimes and write-time
BVC buy/sell estimation (quality='bvc'). One HTTP request per ticker, so the
whole 9-ticker universe fills in well under a minute — this alone unlocks
full-history 1day/1week/1month charts.

Tick-era daily bars produced by the rollup worker (quality='tick'/'mixed')
are never overwritten (quality guard in history.store).

Usage:
    python -m history.backfill_finviz_daily                    # default 9 tickers
    python -m history.backfill_finviz_daily --ticker NVDA AMD
"""

import argparse
import logging
import time

import pandas as pd

from finviz.new_finviz import get_candle_data
from history.bvc import bvc_split
from history.rollup import _wick_delta
from history.schema import mongo_client
from history.store import set_backfill_coverage, upsert_bars

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT", "GME", "AMC", "PLTR", "PENN", "CHWY", "RUM"]


def backfill_daily(ticker: str, client=None) -> int:
    ticker = ticker.upper()
    candles = get_candle_data(ticker, timeframe="d")
    if not candles:
        logging.warning(f"[finviz-daily] {ticker}: no data returned")
        return 0

    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").set_index("date")
    df = df[~df.index.duplicated(keep="last")]

    est = bvc_split(df["close"], df["volume"])
    df["delta_wick"] = _wick_delta(df)      # FinViz daily-only rows: wick on the daily bar
    docs = [{
        "ticker": ticker,
        "timeframe": "1day",
        "date": ts.to_pydatetime(),
        "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "volume": float(r["volume"]),
        "buying_volume": float(est.loc[ts, "buying_volume"]),
        "selling_volume": float(est.loc[ts, "selling_volume"]),
        "delta": float(est.loc[ts, "delta"]),
        "delta_wick": float(r["delta_wick"]),
        "source": "finviz",
        "quality": "bvc",
    } for ts, r in df.iterrows()]

    n = upsert_bars(docs, client=client)
    set_backfill_coverage(ticker, "1day", df.index[0].to_pydatetime(),
                          df.index[-1].to_pydatetime(), client=client)
    logging.info(f"[finviz-daily] {ticker}: {n} daily bars "
                 f"({df.index[0].date()} → {df.index[-1].date()})")
    return n


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="FinViz daily → 1day tier backfill")
    parser.add_argument("--ticker", nargs="+", default=DEFAULT_TICKERS)
    args = parser.parse_args()

    client = mongo_client()
    total = 0
    for i, t in enumerate(args.ticker):
        if i:
            time.sleep(3)   # FinViz rate-limits rapid-fire exports (HTTP 429)
        for attempt in range(3):
            try:
                n = backfill_daily(t, client=client)
                if n:
                    total += n
                    break
                # Empty result usually means a 429 — back off and retry.
                logging.warning(f"[finviz-daily] {t}: empty response, retrying in 30s...")
                time.sleep(30)
            except Exception as e:
                logging.error(f"[finviz-daily] {t} failed: {e}")
                time.sleep(10)
    logging.info(f"[finviz-daily] done — {total} bars total")


if __name__ == "__main__":
    main()
