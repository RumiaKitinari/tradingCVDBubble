"""
history/bvc.py
--------------
Bulk Volume Classification (Easley, López de Prado, O'Hara 2012).

Splits each bar's volume into buy/sell using the standardized close-to-close
price change:   buy_volume = V * Phi(dP / sigma)
where Phi is the standard normal CDF and sigma is a rolling std of dP.

This is the standard estimator for bar data without tick-level quotes.
Unlike the wick decomposition (which back-derives delta from the price move
of the SAME bar — circular, see 7/11 report), BVC is probabilistic and
vectorized, and the literature applies it exactly to our "bars only" case.
It is still an ESTIMATE: rows produced here must carry quality='bvc'.

scipy is not a project dependency, so Phi uses the Abramowitz & Stegun 7.1.26
erf approximation (|error| < 1.5e-7 — far below estimation noise).
"""

import numpy as np
import pandas as pd

SIGMA_WINDOW = 50      # rolling window (bars) for the std of price changes
SIGMA_MIN_PERIODS = 10


def _erf(x: np.ndarray) -> np.ndarray:
    """Vectorized erf, Abramowitz & Stegun formula 7.1.26."""
    sign = np.sign(x)
    x = np.abs(x)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return sign * y


def _norm_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _erf(z / np.sqrt(2.0)))


def bvc_split(close: pd.Series, volume: pd.Series) -> pd.DataFrame:
    """
    Return a DataFrame (same index) with buying_volume / selling_volume / delta.

    close/volume must be aligned Series sorted by time. Bars with an
    undefined price change (first bar, zero sigma) split 50/50.
    """
    dp = close.astype(float).diff()
    sigma = dp.rolling(SIGMA_WINDOW, min_periods=SIGMA_MIN_PERIODS).std()
    # Early bars: fall back to the expanding std, then to the overall std.
    sigma = sigma.fillna(dp.expanding(min_periods=2).std())
    overall = dp.std()
    if pd.notna(overall) and overall > 0:
        sigma = sigma.fillna(overall)

    z = dp / sigma
    z = z.replace([np.inf, -np.inf], np.nan)
    buy_frac = pd.Series(_norm_cdf(z.to_numpy(dtype=float)), index=close.index)
    buy_frac = buy_frac.fillna(0.5)

    v = volume.astype(float)
    buy = v * buy_frac
    sell = v - buy
    return pd.DataFrame(
        {"buying_volume": buy, "selling_volume": sell, "delta": buy - sell},
        index=close.index,
    )
