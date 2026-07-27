"""
scripts/reclassify_corr_compare.py
----------------------------------
Task A deliverable: does time-aligned (Lee-Ready) re-classification make CVD
track price better than the real-time collector's classification?

For each ticker we:
  1. load raw_ticks (trades) + raw_quotes (full NBBO stream),
  2. re-classify every trade against the quote PREVAILING at its own timestamp
     (backward merge_asof) — the industry-standard quote-lag fix,
  3. aggregate BOTH the stored delta and the re-aligned delta into bars at
     several timeframes, and
  4. compare, per session, the honest metric: within-day correlation of
     per-bar delta vs per-bar price CHANGE (stationary — not the spurious
     level correlation of two cumulative series), plus the directional
     hit-rate sign(delta)==sign(ΔP).

NVDA's NBBO stream only starts partway through its tick history, so NVDA is
restricted to the quote-covered window (trades before the first quote would be
silently tick-ruled by merge_asof and pollute the comparison).

Usage:
    python -m scripts.reclassify_corr_compare                 # NVDA SOFI RUM
    python -m scripts.reclassify_corr_compare --tickers SOFI
    python -m scripts.reclassify_corr_compare --write-report
"""
import argparse
from datetime import time as dtime

import numpy as np
import pandas as pd
from pymongo import MongoClient

from ibkr.reclassify import load_streams, reclassify
from scripts.corr_common import SESSIONS

TFS = ["1sec", "5sec", "1min", "5min"]
COVERAGE_MIN = 0.90   # require quotes to cover >=90% of trades, else skip


def bars_from_trades(trades: pd.DataFrame, delta_col: str, tf: str) -> pd.DataFrame:
    """Resample trade-level rows into OHLC + summed delta bars at `tf`."""
    rule = {"1sec": "1s", "5sec": "5s", "1min": "1min", "5min": "5min"}[tf]
    d = trades.set_index("date")
    bar = d.resample(rule).agg(close=("price", "last"),
                               delta_sum=(delta_col, "sum")).dropna(subset=["close"])
    return bar


def session_change_corr(frame: pd.DataFrame, lo: dtime, hi: dtime):
    """Within-day change-corr + directional hit-rate + n, for one session.
    (Local copy of corr_common.session_change_corr — same math, kept explicit.)"""
    if frame is None or frame.empty:
        return np.nan, np.nan, 0
    t = frame.index.time
    sub = frame[(t >= lo) & (t < hi)]
    if sub.empty:
        return np.nan, np.nan, 0
    dP_parts, de_parts = [], []
    for _, g in sub.groupby(sub.index.date):
        g = g.sort_index()
        pair = pd.DataFrame({"dP": g["close"].diff(), "de": g["delta_sum"]}).dropna()
        dP_parts.append(pair["dP"]); de_parts.append(pair["de"])
    dP = pd.concat(dP_parts); de = pd.concat(de_parts)
    if len(dP) < 3:
        return np.nan, np.nan, int(len(dP))
    corr = dP.corr(de)
    nz = (dP != 0) & (de != 0)
    hit = (np.sign(dP[nz]) == np.sign(de[nz])).mean() * 100 if nz.any() else np.nan
    return corr, hit, int(len(dP))


def analyze_ticker(tk: str):
    trades, quotes = load_streams(tk)
    if trades.empty or quotes.empty:
        return None, f"missing streams (trades={len(trades)}, quotes={len(quotes)})"

    q_lo, q_hi = quotes["date"].min(), quotes["date"].max()
    cover = trades["date"].between(q_lo, q_hi).mean()
    note = ""
    if cover < COVERAGE_MIN:
        # restrict to the quote-covered window rather than skip
        trades = trades[trades["date"].between(q_lo, q_hi)].reset_index(drop=True)
        note = (f"restricted to quote-covered window "
                f"({q_lo:%m-%d %H:%M}~{q_hi:%m-%d %H:%M}); "
                f"{cover*100:.0f}% of full tick history")

    merged = reclassify(trades, quotes, tolerance_ms=None)  # stored delta + delta_new
    out = {"ticker": tk, "note": note, "n_trades": len(merged),
           "net_stored": float(merged["delta"].sum()),
           "net_aligned": float(merged["delta_new"].sum()),
           "cells": {}}
    for tf in TFS:
        b_stored = bars_from_trades(merged, "delta", tf)
        b_align = bars_from_trades(merged, "delta_new", tf)
        for sname, (lo, hi) in SESSIONS.items():
            out["cells"][(tf, sname, "stored")] = session_change_corr(b_stored, lo, hi)
            out["cells"][(tf, sname, "aligned")] = session_change_corr(b_align, lo, hi)
    return out, None


def fmt(triple):
    corr, hit, n = triple
    if n == 0 or (isinstance(corr, float) and np.isnan(corr)):
        return "—"
    hs = f"{hit:.0f}%" if not (isinstance(hit, float) and np.isnan(hit)) else "·"
    return f"{corr:+.2f}/{hs}/{n}"


def build_report(results):
    L = ["# CVD↔Price Correlation Comparison Before & After Lee-Ready Reclassification (Task A)", "",
         "Each cell = **change-corr / direction-hit% / n_bars**. `stored` = real-time collector classification, "
         "`aligned` = Lee-Ready reclassification aligned with prevailing quote at trade time (merge_asof).", "",
         "change-corr = intraday correlation between (delta per bar) vs (close change per bar) — a stationarity metric "
         "that avoids spurious level correlation of cumulative series. A positive and high hit% means volume explains price well.", ""]
    for r in results:
        if r is None:
            continue
        L.append(f"## {r['ticker']}")
        if r["note"]:
            L.append(f"_{r['note']}_")
        L.append(f"- Number of analyzed trades: {r['n_trades']:,}")
        L.append(f"- Net delta: stored {r['net_stored']:+,.0f} → aligned {r['net_aligned']:+,.0f}")
        L.append("")
        for tf in TFS:
            L.append(f"### {tf}")
            L.append("| Session | stored | aligned |")
            L.append("|---|---|---|")
            for s in SESSIONS:
                L.append(f"| {s} | {fmt(r['cells'][(tf,s,'stored')])} | "
                         f"{fmt(r['cells'][(tf,s,'aligned')])} |")
            L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "SOFI", "RUM"])
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args()

    results = []
    for tk in args.tickers:
        print(f"[{tk}] analyzing…")
        res, why = analyze_ticker(tk)
        if res is None:
            print(f"  SKIP — {why}")
            continue
        results.append(res)
        reg1 = res["cells"][("1min", "Regular", "stored")]
        reg1a = res["cells"][("1min", "Regular", "aligned")]
        print(f"  1min Regular: stored {fmt(reg1)}  ->  aligned {fmt(reg1a)}")

    report = build_report(results)
    print("\n" + report)
    if args.write_report:
        path = "scripts/CVD_reclassify_report.md"
        with open(path, "w") as f:
            f.write(report)
        print(f"\n[report] written to {path}")


if __name__ == "__main__":
    main()
