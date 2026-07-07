"""
ibkr/ibkr_main.py — IBKR pipeline main entry point.

Starts tick collector(s) and optionally regenerates the CVD chart on a
configurable interval.  One TickCollector per ticker; each needs a unique
clientId (base-client-id + offset per ticker).

Chart regeneration uses the IBKR 1-sec base pipeline (base_timeframe='1sec'),
so buttons in the chart include 1sec / 5sec / 1min / … / 1month.

Usage:
    python -m ibkr.ibkr_main --ticker NVDA
    python -m ibkr.ibkr_main --ticker NVDA --chart-interval 300
    python -m ibkr.ibkr_main --ticker NVDA AAPL --port 7497

For initial data, run backfill first:
    python -m ibkr.backfill --ticker NVDA --days 5
"""

import asyncio
import argparse
import logging

from ibkr.tick_collector import TickCollector


async def _chart_loop(ticker: str, interval_s: int):
    """Regenerate the CVD chart every interval_s seconds using 1-sec IBKR data."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            from cvd.calculator import run_pipeline
            from cvd.visualizer import build_chart, write_chart_html

            logging.info(f"[Chart] Regenerating for {ticker}...")
            df_base, frames = run_pipeline(ticker, base_timeframe="raw_tick")
            if not df_base.empty and frames:
                fig = build_chart(df_base, frames, ticker)
                path = f"{ticker}_ibkr_chart.html"
                write_chart_html(fig, path)
                logging.info(f"[Chart] Saved → {path}")
            else:
                logging.warning(f"[Chart] No data yet for {ticker} — skipping.")
        except Exception as e:
            logging.error(f"[Chart] Failed for {ticker}: {e}")


async def run_pipeline_ibkr(
    tickers: list[str],
    port: int,
    base_client_id: int,
    chart_interval: int,
):
    """
    Launch one TickCollector per ticker, plus an optional chart-regeneration
    coroutine. All run concurrently via asyncio.gather.
    """
    tasks = []
    for i, ticker in enumerate(tickers):
        client_id = base_client_id + i
        collector = TickCollector(ticker, port=port, client_id=client_id)
        tasks.append(collector.run())
        if chart_interval > 0:
            tasks.append(_chart_loop(ticker, chart_interval))

    await asyncio.gather(*tasks)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="IBKR tick collector + optional CVD chart regeneration"
    )
    parser.add_argument(
        "--ticker", nargs="+", default=["NVDA"],
        help="Ticker(s) to collect (space-separated)",
    )
    parser.add_argument(
        "--port", type=int, default=7497,
        help="IB Gateway port (TWS paper=7497, live=7496, GW paper=4002, live=4001)",
    )
    parser.add_argument(
        "--client-id", type=int, default=10, dest="client_id",
        help="Base clientId; tickers get base+0, base+1, … (default: 10)",
    )
    parser.add_argument(
        "--chart-interval", type=int, default=0, dest="chart_interval",
        help="Seconds between chart regenerations (0 = off, e.g. 300 = every 5 min)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_pipeline_ibkr(
            args.ticker,
            args.port,
            args.client_id,
            args.chart_interval,
        )
    )


if __name__ == "__main__":
    main()
