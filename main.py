"""
main.py
-------
Full pipeline:
  1. Token regeneration  (finviz_curl.py)
  2. Fetch + save        (finviz/new_finviz.py  → MongoDB)
  3. CVD calculation     (cvd/calculator.py)
  4. Visualization       (cvd/visualizer.py)
  5. Loop every 60s      (fetch → recalculate → refresh chart)

Usage:
  python main.py               # default: NVDA, loop on
  python main.py --ticker AAPL
  python main.py --ticker TSLA --no-loop   # one-shot, no repeat
"""

import argparse
import time
import traceback

from finviz.finviz_curl import login, get_token, update_api_keys
from finviz.new_finviz import fetch_and_save
from cvd.calculator import run_pipeline
from cvd.visualizer import build_chart


# ─────────────────────────────────────────
# 1. Token regeneration
# ─────────────────────────────────────────

def refresh_token():
    print("\n[Token] Regenerating FinViz API token...")
    try:
        session = login()
        token   = get_token(session)
        update_api_keys(token)
        print(f"[Token] ✅ Token updated.")
    except SystemExit:
        print("[Token] ❌ Token regeneration failed — continuing with existing token.")


# ─────────────────────────────────────────
# 2. Fetch + save to MongoDB
# ─────────────────────────────────────────

def fetch(ticker: str):
    print(f"\n[Fetch] Fetching 1-min candles for {ticker}...")
    candles = fetch_and_save(ticker, timeframe="i1")
    print(f"[Fetch] ✅ {len(candles)} candles fetched and saved.")
    return candles


# ─────────────────────────────────────────
# 3 + 4. Calculate + visualize
# ─────────────────────────────────────────

def calculate_and_show(ticker: str, save_html: bool = True, open_browser: bool = False):
    print(f"\n[Pipeline] Running CVD pipeline for {ticker}...")
    df_1min, frames = run_pipeline(ticker)

    if df_1min.empty or not frames:
        print("[Pipeline] ❌ No data to visualize.")
        return

    fig = build_chart(df_1min, frames, ticker)

    if save_html:
        path = f"{ticker}_cvd_chart.html"
        fig.write_html(path)
        print(f"[Visualizer] Saved → {path}")

    if open_browser:
        fig.show()


# ─────────────────────────────────────────
# 5. Main loop
# ─────────────────────────────────────────

def run(ticker: str, loop: bool = True, interval: int = 60):
    print(f"\n{'='*55}")
    print(f"  CVD Pipeline  |  ticker={ticker}  |  loop={loop}")
    print(f"{'='*55}")

    # Token regeneration once at startup
    refresh_token()

    iteration = 0
    while True:
        iteration += 1
        print(f"\n[Loop] ── Iteration {iteration} ──────────────────────")

        try:
            fetch(ticker)
            calculate_and_show(ticker, save_html=True, open_browser=(iteration == 1))
        except Exception:
            print("[Loop] ❌ Error during pipeline:")
            traceback.print_exc()

        if not loop:
            print("\n[Loop] One-shot mode — exiting.")
            break

        print(f"\n[Loop] Sleeping {interval}s until next fetch...")
        time.sleep(interval)


# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CVD Pipeline")
    parser.add_argument("--ticker",   type=str, default="NVDA", help="Stock ticker (default: NVDA)")
    parser.add_argument("--no-loop",  action="store_true",       help="Run once and exit")
    parser.add_argument("--interval", type=int, default=60,      help="Fetch interval in seconds (default: 60)")
    args = parser.parse_args()

    run(
        ticker   = args.ticker,
        loop     = not args.no_loop,
        interval = args.interval,
    )
