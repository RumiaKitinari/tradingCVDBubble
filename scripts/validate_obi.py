"""
scripts/validate_obi.py
-----------------------
Improvement #3 (OBI) on the clean feed, by session.

OBI = (Σ bid_size − Σ ask_size) / (Σ bid_size + Σ ask_size) over the book, a
resting-order pressure STATE. Unlike CVD/OFI (realized flow, coincident with
price), imbalance may LEAD price — so we test both:
  contemporaneous : corr(OBI_t , mid change over bar t)
  predictive      : corr(OBI_t , mid change over bar t+1)   ← the interesting one

Reads trading_cvd.level2_snapshots (per-snapshot bids/asks). Depth is currently
IEX-only (IBKR Warning 2152: NASDAQ/BATS/... depth not yet entitled), so OBI
magnitude is a thin-book proxy — read direction, not scale, until TotalView
depth is active.

Usage:
    python scripts/validate_obi.py --tf 1min --since 2026-07-21 --levels 5
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

from corr_common import SESSIONS

TIERS = [("Mega", "NVDA"), ("Micro", "SOFI"), ("Nano", "RUM")]
RULE = {"1sec": "1s", "1min": "1min"}


def obi_of(levels, n):
    return sum(l["size"] for l in (levels or [])[:n])


def obi_frame(docs, rule, levels):
    rows = []
    for d in docs:
        b = obi_of(d.get("bids"), levels)
        a = obi_of(d.get("asks"), levels)
        tot = b + a
        rows.append((d["date"], (b - a) / tot if tot else 0.0, d.get("mid_price")))
    df = pd.DataFrame(rows, columns=["date", "obi", "mid"])
    df = df.set_index(pd.to_datetime(df["date"])).drop(columns="date")
    out = df.resample(rule).agg(obi=("obi", "mean"), mid=("mid", "last"))
    return out.dropna(subset=["mid"])


def _corr(frame, xcol, dpcol, lo, hi):
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
    nz = (x != 0) & (dP != 0)
    hit = (np.sign(x[nz]) == np.sign(dP[nz])).mean() * 100 if nz.any() else np.nan
    return x.corr(dP), hit, int(len(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-21")
    ap.add_argument("--tf", default="1min", choices=list(RULE))
    ap.add_argument("--levels", type=int, default=5, help="book levels per side")
    args = ap.parse_args()
    lo_dt = datetime.strptime(args.since, "%Y-%m-%d")
    col = MongoClient("mongodb://localhost:27017/")["trading_cvd"]["level2_snapshots"]

    print(f"\nOBI vs price — {args.tf}, {args.levels} levels, since {args.since}")
    print("cell = change-corr / hit% / n\n")
    hdr = f"{'Ticker':6} {'Session':8} {'contemporaneous':>18} {'predictive(t+1)':>18}"
    print(hdr); print("-" * len(hdr))

    for tier, tk in TIERS:
        docs = list(col.find({"ticker": tk, "date": {"$gte": lo_dt}},
                             {"_id": 0, "date": 1, "bids": 1, "asks": 1, "mid_price": 1})
                    .sort("date", 1))
        if not docs:
            print(f"{tk:6} (no L2 snapshots)"); continue
        f = obi_frame(docs, RULE[args.tf], args.levels)
        f["dmid"] = f["mid"].diff()             # change over bar t
        f["dmid_next"] = f["dmid"].shift(-1)    # change over bar t+1 (predictive)
        for s, (lo, hi) in SESSIONS.items():
            cc, ch, cn = _corr(f, "obi", "dmid", lo, hi)
            pc, ph, pn = _corr(f, "obi", "dmid_next", lo, hi)
            def fmt(c, h, n):
                if np.isnan(c): return "—"
                hs = f"{h:.0f}%" if not np.isnan(h) else "·"
                return f"{c:+.3f}/{hs}/{n}"
            print(f"{tk:6} {s:8} {fmt(cc,ch,cn):>18} {fmt(pc,ph,pn):>18}")
        print()


if __name__ == "__main__":
    main()
