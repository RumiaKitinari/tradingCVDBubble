"""
history/store.py
----------------
Quality-guarded upserts into the candles tiers + rollup/backfill watermarks.

The one rule that keeps mixed-source tiers honest: a bar whose buy/sell split
came from real ticks (quality='tick'/'mixed') must never be overwritten by an
estimate (bvc/wick/neutral). upsert_bars() enforces it by checking existing
qualities for the affected keys before writing.
"""

import logging
from datetime import datetime

from pymongo import MongoClient, UpdateOne

from history.schema import DB_NAME, QUALITY_RANK, ensure_indexes, mongo_client, quality_of


def upsert_bars(docs: list[dict], client: MongoClient | None = None) -> int:
    """
    Bulk-upsert tier documents (each must carry ticker/timeframe/date/quality).
    A doc only replaces an existing one when its quality rank is >= the
    existing rank. Returns number of docs written.
    """
    if not docs:
        return 0
    client = client or mongo_client()
    col = client[DB_NAME]["candles"]

    # Fetch existing qualities for the affected keys in one query per (ticker, tf).
    # Docs written before the quality field existed (live tick_collector bars,
    # legacy backfills) infer their rank from the source tag, so real tick bars
    # are protected even without an explicit quality.
    keys = {(d["ticker"], d["timeframe"]) for d in docs}
    existing: dict[tuple, str] = {}
    for ticker, tf in keys:
        dates = [d["date"] for d in docs if d["ticker"] == ticker and d["timeframe"] == tf]
        cur = col.find(
            {"ticker": ticker, "timeframe": tf, "date": {"$in": dates}},
            {"_id": 0, "date": 1, "quality": 1, "source": 1},
        )
        for row in cur:
            q = row.get("quality") or quality_of(row.get("source", ""))
            existing[(ticker, tf, row["date"])] = q

    ops, skipped = [], 0
    for d in docs:
        prev_q = existing.get((d["ticker"], d["timeframe"], d["date"]))
        if prev_q is not None and QUALITY_RANK.get(d.get("quality", "bvc"), 0) < QUALITY_RANK.get(prev_q, 0):
            skipped += 1
            continue
        # Any rewrite resets the consolidated-volume scaling audit fields
        # (history.rollup.scale_tick_volume): the doc's volume/buy/sell are now
        # raw again, and the scale step re-derives them on its next pass.
        ops.append(UpdateOne(
            {"ticker": d["ticker"], "timeframe": d["timeframe"], "date": d["date"]},
            {"$set": d,
             "$unset": {"vol_scaled": "", "scale_factor": "", "volume_tick": "",
                        "buying_volume_tick": "", "selling_volume_tick": "",
                        "delta_tick": "", "delta_wick_tick": ""}},
            upsert=True,
        ))

    if ops:
        col.bulk_write(ops, ordered=False)
    if skipped:
        logging.info(f"[store] skipped {skipped} docs protected by higher quality")
    return len(ops)


# ── Watermarks ────────────────────────────────────────────────────────────────

def get_watermark(ticker: str, src: str, dst: str, client: MongoClient | None = None) -> datetime | None:
    client = client or mongo_client()
    doc = client[DB_NAME]["rollup_meta"].find_one({"ticker": ticker, "src": src, "dst": dst})
    return doc["watermark"] if doc else None


def set_watermark(ticker: str, src: str, dst: str, wm: datetime, client: MongoClient | None = None) -> None:
    client = client or mongo_client()
    client[DB_NAME]["rollup_meta"].update_one(
        {"ticker": ticker, "src": src, "dst": dst},
        {"$set": {"watermark": wm}},
        upsert=True,
    )


def get_backfill_coverage(ticker: str, tf: str, client: MongoClient | None = None) -> dict | None:
    """Covered range {start, end} already backfilled for (ticker, tf)."""
    client = client or mongo_client()
    return client[DB_NAME]["backfill_meta"].find_one({"ticker": ticker, "timeframe": tf})


def set_backfill_coverage(ticker: str, tf: str, start: datetime, end: datetime,
                          client: MongoClient | None = None) -> None:
    """Extend the covered range (union with any existing coverage)."""
    client = client or mongo_client()
    col = client[DB_NAME]["backfill_meta"]
    prev = col.find_one({"ticker": ticker, "timeframe": tf})
    if prev:
        start = min(start, prev["start"])
        end = max(end, prev["end"])
    col.update_one(
        {"ticker": ticker, "timeframe": tf},
        {"$set": {"start": start, "end": end}},
        upsert=True,
    )


if __name__ == "__main__":
    ensure_indexes()
    print("indexes ensured")
