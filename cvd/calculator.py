"""
cvd/calculator.py
-----------------
Buy/Sell Volume Decomposition + CVD Calculator

Source-aware pipeline:
  source='ibkr_tick'   — IBKR real-time tick data; buying_volume/selling_volume/delta
                         pre-computed by tick_collector.py (quote-based aggressor).
                         Wick decomposition is SKIPPED.
  source='alpaca_tick' — Alpaca real-time/historical tick data; buying_volume/selling_volume/delta
                         pre-computed by alpaca_collector.py / alpaca_backfill.py.
                         Wick decomposition is SKIPPED.
  source='ibkr_hist'   — 1-sec bars from reqHistoricalData (no tick-level quotes).
                         Wick decomposition applied (same as FinViz).
  source='finviz_wick' — FinViz Elite 1-min bars.  Wick decomposition applied.

add_cvd_columns() detects which rows have pre-computed values and branches
accordingly, so mixed DataFrames (ibkr_tick + alpaca_tick + finviz_wick) work
transparently.
"""

import pandas as pd
from pymongo import MongoClient
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ─────────────────────────────────────────
# 1. Extract buy/sell volume from one candle
# ─────────────────────────────────────────

def decompose_candle(o: float, h: float, l: float, c: float, v: float) -> dict:
    """
    Return estimated buy/sell volume from one candle (OHLCV).

    KNOWN LIMITATION — the closing auction (15:59 print):
        US exchanges settle all Market-On-Close orders in a single closing
        cross at one price, so the 15:59 bar carries a huge volume (often ~40x
        a normal minute and ~2/3 of the final hour) packed into a near-zero
        range / doji. This wick model then decides direction from a 1-2 cent
        open/close difference and dumps almost the entire print onto one side,
        so the buy/sell SIGN of that bar flips day to day and is unreliable
        (e.g. a green buy spike on a down day). The volume itself is real; only
        its buy/sell split is meaningless for a single-price auction print.
        Per design decision (6/25) the logic is left as-is and documented here.
    """
    spread = h - l

    # spread=0 -> unable to calculate, assume buying volume == selling volume
    if spread == 0:
        return {"buying_volume": v / 2, "selling_volume": v / 2, "delta": 0.0}

    # ── Wick calculation (direction-aware)
    if c > o:  # Bullish
        upper_wick = h - c
        lower_wick = o - l
    else:      # Bearish
        upper_wick = h - o
        lower_wick = c - l

    body = spread - upper_wick - lower_wick

    # ── Convert each component to a percentage of the spread
    pct_upper = upper_wick / spread
    pct_lower = lower_wick / spread
    pct_body  = body / spread

    # Wicks are contested zones -> split equally between buyers and sellers
    half_wicks = (pct_upper + pct_lower) / 2

    # ── Volume distribution
    if c > o:   # Bullish: buyers take the body + half of the wicks
        buying_volume  = (pct_body + half_wicks) * v
        selling_volume = half_wicks * v
    else:       # Bearish: sellers take the body + half of the wicks
        buying_volume  = half_wicks * v
        selling_volume = (pct_body + half_wicks) * v

    delta = buying_volume - selling_volume

    return {
        "buying_volume":  buying_volume,
        "selling_volume": selling_volume,
        "delta":          delta,        
    }


# ─────────────────────────────────────────
# 2. Add buy/sell/delta/CVD to DataFrame
# ─────────────────────────────────────────

def _flag_auction(df: pd.DataFrame, mult: float = 10.0, spill_mult: float = 3.0) -> pd.Series:
    """Boolean Series marking each day's closing-cross bars (15:59 + 16:00).

    The volume thresholds below are calibrated for 1-MINUTE bars. At 1-second
    granularity the volume distribution has a much heavier tail (a single
    block trade easily exceeds 10x the median 1-sec volume), so running the
    detection directly on sub-minute bars flags ordinary intraday spikes as
    auctions almost every day. For sub-minute data we therefore aggregate the
    volume to 1-minute buckets, run the detection there, and map the flagged
    minutes back onto the underlying bars.
    """
    if len(df) >= 2:
        spacing = df.index.to_series().diff().dt.total_seconds().median()
        if pd.notna(spacing) and spacing < 60:
            vol_1min = df["volume"].resample("1min").sum()
            vol_1min = vol_1min[vol_1min > 0]          # drop empty minutes
            minute_flags = _flag_auction_1min(vol_1min.to_frame("volume"), mult, spill_mult)
            flagged = minute_flags[minute_flags].index
            return pd.Series(df.index.floor("min").isin(flagged), index=df.index)
    return _flag_auction_1min(df, mult, spill_mult)


def _flag_auction_1min(df: pd.DataFrame, mult: float = 10.0, spill_mult: float = 3.0) -> pd.Series:
    """Core closing-cross detection on minute-level (or coarser) volume bars.

    The closing auction footprint spans two bars: the main cross on 15:59
    (~2500x a normal after-hours minute) plus an overflow/official print on
    16:00 (~167x), after which volume drops back to normal by 16:01. So:
      1. anchor = the afternoon (>=12:00) bar with the largest volume, flagged
         only if it dwarfs that day's regular-session median (> `mult`x). This
         lands on 15:59 normally, or the early-close print (e.g. 12:59) on a
         half-day — no clock time is hardcoded.
      2. spill  = walk FORWARD from the anchor, also flagging each following bar
         while it stays elevated (> `spill_mult`x the regular median), stopping
         at the first normal bar. This catches the 16:00 overflow but leaves
         16:01 onward (genuine after-hours) and the pre-close ramp (15:55-15:58,
         genuine continuous trading) untouched. See Personal Study Log §8.

    Note: feeds that don't carry the official closing cross (e.g. Alpaca's
    IEX-only feed — the cross prints on the listing exchange) simply won't
    have an anchor exceeding `mult`x the median, so nothing gets flagged.
    """
    minute = df.index.hour * 60 + df.index.minute
    reg = (minute >= 570) & (minute < 960)                 # 09:30-16:00
    reg_med = df.loc[reg].groupby(lambda ix: ix.date())["volume"].median()

    flag = pd.Series(False, index=df.index)
    
    # Restrict anchor search to typical closing cross windows: 12:59-13:01 (early close) and 15:59-16:01 (regular close).
    # This prevents massive after-hours news spikes (e.g. 16:20) from being misidentified as the closing auction.
    is_candidate = ((minute >= 779) & (minute <= 781)) | ((minute >= 959) & (minute <= 961))
    candidates = df[is_candidate]
    
    for d, g in candidates.groupby(lambda ix: ix.date()):
        if g.empty:
            continue
        med = reg_med.get(d, float("nan"))
        anchor = g["volume"].idxmax()
        if not (pd.isna(med) or g.loc[anchor, "volume"] > mult * med):
            continue
        flag.loc[anchor] = True
        
        # Spill logic must iterate over the full day's index, not just the candidates
        day_idx = df[df.index.date == d].index
        for k in range(day_idx.get_loc(anchor) + 1, len(day_idx)):   # forward spill
            if df.loc[day_idx[k], "volume"] > spill_mult * med:
                flag.loc[day_idx[k]] = True
            else:
                break
    return flag


def _apply_wick_decomp(df: pd.DataFrame) -> pd.DataFrame:
    """Apply decompose_candle to every row of df; returns df with the three columns set."""
    results = df.apply(
        lambda row: decompose_candle(
            row["open"], row["high"], row["low"], row["close"], row["volume"]
        ),
        axis=1,
        result_type="expand",
    )
    df = df.copy()
    df["buying_volume"]  = results["buying_volume"]
    df["selling_volume"] = results["selling_volume"]
    df["delta"]          = results["delta"]
    return df


def add_cvd_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return df with CVD-related columns added.

    Columns added:
        buying_volume  - buy volume per bar (real from ticks, or wick estimate)
        selling_volume - sell volume per bar
        delta          - buying_volume - selling_volume
        source         - 'ibkr_tick' | 'ibkr_hist' | 'finviz_wick'
        is_auction     - True for closing-auction bars (neutralized)
        delta_raw      - delta before auction neutralization
        auction_volume - volume from auction bars only
        cvd_all        - all-time cumulative delta (auction-neutralized)
        cvd_all_raw    - all-time cumulative delta (includes auction)

    Accepts both FinViz 1-min DataFrames and IBKR 1-sec DataFrames.
    If a 'source' column with value 'ibkr_tick' is present AND buying_volume
    is already populated, wick decomposition is skipped for those rows.
    """
    df = df.copy()

    # ── Date parsing / index ──────────────────────────────────────────────────
    # FinViz dates are strings "MM/DD/YYYY HH:MM AM/PM"; IBKR dates come from
    # MongoDB as native datetimes.  Detect by dtype and handle each separately.
    if "date" in df.columns:
        if pd.api.types.is_string_dtype(df["date"]):
            # FinViz format: "13:00 PM" suffix is redundant but present; strip it.
            df["date"] = pd.to_datetime(
                df["date"].astype(str).str.replace(r'\s*(AM|PM)$', '', regex=True),
                format="%m/%d/%Y %H:%M",
                errors="coerce",
            )
        else:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.set_index("date").sort_index()

    # ── Source-aware buy/sell decomposition ───────────────────────────────────
    #
    # IBKR tick data already has buying_volume / selling_volume / delta computed
    # per-tick in tick_collector.py (quote-based aggressor classification).
    # For those rows we skip decompose_candle entirely.
    # All other rows (FinViz, ibkr_hist, or NaN-filled ibkr_tick gaps) get wick
    # decomposition so the pipeline is seamless for mixed DataFrames.

    has_source = "source" in df.columns
    has_precomputed = (
        "buying_volume" in df.columns
        and "selling_volume" in df.columns
        and "delta" in df.columns
    )

    if has_precomputed and has_source:
        # Rows that need wick decomposition: any source that isn't a real-tick source,
        # or rows where buying_volume is missing despite being tagged as tick data.
        # Real-tick sources (ibkr_tick, alpaca_tick) have pre-computed buy/sell/delta.
        needs_wick = (
            ~df["source"].isin(["ibkr_tick", "alpaca_tick"])
        ) | df["buying_volume"].isna()

        if needs_wick.any():
            decomp = _apply_wick_decomp(df[needs_wick])
            df.loc[needs_wick, "buying_volume"]  = decomp["buying_volume"]
            df.loc[needs_wick, "selling_volume"] = decomp["selling_volume"]
            df.loc[needs_wick, "delta"]          = decomp["delta"]
            # Tag rows that didn't already have a source
            no_src = needs_wick & df["source"].isna()
            if no_src.any():
                df.loc[no_src, "source"] = "finviz_wick"
    else:
        # No pre-computed values — apply wick decomposition to all rows.
        decomp = _apply_wick_decomp(df)
        df["buying_volume"]  = decomp["buying_volume"]
        df["selling_volume"] = decomp["selling_volume"]
        df["delta"]          = decomp["delta"]
        if "source" not in df.columns:
            df["source"] = "finviz_wick"

    # ── Closing-auction handling (neutralize direction, keep volume) ──────────
    # The 15:59 closing cross is a single-price doji carrying ~99% of its minute
    # as one batch auction; the wick model assigns its direction from a 1-2 cent
    # open/close difference, so its buy/sell SIGN is noise that would otherwise
    # dominate CVD (it alone drives the curve, ~26% of total |delta|). We KEEP its
    # volume but NEUTRALIZE the split (buy = sell = volume/2, delta = 0) for the
    # default CVD, and keep an un-neutralized `delta_raw` so a "CVD raw (incl.
    # auction)" line stays available for comparison. See Personal Study Log §8.
    df["is_auction"]     = _flag_auction(df)
    
    # Do not neutralize real tick sources. Their high volume at 16:00 represents genuine 
    # directional aggressive trades with real Bid/Ask matching, not a single MOC print.
    if "source" in df.columns:
        real_ticks = df["source"].isin(["alpaca_tick", "ibkr_tick"])
        df.loc[real_ticks, "is_auction"] = False

    df["delta_raw"]      = df["delta"]                       # before neutralization
    auc = df["is_auction"]
    df.loc[auc, "buying_volume"]  = df.loc[auc, "volume"] / 2
    df.loc[auc, "selling_volume"] = df.loc[auc, "volume"] / 2
    df.loc[auc, "delta"]          = 0.0
    df["auction_volume"] = df["volume"].where(auc, 0.0)     # per-bar auction volume

    # CVD (all-time): default = auction-neutralized; raw = includes the auction.
    df["cvd_all"]     = df["delta"].cumsum()
    df["cvd_all_raw"] = df["delta_raw"].cumsum()

    return df


# ─────────────────────────────────────────
# 3. Aggregate 1-min bars into N-min Buy/Sell Pressure
# ─────────────────────────────────────────

TIMEFRAME_RULE = {
    "1min":   "1min",
    "3min":   "3min",
    "5min":   "5min",
    "15min":  "15min",
    "1hr":    "1h",
    "3hr":    "3h",
    "1day":   "1D",
    "1week":  "1W-MON",
    "1month": "1ME",
}

# Extended rule set for IBKR 1-second base data; adds sub-minute timeframes.
# These two extra keys are omitted from TIMEFRAME_RULE (FinViz base) because
# resampling 1-min bars to 1-sec would upsample and produce empty rows.
TIMEFRAME_RULE_IBKR = {
    "raw_tick": "0S", # Special case for raw ticks
    "1sec":   "1s",
    "5sec":   "5s",
    **TIMEFRAME_RULE,
}

# Timeframes at or above daily granularity (no intraday hour breaks on x-axis)
DAILY_OR_ABOVE = {"1day", "1week", "1month"}

# Timeframes where weekend breaks must NOT be applied (labels can land on weekends)
WEEK_OR_ABOVE = {"1week", "1month"}

def aggregate_pressure(df_base: pd.DataFrame, timeframe: str = "1hr") -> pd.DataFrame:
    """
    Resample and aggregate a base-granularity DataFrame (1-min for FinViz,
    1-sec for IBKR) to the requested timeframe.

    timeframe: one of the keys in TIMEFRAME_RULE or TIMEFRAME_RULE_IBKR
               (e.g. "1sec", "1min", "1hr", "1day").
    """
    _all_rules = {**TIMEFRAME_RULE, **TIMEFRAME_RULE_IBKR}
    rule = _all_rules.get(timeframe, "1h")
    df_1min = df_base  # rename kept for clarity; works for any base granularity

    agg_spec = dict(
        open=("open",                "first"),
        high=("high",                "max"),
        low=("low",                  "min"),
        close=("close",              "last"),
        volume=("volume",            "sum"),
        buy_pressure=("buying_volume",  "sum"),
        sell_pressure=("selling_volume","sum"),
        delta_sum=("delta",          "sum"),
        cvd_all_end=("cvd_all",      "last"),   # CVD all-time (auction-neutralized)
        cvd_all_raw_end=("cvd_all_raw", "last"),# CVD all-time (includes auction)
        auction_vol=("auction_volume", "sum"),  # auction volume falling in this bar
    )
    if "source" in df_1min.columns:
        # Carry the (dominant) data source so the visualizer can shade
        # wick-estimated regions on every timeframe, not just the base one.
        agg_spec["source"] = ("source", "first")

    df_agg = df_1min.resample(rule).agg(**agg_spec).dropna(subset=["open"])

    df_agg["net_pressure"] = df_agg["buy_pressure"] - df_agg["sell_pressure"]

    # Fraction of this bar's volume that came from the closing auction. Used to
    # gray out auction-dominated bars in the chart (their direction isn't real):
    # >0.5 on the 15:59-containing intraday bars, ~0.2 on a whole-day bar.
    df_agg["auction_frac"] = (df_agg["auction_vol"] / df_agg["volume"]).fillna(0.0)

    # ── Momentum: Pressure ROC (Rate of Change)
    #   ROC_t = (Pressure_t − Pressure_{t-n}) / Pressure_{t-n} × 100
    # Percent change vs. the previous bar (n=1): measures how fast buy/sell
    # pressure is accelerating or decelerating.
    #
    # Note: computed within each session (by date) so it never compares against
    # the previous day's last bar. Also, the pre-market (low volume) -> regular
    # open (high volume) transition makes the denominator tiny and blows the ROC
    # up, so we clip to ±ROC_CLIP% to suppress spikes and keep only the trend.
    ROC_CLIP = 200.0
    d = df_agg.index.date
    df_agg["buy_pressure_roc"]  = (df_agg.groupby(d)["buy_pressure"].pct_change()  * 100).clip(-ROC_CLIP, ROC_CLIP)
    df_agg["sell_pressure_roc"] = (df_agg.groupby(d)["sell_pressure"].pct_change() * 100).clip(-ROC_CLIP, ROC_CLIP)

    # ── ROC computed using regular-hours bars only (09:30~16:00)
    # Comparing regular-hours bars to each other avoids the after/pre-market jump.
    minutes = df_agg.index.hour * 60 + df_agg.index.minute
    reg_mask = (minutes >= 570) & (minutes < 960)   # 9:30 ~ 16:00
    for src, dst in [("buy_pressure", "buy_roc_reg"), ("sell_pressure", "sell_roc_reg")]:
        reg = df_agg.loc[reg_mask, src]
        roc = (reg.groupby(reg.index.date).pct_change() * 100).clip(-ROC_CLIP, ROC_CLIP)
        df_agg[dst] = roc.reindex(df_agg.index)      # value on regular-hours bars only, NaN elsewhere

    return df_agg


# ─────────────────────────────────────────
# 4. Load data from MongoDB
# ─────────────────────────────────────────

def load_from_mongo(ticker: str, timeframe: str = "i1", days: int | None = None) -> pd.DataFrame:
    """
    Load a given ticker/timeframe from MongoDB finviz_db.candles.

    Returns all OHLCV columns plus pre-computed IBKR columns when present:
    buying_volume, selling_volume, delta, source.  add_cvd_columns() uses
    these to skip wick decomposition for ibkr_tick rows.

    days: only load bars from the last `days` calendar days. Keeps chart
          regeneration fast once weeks of 1-sec data accumulate. Only valid
          for tick-based timeframes whose `date` field is a real datetime
          (FinViz 'i1' docs store dates as strings, which a datetime range
          query would silently exclude).
    """
    client = MongoClient("mongodb://localhost:27017/")
    if timeframe == "raw_tick":
        collection = client["finviz_db"]["raw_ticks"]
        query = {"ticker": ticker}
        if days is not None:
            now_et = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
            query["date"] = {"$gte": now_et - timedelta(days=days)}
        docs = list(collection.find(
            query,
            {"_id": 0, "date": 1, "price": 1, "size": 1, "delta": 1, "source": 1}
        ))
        # Convert raw ticks to OHLCV format so the rest of the pipeline works seamlessly
        for doc in docs:
            p = doc.pop("price")
            s = doc.pop("size")
            d = doc["delta"]
            doc["open"] = doc["high"] = doc["low"] = doc["close"] = p
            doc["volume"] = s
            doc["buying_volume"] = s if d > 0 else 0.0
            doc["selling_volume"] = s if d < 0 else 0.0
    else:
        collection = client["finviz_db"]["candles"]
        query = {"ticker": ticker, "timeframe": timeframe}
        if days is not None and timeframe != "i1":
            now_et = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
            cutoff = now_et - timedelta(days=days)
            query["date"] = {"$gte": cutoff}
        
        docs = list(collection.find(
            query,
            {
                "_id": 0,
                "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
                # Pre-computed by tick_collector (ibkr_tick) — absent for FinViz docs.
                "buying_volume": 1, "selling_volume": 1, "delta": 1, "source": 1,
            }
        ))

    if not docs:
        print(f"[MongoDB] No data found for {ticker} ({timeframe})")
        return pd.DataFrame()

    df = pd.DataFrame(docs)
    print(f"[MongoDB] Loaded {len(df)} candles for {ticker} ({timeframe})")
    return df


# ─────────────────────────────────────────
# 5. Run the whole pipeline at once
# ─────────────────────────────────────────

def run_pipeline(
    ticker: str,
    base_timeframe: str = "i1",
    days: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Load bars from MongoDB → compute CVD → aggregate into every timeframe.

    Args:
        ticker         : stock symbol (e.g. 'NVDA')
        base_timeframe : MongoDB timeframe field to load.
                         'i1'   → FinViz 1-min bars (default, backward-compat)
                         '1sec' → IBKR 1-second bars (tick_collector output)
        days           : only load the last N days (tick timeframes only;
                         see load_from_mongo). None = everything.

    Returns:
        df_base : base-granularity bars with buy/sell/delta/cvd columns
        frames  : dict of aggregated DataFrames keyed by timeframe label
                  FinViz: {"1min": df, ..., "1month": df}
                  IBKR  : {"1sec": df, "5sec": df, "1min": df, ..., "1month": df}
    """
    print(f"\n{'='*50}")
    print(f"  Pipeline: {ticker}  (base_timeframe={base_timeframe})")
    print(f"{'='*50}")

    df_raw = load_from_mongo(ticker, timeframe=base_timeframe, days=days)
    if df_raw.empty:
        return pd.DataFrame(), {}

    df_base = add_cvd_columns(df_raw)

    # Choose the right timeframe map: IBKR adds 1sec / 5sec buttons to the chart.
    if base_timeframe in ["1sec", "raw_tick"]:
        tf_map = TIMEFRAME_RULE_IBKR
    else:
        tf_map = TIMEFRAME_RULE
        
    frames = {}
    for tf, rule in tf_map.items():
        if tf == "raw_tick":
            raw_df = df_base.copy()
            raw_df = raw_df.rename(columns={
                "buying_volume": "buy_pressure",
                "selling_volume": "sell_pressure",
                "cvd_all": "cvd_all_end",
                "cvd_all_raw": "cvd_all_raw_end",
                "auction_volume": "auction_vol"
            })
            raw_df["net_pressure"] = raw_df["buy_pressure"] - raw_df["sell_pressure"]
            raw_df["auction_frac"] = (raw_df["auction_vol"] / raw_df["volume"]).fillna(0.0) if "auction_vol" in raw_df.columns else 0.0
            frames["raw_tick"] = raw_df
            continue
        frames[tf] = aggregate_pressure(df_base, tf)

    sources = df_base["source"].value_counts().to_dict() if "source" in df_base.columns else {}
    print(f"[Pipeline] Base bars  : {len(df_base)}  sources={sources}")
    for tf, df in frames.items():
        print(f"[Pipeline] {tf:>6} bars : {len(df)}")
    print(f"[Pipeline] CVD range  : {df_base['cvd_all'].min():.0f} ~ {df_base['cvd_all'].max():.0f}")
    n_auc = int(df_base["is_auction"].sum())
    auc_vol = df_base["auction_volume"].sum()
    print(f"[Pipeline] Auctions   : {n_auc} bars neutralized, total auction vol {auc_vol:,.0f}")
    print(f"[Pipeline] Done.\n")

    return df_base, frames


# ── Quick test when run directly
if __name__ == "__main__":
    import sys
    base_tf = sys.argv[1] if len(sys.argv) > 1 else "i1"
    df_base, frames = run_pipeline("NVDA", base_timeframe=base_tf)
    if not df_base.empty:
        cols = ["open", "close", "volume", "buying_volume", "selling_volume", "delta", "cvd_all"]
        if "source" in df_base.columns:
            cols.insert(0, "source")
        print(f"\n[Base ({base_tf}) sample (last 3 rows)]")
        print(df_base[cols].tail(3).to_string())
        print("\n[1-hour sample (last 3 rows)]")
        print(frames["1hr"][["open","close","buy_pressure","sell_pressure","net_pressure","cvd_all_end"]].tail(3).to_string())
