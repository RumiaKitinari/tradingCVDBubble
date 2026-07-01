"""
cvd/aggressor.py
----------------
Shared trade-aggressor classification used by every tick source
(ibkr/tick_collector.py, alpaca_mkt/alpaca_collector.py, alpaca_mkt/alpaca_backfill.py).
Keeping one implementation prevents the IBKR and Alpaca pipelines from drifting apart.

Classification priority (Lee-Ready style):
  1. Quote-based : price >= ask -> buy (+size); price <= bid -> sell (-size).
  2. Tick rule   : uptick -> buy, downtick -> sell.
  3. Zero-tick   : price unchanged -> inherit the LAST non-zero tick direction.
  4. Zero if nothing applies (no quotes, no prior trades).
"""

import numpy as np


def classify_aggressor(
    price: float,
    size: float,
    bid: float | None,
    ask: float | None,
    prev_price: float | None,
    prev_dir: float = 0.0,
) -> float:
    """
    Return the signed delta contribution of one trade tick.
      + size  -> buy aggressor  (market buy hitting the ask)
      - size  -> sell aggressor (market sell hitting the bid)
      0       -> indeterminate

    `prev_dir` is the last non-zero tick direction (+1/-1/0), used by the
    zero-tick rule when the price is unchanged from the previous trade.
    Callers maintain it via `next_tick_dir()`.
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
        if prev_dir:                      # zero-tick rule
            return prev_dir * size
    return 0.0


def next_tick_dir(price: float, prev_price: float | None, prev_dir: float) -> float:
    """Update the tick direction state after processing one trade."""
    if prev_price is None or price == prev_price:
        return prev_dir
    return 1.0 if price > prev_price else -1.0


def classify_vectorized(
    price: np.ndarray,
    size: np.ndarray,
    bid: np.ndarray,
    ask: np.ndarray,
    prev_price: float | None = None,
    prev_dir: float = 0.0,
) -> tuple[np.ndarray, float | None, float]:
    """
    Vectorized version of classify_aggressor (same priority + zero-tick rule).

    `prev_price` / `prev_dir` carry classification state across chunk
    boundaries (e.g. hourly backfill chunks), so results are identical to
    processing the full stream in one pass.

    Returns (delta, last_price, last_dir) so the caller can pass the state
    into the next chunk.
    """
    n = len(price)
    if n == 0:
        return np.zeros(0), prev_price, prev_dir

    prev = np.empty(n)
    prev[0] = np.nan if prev_price is None else prev_price
    prev[1:] = price[:-1]

    # Tick direction per trade: +1 uptick, -1 downtick, 0 unchanged/unknown,
    # then zero-tick rule = forward-fill the last non-zero direction
    # (seeded with prev_dir from the previous chunk).
    with np.errstate(invalid="ignore"):
        raw_dir = np.sign(price - prev)
    raw_dir = np.nan_to_num(raw_dir, nan=0.0)
    idx = np.arange(n)
    nz = raw_dir != 0
    last_nz = np.maximum.accumulate(np.where(nz, idx, -1))
    tick_dir = np.where(last_nz >= 0, raw_dir[np.clip(last_nz, 0, None)], prev_dir)

    delta = np.zeros(n)
    has_spread = ~np.isnan(bid) & ~np.isnan(ask) & (bid < ask)

    # 1. Quote-based
    buy_q  = has_spread & (price >= ask)
    sell_q = has_spread & (price <= bid)
    delta[buy_q]  =  size[buy_q]
    delta[sell_q] = -size[sell_q]

    # 2-3. Tick rule (incl. zero-tick) for everything the quotes couldn't classify
    needs_tick = ~buy_q & ~sell_q
    delta[needs_tick] = tick_dir[needs_tick] * size[needs_tick]

    return delta, float(price[-1]), float(tick_dir[-1])
