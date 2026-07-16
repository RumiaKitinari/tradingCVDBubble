"""
scripts/backtest_correlation_obi.py
-----------------------------------
135-case ORDER-BOOK-IMBALANCE (OBI) <-> price tracking — the L2 (depth-of-book)
counterpart to the CVD grids. SKELETON: ready to run the moment L2 data exists.

Why this is separate from the CVD grids:
  CVD (trade flow) asks "which side did executed trades hit?". OBI (book state)
  asks "which side is resting liquidity stacked on?". They are different signals;
  OBI often LEADS price better than trade-CVD, which is the main reason L2 is
  worth the subscription. This measures exactly that, on the same 9x3x5 grid.

Data source: `trading_cvd.level2_snapshots` (100ms depth snapshots from
ibkr/level2_collector.py — needs a NASDAQ TotalView / L2 subscription, which is
gated behind IBKR's account-equity minimum). Price bars come from `raw_ticks`
(or 1sec candles). Until L2 is collected, every cell is `—` and the ticker is
listed under "Skipped" with the exact command to populate it — nothing to change
in this file when the data arrives.

Two grids are produced:
  - CONTEMPORANEOUS: corr( mean OBI in bar , ΔP in same bar )
  - PREDICTIVE:      corr( mean OBI in bar , ΔP in the NEXT bar )   ← OBI leading
plus a directional hit-rate. OBI is a per-snapshot ratio in [-1, +1]; we take the
bar mean. Reuses calculate_obi() from the live webapp so the formula can't drift.

Usage:
    python scripts/backtest_correlation_obi.py
"""
import os
import sys

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

from level2_webapp.data_provider import calculate_obi, get_l2_collection
from corr_common import TIERS, TFS, SESSIONS, build_report

ET = ZoneInfo("America/New_York")
MONGO_URI = "mongodb://localhost:27017/"

# pandas resample rules for the 5 timeframes
TF_RULE = {"1sec": "1s", "5sec": "5s", "1min": "1min", "5min": "5min", "1hr": "1h"}


def _obi_series(ticker):
    """Per-snapshot OBI as a time series (ET-naive index), or None if no L2 data."""
    snaps = list(get_l2_collection().find(
        {"ticker": ticker.upper()},
        {"_id": 0, "timestamp": 1, "bids": 1, "asks": 1},
    ))
    if not snaps:
        return None
    # level2_collector stores timestamp as epoch seconds (time.time(), UTC).
    idx = (pd.to_datetime([s["timestamp"] for s in snaps], unit="s", utc=True)
           .tz_convert(ET).tz_localize(None))
    obi = [calculate_obi(s.get("bids", []), s.get("asks", [])) for s in snaps]
    return pd.Series(obi, index=idx, name="obi").sort_index()


def _price_series(ticker):
    """Trade price series (ET-naive) from raw_ticks, falling back to 1sec candles."""
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)["finviz_db"]
    docs = list(db["raw_ticks"].find({"ticker": ticker}, {"_id": 0, "date": 1, "price": 1}))
    if docs:
        s = pd.DataFrame(docs)
        return s.assign(date=pd.to_datetime(s["date"])).set_index("date")["price"].sort_index()
    docs = list(db["candles"].find(
        {"ticker": ticker, "timeframe": "1sec"}, {"_id": 0, "date": 1, "close": 1}))
    if docs:
        s = pd.DataFrame(docs)
        return s.assign(date=pd.to_datetime(s["date"])).set_index("date")["close"].sort_index()
    return None


def load_obi_bars(ticker):
    """(frames, skip_reason). frames maps tf -> DataFrame(['close','obi'])."""
    obi = _obi_series(ticker)
    if obi is None or obi.empty:
        return None, ("no L2 snapshots (needs L2 subscription, then: "
                      "python -m ibkr.level2_collector --ticker %s)" % ticker)
    price = _price_series(ticker)
    if price is None or price.empty:
        return None, "L2 present but no price series (raw_ticks / 1sec candles) to align"
    frames = {}
    for tf, rule in TF_RULE.items():
        bar = pd.concat(
            {"close": price.resample(rule).last(), "obi": obi.resample(rule).mean()},
            axis=1, sort=True,
        ).dropna()
        frames[tf] = bar
    return frames, None


def obi_session_corr(frame, lo, hi, lead):
    """(corr, hit%, n) of bar-OBI vs price change, `lead` bars ahead (0=same bar).

    Computed within each day so ΔP never spans the overnight gap.
    """
    if frame is None or frame.empty:
        return np.nan, np.nan, 0
    t = frame.index.time
    sub = frame[(t >= lo) & (t < hi)]
    if sub.empty:
        return np.nan, np.nan, 0

    dP_parts, sig_parts = [], []
    for _, g in sub.groupby(sub.index.date):
        g = g.sort_index()
        dP = g["close"].diff().shift(-lead)      # ΔP of bar t+lead
        pair = pd.DataFrame({"dP": dP, "sig": g["obi"]}).dropna()
        dP_parts.append(pair["dP"])
        sig_parts.append(pair["sig"])
    dP = pd.concat(dP_parts)
    sig = pd.concat(sig_parts)
    if len(dP) < 3:
        return np.nan, np.nan, int(len(dP))
    corr = dP.corr(sig)
    nz = (dP != 0) & (sig != 0)
    hit = (np.sign(dP[nz]) == np.sign(sig[nz])).mean() * 100 if nz.any() else np.nan
    return corr, hit, int(len(dP))


def _cells(frames, lead):
    return {(tf, s): obi_session_corr(frames.get(tf), lo, hi, lead)
            for tf in TFS for s, (lo, hi) in SESSIONS.items()}


def main():
    print("135-case OBI↔price grid (L2 depth imbalance)\n")
    rows_now, rows_pred, skipped = [], [], []
    for tier, tickers in TIERS.items():
        for ticker in tickers:
            frames, why = load_obi_bars(ticker)
            if frames is None:
                skipped.append((ticker, tier, why))
                print(f"  {ticker:5} [{tier:5}] SKIP — {why}")
                continue
            rows_now.append({"Ticker": ticker, "Tier": tier, "cells": _cells(frames, 0)})
            rows_pred.append({"Ticker": ticker, "Tier": tier, "cells": _cells(frames, 1)})
            print(f"  {ticker:5} [{tier:5}] ok")

    report = (
        build_report(
            rows_now, skipped,
            title="135-Case OBI↔Price — CONTEMPORANEOUS (same-bar)",
            subtitle="Source: `trading_cvd.level2_snapshots` (L2 depth). Cell = corr(mean OBI, "
                     "same-bar ΔP) / hit% / n. Book imbalance vs concurrent price move.",
        )
        + "\n\n---\n\n"
        + build_report(
            rows_pred, skipped,
            title="135-Case OBI↔Price — PREDICTIVE (OBI leads 1 bar)",
            subtitle="Cell = corr(mean OBI at bar t, ΔP of bar t+1) / hit% / n. **This is the one "
                     "that matters** — whether resting-liquidity imbalance LEADS price (the main "
                     "reason to pay for L2). Compare its hit% against the tick-CVD grid.",
        )
    )
    out = os.path.join(os.path.dirname(__file__), "CVD_135_obi_report.md")
    with open(out, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\nReport written to: {out}")
    if not rows_now:
        print("\n(No L2 data yet — this is the expected skeleton state until the L2 "
              "subscription is active and ibkr/level2_collector.py has run a session.)")


if __name__ == "__main__":
    main()
