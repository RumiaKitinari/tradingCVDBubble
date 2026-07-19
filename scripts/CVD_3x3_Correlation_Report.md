# CVD ↔ Price Correlation (change-based, real tick CVD)

Bar timeframe: **1min**. Metric: within-session, within-day correlation of per-bar **delta** vs per-bar **price change** (stationary), plus directional hit-rate `sign(delta)==sign(ΔP)`. This replaces the earlier level-correlation of two cumulative series (spurious) and the wick-CVD data source (circular).

## Summary by Tier (mean change-corr)
| Tier   |   Pre-Market corr |   Regular corr |   After-Hours corr |
|:-------|------------------:|---------------:|-------------------:|
| Mega   |            -0.221 |          0.045 |              0.228 |

## Detailed change-correlation
| Ticker   |   Bars |   Pre-Market corr |   Regular corr |   After-Hours corr |
|:---------|-------:|------------------:|---------------:|-------------------:|
| NVDA     |   1122 |            -0.221 |          0.045 |              0.228 |

## Directional hit-rate % (50% = coin flip)
| Ticker   |   Pre-Market hit% |   Regular hit% |   After-Hours hit% |
|:---------|------------------:|---------------:|-------------------:|
| NVDA     |              64.1 |           52.1 |               68.3 |

## Skipped (no real tick data)
- **AAPL** (Mega): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **MSFT** (Mega): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **GME** (Micro): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **AMC** (Micro): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **PLTR** (Micro): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **PENN** (Nano): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **CHWY** (Nano): no raw_ticks (skipped — would otherwise use circular wick CVD)
- **RUM** (Nano): no raw_ticks (skipped — would otherwise use circular wick CVD)

> Only NVDA currently has `raw_ticks`. Collect a regular session with `python -m ibkr.tick_collector --ticker <SYM>` to add a ticker to the matrix.
