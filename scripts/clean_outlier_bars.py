#!/usr/bin/env python
"""
scripts/clean_outlier_bars.py
-----------------------------
Retroactively clean NBBO-outlier prints out of already-stored ibkr_tick 1sec
bars, then rebuild the higher timeframes.

The live fix lives in ibkr/tick_collector.py (_price_clean + OHLC-from-clean).
This script applies the same NBBO price-band rule to HISTORICAL 1sec bars that
were built before the fix:

  1. Flag 1sec bars whose high/low deviates more than --band from a local
     rolling median of close (catches both wick-blowout bars AND seconds that
     are ENTIRELY one bad print, where intrabar range is 0).
  2. Re-derive O/H/L/C from raw_ticks, dropping any trade that printed more than
     --band beyond that trade's own stored NBBO (bid/ask). If EVERY tick in the
     second is an outlier, the bar is a phantom (a lone bad print) and is deleted.
  3. Rewind the rollup watermarks for the affected span and re-roll 1min/30min/1day
     so the cleaned 1sec bars propagate up (plain rollup only rebuilds the tail).

Volume/delta on surviving bars are left untouched (raw stays raw; a bad print is
tiny size). Usage:

  python -m scripts.clean_outlier_bars --ticker NVDA            # apply + rollup
  python -m scripts.clean_outlier_bars --ticker NVDA --dry-run  # report only
  python -m scripts.clean_outlier_bars --ticker NVDA --band 0.02
"""

import argparse
import os
from datetime import datetime, timedelta

import pandas as pd
from pymongo import MongoClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"

WINDOW = 181  # rolling-median window in 1sec bars (~3 min), centered


def clean_price(price, bid, ask, band):
    """Mirror of TickCollector._price_clean: keep the print unless it lies more
    than `band` beyond its own NBBO. Unknown NBBO -> keep (can't judge)."""
    if not bid or not ask or bid <= 0 or ask <= 0 or bid > ask:
        return True
    return bid * (1.0 - band) <= price <= ask * (1.0 + band)


def main():
    ap = argparse.ArgumentParser(description="Clean NBBO-outlier prints from stored 1sec bars")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--band", type=float, default=0.015,
                    help="fraction beyond NBBO / local median that counts as outlier (default 0.015)")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--no-rollup", action="store_true", help="skip the rollup step")
    args = ap.parse_args()
    tk = args.ticker.upper()
    band = args.band

    m = MongoClient(MONGO_URI)
    candles = m[DB_NAME]["candles"]
    raw = m[DB_NAME]["raw_ticks"]

    rows = list(candles.find(
        {"ticker": tk, "timeframe": "1sec", "source": "ibkr_tick"},
        {"date": 1, "open": 1, "high": 1, "low": 1, "close": 1, "_id": 0},
    ).sort("date", 1))
    if not rows:
        print(f"{tk}: no ibkr_tick 1sec bars"); return
    df = pd.DataFrame(rows)
    # Local robust reference: centered rolling median of close. Only a genuine
    # spike deviates from it, so trends are never flagged.
    med = df["close"].rolling(WINDOW, center=True, min_periods=5).median()
    med = med.bfill().ffill()
    hi_bad = df["high"] > med * (1 + band)
    lo_bad = df["low"] < med * (1 - band)
    cand = df[hi_bad | lo_bad]
    print(f"{tk}: {len(cand)} suspicious 1sec bar(s) (dev > {band:.1%} from local median)")

    updated = deleted = 0
    dirty = []  # (datetime) of every touched bar, to bound the rollup rewind
    for _, b in cand.iterrows():
        sec = b["date"].to_pydatetime() if hasattr(b["date"], "to_pydatetime") else b["date"]
        ticks = list(raw.find(
            {"ticker": tk, "date": {"$gte": sec, "$lt": sec + timedelta(seconds=1)}},
            {"date": 1, "price": 1, "bid": 1, "ask": 1, "_id": 0},
        ).sort("date", 1))
        if not ticks:
            continue  # no raw detail -> leave as-is
        kept = [t for t in ticks if clean_price(t["price"], t.get("bid"), t.get("ask"), band)]
        if not kept:
            # Whole second is a lone bad print -> phantom bar, delete it.
            print(f"  {sec}  DELETE phantom bar (H {b['high']:.2f} L {b['low']:.2f}, all {len(ticks)} print(s) outlier)")
            deleted += 1; dirty.append(sec)
            if not args.dry_run:
                candles.delete_one({"ticker": tk, "timeframe": "1sec", "source": "ibkr_tick", "date": sec})
            continue
        px = [t["price"] for t in kept]
        new = {"open": px[0], "high": max(px), "low": min(px), "close": px[-1]}
        if all(round(new[k], 4) == round(b[k], 4) for k in ("open", "high", "low", "close")):
            continue
        print(f"  {sec}  H {b['high']:.2f}->{new['high']:.2f}  L {b['low']:.2f}->{new['low']:.2f}"
              f"  (dropped {len(ticks) - len(kept)} print(s))")
        updated += 1; dirty.append(sec)
        if not args.dry_run:
            candles.update_one(
                {"ticker": tk, "timeframe": "1sec", "source": "ibkr_tick", "date": sec},
                {"$set": new},
            )

    print(f"\n{tk}: {updated} bar(s) cleaned, {deleted} phantom bar(s) deleted"
          + (" (dry-run)" if args.dry_run else ""))

    if dirty and not args.dry_run and not args.no_rollup:
        _rebuild(tk, min(dirty), m)
        print("Done. Restart 8050 / hard-refresh to see cleaned bars.")


def _rebuild(ticker, since, client):
    """Rewind rollup watermarks to `since` and re-roll the whole chain so cleaned
    1sec bars propagate into 1min/30min/1day (plain rollup only touches the tail)."""
    import sys
    sys.path.insert(0, REPO_ROOT)
    from history.schema import ROLLUP_CHAIN
    from history.store import set_watermark
    from history.rollup import rollup_pair

    # Rewind to the START OF THE EARLIEST AFFECTED DAY, not just the earliest
    # dirty bar: a cleaned bar's minute/30min bucket may begin earlier, and a
    # prior partial run may have left older minutes un-rebuilt. Whole-day rebuild
    # is idempotent and cheap.
    rewind = datetime(since.year, since.month, since.day)
    print(f"Rewinding rollup watermarks to {rewind} and rebuilding chain...")
    for src, dst in ROLLUP_CHAIN:
        set_watermark(ticker, src, dst, rewind, client=client)
        n = rollup_pair(ticker, src, dst, client=client)
        print(f"  {src}->{dst}: rebuilt {n} bar(s)")


if __name__ == "__main__":
    main()
