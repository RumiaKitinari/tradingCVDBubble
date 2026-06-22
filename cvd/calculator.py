"""
cvd/calculator.py
-----------------
Buy/Sell Volume Decomposition + CVD Calculator
"""

import pandas as pd
from pymongo import MongoClient


# ─────────────────────────────────────────
# 1. Extract buy/sell volume from one candle
# ─────────────────────────────────────────

def decompose_candle(o: float, h: float, l: float, c: float, v: float) -> dict:
    """
    Return estimated buy/sell volume from one candle (OHLCV).
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

def add_cvd_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Take a 1-min DataFrame and return it with these added columns:
        buying_volume  - estimated buy volume of that bar
        selling_volume - estimated sell volume of that bar
        delta          - buying_volume - selling_volume for that bar
        cvd            - cumulative delta (CVD), reset each trading day

    The df must have open/high/low/close/volume columns.
    If a 'date' column exists it is converted to the index automatically.
    """
    df = df.copy()

    # Convert the 'date' column into a datetime index.
    # FinViz formats time as 24-hour but still appends AM/PM (e.g. "13:00 PM"),
    # so strip the trailing AM/PM before parsing as %H:%M.
    if "date" in df.columns:
        df["date"] = pd.to_datetime(
            df["date"].str.replace(r'\s*(AM|PM)$', '', regex=True),
            format="%m/%d/%Y %H:%M",
            errors="coerce"
        )
        df = df.set_index("date").sort_index()

    # Apply decompose_candle to every row; result_type="expand" turns the
    # returned dict into separate columns.
    results = df.apply(
        lambda row: decompose_candle(
            row["open"], row["high"], row["low"], row["close"], row["volume"]
        ),
        axis=1,
        result_type="expand"
    )

    df["buying_volume"]  = results["buying_volume"]
    df["selling_volume"] = results["selling_volume"]
    df["delta"]          = results["delta"]

    # CVD (session-reset): cumulative delta, reset to 0 each trading day
    df["cvd"] = df.groupby(df.index.date)["delta"].cumsum()
    # CVD (all-time): cumulative delta over the entire series, never reset
    df["cvd_all"] = df["delta"].cumsum()

    return df


# ─────────────────────────────────────────
# 3. Aggregate 1-min bars into N-min Buy/Sell Pressure
# ─────────────────────────────────────────

TIMEFRAME_RULE = {
    "1min":  "1min",
    "3min":  "3min",
    "5min":  "5min",
    "15min": "15min",
    "1hr":   "1h",
    "3hr":   "3h",
    "1day":  "1D",
    "1week": "1W-MON",
    "1month": "1ME",
}

# Timeframes at or above daily granularity (no intraday hour breaks on x-axis)
DAILY_OR_ABOVE = {"1day", "1week", "1month"}

# Timeframes where weekend breaks must NOT be applied (labels can land on weekends)
WEEK_OR_ABOVE = {"1week", "1month"}

def aggregate_pressure(df_1min: pd.DataFrame, timeframe: str = "1hr") -> pd.DataFrame:
    """
    Take a 1-min DataFrame (already processed by add_cvd_columns) and
    return it resampled/aggregated to the given timeframe.

    timeframe: one of the keys in TIMEFRAME_RULE (e.g. "1min", "1hr", "1day").
    """
    rule = TIMEFRAME_RULE.get(timeframe, "1h")

    df_agg = df_1min.resample(rule).agg(
        open=("open",                "first"),
        high=("high",                "max"),
        low=("low",                  "min"),
        close=("close",              "last"),
        volume=("volume",            "sum"),
        buy_pressure=("buying_volume",  "sum"),
        sell_pressure=("selling_volume","sum"),
        delta_sum=("delta",          "sum"),
        cvd_end=("cvd",              "last"),   # CVD session-reset at bar's end
        cvd_all_end=("cvd_all",      "last"),   # CVD all-time at bar's end
    ).dropna(subset=["open"])

    df_agg["net_pressure"] = df_agg["buy_pressure"] - df_agg["sell_pressure"]

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

def load_from_mongo(ticker: str, timeframe: str = "i1") -> pd.DataFrame:
    """
    Load a given ticker/timeframe from MongoDB finviz_db.candles.
    """
    client = MongoClient("mongodb://localhost:27017/")
    collection = client["finviz_db"]["candles"]

    docs = list(collection.find(
        {"ticker": ticker, "timeframe": timeframe},
        {"_id": 0, "date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
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

def run_pipeline(ticker: str) -> tuple[pd.DataFrame, dict]:
    """
    Load 1-min bars from MongoDB -> compute CVD -> aggregate every timeframe.

    Returns:
        df_1min : 1-min bars + buying_volume / selling_volume / delta / cvd columns
        frames  : {"1min": df, "3min": df, ..., "1month": df}
    """
    print(f"\n{'='*50}")
    print(f"  Pipeline: {ticker}")
    print(f"{'='*50}")

    df_raw = load_from_mongo(ticker, timeframe="i1")
    if df_raw.empty:
        return pd.DataFrame(), {}

    df_1min = add_cvd_columns(df_raw)

    frames = {tf: aggregate_pressure(df_1min, tf) for tf in TIMEFRAME_RULE}

    print(f"[Pipeline] 1-min bars : {len(df_1min)}")
    for tf, df in frames.items():
        print(f"[Pipeline] {tf:>5} bars  : {len(df)}")
    print(f"[Pipeline] CVD range  : {df_1min['cvd'].min():.0f} ~ {df_1min['cvd'].max():.0f}")
    print(f"[Pipeline] Done.\n")

    return df_1min, frames


# ── Quick test when run directly
if __name__ == "__main__":
    df_1min, frames = run_pipeline("NVDA")
    if not df_1min.empty:
        print("\n[1-min sample (last 3 rows)]")
        print(df_1min[["open","close","volume","buying_volume","selling_volume","delta","cvd"]].tail(3).to_string())
        print("\n[1-hour sample (last 3 rows)]")
        print(frames["1hr"][["open","close","buy_pressure","sell_pressure","net_pressure","cvd_end"]].tail(3).to_string())
