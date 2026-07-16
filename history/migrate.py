"""
history/migrate.py — P1 schema normalization (one-shot, idempotent).

1. Ensure indexes + meta collections.
2. Convert legacy FinViz daily docs (timeframe='d', date as string
   "MM/DD/YYYY") into the canonical '1day' tier: real BSON datetimes,
   source='finviz', quality='bvc', write-time BVC buy/sell/delta.
   Legacy 'd' docs are removed after conversion (the FinViz daily
   backfiller can always re-fetch them).

Usage:
    python -m history.migrate
"""

import logging

import pandas as pd

from history.bvc import bvc_split
from history.schema import DB_NAME, ensure_indexes, mongo_client
from history.store import upsert_bars


def migrate_legacy_daily(client=None) -> int:
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]

    tickers = col.distinct("ticker", {"timeframe": "d"})
    total = 0
    for ticker in tickers:
        docs = list(col.find({"ticker": ticker, "timeframe": "d"}, {"_id": 0}))
        if not docs:
            continue
        df = pd.DataFrame(docs)
        df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").set_index("date")

        est = bvc_split(df["close"], df["volume"])
        out = []
        for ts, row in df.iterrows():
            out.append({
                "ticker": ticker,
                "timeframe": "1day",
                "date": ts.to_pydatetime(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]),
                "buying_volume": float(est.loc[ts, "buying_volume"]),
                "selling_volume": float(est.loc[ts, "selling_volume"]),
                "delta": float(est.loc[ts, "delta"]),
                "source": "finviz",
                "quality": "bvc",
            })
        n = upsert_bars(out, client=client)
        col.delete_many({"ticker": ticker, "timeframe": "d"})
        logging.info(f"[migrate] {ticker}: {n} legacy daily docs → 1day tier")
        total += n
    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = mongo_client()
    ensure_indexes(client)
    n = migrate_legacy_daily(client)
    logging.info(f"[migrate] done — {n} docs migrated")


if __name__ == "__main__":
    main()
