"""
scripts/validate_session.py
---------------------------
One-command, post-session validation of the tick-CVD quote-lag fix.

Run this AFTER a regular session has been collected with the updated
tick_collector (which now persists the full NBBO stream to `raw_quotes`). It
ties together the pieces that already existed as separate tools and prints a
single PASS / ATTENTION verdict per step:

  1. COVERAGE   — does raw_quotes actually span the regular session for --date?
                  (Without this the merge_asof reclassification is meaningless.)
  2. QUOTE-LAG  — NBBO staleness distribution + mid-size (100-1000sh) buy% on
                  fresh vs stale quotes. If buy% recovers toward ~50 on fresh
                  quotes, the sell skew is a quote-lag artifact (see
                  scripts/analyze_quote_lag.py for the standalone version).
  3. RECLASSIFY — time-aligned merge_asof reclassification, level & direction
                  tracking stored-vs-aligned (uses ibkr/reclassify.py).
  4. CORRELATION— corrected change-based CVD<->price correlation for the session
                  (uses the fixed scripts/backtest_correlation.py).

Usage:
    python scripts/validate_session.py --ticker NVDA                 # today (ET)
    python scripts/validate_session.py --ticker NVDA --date 2026-07-13
"""
import os
import sys
import argparse
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.dirname(__file__))

from cvd.calculator import run_pipeline
from ibkr.reclassify import reclassify, _tracking
from corr_common import session_change_corr

ET = ZoneInfo("America/New_York")
MONGO_URI = "mongodb://localhost:27017/"
REG_LO, REG_HI = dtime(9, 30), dtime(16, 0)


def _verdict(ok: bool) -> str:
    return "\033[32mPASS\033[0m" if ok else "\033[33mATTENTION\033[0m"


def _load_day(ticker: str, day):
    """raw_ticks and raw_quotes for `ticker` on calendar `day` (ET-naive), sorted."""
    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)["finviz_db"]
    lo = datetime.combine(day, dtime(0, 0))
    hi = datetime.combine(day, dtime(23, 59, 59))
    q = {"ticker": ticker, "date": {"$gte": lo, "$lte": hi}}
    trades = pd.DataFrame(list(c["raw_ticks"].find(
        q, {"_id": 0, "date": 1, "price": 1, "size": 1, "delta": 1,
            "bid": 1, "ask": 1, "quote_age_ms": 1, "cls": 1})))
    quotes = pd.DataFrame(list(c["raw_quotes"].find(
        q, {"_id": 0, "date": 1, "bid": 1, "ask": 1})))
    for d in (trades, quotes):
        if not d.empty:
            d.sort_values("date", inplace=True, ignore_index=True)
    return trades, quotes


def _reg(df):
    """Rows inside regular hours (09:30-16:00)."""
    if df.empty:
        return df
    t = pd.to_datetime(df["date"]).dt.time
    return df[(t >= REG_LO) & (t < REG_HI)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (ET). Default: today.")
    args = ap.parse_args()

    day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else datetime.now(ET).date())
    print(f"\n=== validate_session  {args.ticker}  {day} (ET) ===\n")

    trades, quotes = _load_day(args.ticker, day)
    tr_reg = _reg(trades)
    q_reg = _reg(quotes)

    if trades.empty:
        print(f"{_verdict(False)}  No raw_ticks for {args.ticker} on {day}. "
              "Run the tick_collector during the session first.")
        return

    # ── 1. COVERAGE ──────────────────────────────────────────────────────────
    print("── 1. COVERAGE " + "─" * 50)
    if quotes.empty:
        cov = 0.0
        print(f"{_verdict(False)}  raw_quotes EMPTY for {day}. The updated collector "
              "hasn't persisted an NBBO stream yet — reclassification (step 3) can't run.")
    else:
        q_lo, q_hi = quotes["date"].min(), quotes["date"].max()
        cov = tr_reg["date"].between(q_lo, q_hi).mean() * 100 if not tr_reg.empty else 0.0
        ok = cov >= 90
        print(f"{_verdict(ok)}  raw_quotes {q_lo.time()}–{q_hi.time()} cover "
              f"{cov:.1f}% of regular-hours trades "
              f"({len(q_reg)} quotes, {len(tr_reg)} reg trades).")
        if not ok:
            print("           <90% → collect a FULL 09:30–16:00 session before trusting step 3.")

    # ── 2. QUOTE-LAG ─────────────────────────────────────────────────────────
    print("\n── 2. QUOTE-LAG " + "─" * 49)
    if "quote_age_ms" not in tr_reg.columns or tr_reg["quote_age_ms"].dropna().empty:
        print(f"{_verdict(False)}  No instrumented ticks (bid/ask/quote_age_ms) in reg hours.")
    else:
        qa = tr_reg["quote_age_ms"].dropna()
        stale100 = (qa > 100).mean() * 100
        print(f"           staleness  p50={qa.quantile(.5):.0f}  p90={qa.quantile(.9):.0f}  "
              f"p99={qa.quantile(.99):.0f} ms   |  >100ms: {stale100:.1f}%")
        mid = tr_reg[(tr_reg["size"] >= 100) & (tr_reg["size"] < 1000)]
        fresh = mid[mid["quote_age_ms"] < 100]
        stale = mid[mid["quote_age_ms"] >= 100]
        def buypct(x):
            return (np.sign(x["delta"]) > 0).mean() * 100 if len(x) else float("nan")
        bf, bs = buypct(fresh), buypct(stale)
        print(f"           mid-size buy%   fresh(<100ms)={bf:.1f}   stale(>=100ms)={bs:.1f}   "
              f"(n_fresh={len(fresh)}, n_stale={len(stale)})")
        artifact = (not np.isnan(bf) and not np.isnan(bs) and (bf - bs) >= 3)
        print(f"{_verdict(artifact)}  " + (
            "fresh buy% higher than stale → sell-skew is a quote-lag artifact (fix helps)."
            if artifact else
            "fresh≈stale → skew looks like real order flow, not quote-lag (fix won't move it)."))

    # ── 3. RECLASSIFY (merge_asof) ───────────────────────────────────────────
    print("\n── 3. RECLASSIFY " + "─" * 48)
    if quotes.empty or cov < 50:
        print(f"{_verdict(False)}  Skipped — need >=50% quote coverage (have {cov:.0f}%). "
              "Collect a full session Monday, then re-run.")
    else:
        # Drop the per-trade NBBO snapshot cols so merge_asof against the quote
        # stream doesn't collide (bid_x/bid_y). reclassify needs only date/price/
        # size/delta from trades; fresh bid/ask come from `quotes`.
        tr = trades.drop(columns=["bid", "ask", "quote_age_ms", "cls"], errors="ignore")
        merged = reclassify(tr, quotes, tolerance_ms=None)
        lvl0, dir0 = _tracking(merged.assign(delta=merged["delta"]), "delta")
        lvl1, dir1 = _tracking(merged, "delta_new")
        print(f"           stored  : LEVEL {lvl0:+.3f}   dir {dir0:.1f}%")
        print(f"           aligned : LEVEL {lvl1:+.3f}   dir {dir1:.1f}%   (merge_asof)")
        improved = (not np.isnan(lvl1) and not np.isnan(lvl0) and lvl1 > lvl0)
        print(f"{_verdict(improved)}  " + (
            "time-aligned reclassification improves level tracking."
            if improved else "no improvement — inspect coverage / tolerance."))
        print(f"           net delta: stored {merged['delta'].sum():+,.0f} "
              f"-> aligned {merged['delta_new'].sum():+,.0f}")

    # ── 4. CORRELATION (corrected, change-based) ─────────────────────────────
    print("\n── 4. CORRELATION " + "─" * 47)
    _, frames = run_pipeline(args.ticker, base_timeframe="raw_tick")
    frame = frames.get("1min")
    if frame is None or frame.empty:
        print(f"{_verdict(False)}  No 1min frame produced.")
    else:
        day_frame = frame[frame.index.date == day]
        scope, fr = ("this session", day_frame) if not day_frame.empty else ("all data", frame)
        corr, hit, n = session_change_corr(fr, REG_LO, REG_HI)
        print(f"           regular-hours ({scope}, {n} 1min bars): "
              f"change-corr {corr:+.3f}   dir hit {hit:.1f}%")
        print(f"{_verdict(hit is not None and hit >= 55)}  "
              "hit-rate >=55% would be a usable directional signal "
              "(prior sessions sat ~52%, near coin-flip).")

    print("\n=== done ===\n")


if __name__ == "__main__":
    main()
