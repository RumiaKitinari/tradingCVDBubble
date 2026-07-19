"""
tests/mock_level2_stream.py
---------------------------
Mock IBKR Level-2 (market depth) stream for UI/visualization testing while
the market-depth subscription is unavailable.

Writes snapshots to trading_cvd.level2_snapshots in the EXACT shape
ibkr/level2_collector.py produces (src='mock' instead of 'ibkr'), so the
app's L2 heatmap + support/resistance rendering can be exercised end-to-end
today and switched to the real collector with zero UI changes later.

Realism model:
  - mid price follows a mean-reverting (OU) random walk anchored at the
    ticker's latest real close from finviz_db.candles (fallback $200)
  - persistent liquidity WALLS sit below/above the anchor → they show up as
    horizontal heatmap bands and are what the S&R detector should flag;
    price gets repelled when it approaches a wall, and occasionally eats
    through one (the wall then relocates further out)
  - per-level sizes are lognormal noise; transient one-snapshot icebergs are
    sprinkled in to verify the S&R persistence filter ignores them
  - 10 levels per side at 1-cent spacing, snapshot every --interval seconds

Timestamps use the naive-ET-as-UTC epoch convention (see data_provider
.et_epoch) so snapshots line up with the candle store. Seeded history is
anchored to the ticker's LATEST 1min candle (not wall-clock now) so the
heatmap always overlaps the chart no matter when you run this.

Usage:
  python -m tests.mock_level2_stream --ticker NVDA --seed-minutes 60          # seed history only
  python -m tests.mock_level2_stream --ticker NVDA --seed-minutes 60 --live   # seed + keep streaming
  python -m tests.mock_level2_stream --ticker NVDA --wipe                     # delete previous MOCK docs only

--wipe only deletes src='mock' documents — real collector data is never touched.
"""

import argparse
import random
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pymongo import MongoClient

from level2_webapp.data_provider import (
    get_l2_collection, snapshot_doc, ensure_l2_indexes,
)

ET = ZoneInfo("America/New_York")
TICK = 0.01          # price grid
DEPTH = 20           # levels per side (TotalView-ish; deep enough to keep
                     # the planted walls inside the visible book)
BASE_SIZE = 800.0    # typical resting size per level


def latest_close(ticker: str) -> float:
    """Anchor the mock at the ticker's latest real 1min close, if any."""
    try:
        col = MongoClient("mongodb://localhost:27017/")["finviz_db"]["candles"]
        doc = col.find_one({"ticker": ticker, "timeframe": "1min"}, sort=[("date", -1)])
        if doc and doc.get("close"):
            return float(doc["close"])
    except Exception:
        pass
    return 200.0


def latest_candle_time(ticker: str):
    """End the seeded history at the last candle so heatmap ↔ chart overlap."""
    try:
        col = MongoClient("mongodb://localhost:27017/")["finviz_db"]["candles"]
        doc = col.find_one({"ticker": ticker, "timeframe": "1min"}, sort=[("date", -1)])
        if doc and doc.get("date") is not None:
            return doc["date"]  # stored ET-naive
    except Exception:
        pass
    return datetime.now(ET).replace(tzinfo=None)


class MockBook:
    """Order-book generator: OU mid + persistent walls + lognormal noise."""

    def __init__(self, ticker: str, anchor: float, rng: random.Random,
                 breakout_prob: float = 0.0005):
        self.ticker = ticker
        self.rng = rng
        self.breakout_prob = breakout_prob
        self.anchor = round(anchor, 2)
        self.mid = self.anchor
        # Walls: (offset in ticks from anchor, side). Persistent & strong —
        # these are the bands the S&R detector must find.
        self.walls = []
        for off in (-40, -25, -12):
            self.walls.append({"price": round(self.anchor + off * TICK, 2),
                               "side": "bid", "mult": rng.uniform(6.0, 14.0)})
        for off in (10, 22, 35):
            self.walls.append({"price": round(self.anchor + off * TICK, 2),
                               "side": "ask", "mult": rng.uniform(6.0, 14.0)})

    def step(self):
        rng = self.rng
        # OU mean reversion toward the anchor + noise
        drift = 0.02 * (self.anchor - self.mid)
        noise = rng.gauss(0, 2.2 * TICK)
        self.mid += drift + noise
        # Wall repulsion: approaching a wall within 4 ticks pushes price back…
        for w in self.walls:
            d = self.mid - w["price"]
            if w["side"] == "bid" and 0 < d < 4 * TICK:
                self.mid += (4 * TICK - d) * 0.5
            elif w["side"] == "ask" and 0 < -d < 4 * TICK:
                self.mid -= (4 * TICK + d) * 0.5
        # …except a rare breakout: the wall gets eaten and relocates outward.
        # (default ≈ a few relocations per 90min seed; --breakout-prob 0 keeps
        # walls perfectly static for deterministic S&R verification.)
        if rng.random() < self.breakout_prob:
            w = rng.choice(self.walls)
            jump = (1 if w["side"] == "ask" else -1) * rng.randint(3, 8) * TICK
            self.mid = w["price"] + jump
            w["price"] = round(w["price"] + jump * 3, 2)
        self.mid = round(round(self.mid / TICK) * TICK, 2)

    def follow(self, target: float):
        """Nudge the anchor (and the walls with it) toward the real price, at
        most 1 tick per step (~2 ticks/s at 0.5s cadence). Without this, the
        OU walk detaches from the real tape as it moves — every wall ends up
        on one side of the chart and the heatmap drifts out from under the
        candles. Real books straddle the real price by definition, so this is
        the realistic behavior; because walls shift gradually, S&R persistence
        accumulates exactly where the real price actually dwelled."""
        if target is None:
            return
        delta = target - self.anchor
        if abs(delta) < TICK:
            return
        mv = TICK if delta > 0 else -TICK
        self.anchor = round(self.anchor + mv, 2)
        self.mid += mv
        for w in self.walls:
            w["price"] = round(w["price"] + mv, 2)

    def _level_size(self, price: float, side: str) -> float:
        size = self.rng.lognormvariate(0, 0.6) * BASE_SIZE
        for w in self.walls:
            if w["side"] == side and abs(price - w["price"]) < TICK / 2:
                size *= w["mult"] * self.rng.uniform(0.85, 1.15)
        # Transient iceberg: huge size for a single snapshot at a random level.
        # The S&R persistence filter must NOT flag these.
        if self.rng.random() < 0.01:
            size *= self.rng.uniform(8.0, 15.0)
        return round(size, 0)

    def snapshot(self, et_dt) -> dict:
        best_bid = round(self.mid - TICK, 2)
        best_ask = round(self.mid + TICK, 2)
        bids = [{"price": round(best_bid - i * TICK, 2),
                 "size": self._level_size(round(best_bid - i * TICK, 2), "bid")}
                for i in range(DEPTH)]
        asks = [{"price": round(best_ask + i * TICK, 2),
                 "size": self._level_size(round(best_ask + i * TICK, 2), "ask")}
                for i in range(DEPTH)]
        return snapshot_doc(self.ticker, et_dt, bids, asks, src="mock")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--seed-minutes", type=int, default=60,
                    help="minutes of history to seed, ending at the latest candle (0 = none)")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between snapshots")
    ap.add_argument("--live", action="store_true", help="keep streaming from now on after seeding")
    ap.add_argument("--wipe", action="store_true", help="delete previous src='mock' docs for the ticker first")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible books)")
    ap.add_argument("--breakout-prob", type=float, default=0.0005,
                    help="per-step probability a wall gets eaten and relocates (0 = static walls)")
    args = ap.parse_args()

    ticker = args.ticker.upper()
    col = get_l2_collection()
    ensure_l2_indexes(col)

    if args.wipe:
        n = col.delete_many({"ticker": ticker, "src": "mock"}).deleted_count
        print(f"[wipe] deleted {n} previous mock snapshots for {ticker}")
        if not args.seed_minutes and not args.live:
            return

    rng = random.Random(args.seed)
    book = MockBook(ticker, latest_close(ticker), rng, breakout_prob=args.breakout_prob)
    print(f"[init] {ticker} anchor={book.anchor} walls="
          f"{[(w['side'], w['price']) for w in book.walls]}")

    if args.seed_minutes > 0:
        end = latest_candle_time(ticker)
        start = end - timedelta(minutes=args.seed_minutes)
        n_steps = int(args.seed_minutes * 60 / args.interval)
        print(f"[seed] {n_steps} snapshots {start} → {end} (ET)")
        docs, t = [], start
        for _ in range(n_steps):
            book.step()
            docs.append(book.snapshot(t))
            t += timedelta(seconds=args.interval)
            if len(docs) >= 2000:
                col.insert_many(docs, ordered=False)
                docs = []
        if docs:
            col.insert_many(docs, ordered=False)
        print(f"[seed] done ({n_steps} docs); final walls="
              f"{[(w['side'], w['price']) for w in book.walls]}")

    if args.live:
        print(f"[live] streaming every {args.interval}s — Ctrl-C to stop")
        target, n_steps = book.anchor, 0
        try:
            while True:
                if n_steps % 20 == 0:           # re-check the real price ~10s
                    target = latest_close(ticker)
                book.follow(target)
                book.step()
                now_et = datetime.now(ET).replace(tzinfo=None)
                col.insert_one(book.snapshot(now_et))
                n_steps += 1
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[live] stopped")


if __name__ == "__main__":
    main()
