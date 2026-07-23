"""
scripts/verify_full_tape.py
---------------------------
Ground-truth check: pull the FULL consolidated tape via reqHistoricalTicks
(TRADES + BID_ASK) for a bounded window, classify it with the SAME aggressor
code the live collector uses, and compare against what we actually stored in
raw_ticks (the free non-consolidated real-time feed).

Answers three things the professor's goal depends on:
  1. VOLUME  — what fraction of the real tape did our live feed capture?
  2. CVD     — does the direction (buy/sell trend) of full-tape CVD match ours,
               or is our non-consolidated sample biased?
  3. PRICE   — does full-tape CVD track price change better than ours?

Usage:
    python scripts/verify_full_tape.py --ticker NVDA --date 2026-07-14 \
        --start 14:00 --end 15:00 --port 7497 --client-id 55
"""
import argparse
import sys, os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from ib_async import IB, Stock
from cvd.aggressor import classify_vectorized

ET = ZoneInfo("America/New_York")


def _aware(t):
    return t if t.tzinfo else t.replace(tzinfo=ET)


def fetch_ticks(ib, contract, start_dt, end_dt, what):
    """Paginate reqHistoricalTicks forward from start_dt to end_dt.

    reqHistoricalTicks cursors are SECOND-granularity, so a naive
    "advance cursor to last tick time" both (a) re-reads every tick in the
    boundary second on the next call (duplicates) and (b) cannot advance past a
    second that holds >1000 ticks. We fix both:
      - boundary dedup: skip leading ticks of the next batch that repeat the
        exact ticks already consumed from the previous batch's last second;
      - forced progress: if the batch never left one second, bump the cursor by
        1s (may drop the >1000th tick of a mega-second — rare, negligible).
    """
    from datetime import timedelta
    out = []
    cursor = start_dt
    bnd_time, bnd_count = None, 0        # previous batch's last-second identity
    for _ in range(2000):               # hard guard
        if cursor >= end_dt:
            break
        batch = ib.reqHistoricalTicks(contract, cursor, end_dt, 1000, what, useRth=False)
        if not batch:
            break
        # drop the leading ticks that repeat the previous boundary second
        i0 = 0
        if bnd_time is not None:
            seen = 0
            while i0 < len(batch) and _aware(batch[i0].time) == bnd_time and seen < bnd_count:
                i0 += 1; seen += 1
        new = [t for t in batch[i0:] if _aware(t.time) < end_dt]
        out.extend(new)

        last_time = _aware(batch[-1].time)
        if last_time >= end_dt:
            break
        if last_time <= cursor:         # stuck inside one >1000-tick second
            cursor = last_time + timedelta(seconds=1)
            bnd_time, bnd_count = None, 0        # boundary jumped; no dedup carry
            continue
        bnd_time = last_time
        bnd_count = sum(1 for t in batch if _aware(t.time) == last_time)
        cursor = last_time
        if len(batch) < 1000:           # a full page ends the window's data
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--date", default="2026-07-14")
    ap.add_argument("--start", default="14:00")
    ap.add_argument("--end", default="15:00")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=55)
    args = ap.parse_args()

    d = datetime.strptime(args.date, "%Y-%m-%d").date()
    sh, sm = map(int, args.start.split(":"))
    eh, em = map(int, args.end.split(":"))
    start_dt = datetime.combine(d, dtime(sh, sm), tzinfo=ET)
    end_dt = datetime.combine(d, dtime(eh, em), tzinfo=ET)

    ib = IB()
    ib.connect("127.0.0.1", args.port, clientId=args.client_id, timeout=20)
    contract = Stock(args.ticker, "SMART", "USD")
    ib.qualifyContracts(contract)

    print(f"Pulling FULL tape {args.ticker} {args.date} "
          f"{args.start}-{args.end} ET ...")
    trades = fetch_ticks(ib, contract, start_dt, end_dt, "TRADES")
    quotes = fetch_ticks(ib, contract, start_dt, end_dt, "BID_ASK")
    ib.disconnect()
    print(f"  full tape: {len(trades)} trades, {len(quotes)} quotes")

    if not trades:
        print("No trades returned — subscription may not cover historical ticks.")
        return

    # ── Full-tape DataFrame, classified with the SAME code ────────────────────
    # reqHistoricalTicks returns UTC tz-aware times; convert to ET wall-clock
    # (naive) so they line up with our ET-naive raw_ticks in Mongo.
    def et_naive(t):
        return (t.astimezone(ET).replace(tzinfo=None) if t.tzinfo
                else t.replace(tzinfo=ET).astimezone(ET).replace(tzinfo=None))

    tf = pd.DataFrame({
        "date": [et_naive(t.time) for t in trades],
        "price": [float(t.price) for t in trades],
        "size": [float(t.size) for t in trades],
    }).sort_values("date").reset_index(drop=True)

    if quotes:
        qf = pd.DataFrame({
            "date": [et_naive(q.time) for q in quotes],
            "bid": [float(q.priceBid) for q in quotes],
            "ask": [float(q.priceAsk) for q in quotes],
        }).sort_values("date").reset_index(drop=True)
        m = pd.merge_asof(tf, qf, on="date", direction="backward")
        bid = m["bid"].to_numpy(); ask = m["ask"].to_numpy()
    else:
        bid = np.full(len(tf), np.nan); ask = np.full(len(tf), np.nan)

    delta_full, _, _ = classify_vectorized(
        tf["price"].to_numpy(), tf["size"].to_numpy(), bid, ask)
    tf["delta"] = delta_full

    # ── Our stored non-consolidated ticks, same window ────────────────────────
    c = MongoClient("mongodb://localhost:27017/")["finviz_db"]["raw_ticks"]
    lo = datetime.combine(d, dtime(sh, sm)); hi = datetime.combine(d, dtime(eh, em))
    ours = pd.DataFrame(list(c.find(
        {"ticker": args.ticker, "date": {"$gte": lo, "$lt": hi}},
        {"_id": 0, "date": 1, "price": 1, "size": 1, "delta": 1})))

    def cvd_stats(df, label):
        vol = df["size"].sum()
        net = df["delta"].sum()
        buy = df.loc[df["delta"] > 0, "delta"].sum()
        sell = -df.loc[df["delta"] < 0, "delta"].sum()
        print(f"  {label:14} trades={len(df):>7,}  vol={vol:>12,.0f}  "
              f"buy={buy:>11,.0f}  sell={sell:>11,.0f}  net_delta={net:>+12,.0f}")
        return vol, net

    print("\n=== VOLUME & DIRECTION ===")
    vf, nf = cvd_stats(tf, "FULL tape")
    vo, no = cvd_stats(ours, "OUR feed")
    print(f"\n  volume capture : {vo/vf*100:5.1f}%   "
          f"(our feed saw {vo:,.0f} of {vf:,.0f} shares)")
    print(f"  net-delta sign : full={'BUY' if nf>0 else 'SELL'}  "
          f"our={'BUY' if no>0 else 'SELL'}  "
          f"{'AGREE' if np.sign(nf)==np.sign(no) else 'DISAGREE'}")

    # ── Per-minute delta + price-change correlation ───────────────────────────
    def per_min(df):
        g = df.copy()
        g["min"] = pd.to_datetime(g["date"]).dt.floor("min")
        agg = g.groupby("min").agg(delta=("delta", "sum"),
                                   close=("price", "last")).sort_index()
        agg["dP"] = agg["close"].diff()
        return agg

    # cache tapes for offline iteration (temp dir — not the repo)
    import tempfile
    tag = f"{args.ticker}_{args.date}_{args.start.replace(':','')}"
    cache = os.path.join(tempfile.gettempdir(), f"_tape_{tag}.parquet")
    tf.to_parquet(cache)
    ours.to_parquet(cache.replace("_tape_", "_ours_"))

    mf, mo = per_min(tf), per_min(ours)
    print(f"  [debug] full-tape minutes={mo.index.nunique() if False else mf.index.nunique()}  "
          f"our minutes={mo.index.nunique()}  "
          f"tf span={tf['date'].min()}..{tf['date'].max()}")
    j = mf[["delta", "dP"]].join(mo[["delta"]], rsuffix="_our", how="inner").dropna()
    if len(j) >= 3:
        print("\n=== PER-MINUTE (n={} min) ===".format(len(j)))
        print(f"  corr(full_delta , our_delta) : {j['delta'].corr(j['delta_our']):+.3f}"
              "   <- how well our sample mirrors the true flow")
        print(f"  corr(full_delta , dPrice)    : {j['delta'].corr(j['dP']):+.3f}"
              "   <- does full-tape CVD track price change")
        print(f"  corr(our_delta  , dPrice)    : {j['delta_our'].corr(j['dP']):+.3f}"
              "   <- does OUR CVD track price change")
        hf = (np.sign(j["delta"]) == np.sign(j["dP"])).mean() * 100
        ho = (np.sign(j["delta_our"]) == np.sign(j["dP"])).mean() * 100
        print(f"  dir hit%  full={hf:.0f}%   our={ho:.0f}%")


if __name__ == "__main__":
    main()
