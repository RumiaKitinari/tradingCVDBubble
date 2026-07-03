"""
alpaca_mkt/alpaca_main.py — Alpaca pipeline main entry point.

Starts AlpacaCollectorGroup in a background thread and optionally regenerates
the CVD chart on a configurable interval.  Mirrors ibkr/ibkr_main.py but uses
threading instead of asyncio.gather (Alpaca's stream.run() is a blocking call).

Workflow:
  1. (Optional) run backfill first to seed MongoDB with historical data:
         python -m alpaca_mkt.alpaca_backfill --ticker NVDA --days 1
  2. Start this main for live collection + chart updates:
         python -m alpaca_mkt.alpaca_main --ticker NVDA --chart-interval 300

Usage:
    python -m alpaca_mkt.alpaca_main --ticker NVDA
    python -m alpaca_mkt.alpaca_main --ticker NVDA AAPL --chart-interval 300
    python -m alpaca_mkt.alpaca_main --ticker NVDA --verbose
"""

import time
import logging
import argparse
import threading

from .alpaca_collector import AlpacaCollectorGroup


# ─────────────────────────────────────────
# Chart regeneration loop
# ─────────────────────────────────────────

def _chart_loop(ticker: str, interval_s: int, chart_days: int | None):
    """Regenerate the CVD chart every `interval_s` seconds using 1-sec Alpaca data."""
    is_first = True
    while True:
        time.sleep(interval_s)
        try:
            from cvd.calculator import run_pipeline
            from cvd.visualizer import build_chart, write_chart_html

            logging.info(f"[Chart] Regenerating for {ticker}...")
            df_base, frames = run_pipeline(ticker, base_timeframe="1sec", days=chart_days)
            if not df_base.empty and frames:
                fig = build_chart(df_base, frames, ticker)
                path = f"{ticker}_alpaca_chart.html"
                write_chart_html(fig, path)
                logging.info(f"[Chart] Saved → {path}")
                
                if is_first:
                    import webbrowser
                    import os
                    webbrowser.open(f"file://{os.path.abspath(path)}")
                    is_first = False
            else:
                logging.warning(f"[Chart] No data yet for {ticker} — skipping (run backfill first).")
        except Exception as e:
            logging.error(f"[Chart] Failed for {ticker}: {e}", exc_info=True)


# ─────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Alpaca tick collector + optional CVD chart regeneration"
    )
    parser.add_argument(
        "--ticker", nargs="+", default=["NVDA"],
        help="Ticker(s) to collect (space-separated). Default: NVDA",
    )
    parser.add_argument(
        "--chart-interval", type=int, default=0, dest="chart_interval",
        help="Seconds between chart regenerations (0 = off, e.g. 300 = every 5 min)",
    )
    parser.add_argument(
        "--chart-days", type=int, default=0, dest="chart_days",
        help="Only chart the last N days of 1-sec data (0 = everything). "
             "Keeps regeneration and the HTML small once weeks of data accumulate.",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--save-ticks", action="store_true", dest="save_ticks",
        help="Also archive every raw trade tick (timeframe='tick') alongside 1-sec bars",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    group = AlpacaCollectorGroup(args.ticker, save_ticks=args.save_ticks)

    # Run the blocking collector in a daemon thread
    collector_thread = threading.Thread(
        target=group.run, daemon=True, name="alpaca-collector"
    )
    collector_thread.start()
    logging.info(f"[Main] Collector started for {args.ticker}.")

    # Optional chart threads (one per ticker)
    if args.chart_interval > 0:
        chart_days = args.chart_days if args.chart_days > 0 else None
        for ticker in args.ticker:
            ct = threading.Thread(
                target=_chart_loop,
                args=(ticker, args.chart_interval, chart_days),
                daemon=True,
                name=f"chart-{ticker}",
            )
            ct.start()
            logging.info(f"[Main] Chart loop started for {ticker} (every {args.chart_interval}s).")

    # Block until the collector exits (Ctrl+C) or raises
    try:
        collector_thread.join()
    except KeyboardInterrupt:
        logging.info("[Main] Interrupt received — shutting down...")
        group.stop()
    logging.info("[Main] Done.")


if __name__ == "__main__":
    main()
