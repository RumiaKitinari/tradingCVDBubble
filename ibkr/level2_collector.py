"""
ibkr/level2_collector.py
------------------------
IBKR Level-2 (market depth / DOM) collector.

Polls reqMktDepth books and stores a snapshot per ticker every --interval
seconds into trading_cvd.level2_snapshots, in the exact document shape the
mock stream (tests/mock_level2_stream.py) produces — the heatmap/S&R code
cannot tell them apart except by the src field ('ibkr' vs 'mock').

Snapshot timestamps use the naive-ET-as-UTC epoch convention shared with the
candle store (see level2_webapp.data_provider.et_epoch) — a previous version
of this file wrote real UTC epochs (time.time()), which land 4-5 hours off
the candles and silently break the heatmap time matching.

REQUIREMENTS
  - A market-depth subscription on the IBKR account (e.g. NASDAQ TotalView).
    Without it reqMktDepth fails (error 309 / "not subscribed"); the collector
    logs the failure per ticker and keeps running the others.
  - IB Gateway / TWS on --port (default 7497).

NOT yet run against a live gateway (written while the account was locked);
the connection/reconnect scaffolding mirrors ibkr/tick_collector.py which is
battle-tested. First live run: start with one ticker and watch the log for
"Subscribed depth".

clientId 63 (taken: research 40-42, user backfill 11, dynamic 60/61, one-off 62).

Usage:
  python -m ibkr.level2_collector --tickers NVDA --depth 10
  python -m ibkr.level2_collector --tickers NVDA SOFI --interval 0.5 --no-smart
"""

import argparse
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ib_async import IB, Stock

from level2_webapp.data_provider import (
    get_l2_collection, snapshot_doc, ensure_l2_indexes,
)

ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("level2_collector")


class Level2Collector:
    def __init__(self, tickers: list[str], port: int = 7497, client_id: int = 63,
                 depth: int = 10, interval: float = 0.5, smart: bool = True,
                 rth_only: bool = False):
        self.symbols = [t.upper() for t in tickers]
        self.port = port
        self.client_id = client_id
        self.depth = depth
        self.interval = interval
        self.smart = smart          # isSmartDepth=True aggregates across exchanges
        self.rth_only = rth_only    # collect 09:30-16:00 ET only (Eric-style)
        self.ib = IB()
        self.books = {}             # symbol -> ib_async Ticker with domBids/domAsks
        self.col = get_l2_collection()
        ensure_l2_indexes(self.col)
        self._batch = []            # buffered snapshot docs
        self._last_flush = 0.0

    async def _connect_and_subscribe(self):
        await self.ib.connectAsync("127.0.0.1", self.port, clientId=self.client_id)
        log.info(f"Connected (clientId={self.client_id})")
        self.books = {}
        for sym in self.symbols:
            try:
                contract = Stock(sym, "SMART", "USD")
                await self.ib.qualifyContractsAsync(contract)
                self.books[sym] = self.ib.reqMktDepth(
                    contract, numRows=self.depth, isSmartDepth=self.smart)
                log.info(f"Subscribed depth: {sym} (rows={self.depth}, smart={self.smart})")
            except Exception as e:
                # Typical without a depth subscription: error 309/2152.
                log.error(f"Depth subscribe failed for {sym}: {e} — skipping this ticker")
        if not self.books:
            raise RuntimeError("No depth subscription succeeded — check market-data subscriptions")

    @staticmethod
    def _side(levels) -> list:
        out = []
        for lvl in levels or []:
            try:
                price, size = float(lvl.price), float(lvl.size)
            except (TypeError, ValueError):
                continue
            if price > 0 and size > 0:
                out.append({"price": price, "size": size})
        return out

    def _snapshot_all(self) -> int:
        now_et = datetime.now(ET).replace(tzinfo=None)
        if self.rth_only and not (9 * 60 + 30 <= now_et.hour * 60 + now_et.minute < 16 * 60):
            return 0
        n = 0
        for sym, book in self.books.items():
            bids = self._side(getattr(book, "domBids", None))
            asks = self._side(getattr(book, "domAsks", None))
            if not bids and not asks:
                continue   # book not populated yet (or outside hours)
            self._batch.append(snapshot_doc(sym, now_et, bids, asks, src="ibkr"))
            n += 1
        # Batch inserts (Eric-style): one Mongo round-trip per ~5s / 20 docs
        # instead of per snapshot.
        import time as _time
        now = _time.monotonic()
        if self._batch and (len(self._batch) >= 20 or now - self._last_flush >= 5.0):
            self.col.insert_many(self._batch, ordered=False)
            self._batch = []
            self._last_flush = now
        return n

    async def run(self):
        n_written = 0
        while True:
            try:
                await self._connect_and_subscribe()
                last_log = 0.0
                while self.ib.isConnected():
                    await asyncio.sleep(self.interval)
                    n_written += self._snapshot_all()
                    now = asyncio.get_event_loop().time()
                    if now - last_log > 60:
                        log.info(f"snapshots written total: {n_written}")
                        last_log = now
                log.warning("Disconnected — reconnecting in 10s")
            except (KeyboardInterrupt, asyncio.CancelledError):
                log.info("Stopping")
                break
            except Exception as e:
                log.error(f"Collector error: {e} — retrying in 10s")
            finally:
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(10)


def main():
    ap = argparse.ArgumentParser(description="IBKR Level-2 depth snapshot collector")
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=63)
    ap.add_argument("--depth", type=int, default=10, help="book rows per side")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between snapshots")
    ap.add_argument("--no-smart", action="store_true",
                    help="single-exchange depth instead of SMART aggregated")
    ap.add_argument("--rth-only", action="store_true",
                    help="collect regular trading hours (09:30-16:00 ET) only")
    args = ap.parse_args()

    collector = Level2Collector(
        tickers=args.tickers, port=args.port, client_id=args.client_id,
        depth=args.depth, interval=args.interval, smart=not args.no_smart,
        rth_only=args.rth_only)
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
