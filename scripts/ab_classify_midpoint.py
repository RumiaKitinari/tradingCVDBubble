"""
scripts/ab_classify_midpoint.py
-------------------------------
A/B test improvement #2 (midpoint Lee-Ready) on the clean consolidated feed.

For each ticker we reclassify the SAME stored ticks two ways and compare how well
the resulting per-bar delta tracks price change (change-corr / hit%), by session:

  OLD (touch)    : buy iff price >= ask, sell iff price <= bid, else tick rule
                   (inside-spread trades fall to the weaker tick rule)
  NEW (midpoint) : classic Lee-Ready — inside-spread trades classified vs the mid

Both use cvd/aggressor.classify_vectorized (single source of truth), so the only
difference is the use_midpoint flag. Reads raw_ticks (which store per-trade
bid/ask) for every clean day >= --since, so it is fully retroactive.

Usage:
    python scripts/ab_classify_midpoint.py
    python scripts/ab_classify_midpoint.py --since 2026-07-21 --tf 1min
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from cvd.aggressor import classify_vectorized
from corr_common import session_change_corr, SESSIONS

TIERS = [("Mega", "NVDA"), ("Micro", "SOFI"), ("Nano", "RUM")]
RULE = {"1sec": "1s", "1min": "1min"}


def load_ticks(ticker, since):
    c = MongoClient("mongodb://localhost:27017/")["finviz_db"]["raw_ticks"]
    lo = datetime.strptime(since, "%Y-%m-%d")
    docs = list(c.find({"ticker": ticker, "date": {"$gte": lo}},
                       {"_id": 0, "date": 1, "price": 1, "size": 1,
                        "bid": 1, "ask": 1}).sort("date", 1))
    if not docs:
        return None
    df = pd.DataFrame(docs)
    df["bid"] = pd.to_numeric(df.get("bid"), errors="coerce")
    df["ask"] = pd.to_numeric(df.get("ask"), errors="coerce")
    return df


def frame_for(df, use_midpoint, tf):
    """1-bar frame (index, close, delta_sum) for a classification method."""
    delta, _, _ = classify_vectorized(
        df["price"].to_numpy(float), df["size"].to_numpy(float),
        df["bid"].to_numpy(float), df["ask"].to_numpy(float),
        use_midpoint=use_midpoint)
    g = pd.DataFrame({"close": df["price"].to_numpy(float), "delta": delta},
                     index=pd.to_datetime(df["date"]))
    r = g.resample(RULE[tf]).agg(close=("close", "last"), delta_sum=("delta", "sum"))
    return r.dropna(subset=["close"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-21")
    ap.add_argument("--tf", default="1min", choices=list(RULE))
    args = ap.parse_args()

    print(f"\nA/B midpoint vs touch — {args.tf}, clean feed since {args.since}")
    print("cell = change-corr / hit% / n   (Δ = mid − touch)\n")
    hdr = f"{'Ticker':6} {'Session':8} {'touch':>18} {'midpoint':>18}   Δcorr"
    print(hdr); print("-" * len(hdr))

    wins = {"better": 0, "worse": 0, "flat": 0}
    for tier, tk in TIERS:
        df = load_ticks(tk, args.since)
        if df is None:
            print(f"{tk:6} (no data)"); continue
        f_old = frame_for(df, False, args.tf)
        f_mid = frame_for(df, True, args.tf)
        for s, (lo, hi) in SESSIONS.items():
            co, ho, no = session_change_corr(f_old, lo, hi)
            cm, hm, nm = session_change_corr(f_mid, lo, hi)
            if np.isnan(co) or np.isnan(cm):
                dc = float("nan"); tag = ""
            else:
                dc = cm - co
                if abs(dc) < 0.005: wins["flat"] += 1; tag = "≈"
                elif dc > 0:        wins["better"] += 1; tag = "↑"
                else:               wins["worse"] += 1;  tag = "↓"
            def fmt(c, h, n):
                if np.isnan(c): return "—"
                hs = f"{h:.0f}%" if not np.isnan(h) else "·"
                return f"{c:+.3f}/{hs}/{n}"
            dcs = f"{dc:+.3f} {tag}" if not np.isnan(dc) else "—"
            print(f"{tk:6} {s:8} {fmt(co,ho,no):>18} {fmt(cm,hm,nm):>18}   {dcs}")
        print()

    print(f"midpoint vs touch across cells:  ↑better={wins['better']}  "
          f"↓worse={wins['worse']}  ≈flat={wins['flat']}")


if __name__ == "__main__":
    main()
