# 135-Case OBI↔Price — CONTEMPORANEOUS (same-bar)

Source: `trading_cvd.level2_snapshots` (L2 depth). Cell = corr(mean OBI, same-bar ΔP) / hit% / n. Book imbalance vs concurrent price move.

Each cell is **change-corr / directional-hit% / n bars**. `—` = no data. Higher corr & hit% = the signal tracks price better; watch n (tiny n, e.g. at 1hr, = unreliable).

## 1sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.04/48%/1736 | +0.46/45%/39 |
| **mean** | **corr** | **—** | **-0.04** | **+0.46** |

## 5sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.14/43%/351 | +0.29/50%/25 |
| **mean** | **corr** | **—** | **-0.14** | **+0.29** |

## 1min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.59/45%/33 | +0.34/50%/16 |
| **mean** | **corr** | **—** | **-0.59** | **+0.34** |

## 5min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.31/43%/7 | -0.12/70%/10 |
| **mean** | **corr** | **—** | **-0.31** | **-0.12** |

## 1hr
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | — | — |
| **mean** | **corr** | **—** | **—** | **—** |

## Skipped
- **AAPL** (Mega): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker AAPL)
- **MSFT** (Mega): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker MSFT)
- **GME** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker GME)
- **AMC** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker AMC)
- **PLTR** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker PLTR)
- **PENN** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker PENN)
- **CHWY** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker CHWY)
- **RUM** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker RUM)


---

# 135-Case OBI↔Price — PREDICTIVE (OBI leads 1 bar)

Cell = corr(mean OBI at bar t, ΔP of bar t+1) / hit% / n. **This is the one that matters** — whether resting-liquidity imbalance LEADS price (the main reason to pay for L2). Compare its hit% against the tick-CVD grid.

Each cell is **change-corr / directional-hit% / n bars**. `—` = no data. Higher corr & hit% = the signal tracks price better; watch n (tiny n, e.g. at 1hr, = unreliable).

## 1sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.04/47%/1736 | -0.11/52%/39 |
| **mean** | **corr** | **—** | **-0.04** | **-0.11** |

## 5sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.07/45%/351 | -0.15/55%/25 |
| **mean** | **corr** | **—** | **-0.07** | **-0.15** |

## 1min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.36/48%/33 | +0.03/57%/16 |
| **mean** | **corr** | **—** | **-0.36** | **+0.03** |

## 5min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | -0.51/29%/7 | -0.15/80%/10 |
| **mean** | **corr** | **—** | **-0.51** | **-0.15** |

## 1hr
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | — | — | — |
| **mean** | **corr** | **—** | **—** | **—** |

## Skipped
- **AAPL** (Mega): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker AAPL)
- **MSFT** (Mega): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker MSFT)
- **GME** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker GME)
- **AMC** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker AMC)
- **PLTR** (Micro): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker PLTR)
- **PENN** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker PENN)
- **CHWY** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker CHWY)
- **RUM** (Nano): no L2 snapshots (needs L2 subscription, then: python -m ibkr.level2_collector --ticker RUM)
