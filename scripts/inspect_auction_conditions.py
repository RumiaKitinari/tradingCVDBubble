#!/usr/bin/env python3
"""
inspect_auction_conditions.py — verify the closing-auction condition codes.

Run this AFTER a regular market close (16:00 ET) on a ticker the collector has
been recording tick-by-tick (e.g. NVDA). It pulls the bars around the close and
prints their stored `special_conditions`, so we can confirm empirically which
token IBKR stamps on the closing cross — and whether the codes hard-wired in
cvd.calculator._AUCTION_CONDITION_CODES actually match.

Usage:
    python -m scripts.inspect_auction_conditions --ticker NVDA
    python -m scripts.inspect_auction_conditions --ticker NVDA --date 2026-07-27

Reads finviz_db.candles (where tick_collector writes 1-second bars). No writes.
"""
import argparse
from collections import Counter
from datetime import datetime

from pymongo import MongoClient

from ibkr.tick_collector import MONGO_URI, DB_NAME
from cvd.calculator import _AUCTION_CONDITION_CODES, _has_auction_condition


def _tokens(cond: str) -> set[str]:
    if not isinstance(cond, str):
        return set()
    return {c.strip() for part in cond.split(",") for c in part.split() if c.strip()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--date", help="YYYY-MM-DD (ET). Default: the latest date with data.")
    ap.add_argument("--window", default="15:55-16:05",
                    help="closing window HH:MM-HH:MM to inspect (default 15:55-16:05)")
    args = ap.parse_args()

    col = MongoClient(MONGO_URI)[DB_NAME]["candles"]
    tkr = args.ticker.upper()

    # Resolve the day to inspect.
    if args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        last = col.find_one({"ticker": tkr, "special_conditions": {"$exists": True}},
                            sort=[("date", -1)])
        if not last:
            print(f"No condition-tagged bars for {tkr}. Is the collector running?")
            return
        day = last["date"].date()

    (h0, m0), (h1, m1) = (t.split(":") for t in args.window.split("-"))
    lo = datetime(day.year, day.month, day.day, int(h0), int(m0))
    hi = datetime(day.year, day.month, day.day, int(h1), int(m1), 59)

    print(f"=== {tkr}  {day}  closing window {args.window} ET ===")
    print(f"hard-wired auction codes: {sorted(_AUCTION_CONDITION_CODES) or '(empty)'}\n")

    cur = list(col.find({"ticker": tkr, "date": {"$gte": lo, "$lte": hi}},
                        {"_id": 0, "date": 1, "volume": 1, "special_conditions": 1}
                        ).sort("date", 1))
    if not cur:
        print("No bars in that window. Market may not have closed yet, or no capture.")
        return

    # Highest-volume bars are the auction candidates.
    cur.sort(key=lambda d: d.get("volume", 0), reverse=True)
    print("Top 8 bars by volume in the window (auction should be the largest):")
    for d in cur[:8]:
        cond = d.get("special_conditions", "")
        hit = "  <-- MATCHES hard-wired codes" if _has_auction_condition(cond) else ""
        print(f"  {d['date']:%H:%M:%S}  vol={d.get('volume',0):>12,.0f}  cond={cond!r}{hit}")

    # Distinct tokens seen in the closing window, ranked.
    tok = Counter()
    for d in cur:
        tok.update(_tokens(d.get("special_conditions", "")))
    print("\nDistinct condition tokens in the closing window (count):")
    for t, n in tok.most_common():
        flag = "  [hard-wired auction]" if t in _AUCTION_CONDITION_CODES else ""
        print(f"  {t!r:>6}  x{n}{flag}")

    matched = sum(1 for d in cur if _has_auction_condition(d.get("special_conditions", "")))
    print(f"\n{matched}/{len(cur)} bars in the window matched the hard-wired codes.")
    if matched == 0:
        print("→ No match. Inspect the largest bar's cond above and add its "
              "closing token to _AUCTION_CONDITION_CODES in cvd/calculator.py.")


if __name__ == "__main__":
    main()
