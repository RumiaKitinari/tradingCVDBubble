"""
ibkr/ibkr_connect.py — Step 1: Connection PoC.

Connects to IB Gateway (or TWS), qualifies a contract, waits for one 5-second
real-time bar, prints it, and disconnects cleanly. This is the minimum test
that the gateway is reachable and the ib_async stack works end-to-end.

Run this BEFORE setting up the full tick collector.

Ports:
    TWS paper trading : 7497  (default, recommended for dev)
    TWS live trading  : 7496
    IB Gateway paper  : 4002
    IB Gateway live   : 4001

Prerequisites:
    1. IB Gateway or TWS running and logged in (paper or live account).
    2. API connections enabled in TWS/Gateway settings (Edit → Global Config
       → API → Settings → Enable ActiveX and Socket Clients, port matches above).
    3. `pip install ib_async` in the project venv.

Usage:
    python -m ibkr.ibkr_connect
    python -m ibkr.ibkr_connect --ticker AAPL --port 7497 --client-id 1
"""

import asyncio
import argparse
import logging

from ib_async import IB, Stock


async def poc_connect(ticker: str = "NVDA", port: int = 7497, client_id: int = 1) -> dict:
    """
    Connect to IB Gateway, request one 5-sec real-time bar, return it.
    Returns {} on failure (timeout or connection error).
    """
    ib = IB()
    result: dict = {}

    logging.info(f"Connecting to IB Gateway at 127.0.0.1:{port} (clientId={client_id})...")
    try:
        await ib.connectAsync("127.0.0.1", port, clientId=client_id)
    except Exception as e:
        logging.error(f"Connection failed: {e}")
        logging.error("Is TWS/Gateway running and API connections enabled?")
        return result

    logging.info(f"Connected. Server version: {ib.client.serverVersion()}")

    contract = Stock(ticker.upper(), "SMART", "USD")
    try:
        [qualified] = await ib.qualifyContractsAsync(contract)
        logging.info(f"Contract qualified: {qualified}")
    except Exception as e:
        logging.error(f"Contract qualification failed: {e}")
        ib.disconnect()
        return result

    bars_received = asyncio.Event()

    def on_bar(bars, has_new):
        if has_new and bars:
            b = bars[-1]
            result.update({
                "time": b.time,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            })
            bars_received.set()

    logging.info(f"Requesting 5-sec real-time bars for {ticker.upper()} ...")
    bars = ib.reqRealTimeBars(qualified, barSize=5, whatToShow="TRADES", useRTH=False)
    bars.updateEvent += on_bar

    try:
        await asyncio.wait_for(bars_received.wait(), timeout=30.0)
        logging.info(f"SUCCESS — bar received: {result}")
    except asyncio.TimeoutError:
        logging.error(
            "TIMEOUT (30 s) — no bar received.\n"
            "  • Outside regular/extended market hours? 5-sec bars only stream during open sessions.\n"
            "  • Check gateway port and clientId clash (another app using same clientId?)."
        )
    finally:
        ib.cancelRealTimeBars(bars)
        ib.disconnect()
        logging.info("Disconnected.")

    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="IBKR connection PoC — wait for one 5-sec bar and exit."
    )
    parser.add_argument("--ticker", default="NVDA", help="Stock ticker (default: NVDA)")
    parser.add_argument(
        "--port",
        type=int,
        default=7497,
        help="Gateway port: TWS paper=7497, TWS live=7496, GW paper=4002, GW live=4001",
    )
    parser.add_argument("--client-id", type=int, default=1, dest="client_id")
    args = parser.parse_args()

    asyncio.run(poc_connect(args.ticker, args.port, args.client_id))


if __name__ == "__main__":
    main()
