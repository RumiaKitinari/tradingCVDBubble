"""
ibkr/tick_collector.py — Step 2: Real-time tick-by-tick collector.

Subscribes to two IBKR data streams simultaneously:
  - AllLast  (trade ticks)  : price, size, time for each execution
  - BidAsk   (quote ticks)  : best bid/ask at each update

Each trade tick is classified as buy/sell aggressor using the concurrent
bid/ask quote (quote-based method, ~50 lines). Classified ticks are then
accumulated within 1-second buckets and flushed to MongoDB as 1-sec OHLCV
bars with pre-computed buying_volume / selling_volume / delta columns.

These bars are tagged source='ibkr_tick', which causes calculator.py to skip
the wick-decomposition step and use the real buy/sell values directly.

Timestamps are converted from UTC to US/Eastern so the auction-detection
logic in calculator.py (which checks for 15:59 / 16:00 ET) still works.

IBC reconnection: ib_async automatically reconnects on dropped connections.
If IB Gateway restarts (daily forced restart), ib_async will reconnect and
re-subscribe. The weekly Sunday 1am ET forced logout requires IBC's
weekly-reconnect feature (external setup, see DONE.md).

Usage:
    python -m ibkr.tick_collector --ticker NVDA
    python -m ibkr.tick_collector --ticker NVDA --port 7496   # live TWS
    python -m ibkr.tick_collector --ticker NVDA AAPL TSLA     # multi-ticker

Ports: TWS paper=7497, TWS live=7496, IB Gateway paper=4002, live=4001
"""

import asyncio
import argparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from pymongo import MongoClient
from ib_async import IB, Stock

# Shared aggressor classification (same logic as the Alpaca pipeline).
# Note: during the closing auction (15:59 ET) the entire MOC order book
# clears at one price, so all ticks land on either pure-buy or pure-sell —
# same noise issue as wick decomp; _flag_auction() in calculator.py
# neutralizes those bars.
from cvd.aggressor import classify_aggressor, next_tick_dir

ET = ZoneInfo("America/New_York")

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "finviz_db"


# ─────────────────────────────────────────
# TickCollector class
# ─────────────────────────────────────────

class TickCollector:
    """
    Connects to IB Gateway and streams tick-by-tick data for one ticker.

    Internal state:
        bid / ask           — latest NBBO from the BidAsk stream
        current_second      — ET datetime (second precision) of the open bucket
        tick_buffer         — list of (price, size, signed_delta) within the bucket
        prev_trade_price    — last trade price (for tick-rule fallback)
    """

    def __init__(self, ticker: str, port: int = 7497, client_id: int = 10):
        self.ticker = ticker.upper()
        self.port = port
        self.client_id = client_id
        self.ib = IB()

        self.bid: float | None = None
        self.ask: float | None = None
        self.prev_trade_price: float | None = None
        self.prev_tick_dir: float = 0.0

        self.current_second: datetime | None = None
        self.tick_buffer: list[tuple[float, float, float]] = []
        self.raw_buffer = []

        mongo = MongoClient(MONGO_URI)
        self.col = mongo[DB_NAME]["candles"]
        self.raw_col = mongo[DB_NAME]["raw_ticks"]
        self.raw_col.create_index([("ticker", 1), ("date", 1)], background=True)
        self.col.create_index(
            [("ticker", 1), ("timeframe", 1), ("date", 1)],
            unique=True,
            name="ticker_tf_date",
            background=True,
        )

    # ── Event handlers ──────────────────────────────────────────────────────

    def _on_trade_tick(self, ticker_obj):
        """Called by ib_async when new AllLast (trade) ticks arrive."""
        for tick in ticker_obj.tickByTicks:
            ts = tick.time
            # Normalize to an ET naive datetime (second precision)
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=ET)
                ts = ts.astimezone(ET)
            else:
                ts = datetime.fromtimestamp(float(ts), tz=ET)
            sec = ts.replace(microsecond=0, tzinfo=None)

            # Boundary: flush completed second, start new bucket
            if sec != self.current_second:
                if self.current_second is not None:
                    self._flush(self.current_second)
                self.current_second = sec
                self.tick_buffer = []

            price = float(tick.price)
            size = float(tick.size)
            delta = classify_aggressor(
                price, size, self.bid, self.ask,
                self.prev_trade_price, self.prev_tick_dir,
            )
            self.prev_tick_dir = next_tick_dir(price, self.prev_trade_price, self.prev_tick_dir)
            self.prev_trade_price = price
            self.tick_buffer.append((price, size, delta))
            
            # Save raw tick
            self.raw_buffer.append({
                "ticker": self.ticker,
                "date": ts, # Exact datetime with microsecond precision
                "price": price,
                "size": size,
                "delta": delta,
                "source": "ibkr_tick"
            })

    def _on_bidask_tick(self, ticker_obj):
        """Called by ib_async when new BidAsk (quote) ticks arrive."""
        for tick in ticker_obj.tickByTicks:
            bid = getattr(tick, "bidPrice", None)
            ask = getattr(tick, "askPrice", None)
            if bid and float(bid) > 0:
                self.bid = float(bid)
            if ask and float(ask) > 0:
                self.ask = float(ask)

    # ── Bar flush ────────────────────────────────────────────────────────────

    def _flush(self, second: datetime):
        """Aggregate tick_buffer into a 1-sec OHLCV bar and upsert to MongoDB."""
        if not self.tick_buffer:
            return
            
        # Bulk insert raw ticks
        if self.raw_buffer:
            try:
                self.raw_col.insert_many(self.raw_buffer)
            except Exception as e:
                logging.error(f"MongoDB raw tick insert error: {e}")
            self.raw_buffer = []

        prices = [t[0] for t in self.tick_buffer]
        sizes = [t[1] for t in self.tick_buffer]
        deltas = [t[2] for t in self.tick_buffer]

        bv = sum(d for d in deltas if d > 0)
        sv = sum(-d for d in deltas if d < 0)

        bar = {
            "ticker":         self.ticker,
            "timeframe":      "1sec",
            "date":           second,
            "open":           prices[0],
            "high":           max(prices),
            "low":            min(prices),
            "close":          prices[-1],
            "volume":         sum(sizes),
            "buying_volume":  bv,
            "selling_volume": sv,
            "delta":          bv - sv,
            "source":         "ibkr_tick",
        }

        try:
            self.col.update_one(
                {"ticker": self.ticker, "timeframe": "1sec", "date": second},
                {"$set": bar},
                upsert=True,
            )
            logging.debug(
                f"[{second}] O={bar['open']} C={bar['close']} "
                f"V={bar['volume']:.0f} Δ={bar['delta']:.0f} "
                f"(B={bv:.0f} S={sv:.0f})"
            )
        except Exception as e:
            logging.error(f"MongoDB upsert error for {second}: {e}")

    # ── Main async loop ──────────────────────────────────────────────────────

    async def run(self):
        logging.info(
            f"[{self.ticker}] Connecting to IB Gateway "
            f"(port={self.port}, clientId={self.client_id})..."
        )
        await self.ib.connectAsync("127.0.0.1", self.port, clientId=self.client_id)
        logging.info(f"[{self.ticker}] Connected.")

        contract = Stock(self.ticker, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)
        logging.info(f"[{self.ticker}] Contract qualified: {contract}")

        # Subscribe to trade ticks (AllLast = all last-trade prints)
        trade_ticker = self.ib.reqTickByTickData(
            contract, "AllLast", numberOfTicks=0, ignoreSize=False
        )
        trade_ticker.updateEvent += self._on_trade_tick

        # Subscribe to quote ticks for aggressor classification
        quote_ticker = self.ib.reqTickByTickData(
            contract, "BidAsk", numberOfTicks=0, ignoreSize=False
        )
        quote_ticker.updateEvent += self._on_bidask_tick

        logging.info(
            f"[{self.ticker}] Streaming tick data → MongoDB (1-sec bars). "
            f"Press Ctrl+C to stop."
        )
        try:
            while True:
                await asyncio.sleep(60)
                logging.info(f"[{self.ticker}] Heartbeat — still collecting...")
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            # Flush any partial second still in the buffer
            if self.current_second is not None:
                self._flush(self.current_second)
            self.ib.disconnect()
            logging.info(f"[{self.ticker}] Disconnected.")


# ─────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────

async def _run_all(tickers: list[str], port: int, base_client_id: int):
    tasks = [
        TickCollector(t, port=port, client_id=base_client_id + i).run()
        for i, t in enumerate(tickers)
    ]
    await asyncio.gather(*tasks)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="IBKR tick-by-tick collector → MongoDB 1-sec bars"
    )
    parser.add_argument(
        "--ticker", nargs="+", default=["NVDA"],
        help="Ticker(s) to collect (space-separated). Each gets a unique clientId.",
    )
    parser.add_argument(
        "--port", type=int, default=7497,
        help="IB Gateway port: TWS paper=7497, TWS live=7496, GW paper=4002, live=4001",
    )
    parser.add_argument(
        "--client-id", type=int, default=10, dest="client_id",
        help="Base clientId; each additional ticker gets +1 (default: 10)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show per-bar debug output",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(_run_all(args.ticker, args.port, args.client_id))


if __name__ == "__main__":
    main()
