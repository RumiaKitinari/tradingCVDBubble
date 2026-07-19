"""
history/session_grid.py — expected-session grid ("empty candles").

For intraday timeframes, any bar slot that falls inside trading hours
(pre-market 04:00 → after-hours 20:00 ET) on a day that traded should exist
on the x-axis even when no data was collected for it — as an EMPTY slot
(NaN OHLC → Plotly draws nothing → a visible gap) instead of being silently
compressed away, which distorts the time axis.

Days are taken from the data itself (any day with at least one bar), so
weekends and full holidays never produce ghost slots. Early-close half days
will show empty afternoon slots — a known, rare cosmetic limitation without
an exchange-calendar dependency.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from history.schema import GRID_TIMEFRAMES, SESSION_END, SESSION_START

# pandas resample rules per chart timeframe (subset that gets the grid)
_RULES = {
    "1min": "1min", "3min": "3min", "5min": "5min", "15min": "15min",
    "1hr": "1h", "3hr": "3h",
}

# Columns that must read as "no activity" (0) in an empty slot.
_FILL_ZERO = ["volume", "buy_pressure", "sell_pressure", "delta_sum",
              "net_pressure", "auction_vol", "auction_frac", "delta_wins_sum",
              "delta_bvc_sum"]
# Cumulative lines carry the last known level across the gap.
_FILL_FFILL = ["cvd_all_end", "cvd_all_raw_end", "cvd_session_end",
               "cvd_wins_end", "cvd_session_wins_end", "cvd_bvc_end"]


def session_grid_index(days, rule: str) -> pd.DatetimeIndex:
    """Full expected bin starts for each traded day at the given frequency."""
    parts = []
    for d in days:
        day = pd.Timestamp(d)
        start = day + pd.Timedelta(hours=SESSION_START.hour, minutes=SESSION_START.minute)
        end = day + pd.Timedelta(hours=SESSION_END.hour, minutes=SESSION_END.minute)
        parts.append(pd.date_range(start, end, freq=rule, inclusive="left"))
    if not parts:
        return pd.DatetimeIndex([])
    idx = parts[0]
    for p in parts[1:]:
        idx = idx.union(p)
    return idx


def reindex_to_session_grid(df_agg: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Reindex an aggregated frame onto the expected session grid.
    Missing slots get NaN OHLC (empty candle), zero flow, forward-filled CVD.
    Non-grid timeframes (1sec raw / daily+) are returned untouched.
    """
    if timeframe not in GRID_TIMEFRAMES or df_agg.empty:
        return df_agg
    rule = _RULES[timeframe]

    days = sorted({ts.date() for ts in df_agg.index})
    grid = session_grid_index(days, rule)
    # Never emit FUTURE slots: mid-session, today's grid would otherwise run
    # ahead to 20:00 ET with empty bins, so "tail to the newest bars" views
    # land on minutes that have not happened yet and render a blank chart.
    now_et = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    grid = grid[grid <= now_et]
    # Keep real bars that land outside 04:00–20:00 too (rare, but never drop data).
    full = grid.union(df_agg.index)
    out = df_agg.reindex(full)

    for c in _FILL_ZERO:
        if c in out.columns:
            out[c] = out[c].fillna(0.0)
    for c in _FILL_FFILL:
        if c in out.columns:
            out[c] = out[c].ffill()
    return out
