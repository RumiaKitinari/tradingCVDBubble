"""
alpaca/alpaca_connect.py — Step 1 PoC: subscribe to Alpaca trades, print first tick, exit.

Mirrors ibkr/ibkr_connect.py but for Alpaca — no gateway or local app needed.
Uses the IEX free feed (15-min delayed, ~2.5% of total market volume).

Run this BEFORE the full collector to verify keys and WebSocket connectivity.

Usage:
    python -m alpaca.alpaca_connect
    python -m alpaca.alpaca_connect --ticker AAPL --timeout 30

Notes:
    - Outside regular/extended market hours there are no live trades → timeout is expected.
    - IEX free feed lags ~15 min during market hours; upgrade to paid SIP for real-time.
    - On a successful run you will see: "SUCCESS — trade received: {...}"
"""

import argparse
import logging
import threading

from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

from .alpaca_keys import ALPACA_API_KEY, ALPACA_SECRET_KEY


def poc_connect(ticker: str = "NVDA", timeout: float = 30.0) -> dict:
    """
    Subscribe to Alpaca trade stream for `ticker`, capture the first tick,
    stop the stream, and return the trade as a dict.
    Returns {} on timeout (outside market hours / no IEX data).
    """
    result: dict = {}
    stop_event = threading.Event()

    stream = StockDataStream(ALPACA_API_KEY, ALPACA_SECRET_KEY, feed=DataFeed.IEX)

    async def on_trade(data):
        result.update({
            "timestamp": data.timestamp,
            "price":     float(data.price),
            "size":      float(data.size),
            "exchange":  data.exchange,
        })
        stop_event.set()

    stream.subscribe_trades(on_trade, ticker.upper())

    # Run the blocking stream.run() in a daemon thread so we can impose a timeout.
    t = threading.Thread(target=stream.run, daemon=True, name="alpaca-poc")
    t.start()

    got_data = stop_event.wait(timeout=timeout)
    stream.stop()
    t.join(timeout=5.0)

    if got_data:
        logging.info(f"SUCCESS — trade received: {result}")
    else:
        logging.warning(
            f"TIMEOUT ({timeout}s) — no trade received.\n"
            "  • Outside regular/extended market hours? No ticks stream when market is closed.\n"
            "  • IEX free feed has ~15-min delay during regular hours; upgrade to paid SIP for real-time."
        )
    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Alpaca connection PoC — print first trade tick and exit."
    )
    parser.add_argument("--ticker", default="NVDA", help="Stock ticker (default: NVDA)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Seconds to wait for first tick (default: 30)")
    args = parser.parse_args()
    poc_connect(args.ticker, args.timeout)


if __name__ == "__main__":
    main()
