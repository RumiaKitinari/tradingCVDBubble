"""
scripts/corr_common.py
----------------------
Shared engine for the 135-case CVD<->price tracking analysis:

    9 tickers (Mega/Micro/Nano x3) x 3 sessions x 5 timeframes = 135 cells.

Both entry points use this so tick-CVD and wick-CVD are measured IDENTICALLY:
  - scripts/backtest_correlation.py       (real tick aggressor CVD, raw_ticks)
  - scripts/backtest_correlation_wick.py  (wick-decomposed 1sec-backfill CVD)

Metric per cell: within-session, within-day correlation of per-bar **delta** vs
per-bar **price change** (stationary — avoids the spurious level-correlation of
two cumulative series), plus the directional hit-rate `sign(delta)==sign(ΔP)`
and the sample size n (number of paired bars). n matters: at 1hr a session only
holds a handful of bars, so those cells are noisy by construction.
"""
import numpy as np
import pandas as pd
from datetime import time as dtime

# 3 market-cap tiers x 3 tickers
TIERS = {
    "Mega":  ["NVDA", "AAPL", "MSFT"],
    "Micro": ["SOFI", "PLTR", "GME"],
    "Nano":  ["RUM", "CHWY", "PENN"],
}
TIER_ORDER = ["Mega", "Micro", "Nano"]

# 3 trading sessions (ET)
SESSIONS = {
    "Pre":     (dtime(4, 0),  dtime(9, 30)),
    "Regular": (dtime(9, 30), dtime(16, 0)),
    "After":   (dtime(16, 0), dtime(20, 0)),
}

# 5 bar timeframes
TFS = ["1sec", "5sec", "1min", "5min", "1hr"]


def session_change_corr(frame: pd.DataFrame, lo: dtime, hi: dtime):
    """(change-corr, directional hit-rate %, n paired bars) for one session.

    Correlates per-bar delta against per-bar price change, computed WITHIN each
    trading day (so close.diff() never spans the overnight gap) then pooled.
    Requires `frame` to have a DatetimeIndex and columns 'close' + 'delta_sum'.
    """
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
        dP_parts.append(pair["dP"])
        de_parts.append(pair["de"])
    dP = pd.concat(dP_parts)
    de = pd.concat(de_parts)
    if len(dP) < 3:
        return np.nan, np.nan, int(len(dP))

    corr = dP.corr(de)
    nz = (dP != 0) & (de != 0)               # a flat bar has no direction to hit
    hit = (np.sign(dP[nz]) == np.sign(de[nz])).mean() * 100 if nz.any() else np.nan
    return corr, hit, int(len(dP))


def analyze_all(tickers_by_tier, frames_for):
    """Run the 135-cell grid.

    frames_for(ticker) -> (frames_dict | None, skip_reason | None), where
    frames_dict maps a timeframe in TFS to an aggregated DataFrame.

    Returns (rows, skipped): rows is a list of
    {"Ticker","Tier","cells":{(tf,session):(corr,hit,n)}}.
    """
    rows, skipped = [], []
    for tier, tickers in tickers_by_tier.items():
        for ticker in tickers:
            frames, why = frames_for(ticker)
            if frames is None:
                skipped.append((ticker, tier, why))
                print(f"  {ticker:5} [{tier:5}] SKIP — {why}")
                continue
            cells = {}
            for tf in TFS:
                fr = frames.get(tf)
                for sname, (lo, hi) in SESSIONS.items():
                    cells[(tf, sname)] = session_change_corr(fr, lo, hi)
            rows.append({"Ticker": ticker, "Tier": tier, "cells": cells})
            reg = cells[("1min", "Regular")]
            print(f"  {ticker:5} [{tier:5}] ok   (1min Regular corr {reg[0]:+.3f}, n={reg[2]})")
    return rows, skipped


def _cell(triple):
    """Format one cell as 'corr/hit%/n', or '—' when empty."""
    corr, hit, n = triple
    if n == 0 or (isinstance(corr, float) and np.isnan(corr)):
        return "—"
    hs = f"{hit:.0f}%" if not (isinstance(hit, float) and np.isnan(hit)) else "·"
    return f"{corr:+.2f}/{hs}/{n}"


def build_report(rows, skipped, title, subtitle):
    """Render the 135-cell grid: one table per timeframe (rows=tickers by tier,
    cols=sessions, cell='corr/hit%/n')."""
    L = [f"# {title}", "", subtitle, "",
         "Each cell is **change-corr / directional-hit% / n bars**. "
         "`—` = no data. Higher corr & hit% = the signal tracks price better; "
         "watch n (tiny n, e.g. at 1hr, = unreliable).", ""]

    for tf in TFS:
        L.append(f"## {tf}")
        head = "| Tier | Ticker | " + " | ".join(SESSIONS.keys()) + " |"
        sep = "|---|---|" + "---|" * len(SESSIONS)
        L += [head, sep]
        for tier in TIER_ORDER:
            for r in [x for x in rows if x["Tier"] == tier]:
                cells = " | ".join(_cell(r["cells"][(tf, s)]) for s in SESSIONS)
                L.append(f"| {tier} | {r['Ticker']} | {cells} |")
        # tier-mean change-corr row (Regular session) for a quick read
        means = []
        for s in SESSIONS:
            vals = [r["cells"][(tf, s)][0] for r in rows
                    if not np.isnan(r["cells"][(tf, s)][0])]
            means.append(f"{np.mean(vals):+.2f}" if vals else "—")
        L.append(f"| **mean** | **corr** | " + " | ".join(f"**{m}**" for m in means) + " |")
        L.append("")

    if skipped:
        L.append("## Skipped")
        for tk, tier, why in skipped:
            L.append(f"- **{tk}** ({tier}): {why}")
        L.append("")
    return "\n".join(L)
