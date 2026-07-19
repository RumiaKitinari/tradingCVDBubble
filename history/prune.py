"""
history/prune.py — retention pruner + Parquet cold archiver (run daily).

Retention ladder (history/schema.py TIERS): 1sec 30d, 1min 180d, 30min 2y,
1day forever. A tier's expired bars are deleted ONLY after verifying that the
next tier's rollup watermark has passed the cutoff — the TimescaleDB
"aggregate first, drop raw later" rule — so a stalled rollup worker can never
cause silent data loss.

Raw ticks/quotes are NOT just deleted: days older than RAW_RETENTION_DAYS are
first archived to per-day Parquet (zstd) files under archive/{collection}/
{ticker}/{YYYY-MM-DD}.parquet (~10x smaller than BSON). The Lee-Ready /
quote-lag reclassification research needs the raw prints, and an archive can
be replayed to rebuild every tier if the aggressor algorithm changes.

Usage:
    python -m history.prune                 # archive + prune, all tickers
    python -m history.prune --dry-run       # report what would happen
    python -m history.prune --archive-dir /path/to/archive
"""

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from history.schema import DB_NAME, ROLLUP_CHAIN, TIERS, mongo_client
from history.store import get_watermark

RAW_RETENTION_DAYS = 14
RAW_COLLECTIONS = ["raw_ticks", "raw_quotes"]
DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"

# src tier whose expiry depends on this dst tier's watermark
_NEXT_TIER = {src: dst for src, dst in ROLLUP_CHAIN}


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)


def archive_raw(client, archive_dir: Path, dry_run: bool = False) -> int:
    """Archive + delete raw tick/quote days older than RAW_RETENTION_DAYS."""
    cutoff = _now_et() - timedelta(days=RAW_RETENTION_DAYS)
    cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0

    for coll_name in RAW_COLLECTIONS:
        col = client[DB_NAME][coll_name]
        tickers = col.distinct("ticker", {"date": {"$lt": cutoff}})
        for ticker in tickers:
            # Process one whole day at a time so each parquet file is complete.
            while True:
                first = col.find_one({"ticker": ticker, "date": {"$lt": cutoff}},
                                     sort=[("date", 1)])
                if first is None:
                    break
                day = first["date"].replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day + timedelta(days=1)
                query = {"ticker": ticker, "date": {"$gte": day, "$lt": day_end}}
                docs = list(col.find(query, {"_id": 0}))
                if not docs:
                    break

                out = archive_dir / coll_name / ticker
                path = out / f"{day.date()}.parquet"
                if dry_run:
                    logging.info(f"[prune] DRY {coll_name}/{ticker}/{day.date()}: "
                                 f"{len(docs)} docs → {path}")
                else:
                    out.mkdir(parents=True, exist_ok=True)
                    pd.DataFrame(docs).to_parquet(path, compression="zstd", index=False)
                    col.delete_many(query)
                    logging.info(f"[prune] archived {coll_name}/{ticker}/{day.date()}: "
                                 f"{len(docs)} docs → {path}")
                total += len(docs)
                if dry_run:
                    break   # dry-run would loop forever on the same day
    return total


def prune_tiers(client, dry_run: bool = False) -> int:
    """Delete tier bars past retention, gated on the next tier's watermark."""
    col = client[DB_NAME]["candles"]
    now = _now_et()
    total = 0

    for tf, cfg in TIERS.items():
        days = cfg["retention_days"]
        if days is None:
            continue
        cutoff = now - timedelta(days=days)
        dst = _NEXT_TIER.get(tf)

        tickers = col.distinct("ticker", {"timeframe": tf, "date": {"$lt": cutoff}})
        for ticker in tickers:
            if dst is not None:
                wm = get_watermark(ticker, tf, dst, client=client)
                if wm is None or wm < cutoff:
                    logging.warning(
                        f"[prune] SKIP {ticker} {tf}: rollup {tf}→{dst} watermark "
                        f"({wm}) has not passed the cutoff ({cutoff.date()}) — "
                        f"run history.rollup first."
                    )
                    continue
            query = {"ticker": ticker, "timeframe": tf, "date": {"$lt": cutoff}}
            n = col.count_documents(query)
            if dry_run:
                logging.info(f"[prune] DRY {ticker} {tf}: would delete {n} bars < {cutoff.date()}")
            else:
                col.delete_many(query)
                logging.info(f"[prune] {ticker} {tf}: deleted {n} bars < {cutoff.date()}")
            total += n
    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Retention pruner + Parquet archiver")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    args = parser.parse_args()

    client = mongo_client()
    n_raw = archive_raw(client, Path(args.archive_dir), dry_run=args.dry_run)
    n_tier = prune_tiers(client, dry_run=args.dry_run)
    logging.info(f"[prune] done — {n_raw} raw docs archived, {n_tier} tier bars pruned")


if __name__ == "__main__":
    main()
