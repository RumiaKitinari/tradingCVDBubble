"""
scripts/backtest_correlation_wick.py
------------------------------------
135-case CVD<->price tracking, using WICK-decomposed CVD on 1sec-BACKFILL data.

    9 tickers (Mega/Micro/Nano x3) x 3 sessions x 5 timeframes = 135 cells.

Unlike the tick version, 1-second OHLCV bars CAN be backfilled from IBKR
(`reqHistoricalData`, see ibkr/backfill.py, stored source='ibkr_hist'). Those
bars carry no trade-level buy/sell, so CVD is estimated by wick decomposition
(`decompose_candle`): buy/sell volume inferred from each candle's wick lengths.

IMPORTANT — wick CVD is partly CIRCULAR: delta is derived from the candle's
open→close direction, so it mechanically tracks price and will show a HIGHER
"correlation" than tick CVD. That is not evidence of better order-flow capture;
it's the price signal fed back in. This script exists to quantify that gap
side-by-side with scripts/backtest_correlation.py — not as a signal to trust.

Only tickers with backfilled `ibkr_hist` 1sec bars appear; to add one, run
`python -m ibkr.backfill --ticker <SYM> --days N`.

Usage:
    python scripts/backtest_correlation_wick.py
"""
import os
import sys

import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

from cvd.calculator import add_cvd_columns, aggregate_pressure
from corr_common import TIERS, TFS, analyze_all, build_report

MONGO_URI = "mongodb://localhost:27017/"


def _load_hist_1sec(ticker):
    """Backfilled 1sec OHLCV bars (source='ibkr_hist') as a DataFrame, or empty."""
    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)["finviz_db"]["candles"]
    docs = list(c.find(
        {"ticker": ticker, "timeframe": "1sec", "source": "ibkr_hist"},
        {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1,
         "close": 1, "volume": 1, "source": 1},
    ))
    return pd.DataFrame(docs)


def frames_for(ticker):
    """(frames, skip_reason). Wick-decompose backfilled 1sec bars, then aggregate."""
    df = _load_hist_1sec(ticker)
    if df.empty:
        return None, "no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker %s --days N)" % ticker
    # add_cvd_columns applies wick decomposition to non-'ibkr_tick' sources.
    base = add_cvd_columns(df)
    frames = {tf: aggregate_pressure(base, tf) for tf in TFS}
    return frames, None


def main():
    print("135-case WICK-CVD correlation grid (1sec backfill, wick decomp)\n")
    rows, skipped = analyze_all(TIERS, frames_for)

    report = build_report(
        rows, skipped,
        title="135-Case CVD↔Price Tracking — WICK CVD (1sec backfill)",
        subtitle="Source: `ibkr_hist` 1sec backfill, buy/sell estimated by wick decomposition. "
                 "**Correlation here is partly circular (delta derived from price) — compare against "
                 "the tick report, do not trust in isolation.** 9 tickers × 3 sessions × 5 timeframes.",
    )
    out = os.path.join(os.path.dirname(__file__), "CVD_135_wick_report.md")
    with open(out, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\nReport written to: {out}")


if __name__ == "__main__":
    main()
