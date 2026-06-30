"""
alpaca/alpaca_collector.py — Step 3: Real-time Alpaca tick collector.

Mirrors ibkr/tick_collector.py: subscribes to Alpaca trade + quote streams,
classifies each trade as buy/sell aggressor using the most recent quote
(quote-based method, identical to tick_collector.py), aggregates ticks into
1-second OHLCV buckets, and upserts to MongoDB with source='alpaca_tick'.

No IB Gateway or local app needed — only Alpaca API keys.

Architecture:
    AlpacaCollector   — processes ticks for ONE ticker; owns MongoDB connection.
    AlpacaCollectorGroup — owns the shared StockDataStream; routes callbacks to
                          the right AlpacaCollector by data.symbol.

Feed and limitations (IEX free):
    - ~2.5% of total US equities volume (IEX exchange only).
    - bid/ask is IEX best bid/ask, NOT NBBO consolidated → classification accuracy ↓.
    - 15-min delay; upgrade to paid Alpaca SIP feed for real-time.

Usage:
    python -m alpaca.alpaca_collector --ticker NVDA
    python -m alpaca.alpaca_collector --ticker NVDA AAPL TSLA
    python -m alpaca.alpaca_collector --ticker NVDA --verbose
"""

import argparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"


# ─────────────────────────────────────────
# Aggressor classification (identical to ibkr/tick_collector.py)
# ─────────────────────────────────────────

def classify_aggressor(
    price: float,
    size: float,
    bid: float | None,
    ask: float | None,
    prev_price: float | None,
) -> float:
    """
    Return the signed delta contribution of one trade tick.
      + size  → buy aggressor  (market buy hitting the ask)
      - size  → sell aggressor (market sell hitting the bid)
      0       → indeterminate

    Priority:
      1. Quote-based: price >= ask → buy; price <= bid → sell.
      2. Tick-rule fallback (mid-market): uptick → buy, downtick → sell.
      3. Zero if neither applies.
    """
    if bid is not None and ask is not None and bid < ask:
        if price >= ask:
            return size
        if price <= bid:
            return -size
    if prev_price is not None:
        if price > prev_price:
            return size
        if price < prev_price:
            return -size
    return 0.0


# ─────────────────────────────────────────
# Per-ticker data processor
# ─────────────────────────────────────────

class AlpacaCollector:
    """
    Processes trade and quote ticks for one ticker.
    Maintains 1-second buckets and flushes completed seconds to MongoDB.

    If save_ticks=True, each raw trade tick is also stored as a separate
    document (timeframe='tick', microsecond-precision date) in addition to
    the 1-sec OHLCV bar.  Tick documents are NOT upserted (insert_many)
    because multiple ticks share the same second and have no natural unique key.

    Does NOT own the WebSocket stream; that belongs to AlpacaCollectorGroup.
    """

    def __init__(self, ticker: str, save_ticks: bool = False):
        self.ticker = ticker.upper()
        self.save_ticks = save_ticks

        self.bid: float | None = None
        self.ask: float | None = None
        self.prev_trade_price: float | None = None

        self.current_second: datetime | None = None
        self.tick_buffer: list[tuple[float, float, float, float | None, float | None]] = []
        # (price, size, delta, bid_at_trade, ask_at_trade)

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
            self.tick_col.create_index(
                [("ticker", 1), ("date", 1)],
                name="ticker_date",
                background=True,
            )

    # ── Event handlers (called by AlpacaCollectorGroup) ──────────────────────

    async def on_trade(self, data):
        """Process one trade tick from the Alpaca stream."""
        ts = data.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        sec = ts.astimezone(ET).replace(microsecond=0, tzinfo=None)

        if sec != self.current_second:
            if self.current_second is not None:
                self._flush(self.current_second)
            self.current_second = sec
            self.tick_buffer = []

        price = float(data.price)
        size  = float(data.size)
        delta = classify_aggressor(price, size, self.bid, self.ask, self.prev_trade_price)
        self.tick_buffer.append((price, size, delta, self.bid, self.ask))
        self.prev_trade_price = price

    async def on_quote(self, data):
        """Update best bid/ask from the Alpaca quote stream."""
        bid = getattr(data, "bid_price", None)
        ask = getattr(data, "ask_price", None)
        if bid is not None and float(bid) > 0:
            self.bid = float(bid)
        if ask is not None and float(ask) > 0:
            self.ask = float(ask)

    # ── Bar flush ─────────────────────────────────────────────────────────────

    def _flush(self, second: datetime):
        """Aggregate tick_buffer into a 1-sec OHLCV bar and upsert to MongoDB.
        If save_ticks=True, also insert each raw tick as a separate document."""
        if not self.tick_buffer:
            return

        prices = [t[0] for t in self.tick_buffer]
        sizes  = [t[1] for t in self.tick_buffer]
        deltas = [t[2] for t in self.tick_buffer]

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
                    "ticker":  self.ticker,
                    "date":    second,       # second-precision; real-time stream has no sub-sec ts
                    "price":   t[0],
                    "size":    t[1],
                    "bid":     t[3],
                    "ask":     t[4],
                    "delta":   t[2],
                    "source":  "alpaca_tick",
                }
                for t in self.tick_buffer
            ]
            try:
                self.tick_col.insert_many(tick_docs, ordered=False)
            except Exception as e:
                logging.error(f"MongoDB tick insert error [{self.ticker} {second}]: {e}")

    def flush_last(self):
        """Flush the partial second still in the buffer (called on shutdown)."""
        if self.current_second is not None:
            self._flush(self.current_second)


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
        """Subscribe to all tickers and start the blocking stream loop."""
        self.stream.subscribe_trades(self._on_trade, *self.tickers)
        self.stream.subscribe_quotes(self._on_quote, *self.tickers)
        logging.info(
            f"[Alpaca] Streaming ticks for {self.tickers} → MongoDB (1-sec bars). "
            "Press Ctrl+C to stop."
        )
        try:
            self.stream.run()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
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
        help="Also store every raw trade tick (timeframe='tick') in addition to 1-sec bars",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    AlpacaCollectorGroup(args.ticker, save_ticks=args.save_ticks).run()


if __name__ == "__main__":
    main()
