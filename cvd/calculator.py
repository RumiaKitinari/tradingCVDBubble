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
    Returning buy/sell volumn from one candle(OHLCV).
    """
    spread = h - l

    # spread=0 -> unable to calculate, assume buying volume == selling volume
    if spread == 0:
        return {"buying_volume": v / 2, "selling_volume": v / 2, "delta": 0.0}

    # ── Wick Calculation
    if c > o: #Bullish
        upper_wick = h - c
        lower_wick = o - l
    else: #Bearish
        upper_wick = h - o
        lower_wick = c - l

    body = spread - upper_wick - lower_wick

    # ── Calculate percentage
    pct_upper = upper_wick / spread
    pct_lower = lower_wick / spread
    pct_body  = body / spread

    # Half of a wick -> assigned equally to buying/selling volumn
    half_wicks = (pct_upper + pct_lower) / 2

    # ── Volumn Distribution
    if c > o:   # Bullish: Buying = Body + half_wick
        buying_volume  = (pct_body + half_wicks) * v
        selling_volume = half_wicks * v
    else:       # Bearish: Selling = body + half_wick
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
    1분봉 DataFrame을 받아서 아래 컬럼 추가 후 반환:
        buying_volume  - 해당 봉의 매수 거래량
        selling_volume - 해당 봉의 매도 거래량
        delta          - 봉 하나의 매수-매도 차이
        cvd            - 누적 delta (CVD: Cumulative Volume Delta)

    df에는 open/high/low/close/volume 컬럼이 있어야 함.
    date 컬럼이 있으면 자동으로 인덱스로 변환.
    """
    df = df.copy()

    # date 컬럼 → datetime 인덱스로
    if "date" in df.columns:
        df["date"] = pd.to_datetime(
            df["date"].str.replace(r'\s*(AM|PM)$', '', regex=True),
            format="%m/%d/%Y %H:%M",
            errors="coerce"
        )
        df = df.set_index("date").sort_index()

    # 봉마다 분해 적용
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

    # CVD: 매일 세션(날짜)마다 0에서 리셋
    df["cvd"] = df.groupby(df.index.date)["delta"].cumsum()

    return df


# ─────────────────────────────────────────
# 3. 1분봉 → N분봉 Buy/Sell Pressure 집계
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
    1분봉 DataFrame(add_cvd_columns 처리 완료)을 받아
    지정한 timeframe으로 집계한 DataFrame 반환.

    timeframe: "1min" | "3min" | "5min" | "15min" | "1hr"
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
        cvd_end=("cvd",              "last"),
    ).dropna(subset=["open"])

    df_agg["net_pressure"] = df_agg["buy_pressure"] - df_agg["sell_pressure"]

    return df_agg


# ─────────────────────────────────────────
# 4. MongoDB에서 데이터 불러오기
# ─────────────────────────────────────────

def load_from_mongo(ticker: str, timeframe: str = "i1") -> pd.DataFrame:
    """
    MongoDB finviz_db.candles에서 특정 ticker/timeframe 데이터 로드.
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
# 5. 전체 파이프라인 한 번에 실행
# ─────────────────────────────────────────

def run_pipeline(ticker: str) -> tuple[pd.DataFrame, dict]:
    """
    MongoDB에서 1분봉 로드 → CVD 계산 → 모든 timeframe 집계 → 반환.

    Returns:
        df_1min : 1분봉 + buying_volume / selling_volume / delta / cvd 컬럼
        frames  : {"1min": df, "3min": df, "5min": df, "15min": df, "1hr": df}
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


# ── 직접 실행 시 테스트
if __name__ == "__main__":
    df_1min, frames = run_pipeline("NVDA")
    if not df_1min.empty:
        print("\n[1-min sample (last 3 rows)]")
        print(df_1min[["open","close","volume","buying_volume","selling_volume","delta","cvd"]].tail(3).to_string())
        print("\n[1-hour sample (last 3 rows)]")
        print(frames["1hr"][["open","close","buy_pressure","sell_pressure","net_pressure","cvd_end"]].tail(3).to_string())
