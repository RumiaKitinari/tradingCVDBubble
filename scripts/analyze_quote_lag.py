"""
scripts/analyze_quote_lag.py
----------------------------
Quote-lag diagnostics for IBKR tick classification.

Requires raw_ticks docs collected AFTER the tick_collector instrumentation
(fields: bid, ask, quote_age_ms, cls). Older docs lack these and are skipped.

Answers:
  1. How stale is the NBBO when we classify a trade? (quote_age_ms distribution)
  2. What fraction of trades are classified by quote vs tick-rule vs zero-tick?
  3. Is the mid-size (100-1000 sh) sell skew concentrated in STALE-quote trades?
     -> if the sell skew disappears once we require a fresh quote, the skew is a
        quote-lag misclassification artifact, not real order flow.

Usage:  python scripts/analyze_quote_lag.py [TICKER]
"""
import sys
import numpy as np
import pandas as pd
from pymongo import MongoClient

pd.set_option("display.width", 170)
TICKER = sys.argv[1] if len(sys.argv) > 1 else "NVDA"

c = MongoClient("mongodb://localhost:27017/")
docs = list(c["finviz_db"]["raw_ticks"].find(
    {"ticker": TICKER, "cls": {"$exists": True}},   # instrumented docs only
    {"_id": 0, "date": 1, "price": 1, "size": 1, "delta": 1,
     "bid": 1, "ask": 1, "quote_age_ms": 1, "cls": 1},
))
if not docs:
    print("No instrumented ticks yet. Re-run tick_collector to collect docs with "
          "bid/ask/quote_age_ms/cls, then run this script.")
    sys.exit(0)

t = pd.DataFrame(docs)
t["date"] = pd.to_datetime(t["date"])
t = t.set_index("date").sort_index()
m = t.index.hour * 60 + t.index.minute
t = t[(m >= 570) & (m < 960)]            # regular hours
print(f"{TICKER}: {len(t)} instrumented reg-hours ticks  "
      f"{t.index.min()} ~ {t.index.max()}\n")

# 1. classification path breakdown
print("Classification path:")
print((t["cls"].value_counts(normalize=True) * 100).round(1).to_string())

# 2. quote-age distribution
qa = t["quote_age_ms"].dropna()
print("\nquote_age_ms (NBBO staleness at classify time):")
print(f"  p50={qa.quantile(.5):.0f}  p90={qa.quantile(.9):.0f}  "
      f"p99={qa.quantile(.99):.0f}  max={qa.max():.0f}")
print(f"  % trades with quote older than 100ms: {(qa > 100).mean()*100:.1f}%")
print(f"  % trades with quote older than 500ms: {(qa > 500).mean()*100:.1f}%")

# 3. mid-size sell skew vs quote freshness
mid = t[(t["size"] >= 100) & (t["size"] < 1000)].copy()
print(f"\nMid-size trades (100-1000 sh): n={len(mid)}")
for lbl, sub in [("ALL", mid),
                 ("FRESH quote (<100ms)", mid[mid["quote_age_ms"] < 100]),
                 ("STALE quote (>=100ms)", mid[mid["quote_age_ms"] >= 100]),
                 ("quote-classified only", mid[mid["cls"] == "quote"])]:
    if len(sub) == 0:
        continue
    buy_pct = (np.sign(sub["delta"]) > 0).mean() * 100
    print(f"  {lbl:24}: n={len(sub):>7}  buy%={buy_pct:4.1f}  net_delta={sub['delta'].sum():>12,.0f}")

print("\nInterpretation: if buy% jumps toward ~50 (or higher) once you restrict to "
      "FRESH / quote-classified trades, the sell skew is a quote-lag artifact and the "
      "fix is quote/trade timestamp alignment. If it stays ~42%, the skew is real "
      "order flow (institutional distribution) and CVD is behaving correctly.")
