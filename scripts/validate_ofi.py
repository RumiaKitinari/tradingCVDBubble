"""
scripts/validate_ofi.py
-----------------------
Improvement #1 (OFI) head-to-head vs CVD on the clean feed, by session.

Per bar we compare how well each explains the price change:
  CVD delta : signed trade volume (raw_ticks.delta)
  OFI       : best-queue order-flow imbalance (cvd/ofi.py, from sized raw_quotes)

Both are correlated against the SAME per-bar mid-price change. OFI needs
bid/ask sizes, stored in raw_quotes only since 2026-07-22 19:43 ET — so before a
full regular session accumulates this mostly reports "n too small". Re-run after
tomorrow's session for the real verdict.

Usage:
    python scripts/validate_ofi.py --tf 1min --since 2026-07-22
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

from cvd.ofi import ofi_frame
from corr_common import SESSIONS

TIERS = [("Mega", "NVDA"), ("Micro", "SOFI"), ("Nano", "RUM")]
RULE = {"1sec": "1s", "1min": "1min"}


def _session_corr(frame, xcol, dpcol, lo, hi):
    """change-corr(x, price-change) + hit% + n, within session & day."""
    if frame is None or frame.empty:
        return np.nan, np.nan, 0
    t = frame.index.time
    sub = frame[(t >= lo) & (t < hi)]
    xs, ds = [], []
    for _, g in sub.groupby(sub.index.date):
        g = g.sort_index()
        xs.append(g[xcol]); ds.append(g[dpcol])
    if not xs:
        return np.nan, np.nan, 0
    x = pd.concat(xs); dP = pd.concat(ds)
    m = (~x.isna()) & (~dP.isna())
    x, dP = x[m], dP[m]
    if len(x) < 3:
        return np.nan, np.nan, int(len(x))
    corr = x.corr(dP)
    nz = (x != 0) & (dP != 0)
    hit = (np.sign(x[nz]) == np.sign(dP[nz])).mean() * 100 if nz.any() else np.nan
    return corr, hit, int(len(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-22")
    ap.add_argument("--tf", default="1min", choices=list(RULE))
    args = ap.parse_args()
    lo_dt = datetime.strptime(args.since, "%Y-%m-%d")
    db = MongoClient("mongodb://localhost:27017/")["finviz_db"]

    print(f"\nOFI vs CVD — {args.tf}, since {args.since}")
    print("cell = change-corr / hit% / n   (both vs per-bar mid change)\n")
    hdr = f"{'Ticker':6} {'Session':8} {'CVD':>16} {'OFI':>16}   winner"
    print(hdr); print("-" * len(hdr))

    for tier, tk in TIERS:
        quotes = pd.DataFrame(list(db["raw_quotes"].find(
            {"ticker": tk, "date": {"$gte": lo_dt},
             "bid_size": {"$exists": True, "$ne": None}},
            {"_id": 0, "date": 1, "bid": 1, "ask": 1, "bid_size": 1, "ask_size": 1})))
        ticks = pd.DataFrame(list(db["raw_ticks"].find(
            {"ticker": tk, "date": {"$gte": lo_dt}},
            {"_id": 0, "date": 1, "price": 1, "delta": 1})))
        if quotes.empty:
            print(f"{tk:6} (no sized quotes yet)"); continue

        of = ofi_frame(quotes, RULE[args.tf])
        of["dmid"] = of["mid"].diff()
        # CVD bar: sum stored delta, aligned to the same bars
        if not ticks.empty:
            tg = pd.DataFrame({"delta": ticks["delta"].to_numpy(float)},
                              index=pd.to_datetime(ticks["date"]))
            cvd = tg.resample(RULE[args.tf]).agg(delta=("delta", "sum"))
            of = of.join(cvd, how="left")
        else:
            of["delta"] = np.nan

        for s, (lo, hi) in SESSIONS.items():
            cc, ch, cn = _session_corr(of, "delta", "dmid", lo, hi)
            oc, oh, on = _session_corr(of, "ofi", "dmid", lo, hi)
            def fmt(c, h, n):
                if np.isnan(c): return "—"
                hs = f"{h:.0f}%" if not np.isnan(h) else "·"
                return f"{c:+.3f}/{hs}/{n}"
            win = ""
            if not (np.isnan(cc) or np.isnan(oc)):
                win = "OFI" if abs(oc) > abs(cc) else "CVD" if abs(cc) > abs(oc) else "="
            print(f"{tk:6} {s:8} {fmt(cc,ch,cn):>16} {fmt(oc,oh,on):>16}   {win}")
        print()


if __name__ == "__main__":
    main()
