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
    use_midpoint: bool = False,
) -> float:
    """
    Return the signed delta contribution of one trade tick.
      + size  -> buy aggressor  (market buy hitting the ask)
      - size  -> sell aggressor (market sell hitting the bid)
      0       -> indeterminate

    `prev_dir` is the last non-zero tick direction (+1/-1/0), used by the
    zero-tick rule when the price is unchanged from the previous trade.
    Callers maintain it via `next_tick_dir()`.

    use_midpoint: classic Lee-Ready quote test — an inside-spread trade is
      classified vs the MIDPOINT (price > mid -> buy, < mid -> sell) instead of
      only at/through the touch. This recovers a direction for trades between the
      quotes that would otherwise fall to the (weaker) tick rule. A trade exactly
      at the mid still falls to the tick rule.
    """
    if bid is not None and ask is not None and bid < ask:
        if use_midpoint:
            mid = (bid + ask) / 2.0
            if price > mid:
                return size
            if price < mid:
                return -size
        else:
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
    use_midpoint: bool = False,
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

    # 1. Quote-based. use_midpoint = classic Lee-Ready (classify inside-spread
    #    trades vs the mid); else only at/through the touch (inside-spread -> tick).
    if use_midpoint:
        mid = (bid + ask) / 2.0
        buy_q  = has_spread & (price > mid)
        sell_q = has_spread & (price < mid)
    else:
        buy_q  = has_spread & (price >= ask)
        sell_q = has_spread & (price <= bid)
    delta[buy_q]  =  size[buy_q]
    delta[sell_q] = -size[sell_q]

    # 2-3. Tick rule (incl. zero-tick) for everything the quotes couldn't classify
    needs_tick = ~buy_q & ~sell_q
    delta[needs_tick] = tick_dir[needs_tick] * size[needs_tick]

    return delta, float(price[-1]), float(tick_dir[-1])


def classify_demote_stale(
    price: np.ndarray,
    size: np.ndarray,
    bid: np.ndarray,
    ask: np.ndarray,
    quote_age_ms: np.ndarray,
    max_age_ms: float = 50.0,
    prev_price: float | None = None,
    prev_dir: float = 0.0,
    use_midpoint: bool = False,
) -> np.ndarray:
    """Re-classify trades, DEMOTING stale quotes to the tick rule.

    A quote-based aggressor label is only trustworthy if the NBBO used was
    actually current when the trade printed. IBKR delivers trades (AllLast) and
    quotes (BidAsk) as two independent streams, so the quote snapshot held at
    classification time can lag the trade by hundreds of ms; empirically stale
    quotes bias the label toward SELL. Industry practice (Lee-Ready with proper
    quote alignment) is to only quote-classify when the quote is time-aligned;
    here we approximate that by NaN-ing the bid/ask for any trade whose quote was
    older than `max_age_ms`, which forces classify_vectorized to fall back to the
    tick rule for those trades.

    The superior fix is merge_asof against the full NBBO stream (raw_quotes); use
    ibkr/reclassify.py once that stream has been collected. This function works on
    the per-trade bid/ask already stored in raw_ticks, so it needs no extra data.

    Inputs must be time-sorted. Returns the signed per-trade delta array.
    """
    bid = np.asarray(bid, dtype=float).copy()
    ask = np.asarray(ask, dtype=float).copy()
    age = np.asarray(quote_age_ms, dtype=float)
    stale = np.isnan(age) | (age > max_age_ms)
    bid[stale] = np.nan
    ask[stale] = np.nan
    delta, _, _ = classify_vectorized(
        np.asarray(price, dtype=float), np.asarray(size, dtype=float),
        bid, ask, prev_price, prev_dir, use_midpoint=use_midpoint,
    )
    return delta
