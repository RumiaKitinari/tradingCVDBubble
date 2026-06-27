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
    """
    minute = df.index.hour * 60 + df.index.minute
    reg = (minute >= 570) & (minute < 960)                 # 09:30-16:00
    reg_med = df.loc[reg].groupby(lambda ix: ix.date())["volume"].median()

    flag = pd.Series(False, index=df.index)
    afternoon = df[minute >= 720]                          # >= 12:00
    for d, g in afternoon.groupby(lambda ix: ix.date()):
        if g.empty:
            continue
        med = reg_med.get(d, float("nan"))
        anchor = g["volume"].idxmax()
        if not (pd.isna(med) or g.loc[anchor, "volume"] > mult * med):
            continue
        flag.loc[anchor] = True
        gi = g.index
        for k in range(gi.get_loc(anchor) + 1, len(gi)):   # forward spill
            if g["volume"].iloc[k] > spill_mult * med:
                flag.loc[gi[k]] = True
            else:
                break
    return flag


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

    # ── Closing-auction handling (neutralize direction, keep volume) ──────────
    # The 15:59 closing cross is a single-price doji carrying ~99% of its minute
    # as one batch auction; the wick model assigns its direction from a 1-2 cent
    # open/close difference, so its buy/sell SIGN is noise that would otherwise
    # dominate CVD (it alone drives the curve, ~26% of total |delta|). We KEEP its
    # volume but NEUTRALIZE the split (buy = sell = volume/2, delta = 0) for the
    # default CVD, and keep an un-neutralized `delta_raw` so a "CVD raw (incl.
    # auction)" line stays available for comparison. See Personal Study Log §8.
    df["is_auction"]     = _flag_auction(df)
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
        cvd_all_end=("cvd_all",      "last"),   # CVD all-time (auction-neutralized)
        cvd_all_raw_end=("cvd_all_raw", "last"),# CVD all-time (includes auction)
        auction_vol=("auction_volume", "sum"),  # auction volume falling in this bar
    ).dropna(subset=["open"])

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
    print(f"[Pipeline] CVD range  : {df_1min['cvd_all'].min():.0f} ~ {df_1min['cvd_all'].max():.0f}")
    n_auc = int(df_1min["is_auction"].sum())
    auc_vol = df_1min["auction_volume"].sum()
    print(f"[Pipeline] Auctions   : {n_auc} closing-cross bars neutralized, total auction vol {auc_vol:,.0f}")
    print(f"[Pipeline] Done.\n")

    return df_1min, frames


# ── Quick test when run directly
if __name__ == "__main__":
    df_1min, frames = run_pipeline("NVDA")
    if not df_1min.empty:
        print("\n[1-min sample (last 3 rows)]")
        print(df_1min[["open","close","volume","buying_volume","selling_volume","delta","cvd_all"]].tail(3).to_string())
        print("\n[1-hour sample (last 3 rows)]")
        print(frames["1hr"][["open","close","buy_pressure","sell_pressure","net_pressure","cvd_all_end"]].tail(3).to_string())
