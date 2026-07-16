"""
scripts/backtest_correlation.py
-------------------------------
135-case CVD<->price tracking, using REAL tick-aggressor CVD.

    9 tickers (Mega/Micro/Nano x3) x 3 sessions (Pre/Regular/After)
    x 5 timeframes (1sec/5sec/1min/5min/1hr) = 135 cells.

Data source is `raw_ticks` (from the real-time tick_collector), classified per
trade by quote/tick rule — NOT wick decomposition. Tickers with no raw_ticks are
SKIPPED (rather than silently falling back to wick, which is circular and would
fake a high correlation). See scripts/backtest_correlation_wick.py for the
matching wick-decomposed 1sec-backfill analysis to compare against.

NOTE: real tick data can only be gathered LIVE (IBKR reqHistoricalTicks is capped
at 1000 ticks/request → impractical for multi-day). Collect a session with
`python -m ibkr.tick_collector --ticker <SYM>` to add a ticker to the grid.

Metric per cell: within-session, within-day change-correlation of per-bar delta
vs per-bar price change, + directional hit-rate + n. (This replaces the old
report's spurious level-correlation of two cumulative series.)

Usage:
    python scripts/backtest_correlation.py
"""
import os
import sys

from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

from cvd.calculator import run_pipeline
from corr_common import TIERS, TFS, analyze_all, build_report

MONGO_URI = "mongodb://localhost:27017/"


def frames_for(ticker):
    """(frames, skip_reason) for the tick pipeline. frames maps tf -> aggregated df."""
    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
    if c["finviz_db"]["raw_ticks"].count_documents({"ticker": ticker}, limit=1) == 0:
        return None, "no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker %s)" % ticker
    _, frames = run_pipeline(ticker, base_timeframe="raw_tick")
    return {tf: frames.get(tf) for tf in TFS}, None


def main():
    print("135-case TICK-CVD correlation grid (real aggressor, raw_ticks only)\n")
    rows, skipped = analyze_all(TIERS, frames_for)

    report = build_report(
        rows, skipped,
        title="135-Case CVD↔Price Tracking — TICK CVD (real aggressor)",
        subtitle="Source: `raw_ticks` (live tick_collector), per-trade quote/tick classification. "
                 "9 tickers × 3 sessions × 5 timeframes.",
    )
    out = os.path.join(os.path.dirname(__file__), "CVD_135_tick_report.md")
    with open(out, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\nReport written to: {out}")


if __name__ == "__main__":
    main()
