"""
scripts/eval_support_resistance.py
----------------------------------
Task D: an evaluation harness for the L2 support/resistance detector, usable on
mock data TODAY and on real market-depth snapshots once the subscription opens
(same code path — it reads trading_cvd.level2_snapshots regardless of src).

Question: do the levels the detector flags actually get RESPECTED by price more
than arbitrary price levels would? A real S&R level should be (a) touched
repeatedly and (b) hold — price reverses away from it rather than slicing
through.

Method:
  1. Build a price path from the snapshots' own mid_price (for mock this is the
     book-interacting mid; for real L2 mid_price ≈ the real mid — same series
     the book belongs to), resampled to 1-min OHLC.
  2. Run fetch_and_aggregate_l2_data + compute_support_resistance to get levels.
  3. For each level, count TOUCHES (a bar whose range enters ±band of the level)
     and measure the HOLD rate: after a touch, does price stay on the level's
     side over the next k bars (support holds if it closes back above; resistance
     holds if it closes back below) rather than breaking through.
  4. NULL baseline: run the identical test at many random levels drawn from the
     observed price range. Detector adds value only if detected-level hold rate
     and touch frequency beat the null.

On mock data with static planted walls (--breakout-prob 0) the mid gets repelled
at the walls, so detected levels should show a clearly-above-null hold rate —
that is the harness self-test. On real data the same numbers tell you whether
the resting-liquidity levels are tradeable.

Usage:
  python -m scripts.eval_support_resistance --ticker NVDA
  python -m scripts.eval_support_resistance --ticker NVDA --band-ticks 2 --horizon 5
"""
import argparse

import numpy as np
import pandas as pd

from level2_webapp.data_provider import (
    get_l2_collection, fetch_and_aggregate_l2_data, compute_support_resistance,
)


def load_price_bars(ticker: str, rule: str = "1min") -> pd.DataFrame:
    """OHLC bars of snapshot mid_price, indexed by ET-naive datetime (so
    .timestamp() matches the snapshots' epoch convention)."""
    col = get_l2_collection()
    docs = list(col.find({"ticker": ticker.upper()},
                         {"_id": 0, "date": 1, "mid_price": 1}).sort("date", 1))
    if not docs:
        return pd.DataFrame()
    s = pd.DataFrame(docs)
    s["date"] = pd.to_datetime(s["date"])
    s = s.dropna(subset=["mid_price"]).set_index("date")
    bar = s["mid_price"].resample(rule).ohlc().dropna()
    return bar


def hold_rate(bar: pd.DataFrame, level: float, side: str, band: float, horizon: int):
    """(touches, hold_rate) for one price level.

    touch  = bar whose [low,high] enters [level-band, level+band].
    holds  = over the next `horizon` bars price returns to / stays on the
             level's own side (support: some close > level+band; resistance:
             some close < level-band) instead of closing clean through.
    """
    lows, highs, closes = bar["low"].to_numpy(), bar["high"].to_numpy(), bar["close"].to_numpy()
    n = len(bar)
    touches = holds = 0
    for i in range(n - 1):
        touched = (lows[i] <= level + band) and (highs[i] >= level - band)
        if not touched:
            continue
        touches += 1
        fwd = closes[i + 1: i + 1 + horizon]
        if len(fwd) == 0:
            continue
        if side == "support":
            broke = np.any(fwd < level - band) and not np.any(fwd > level + band)
        else:
            broke = np.any(fwd > level + band) and not np.any(fwd < level - band)
        if not broke:
            holds += 1
    return touches, (holds / touches if touches else np.nan)


def null_baseline(bar: pd.DataFrame, band: float, horizon: int, n_levels: int = 200, seed: int = 0):
    """Mean touch count + hold rate at random levels across the price range."""
    rng = np.random.default_rng(seed)
    lo, hi = bar["low"].min(), bar["high"].max()
    t_list, h_list = [], []
    for _ in range(n_levels):
        lvl = rng.uniform(lo, hi)
        side = rng.choice(["support", "resistance"])
        t, h = hold_rate(bar, lvl, side, band, horizon)
        if t > 0:
            t_list.append(t); h_list.append(h)
    return (np.mean(t_list) if t_list else np.nan,
            np.nanmean(h_list) if h_list else np.nan,
            len(t_list))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="NVDA")
    ap.add_argument("--band-ticks", type=int, default=2, help="touch band, in $0.01 ticks")
    ap.add_argument("--horizon", type=int, default=5, help="bars to judge hold over")
    ap.add_argument("--max-candles", type=int, default=300)
    args = ap.parse_args()

    band = args.band_ticks * 0.01
    bar = load_price_bars(args.ticker)
    if bar.empty:
        print(f"No L2 snapshots for {args.ticker}. Seed the mock first:\n"
              f"  python -m tests.mock_level2_stream --ticker {args.ticker} --seed-minutes 90 --breakout-prob 0")
        return
    print(f"[{args.ticker}] {len(bar)} price bars  {bar.index[0]} ~ {bar.index[-1]}")

    # detector needs a candle frame + the z matrices; feed it the mid-price bars
    df = bar.rename(columns={"close": "close"}).copy()
    df["close"] = bar["close"]
    _, y_levels, z_matrix, z_bid = fetch_and_aggregate_l2_data(
        args.ticker, df, max_candles=args.max_candles)
    if z_matrix is None:
        print("No z-matrix (snapshots didn't match bars — check time overlap).")
        return
    mid = float(bar["close"].iloc[-1])
    levels = compute_support_resistance(y_levels, z_matrix, mid, z_bid=z_bid)
    if not levels:
        print("Detector found no S&R levels.")
        return

    n_null, h_null, cov = null_baseline(bar, band, args.horizon)
    print(f"\nNULL baseline (random levels): mean touches={n_null:.1f}, "
          f"hold rate={h_null*100:.0f}%  (n={cov})\n")
    print(f"{'side':11} {'price':>9} {'score':>6} {'touches':>8} {'hold%':>7}  vs null")
    print("-" * 58)
    rows = []
    for lv in levels:
        t, h = hold_rate(bar, lv["price"], lv["side"], band, args.horizon)
        hp = h * 100 if np.isfinite(h) else np.nan
        edge = "—"
        if np.isfinite(h) and np.isfinite(h_null):
            edge = f"{(h - h_null)*100:+.0f}pt"
        print(f"{lv['side']:11} {lv['price']:>9.2f} {lv['score']:>6.2f} "
              f"{t:>8} {hp:>6.0f}%  {edge}")
        rows.append((lv, t, h))

    held = [h for _, t, h in rows if np.isfinite(h)]
    if held:
        print(f"\nDetected-level mean hold rate: {np.mean(held)*100:.0f}%  "
              f"(null {h_null*100:.0f}%) — "
              f"{'ABOVE null ✓' if np.mean(held) > h_null else 'not above null'}")
    print(f"Detected levels avg touches: "
          f"{np.mean([t for _, t, _ in rows]):.1f}  (null {n_null:.1f})")


if __name__ == "__main__":
    main()
