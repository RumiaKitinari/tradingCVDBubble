"""
history/serve.py — tier-aware chart serving with an incremental cache.

run_pipeline_tiered() is the fast replacement for calculator.run_pipeline()
for tier-served timeframes: it loads ONLY the mapped tier (1day chart reads
~2,000 daily rows, never millions of 1-sec bars), skips wick decomposition
(tiers carry write-time buy/sell/delta), resamples ONCE for the requested
timeframe, and applies the expected-session grid so missing intraday bars
render as empty slots.

CVD note: cvd_all here is the cumulative delta anchored at the start of the
loaded window. Over estimated (quality='bvc'/'wick') history the LEVEL is an
estimate — the chart shades those regions; within the tick era the deltas
(and thus the curve shape) are real because rollups sum tick-classified flow.

A module-level cache keeps the raw tier frame per (ticker, tier) and only
fetches bars newer than the cached tail on refresh, so the 10-second Dash
poll costs a tiny $gte query instead of a full reload.
"""

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from cvd.calculator import add_cvd_columns, aggregate_pressure
from history.schema import (
    DB_NAME, SERVE_TIER, SERVE_WINDOW_DAYS, TIER_FALLBACK, mongo_client,
)
from history.session_grid import reindex_to_session_grid

_PROJ = {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1,
         "volume": 1, "buying_volume": 1, "selling_volume": 1, "delta": 1,
         "delta_wick": 1, "source": 1, "quality": 1, "special_conditions": 1}

# (ticker, tier) → {"raw": DataFrame(indexed by date), "window_start": datetime|None}
_CACHE: dict = {}
_LOCK = threading.Lock()
_REFRESH_OVERLAP = timedelta(minutes=5)   # re-fetch tail to absorb partial-bar upserts


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)


def _fetch(col, ticker: str, tier: str, since: datetime | None) -> pd.DataFrame:
    query = {"ticker": ticker, "timeframe": tier}
    if since is not None:
        query["date"] = {"$gte": since}
    df = pd.DataFrame(list(col.find(query, _PROJ)))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def load_tier(ticker: str, tier: str, window_days: float | None, client=None) -> pd.DataFrame:
    """Load a tier frame through the incremental cache."""
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]
    window_start = None if window_days is None else _now_et() - timedelta(days=float(window_days))
    key = (ticker, tier)

    with _LOCK:
        cached = _CACHE.get(key)
        # Full reload when nothing cached or the requested window reaches
        # further back than what we have.
        needs_full = (
            cached is None
            or (window_start is None and cached["window_start"] is not None)
            or (window_start is not None and cached["window_start"] is not None
                and window_start < cached["window_start"])
        )
        if needs_full:
            raw = _fetch(col, ticker, tier, window_start)
            _CACHE[key] = {"raw": raw, "window_start": window_start}
            return raw.copy()

        raw = cached["raw"]
        since = (raw.index.max() - _REFRESH_OVERLAP) if not raw.empty else window_start
        fresh = _fetch(col, ticker, tier, since)
        if not fresh.empty:
            raw = pd.concat([raw[raw.index < fresh.index.min()], fresh])
            cached["raw"] = raw
        return raw.copy()


# Chart-timeframe → bar length in seconds, for sizing an anchor window.
# 1week/1month have no fixed length; approximated for the ±N-bars span only.
_TF_SECONDS = {
    "1sec": 1, "5sec": 5, "10sec": 10, "30sec": 30,
    "1min": 60, "3min": 180, "5min": 300, "15min": 900,
    "1hr": 3600, "3hr": 10800,
    "1day": 86400, "1week": 604800, "1month": 2629800,
}


def _fetch_range(col, ticker: str, tier: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Load one tier's bars within [start, end] (inclusive), uncached."""
    df = pd.DataFrame(list(col.find(
        {"ticker": ticker, "timeframe": tier, "date": {"$gte": start, "$lte": end}},
        _PROJ,
    )))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def load_tier_around(ticker: str, tier: str, anchor: datetime, active_tf: str,
                     bars_each_side: int = 1500, client=None) -> pd.DataFrame:
    """Load a tier frame spanning roughly `bars_each_side` CHART bars on each
    side of `anchor`. Bypasses the incremental cache (this is a one-off
    historical view, not the live tail)."""
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]
    span = timedelta(seconds=_TF_SECONDS.get(active_tf, 60) * max(1, int(bars_each_side)))
    return _fetch_range(col, ticker, tier, anchor - span, anchor + span)


def invalidate_cache(ticker: str | None = None) -> None:
    with _LOCK:
        if ticker is None:
            _CACHE.clear()
        else:
            for key in [k for k in _CACHE if k[0] == ticker]:
                del _CACHE[key]


def run_pipeline_tiered(ticker: str, active_tf: str, days: float | None = None,
                        client=None, anchor: datetime | None = None,
                        bars_each_side: int = 1500) -> tuple[pd.DataFrame, dict]:
    """
    Serve (df_base, frames) for one timeframe from its materialized tier.
    Signature-compatible with calculator.run_pipeline consumers: frames
    contains just {active_tf: aggregated_frame}.

    When `anchor` (an ET-naive datetime) is given, load a fixed window of
    ~`bars_each_side` chart bars on each side of that instant instead of the
    live "N days back from now" window — used by the date/time jump control.
    """
    ticker = ticker.upper()
    tier = SERVE_TIER.get(active_tf)
    if tier is None:
        raise ValueError(f"timeframe {active_tf!r} is not tier-served")

    window = days if days is not None else SERVE_WINDOW_DAYS.get(active_tf)

    if anchor is not None:
        raw = load_tier_around(ticker, tier, anchor, active_tf, bars_each_side, client=client)
        if raw.empty:
            for fb in TIER_FALLBACK.get(tier, []):
                raw = load_tier_around(ticker, fb, anchor, active_tf, bars_each_side, client=client)
                if not raw.empty:
                    logging.info(f"[serve] {ticker} {active_tf} @anchor {anchor}: "
                                 f"tier {tier} empty, falling back to {fb}")
                    break
    else:
        raw = load_tier(ticker, tier, window, client=client)
        if raw.empty:
            # Tier not materialized yet — cascade to finer tiers (tick-era only).
            for fb in TIER_FALLBACK.get(tier, []):
                raw = load_tier(ticker, fb, window, client=client)
                if not raw.empty:
                    logging.info(f"[serve] {ticker} {active_tf}: tier {tier} empty, "
                                 f"falling back to {fb}")
                    break
    if raw.empty:
        return pd.DataFrame(), {}

    # Tiers carry precomputed buy/sell/delta → wick decomposition is skipped;
    # this only adds auction flags + CVD cumsums (cheap).
    df_base = add_cvd_columns(raw.reset_index())

    df_agg = aggregate_pressure(df_base, active_tf)
    df_agg = reindex_to_session_grid(df_agg, active_tf)

    logging.info(f"[serve] {ticker} {active_tf}: {len(df_base)} tier({tier}) bars "
                 f"→ {len(df_agg)} {active_tf} bars")
    return df_base, {active_tf: df_agg}
