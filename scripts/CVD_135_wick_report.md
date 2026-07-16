# 135-Case CVD↔Price Tracking — WICK CVD (1sec backfill)

Source: `ibkr_hist` 1sec backfill, buy/sell estimated by wick decomposition. **Correlation here is partly circular (delta derived from price) — compare against the tick report, do not trust in isolation.** 9 tickers × 3 sessions × 5 timeframes.

Each cell is **change-corr / directional-hit% / n bars**. `—` = no data. Higher corr & hit% = CVD tracks price better; watch n (tiny n, e.g. at 1hr, = unreliable).

## 1sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.23/80%/59397 | +0.56/94%/70197 | +0.02/87%/44996 |
| Mega | AAPL | +0.28/79%/59397 | +0.52/92%/70197 | +0.28/85%/44996 |
| Mega | MSFT | +0.23/77%/19799 | +0.33/85%/23399 | +0.24/88%/16198 |
| **mean** | **corr** | **+0.24** | **+0.47** | **+0.18** |

## 5sec
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.24/76%/11877 | +0.64/81%/14037 | +0.01/80%/8996 |
| Mega | AAPL | +0.28/73%/11877 | +0.55/78%/14037 | +0.28/84%/8996 |
| Mega | MSFT | +0.26/78%/3959 | +0.56/74%/4679 | +0.24/83%/3238 |
| **mean** | **corr** | **+0.26** | **+0.58** | **+0.18** |

## 1min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.35/65%/987 | +0.68/78%/1167 | +0.02/63%/746 |
| Mega | AAPL | +0.31/65%/987 | +0.55/70%/1167 | +0.28/77%/746 |
| Mega | MSFT | +0.50/73%/329 | +0.62/69%/389 | +0.25/70%/268 |
| **mean** | **corr** | **+0.38** | **+0.62** | **+0.18** |

## 5min
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.29/67%/195 | +0.66/74%/231 | +0.41/66%/146 |
| Mega | AAPL | +0.55/68%/195 | +0.45/67%/231 | +0.32/67%/146 |
| Mega | MSFT | +0.52/71%/65 | +0.53/71%/77 | +0.44/71%/52 |
| **mean** | **corr** | **+0.45** | **+0.55** | **+0.39** |

## 1hr
| Tier | Ticker | Pre | Regular | After |
|---|---|---|---|---|
| Mega | NVDA | +0.58/80%/15 | +0.76/93%/15 | +0.72/78%/9 |
| Mega | AAPL | +0.23/53%/15 | +0.63/53%/15 | +0.01/50%/9 |
| Mega | MSFT | +0.87/100%/5 | +0.98/100%/5 | +0.36/33%/3 |
| **mean** | **corr** | **+0.56** | **+0.79** | **+0.36** |

## Skipped
- **GME** (Micro): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker GME --days N)
- **AMC** (Micro): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker AMC --days N)
- **PLTR** (Micro): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker PLTR --days N)
- **PENN** (Nano): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker PENN --days N)
- **CHWY** (Nano): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker CHWY --days N)
- **RUM** (Nano): no ibkr_hist 1sec backfill (run: python -m ibkr.backfill --ticker RUM --days N)
