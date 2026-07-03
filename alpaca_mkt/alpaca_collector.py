"""
alpaca_mkt/alpaca_collector.py — Step 3: Real-time Alpaca tick collector.

Mirrors ibkr/tick_collector.py: subscribes to Alpaca trade + quote streams,
classifies each trade as buy/sell aggressor using the most recent quote
(shared logic in cvd/aggressor.py), aggregates ticks into 1-second OHLCV
buckets, and upserts to MongoDB with source='alpaca_tick'.

No IB Gateway or local app needed — only Alpaca API keys.

Architecture:
    AlpacaCollector   — processes ticks for ONE ticker; owns MongoDB connection.
    AlpacaCollectorGroup — owns the shared StockDataStream; routes callbacks to
                          the right AlpacaCollector by data.symbol.

Bucketing:
    Ticks accumulate in per-second buckets keyed by their ET second. A bucket
    is flushed only once the stream watermark (newest trade/quote timestamp)
    has advanced LATE_TOLERANCE_S past it, so slightly out-of-order ticks land
    in the correct bar instead of overwriting an already-flushed one. Quote
    events also advance the watermark, so the last bar of a burst still gets
    flushed promptly even when trades go quiet (thin tickers, after-hours).

Feed and limitations (IEX free):
    - ~2.5% of total US equities volume (IEX exchange only).
    - bid/ask is IEX best bid/ask, NOT NBBO consolidated → classification accuracy ↓.
    - Websocket delivers real-time IEX data; the full consolidated SIP feed
      requires a paid subscription.

Usage:
    python -m alpaca_mkt.alpaca_collector --ticker NVDA
    python -m alpaca_mkt.alpaca_collector --ticker NVDA AAPL TSLA
    python -m alpaca_mkt.alpaca_collector --ticker NVDA --verbose
"""

import argparse
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from pymongo.errors import BulkWriteError
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

from cvd.aggressor import classify_aggressor, next_tick_dir
from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"

# Seconds the watermark must advance past a bucket before it is flushed.
# Ticks arriving later than this may still create a sparse duplicate bar.
LATE_TOLERANCE_S = 2


def _to_et_naive(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(ET).replace(tzinfo=None)


# ─────────────────────────────────────────
# Per-ticker data processor
# ─────────────────────────────────────────

class AlpacaCollector:
    """
    Processes trade and quote ticks for one ticker.
    Maintains per-second buckets and flushes seconds that have fallen behind
    the stream watermark (see module docstring) to MongoDB.

    If save_ticks=True, each raw trade tick is also stored as a separate
    document in finviz_db.ticks with its precise timestamp, trade id and
    condition codes.  Tick inserts use the unique (ticker, date, id) index,
    so re-delivered ticks are skipped instead of duplicated.

    Does NOT own the WebSocket stream; that belongs to AlpacaCollectorGroup.
    """

    def __init__(self, ticker: str, save_ticks: bool = False):
        self.ticker = ticker.upper()
        self.save_ticks = save_ticks

        self.bid: float | None = None
        self.ask: float | None = None
        self.prev_trade_price: float | None = None
        self.prev_tick_dir: float = 0.0

        # second (ET-naive) → list of tick tuples
        # (ts, price, size, delta, bid_at_trade, ask_at_trade, trade_id, conditions)
        self.buckets: dict[datetime, list[tuple]] = {}
        self.watermark: datetime | None = None   # newest event second seen

        mongo = MongoClient(MONGO_URI)
        self.col = mongo[DB_NAME]["candles"]      # 1-sec bars (unique index)
        self.col.create_index(
            [("ticker", 1), ("timeframe", 1), ("date", 1)],
            unique=True,
            name="ticker_tf_date",
            background=True,
        )
        self.tick_col = mongo[DB_NAME]["ticks"]   # raw tick archive (separate collection)
        if save_ticks:
            _ensure_tick_index(self.tick_col)

    # ── Event handlers (called by AlpacaCollectorGroup) ──────────────────────

    async def on_trade(self, data):
        """Process one trade tick from the Alpaca stream."""
        ts = _to_et_naive(data.timestamp)
        sec = ts.replace(microsecond=0)

        price = float(data.price)
        size  = float(data.size)
        delta = classify_aggressor(
            price, size, self.bid, self.ask,
            self.prev_trade_price, self.prev_tick_dir,
        )
        self.prev_tick_dir = next_tick_dir(price, self.prev_trade_price, self.prev_tick_dir)
        self.prev_trade_price = price

        self.buckets.setdefault(sec, []).append((
            ts, price, size, delta, self.bid, self.ask,
            getattr(data, "id", None),
            getattr(data, "conditions", None),
        ))
        self._advance_watermark(sec)

    async def on_quote(self, data):
        """Update best bid/ask from the Alpaca quote stream."""
        bid = getattr(data, "bid_price", None)
        ask = getattr(data, "ask_price", None)
        if bid is not None and float(bid) > 0:
            self.bid = float(bid)
        if ask is not None and float(ask) > 0:
            self.ask = float(ask)
        # Quotes keep flowing when trades go quiet — let them advance the
        # watermark so the last trade bucket still gets flushed promptly.
        ts = getattr(data, "timestamp", None)
        if ts is not None:
            self._advance_watermark(_to_et_naive(ts).replace(microsecond=0))

    # ── Bucket management ─────────────────────────────────────────────────────

    def _advance_watermark(self, sec: datetime):
        if self.watermark is None or sec > self.watermark:
            self.watermark = sec
        self._flush_completed()

    def _flush_completed(self):
        """Flush every bucket the watermark has left LATE_TOLERANCE_S behind."""
        if self.watermark is None or not self.buckets:
            return
        cutoff = self.watermark - timedelta(seconds=LATE_TOLERANCE_S)
        for sec in sorted(s for s in self.buckets if s < cutoff):
            self._flush(sec)

    def _flush(self, second: datetime):
        """Aggregate one bucket into a 1-sec OHLCV bar and upsert to MongoDB.
        If save_ticks=True, also insert each raw tick as a separate document.
        The bucket is removed from memory, so a repeated flush is a no-op."""
        buf = self.buckets.pop(second, None)
        if not buf:
            return

        prices = [t[1] for t in buf]
        sizes  = [t[2] for t in buf]
        deltas = [t[3] for t in buf]

        bv = sum(d for d in deltas if d > 0)
        sv = sum(-d for d in deltas if d < 0)

        # ── 1-sec OHLCV bar ─────────────────────────────────────────────────
        bar = {
            "ticker":          self.ticker,
            "timeframe":       "1sec",
            "date":            second,
            "open":            prices[0],
            "high":            max(prices),
            "low":             min(prices),
            "close":           prices[-1],
            "volume":          sum(sizes),
            "buying_volume":   bv,
            "selling_volume":  sv,
            "delta":           bv - sv,
            "source":          "alpaca_tick",
        }
        try:
            self.col.update_one(
                {"ticker": self.ticker, "timeframe": "1sec", "date": second},
                {"$set": bar},
                upsert=True,
            )
            logging.debug(
                f"[{self.ticker} {second}] O={bar['open']} C={bar['close']} "
                f"V={bar['volume']:.0f} Δ={bar['delta']:.0f} "
                f"(B={bv:.0f} S={sv:.0f})"
            )
        except Exception as e:
            logging.error(f"MongoDB upsert error [{self.ticker} {second}]: {e}")

        # ── Raw tick archive → finviz_db.ticks (optional) ───────────────────
        if self.save_ticks:
            tick_docs = [
                {
                    "ticker":     self.ticker,
                    "date":       t[0],          # ET-naive, microsecond precision
                    "price":      t[1],
                    "size":       t[2],
                    "delta":      t[3],
                    "bid":        t[4],
                    "ask":        t[5],
                    "id":         t[6],
                    "conditions": t[7],
                    "source":     "alpaca_tick",
                }
                for t in buf
            ]
            _insert_ticks(self.tick_col, tick_docs, f"{self.ticker} {second}")

    def flush_last(self):
        """Flush every bucket still in memory (called on shutdown).
        Buckets are popped on flush, so calling this twice is harmless."""
        for sec in sorted(self.buckets):
            self._flush(sec)


# ─────────────────────────────────────────
# Tick-collection helpers (shared with backfill)
# ─────────────────────────────────────────

def _ensure_tick_index(tick_col):
    """Unique (ticker, date, id) index so re-delivered/re-backfilled ticks are
    skipped. Falls back to a plain (ticker, date) index if legacy duplicate
    documents block the unique index creation."""
    try:
        tick_col.create_index(
            [("ticker", 1), ("date", 1), ("id", 1)],
            unique=True, name="ticker_date_id", background=True,
        )
    except Exception as e:
        logging.warning(
            f"[ticks] Could not create unique ticker_date_id index ({e}). "
            "Existing duplicate documents likely — dedupe or drop the ticks "
            "collection to enable duplicate protection."
        )
        tick_col.create_index(
            [("ticker", 1), ("date", 1)], name="ticker_date", background=True,
        )


def _insert_ticks(tick_col, docs: list[dict], label: str) -> int:
    """insert_many that tolerates duplicate-key errors (already-stored ticks)."""
    if not docs:
        return 0
    try:
        res = tick_col.insert_many(docs, ordered=False)
        return len(res.inserted_ids)
    except BulkWriteError as e:
        inserted = e.details.get("nInserted", 0)
        logging.debug(f"[ticks {label}] {len(docs) - inserted} duplicates skipped")
        return inserted
    except Exception as e:
        logging.error(f"MongoDB tick insert error [{label}]: {e}")
        return 0


# ─────────────────────────────────────────
# Multi-ticker group (one shared WebSocket)
# ─────────────────────────────────────────

class AlpacaCollectorGroup:
    """
    Manages one Alpaca WebSocket stream for multiple tickers.
    Routes incoming trade/quote events to the correct AlpacaCollector by symbol.
    """

    def __init__(self, tickers: list[str], save_ticks: bool = False):
        self.tickers = [t.upper() for t in tickers]
        self.collectors: dict[str, AlpacaCollector] = {
            t: AlpacaCollector(t, save_ticks=save_ticks) for t in self.tickers
        }
        self.stream = StockDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY, feed=DataFeed.IEX)

    async def _on_trade(self, data):
        col = self.collectors.get(data.symbol)
        if col:
            await col.on_trade(data)

    async def _on_quote(self, data):
        col = self.collectors.get(data.symbol)
        if col:
            await col.on_quote(data)

    def run(self):
        """Subscribe to all tickers and start the blocking stream loop.

        On connection failure (e.g. Alpaca's 1-connection limit), waits 30s
        before retrying so we don't hammer the server with rapid reconnects.
        """
        self.stream.subscribe_trades(self._on_trade, *self.tickers)
        self.stream.subscribe_quotes(self._on_quote, *self.tickers)
        logging.info(
            f"[Alpaca] Streaming ticks for {self.tickers} → MongoDB (1-sec bars). "
            "Press Ctrl+C to stop."
        )
        import time
        retry_delay = 30
        while True:
            try:
                self.stream.run()
                break  # clean exit (KeyboardInterrupt handled inside stream.run)
            except (KeyboardInterrupt, SystemExit):
                break
            except ValueError as e:
                if "connection limit" in str(e).lower():
                    logging.error(
                        f"[Alpaca] Connection limit exceeded — another session may be open. "
                        f"Retrying in {retry_delay}s... (kill other alpaca_main processes first)"
                    )
                    time.sleep(retry_delay)
                else:
                    raise
            except Exception as e:
                logging.error(f"[Alpaca] Stream error: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
        for col in self.collectors.values():
            col.flush_last()
        logging.info("[Alpaca] Collector stopped.")

    def stop(self):
        """Flush all partial buffers and stop the stream."""
        for col in self.collectors.values():
            col.flush_last()
        self.stream.stop()


# ─────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Alpaca tick-by-tick collector → MongoDB 1-sec bars (source='alpaca_tick')"
    )
    parser.add_argument(
        "--ticker", nargs="+", default=["NVDA"],
        help="Ticker(s) to collect (space-separated). Default: NVDA",
    )
    parser.add_argument("--verbose", action="store_true", help="Show per-bar debug output")
    parser.add_argument(
        "--save-ticks", action="store_true", dest="save_ticks",
        help="Also store every raw trade tick in finviz_db.ticks in addition to 1-sec bars",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    AlpacaCollectorGroup(args.ticker, save_ticks=args.save_ticks).run()


if __name__ == "__main__":
    main()
