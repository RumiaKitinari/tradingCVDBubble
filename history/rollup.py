"""
history/rollup.py — incremental tier rollups (continuous-aggregate style).

Chain: 1sec → 1min → 30min → 1day, plus a FinViz i1 merge into the 1min tier
for the pre-tick era. Each (ticker, src→dst) pair keeps a watermark in
rollup_meta; a run only reloads source bars from the start of the last
(possibly partial) destination bucket, so steady-state work per minute is a
handful of bars — never a full rescan.

Buy/sell/delta are SUMMED up the ladder, so real tick-classified flow is
preserved at every resolution. Source bars without a precomputed split
(ibkr_hist 1-sec bars, FinViz i1) get BVC estimation at their own (finest
available) granularity before aggregation — estimating fine and summing up
beats estimating on the coarse bar directly.

Write order matters for equal-quality collisions (upserts overwrite on equal
rank): the CLI runs the i1 merge FIRST and the 1sec-derived rollup AFTER, so
IBKR-derived buckets win over FinViz i1 where both cover the same minute.

Usage:
    python -m history.rollup --ticker NVDA AAPL       # one pass
    python -m history.rollup                          # all tickers found in DB
    python -m history.rollup --loop 60                # run forever, every 60 s
"""

import argparse
import logging
import time
from datetime import timedelta

import numpy as np
import pandas as pd

from history.bvc import bvc_split
from history.schema import (
    DB_NAME, ROLLUP_CHAIN, TIERS, mongo_client, quality_of, worst_quality,
)
from history.store import get_watermark, set_watermark, upsert_bars

# Reload overlap: rebuild everything from the start of the last dst bucket.
_PROJ = {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1,
         "volume": 1, "buying_volume": 1, "selling_volume": 1, "delta": 1,
         "delta_wick": 1, "source": 1, "quality": 1}


def _wick_delta(df: pd.DataFrame) -> np.ndarray:
    """Per-bar wick-decomposition delta = (close - open) / (high - low) * volume.

    Algebraically identical to cvd.calculator.decompose_candle's delta (the
    equal-split half-wick terms cancel out of buy - sell), so SUMMING this up
    the tier ladder reproduces a finest-granularity wick CVD — IBKR bars enter
    at 1-sec, FinViz bars at 1-min, and every coarser tier is just their sum.
    Zero-range bars (single-price seconds, dojis) contribute 0."""
    spread = (df["high"] - df["low"]).to_numpy(dtype=float)
    direction = (df["close"] - df["open"]).to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    out = np.zeros(len(df), dtype=float)
    nz = spread > 0
    out[nz] = direction[nz] / spread[nz] * vol[nz]
    return out


def _ensure_wick(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure a delta_wick column. Rows missing it — the finest-tier entry
    (raw 1-sec bars, FinViz i1) — get it from their own OHLC; rows that already
    carry a rolled-up value keep it, so it stays a sum of finest-granularity
    wick deltas rather than being recomputed on the coarse bar."""
    if "delta_wick" not in df.columns:
        df["delta_wick"] = np.nan
    need = df["delta_wick"].isna()
    if need.any():
        df.loc[need, "delta_wick"] = _wick_delta(df.loc[need])
    return df


def _load_src(col, ticker: str, tf: str, since) -> pd.DataFrame:
    query = {"ticker": ticker, "timeframe": tf}
    if since is not None:
        query["date"] = {"$gte": since}
    df = pd.DataFrame(list(col.find(query, _PROJ)))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")


def _ensure_split(df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing buy/sell/delta with BVC at this (finest available) granularity."""
    for c in ("buying_volume", "selling_volume", "delta"):
        if c not in df.columns:
            df[c] = pd.NA
    need = df["buying_volume"].isna() | df["selling_volume"].isna() | df["delta"].isna()
    if need.any():
        est = bvc_split(df["close"], df["volume"])
        for c in ("buying_volume", "selling_volume", "delta"):
            df.loc[need, c] = est.loc[need, c]
    if "quality" not in df.columns:
        df["quality"] = pd.NA
    src = df["source"] if "source" in df.columns else pd.Series(pd.NA, index=df.index)
    df["quality"] = df["quality"].fillna(src.map(quality_of).fillna("bvc"))
    # BVC-filled rows are estimates regardless of their source tag
    df.loc[need & (df["quality"] == "tick"), "quality"] = "bvc"
    if "source" not in df.columns:
        df["source"] = "unknown"
    return df


def _aggregate(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = df.resample(rule).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
        buying_volume=("buying_volume", "sum"),
        selling_volume=("selling_volume", "sum"),
        delta=("delta", "sum"),
        delta_wick=("delta_wick", "sum"),
        source=("source", lambda s: s.mode().iloc[0] if len(s) else "unknown"),
        quality=("quality", worst_quality),
    ).dropna(subset=["open"])
    return agg


def rollup_pair(ticker: str, src_tf: str, dst_tf: str, client=None) -> int:
    """Incrementally roll src_tf bars into dst_tf for one ticker."""
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]
    rule = TIERS[dst_tf]["rule"]

    wm = get_watermark(ticker, src_tf, dst_tf, client=client)
    since = None
    if wm is not None:
        # Rebuild from the start of the watermark's bucket (partial-bucket safe).
        since = pd.Timestamp(wm).floor(rule if rule != "1D" else "D").to_pydatetime()

    df = _load_src(col, ticker, src_tf, since)
    if df.empty:
        return 0

    df = _ensure_split(df)
    df = _ensure_wick(df)
    agg = _aggregate(df, rule)
    if agg.empty:
        return 0

    docs = []
    for ts, r in agg.iterrows():
        docs.append({
            "ticker": ticker, "timeframe": dst_tf, "date": ts.to_pydatetime(),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r["volume"]),
            "buying_volume": float(r["buying_volume"]),
            "selling_volume": float(r["selling_volume"]),
            "delta": float(r["delta"]),
            "delta_wick": float(r["delta_wick"]),
            "source": str(r["source"]), "quality": str(r["quality"]),
        })
    n = upsert_bars(docs, client=client)
    set_watermark(ticker, src_tf, dst_tf, df.index.max().to_pydatetime(), client=client)
    return n


def merge_finviz_i1(ticker: str, client=None) -> int:
    """Merge legacy FinViz i1 (1-min, string dates) into the 1min tier with BVC."""
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]

    docs = list(col.find({"ticker": ticker, "timeframe": "i1"},
                         {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1,
                          "close": 1, "volume": 1}))
    if not docs:
        return 0
    df = pd.DataFrame(docs)
    df["date"] = pd.to_datetime(
        df["date"].astype(str).str.replace(r"\s*(AM|PM)$", "", regex=True),
        format="%m/%d/%Y %H:%M", errors="coerce",
    )
    df = df.dropna(subset=["date"]).sort_values("date").set_index("date")
    df = df[~df.index.duplicated(keep="last")]

    wm = get_watermark(ticker, "i1", "1min", client=client)
    if wm is not None:
        df = df[df.index >= pd.Timestamp(wm).floor("min")]
    if df.empty:
        return 0

    est = bvc_split(df["close"], df["volume"])
    df["delta_wick"] = _wick_delta(df)      # FinViz enters at 1-min: wick on 1-min
    out = [{
        "ticker": ticker, "timeframe": "1min", "date": ts.to_pydatetime(),
        "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "volume": float(r["volume"]),
        "buying_volume": float(est.loc[ts, "buying_volume"]),
        "selling_volume": float(est.loc[ts, "selling_volume"]),
        "delta": float(est.loc[ts, "delta"]),
        "delta_wick": float(r["delta_wick"]),
        "source": "finviz", "quality": "bvc",
    } for ts, r in df.iterrows()]
    n = upsert_bars(out, client=client)
    set_watermark(ticker, "i1", "1min", df.index.max().to_pydatetime(), client=client)
    return n


def _load_i1_volume(col, ticker: str) -> pd.Series:
    """Consolidated per-minute volume from the FinViz i1 docs (string dates)."""
    docs = list(col.find({"ticker": ticker, "timeframe": "i1"},
                         {"_id": 0, "date": 1, "volume": 1}))
    if not docs:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(docs)
    df["date"] = pd.to_datetime(
        df["date"].astype(str).str.replace(r"\s*(AM|PM)$", "", regex=True),
        format="%m/%d/%Y %H:%M", errors="coerce",
    )
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df[~df["date"].duplicated(keep="last")]
    return df.set_index("date")["volume"].astype(float)


# Live tick-by-tick volume (AllLast) misses odd lots and most off-exchange
# prints — measured ≈10% of consolidated volume for NVDA — yet its
# quality='tick' rank rightly blocks the FinViz merge from overwriting the
# real aggressor classification. Don't cap the factor too tightly: thin-capture
# minutes on liquid names legitimately need >10x.
VOLUME_SCALE_CAP = 200.0


def scale_tick_volume(ticker: str, since=None, client=None) -> tuple[int, "pd.Timestamp | None"]:
    """Scale 1min tick-quality buckets up to consolidated (FinViz i1) volume.

    Keeps the measured buy/sell RATIO (the research signal) but scales
    volume/buying/selling/delta by consolidated÷captured so bar sizes match
    the tape. Raw tick values are preserved in *_tick fields and vol_scaled/
    scale_factor mark the doc; upsert_bars strips those markers whenever a
    bucket is rewritten from fresh 1sec data, so this step (which skips
    already-scaled docs) is idempotent and always scales from raw values.
    The 1sec tier and raw_ticks are never touched.
    """
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]

    # Never scale the in-progress minute (or one just closed): its i1 doc may
    # be a partial mid-minute snapshot, and vol_scaled=True would freeze the
    # undershot value once the 1sec rollup stops rewriting the bucket.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    cutoff = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None) - timedelta(seconds=90)

    q = {"ticker": ticker, "timeframe": "1min",
         "quality": {"$in": ["tick", "mixed"]},
         "vol_scaled": {"$ne": True},
         "date": {"$lt": cutoff}}
    if since is not None:
        q["date"]["$gte"] = since
    docs = list(col.find(q))
    if not docs:
        return 0, None

    fv = _load_i1_volume(col, ticker)
    if fv.empty:
        return 0, None

    from pymongo import UpdateOne
    ops = []
    min_scaled = None
    for d in docs:
        vol = float(d.get("volume") or 0.0)
        fv_vol = fv.get(pd.Timestamp(d["date"]))
        if vol <= 0 or fv_vol is None or float(fv_vol) <= vol:
            continue
        if min_scaled is None or d["date"] < min_scaled:
            min_scaled = d["date"]
        factor = min(float(fv_vol) / vol, VOLUME_SCALE_CAP)
        ops.append(UpdateOne(
            {"ticker": ticker, "timeframe": "1min", "date": d["date"]},
            {"$set": {
                "volume": vol * factor,
                "buying_volume": float(d.get("buying_volume") or 0.0) * factor,
                "selling_volume": float(d.get("selling_volume") or 0.0) * factor,
                "delta": float(d.get("delta") or 0.0) * factor,
                # delta_wick is volume-proportional too, so it scales by the same
                # factor — otherwise the wick CVD would sit on the raw-tick scale
                # while cvd_all is on the consolidated scale.
                "delta_wick": float(d.get("delta_wick") or 0.0) * factor,
                "volume_tick": vol,
                "buying_volume_tick": float(d.get("buying_volume") or 0.0),
                "selling_volume_tick": float(d.get("selling_volume") or 0.0),
                "delta_tick": float(d.get("delta") or 0.0),
                "delta_wick_tick": float(d.get("delta_wick") or 0.0),
                "vol_scaled": True,
                "scale_factor": factor,
            }},
        ))
    if ops:
        col.bulk_write(ops, ordered=False)
    return len(ops), min_scaled


def rollup_ticker(ticker: str, client=None) -> dict:
    """One full pass for a ticker: i1 merge first, then the rollup chain.

    The consolidated-volume scale step runs between 1sec→1min and 1min→30min
    so the coarser tiers aggregate the SCALED minute bars.
    """
    client = client or mongo_client()
    stats = {}
    stats["i1→1min"] = merge_finviz_i1(ticker, client=client)
    for src, dst in ROLLUP_CHAIN:
        if (src, dst) == ("1min", "30min"):
            # No `since` bound: unscaled buckets can sit arbitrarily far back
            # (e.g. a backlog unlocked by a fresh i1 fetch), and the
            # vol_scaled-marker query is cheap on the ticker+timeframe index.
            n_scaled, min_scaled = scale_tick_volume(ticker, client=client)
            stats["scale(1min)"] = n_scaled
            # If scaling reached back past the coarse-tier watermarks (e.g. a
            # backlog unlocked by a fresh i1 fetch), rewind them so those
            # buckets re-aggregate from the scaled minutes.
            if min_scaled is not None:
                for s, d2 in (("1min", "30min"), ("30min", "1day")):
                    wm = get_watermark(ticker, s, d2, client=client)
                    if wm is not None and wm > min_scaled:
                        set_watermark(ticker, s, d2, min_scaled, client=client)
        stats[f"{src}→{dst}"] = rollup_pair(ticker, src, dst, client=client)
    return stats


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Incremental tier rollup worker")
    parser.add_argument("--ticker", nargs="+", default=None,
                        help="Tickers to roll up (default: every ticker in DB)")
    parser.add_argument("--loop", type=int, default=0,
                        help="Run forever with this many seconds between passes")
    parser.add_argument("--rescale", action="store_true",
                        help="One-time: scale ALL historical 1min tick buckets "
                             "to consolidated volume, then drop the 1min→30min/"
                             "30min→1day watermarks (metadata only) so the "
                             "coarse tiers re-aggregate from scaled bars")
    parser.add_argument("--reset-wick", action="store_true",
                        help="One-time: drop ALL rollup watermarks for the given "
                             "tickers so the next pass re-rolls the whole chain and "
                             "backfills delta_wick (finest-granularity wick CVD) "
                             "into every existing tier bar. Idempotent for the "
                             "other columns (buy/sell/delta re-sum to the same "
                             "values). Follow with a normal pass (this flag runs "
                             "one automatically).")
    args = parser.parse_args()

    client = mongo_client()
    col = client[DB_NAME]["candles"]

    if args.reset_wick:
        tickers = args.ticker or sorted(col.distinct("ticker"))
        for t in tickers:
            r = client[DB_NAME]["rollup_meta"].delete_many({"ticker": t.upper()})
            logging.info(f"[reset-wick] {t}: dropped {r.deleted_count} watermarks "
                         f"— chain will re-roll and backfill delta_wick")

    if args.rescale:
        tickers = args.ticker or sorted(col.distinct("ticker"))
        for t in tickers:
            t = t.upper()
            n, _ = scale_tick_volume(t, since=None, client=client)
            if n:
                client[DB_NAME]["rollup_meta"].delete_many(
                    {"ticker": t, "src": {"$in": ["1min", "30min"]}})
            logging.info(f"[rescale] {t}: scaled {n} 1min tick buckets"
                         + (" — coarse-tier watermarks reset" if n else ""))

    while True:
        tickers = args.ticker or sorted(col.distinct("ticker"))
        for t in tickers:
            try:
                stats = rollup_ticker(t.upper(), client=client)
                written = {k: v for k, v in stats.items() if v}
                if written:
                    logging.info(f"[rollup] {t}: {written}")
            except Exception as e:
                logging.error(f"[rollup] {t} failed: {e}")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
