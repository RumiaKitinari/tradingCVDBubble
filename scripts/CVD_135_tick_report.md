# 135-Case CVD↔Price Tracking — TICK CVD (real aggressor)

Source: `raw_ticks` (live tick_collector), per-trade quote/tick classification. 9 tickers × 3 sessions × 5 timeframes.

Each cell is **change-corr / directional-hit% / n bars**. `—` = no data. Higher corr & hit% = the signal tracks price better; watch n (tiny n, e.g. at 1hr, = unreliable).

## 1sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.17/93%/5886 | +0.00/63%/23200 | +0.04/68%/14289 |
| Micro | SOFI | +0.14/89%/777 | -0.17/53%/5396 | +0.14/84%/152 |
| Nano | RUM | +0.73/100%/4 | +0.06/76%/587 | +0.21/50%/9 |
| **mean** | **corr** | **+0.34** | **-0.04** | **+0.13** |

## 5sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.11/87%/4058 | +0.00/52%/6628 | +0.09/77%/4937 |
| Micro | SOFI | +0.14/88%/670 | -0.22/45%/3105 | +0.14/84%/141 |
| Nano | RUM | +0.73/100%/4 | +0.04/76%/519 | +0.21/50%/9 |
| **mean** | **corr** | **+0.33** | **-0.06** | **+0.15** |

## 1min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.04/64%/658 | +0.02/50%/556 | +0.22/69%/836 |
| Micro | SOFI | +0.29/76%/253 | -0.38/33%/389 | +0.20/83%/94 |
| Nano | RUM | +0.73/100%/4 | -0.03/68%/263 | +0.35/67%/8 |
| **mean** | **corr** | **+0.35** | **-0.13** | **+0.26** |

## 5min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.08/60%/130 | -0.00/47%/109 | +0.12/73%/177 |
| Micro | SOFI | +0.49/68%/64 | -0.50/30%/77 | +0.23/82%/40 |
| Nano | RUM | +0.79/100%/3 | +0.00/57%/76 | +0.21/50%/7 |
| **mean** | **corr** | **+0.45** | **-0.17** | **+0.19** |

## 1hr
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.79/60%/10 | -0.67/50%/6 | -0.43/33%/12 |
| Micro | SOFI | -0.87/80%/5 | -0.55/20%/5 | -0.98/0%/3 |
| Nano | RUM | -0.55/67%/3 | +0.45/75%/5 | +0.43/50%/3 |
| **mean** | **corr** | **-0.21** | **-0.26** | **-0.33** |

## Skipped
- **AAPL** (Mega): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker AAPL)
- **MSFT** (Mega): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker MSFT)
- **PLTR** (Micro): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker PLTR)
- **GME** (Micro): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker GME)
- **CHWY** (Nano): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker CHWY)
- **PENN** (Nano): no raw_ticks (live-only; run: python -m ibkr.tick_collector --ticker PENN)
