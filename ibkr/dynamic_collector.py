"""
ibkr/dynamic_collector.py — on-demand tick collection for searched tickers.

The research collectors (`python -m ibkr.tick_collector --ticker NVDA SOFI RUM`)
are long-lived, fixed-universe processes. This service complements them: when
the Dash app serves a ticker in tiered mode it upserts a request into the
`collector_requests` collection, and this manager — a SEPARATE process with its
own clientId — subscribes tick-by-tick streams for the most recently requested
tickers on the fly. Search AAPL in the app and its 1-sec tick bars start
flowing within a few seconds, without touching the research collectors.

Design:
  * ONE IB connection for all dynamic tickers (tick-by-tick subscriptions are
    per-contract, not per-client), plus one short-lived connection at a time
    (clientId+1) for catch-up backfills.
  * At most --max tickers subscribed at once (tick-by-tick line limits);
    least-recently-requested tickers are evicted when a new one needs the slot.
  * --exclude tickers are never touched here: they belong to the research
    collectors, and double-collecting would duplicate raw_ticks rows.
  * On first subscription of a ticker, the gap between its latest stored 1sec
    bar and now (capped at --backfill-hours) is backfilled with historical
    1-sec bars (source='ibkr_hist', BVC split, quality-guarded upserts — can
    never overwrite real tick bars).
  * Per-ticker buffering/flush logic is reused from TickCollector; only its
    connection handling is bypassed.

Usage:
    python -m ibkr.dynamic_collector                     # defaults below
    python -m ibkr.dynamic_collector --max 3 --exclude NVDA SOFI RUM
"""

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from ib_async import IB, Stock

from ibkr.tick_collector import TickCollector, MONGO_URI, DB_NAME
from ibkr.backfill import backfill_ticker

ET = ZoneInfo("America/New_York")

POLL_SEC = 5                 # how often to reconcile subscriptions with requests
REQUEST_TTL_SEC = 1800       # only requests seen this recently are LRU candidates
FAIL_COOLDOWN_SEC = 300      # don't retry a ticker that failed to subscribe for this long
RECONNECT_DELAY = 30
MIN_BACKFILL_GAP_SEC = 120   # skip catch-up backfill for gaps smaller than this
FINVIZ_REFRESH_SEC = 600     # consolidated-volume reference refresh cadence


class DynamicTickManager:
    def __init__(self, port: int, client_id: int, max_tickers: int,
                 exclude: list[str], backfill_hours: float):
        self.port = port
        self.client_id = client_id
        self.max_tickers = max_tickers
        self.exclude = {t.upper() for t in exclude}
        self.backfill_hours = backfill_hours

        self.ib = IB()
        mongo = MongoClient(MONGO_URI)
        self.req_col = mongo[DB_NAME]["collector_requests"]
        self.candles = mongo[DB_NAME]["candles"]

        # ticker -> {"collector": TickCollector, "contract": Stock}
        self.active: dict[str, dict] = {}
        self.failed_until: dict[str, float] = {}
        self.backfill_queue: asyncio.Queue[str] = asyncio.Queue()
        self.backfilled: set[str] = set()

    # ── Request → desired set ────────────────────────────────────────────────

    def _desired(self) -> list[str]:
        """Most recently requested tickers, minus exclusions and cooldowns.
        Only requests seen within REQUEST_TTL_SEC count, so a ticker glanced at
        once — or a stale/partial symbol left in the queue — ages out of the LRU
        instead of churning a live-tick slot forever."""
        now = time.time()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=REQUEST_TTL_SEC)
        out = []
        for d in self.req_col.find({"last_requested": {"$gte": cutoff}},
                                   {"_id": 1}).sort("last_requested", -1):
            t = str(d["_id"]).upper()
            if t in self.exclude or self.failed_until.get(t, 0) > now:
                continue
            out.append(t)
            if len(out) >= self.max_tickers:
                break
        return out

    # ── Subscribe / unsubscribe ──────────────────────────────────────────────

    async def _subscribe(self, ticker: str):
        # TickCollector is used purely for its buffering/flush/classification
        # state; its own self.ib is never connected here.
        collector = TickCollector(ticker, port=self.port, client_id=self.client_id)
        contract = Stock(ticker, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)

        trade_tk = self.ib.reqTickByTickData(contract, "AllLast", numberOfTicks=0, ignoreSize=False)
        trade_tk.updateEvent += collector._on_trade_tick
        quote_tk = self.ib.reqTickByTickData(contract, "BidAsk", numberOfTicks=0, ignoreSize=False)
        quote_tk.updateEvent += collector._on_bidask_tick

        self.active[ticker] = {"collector": collector, "contract": contract}
        logging.info(f"[dyn] SUBSCRIBED {ticker} "
                     f"({len(self.active)}/{self.max_tickers} slots)")

        if ticker not in self.backfilled:
            self.backfilled.add(ticker)
            await self.backfill_queue.put(ticker)

    def _unsubscribe(self, ticker: str, reason: str = "evicted"):
        st = self.active.pop(ticker, None)
        if st is None:
            return
        try:
            self.ib.cancelTickByTickData(st["contract"], "AllLast")
            self.ib.cancelTickByTickData(st["contract"], "BidAsk")
        except Exception:
            pass  # already disconnected
        c = st["collector"]
        try:
            if c.current_second is not None:
                c._flush(c.current_second)
                c.current_second = None
            else:
                c._flush_quotes()
        except Exception as e:
            logging.error(f"[dyn] flush on unsubscribe failed for {ticker}: {e}")
        logging.info(f"[dyn] unsubscribed {ticker} ({reason})")

    async def _reconcile(self):
        desired = self._desired()
        for t in [t for t in self.active if t not in desired]:
            self._unsubscribe(t)
        for t in desired:
            if t in self.active:
                continue
            try:
                await self._subscribe(t)
            except Exception as e:
                # Typical causes: unknown symbol, tick-by-tick line limit
                # (error 10189). Cool down so we don't hammer the gateway.
                logging.error(f"[dyn] subscribe {t} failed: {e!r} — cooldown "
                              f"{FAIL_COOLDOWN_SEC}s")
                self.failed_until[t] = time.time() + FAIL_COOLDOWN_SEC
                self._unsubscribe(t, reason="subscribe failed")

    # ── Catch-up backfill worker (sequential, own clientId) ─────────────────

    async def _backfill_worker(self):
        while True:
            ticker = await self.backfill_queue.get()
            try:
                now = datetime.now(ET).replace(tzinfo=None)
                start = now - timedelta(hours=self.backfill_hours)
                last = self.candles.find_one(
                    {"ticker": ticker, "timeframe": "1sec"}, sort=[("date", -1)])
                if last and last["date"] > start:
                    start = last["date"]
                if (now - start).total_seconds() < MIN_BACKFILL_GAP_SEC:
                    continue
                logging.info(f"[dyn] catch-up 1sec backfill {ticker}: "
                             f"{start} → {now}")
                # resume=False: backfill_meta coverage is a start/end UNION, so
                # after any earlier backfill it "covers" the very gap we're
                # here to fill and the resume logic would skip it entirely.
                # Upserts are idempotent, so refetching is safe.
                await backfill_ticker(
                    ticker, start, now, barsize="1sec",
                    port=self.port, client_id=self.client_id + 1,
                    resume=False,
                )
            except Exception as e:
                logging.error(f"[dyn] backfill {ticker} failed: {e!r}")

    # ── FinViz i1 refresher ──────────────────────────────────────────────────
    # Every tick-collected ticker (dynamic AND the research exclusions) needs a
    # fresh FinViz i1 series: it is the consolidated-volume reference that
    # history.rollup.scale_tick_volume uses to scale thin tick-stream volume
    # (~10% of tape) up to real traded volume. The Dash app only fetches the
    # ticker being VIEWED, so without this, unviewed collected tickers (e.g.
    # SOFI) accumulate unscalable tick buckets.

    async def _finviz_refresh_worker(self):
        while True:
            for t in sorted(set(self.active) | self.exclude):
                try:
                    from finviz.new_finviz import fetch_and_save
                    from history.rollup import rollup_ticker
                    await asyncio.to_thread(fetch_and_save, t, "i1")
                    stats = await asyncio.to_thread(rollup_ticker, t)
                    written = {k: v for k, v in stats.items() if v}
                    if written:
                        logging.info(f"[dyn] i1 refresh + rollup {t}: {written}")
                except Exception as e:
                    logging.warning(f"[dyn] i1 refresh {t} failed: {e!r}")
                await asyncio.sleep(3)   # be polite to FinViz
            await asyncio.sleep(FINVIZ_REFRESH_SEC)

    # ── Main loop with reconnect ─────────────────────────────────────────────

    async def run(self):
        try:
            while True:
                try:
                    logging.info(f"[dyn] connecting to IB "
                                 f"(port={self.port}, clientId={self.client_id})...")
                    await self.ib.connectAsync("127.0.0.1", self.port,
                                               clientId=self.client_id)
                    logging.info("[dyn] connected — watching collector_requests")
                    while self.ib.isConnected():
                        await self._reconcile()
                        await asyncio.sleep(POLL_SEC)
                    logging.warning(f"[dyn] connection LOST — reconnecting in "
                                    f"{RECONNECT_DELAY}s")
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as e:
                    logging.error(f"[dyn] session error: {e!r} — retrying in "
                                  f"{RECONNECT_DELAY}s")
                finally:
                    for t in list(self.active):
                        self._unsubscribe(t, reason="session ended")
                    if self.ib.isConnected():
                        self.ib.disconnect()
                await asyncio.sleep(RECONNECT_DELAY)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            for t in list(self.active):
                self._unsubscribe(t, reason="shutdown")
            if self.ib.isConnected():
                self.ib.disconnect()
            logging.info("[dyn] stopped.")


async def _main_async(args):
    mgr = DynamicTickManager(
        port=args.port, client_id=args.client_id, max_tickers=args.max,
        exclude=args.exclude, backfill_hours=args.backfill_hours,
    )
    await asyncio.gather(mgr.run(), mgr._backfill_worker(),
                         mgr._finviz_refresh_worker())


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from ibkr.log_noise import install as _install_log_filter
    _install_log_filter()   # drop benign IBKR data-farm / account chatter
    parser = argparse.ArgumentParser(
        description="On-demand IBKR tick collector driven by app searches")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=60, dest="client_id",
                        help="clientId for the streaming connection; +1 is used "
                             "for catch-up backfills (default: 60)")
    parser.add_argument("--max", type=int, default=3,
                        help="max simultaneously subscribed tickers (default: 3)")
    parser.add_argument("--exclude", nargs="+", default=["NVDA", "SOFI", "RUM"],
                        help="tickers owned by the research collectors — never "
                             "collected here (default: NVDA SOFI RUM)")
    parser.add_argument("--backfill-hours", type=float, default=24,
                        dest="backfill_hours",
                        help="max catch-up 1sec backfill span on first "
                             "subscription (default: 24)")
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
