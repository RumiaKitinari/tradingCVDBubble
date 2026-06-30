"""
alpaca_mkt/alpaca_check_columns.py — Alpaca API 실제 응답 컬럼명 확인.

trade / quote 응답의 .df 컬럼명과 샘플 데이터를 출력한다.
alpaca_backfill.py에서 bid_price/ask_price 컬럼명이 실제와 맞는지 검증용.

Usage:
    python -m alpaca_mkt.alpaca_check_columns
"""

from datetime import datetime, timezone, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest
from alpaca.data.enums import DataFeed
from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY

TICKER = "NVDA"

# 어제 정규장 30분 구간 (어제 14:00~14:30 UTC = 10:00~10:30 ET)
end   = datetime.now(timezone.utc).replace(hour=19, minute=0, second=0, microsecond=0) - timedelta(days=1)
start = end - timedelta(minutes=30)

client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

print(f"조회 구간: {start} ~ {end}\n")

# ── Trade 컬럼 확인 ──────────────────────────────────────────────────────────
print("=" * 50)
print("TRADES")
print("=" * 50)
try:
    trade_req = StockTradesRequest(
        symbol_or_symbols=TICKER, start=start, end=end, feed=DataFeed.IEX
    )
    trade_result = client.get_stock_trades(trade_req)
    trade_df = trade_result.df
    print(f"컬럼: {trade_df.columns.tolist()}")
    print(f"인덱스: {trade_df.index.names}")
    print(f"총 {len(trade_df)} 건")
    print(trade_df.head(3).to_string())
except Exception as e:
    print(f"오류: {e}")

print()

# ── Quote 컬럼 확인 ──────────────────────────────────────────────────────────
print("=" * 50)
print("QUOTES")
print("=" * 50)
try:
    quote_req = StockQuotesRequest(
        symbol_or_symbols=TICKER, start=start, end=end, feed=DataFeed.IEX
    )
    quote_result = client.get_stock_quotes(quote_req)
    quote_df = quote_result.df
    print(f"컬럼: {quote_df.columns.tolist()}")
    print(f"인덱스: {quote_df.index.names}")
    print(f"총 {len(quote_df)} 건")
    print(quote_df.head(3).to_string())
except Exception as e:
    print(f"오류: {e}")
