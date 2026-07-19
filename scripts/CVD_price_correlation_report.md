# IBKR Tick CVD ↔ Price Correlation Study

**Symbol:** NVDA · **Data:** MongoDB `finviz_db.raw_ticks` / `candles(1sec, source=ibkr_tick)`
**Window:** 2026-07-08 15:11 ET ~ 2026-07-09 15:51 ET (~1.3 sessions, 978,555 ticks / 16,827 1-sec bars)
**Date:** 2026-07-09

---

## 0. One-line conclusion

> **Your intuition is right. Right now CVD barely tracks price.** The correlation between
> cumulative CVD level and price level is **near zero across every timeframe (mostly negative,
> −0.04 ~ −0.2)**. But the cause is not "the CVD logic is fundamentally wrong" — rather it is
> **① we only have 1.3 sessions of data so the estimate is unstable**, and **② the size-weighted
> approach is dominated by a handful of large prints plus a sell-classification bias on
> mid-sized fills**. **Moving up to 1-min / 3-min bars does not fix the "cumulative level"
> divergence** (the sum is preserved under aggregation). It does slightly improve the per-bar
> change signal, though.

---

## 1. Measurements

### A) Level correlation — `corr(price, CVD)` (regular hours 09:30–16:00, per session)

| TF | 07-08 | 07-09 | Session avg | Pooled |
|----|------:|------:|-------:|--------:|
| 1sec | +0.061 | −0.133 | −0.036 | −0.063 |
| 5sec | +0.057 | −0.153 | −0.048 | −0.111 |
| 1min | +0.016 | −0.146 | −0.065 | −0.139 |
| 3min | −0.051 | −0.096 | −0.074 | −0.197 |
| 5min | −0.047 | +0.038 | −0.005 | −0.186 |
| 15min| −0.092 | +0.250 | +0.079 | −0.205 |

→ Effectively **no correlation, if anything a weak negative one**. The sign flips between
sessions (unstable estimate).

### B) Change correlation — `corr(ΔPrice, per-bar delta)` (regular hours, pooled)

| TF | n | corr |
|----|--:|----:|
| 1sec | 16,677 | +0.033 |
| 5sec | 3,734 | +0.075 |
| **1min** | 323 | **+0.172** |
| 3min | 113 | +0.117 |
| 5min | 71 | +0.040 |
| 15min| 26 | +0.172 |

→ At the bar level there is a **weak positive correlation**. The 1-min region is the best,
but it is still very weak.

### C) Lead/lag (ΔPrice vs Δdelta, 1-min bars)

```
lag −1 : +0.070    lag 0 : +0.172 (max at same bar)    lag +1 : +0.018
```
→ CVD does **not lead** price. It only agrees weakly within the same bar.

### D) Directional hit rate — `sign(delta) == sign(ΔPrice)`

| TF | Hit rate |
|----|-----:|
| 1sec | 59.9% |
| 1min | 58.1% |
| 3min | 56.6% |
| 5min | 53.5% |

→ Only slightly better than a coin flip (50%). It actually declines toward higher timeframes.

---

## 2. Why it doesn't track — root-cause diagnosis

### Cause ①: Trade-size asymmetry (the key one)
| | BUY | SELL |
|--|--------:|---------:|
| Share of tick count | **57%** | 43% |
| Share of volume | 49.4% | **50.6%** |
| Avg fill size (regular hours) | **24.6** | **36.6** |
| Median size | 1 share | 3 shares |

→ **Buys are dominated by odd lots (1-share orders etc.), while sells are chunkier.** By tick
count buys lead, but in the size-weighted sum sells win, so CVD drifts down against price.

### Cause ②: Sell-classification bias on mid-sized fills
| Size bucket | net delta | buy% |
|--------|--------:|----:|
| 100 shares (exact) | −294,900 | 42.6% |
| 101–500 | −278,939 | 43.9% |
| 500–1000 | −146,145 | 40.4% |

→ Round-lot fills of 100–1000 shares are **consistently classified as only ~42% buys** → the
main driver of the CVD downward drift. This smells like classic **quote-lag misclassification**.
Because AllLast (trades) and BidAsk (quotes) are separate streams, if the quote updates late
relative to the trade, mid-sized fills get stamped as sells by mistake.

### Cause ③: Extreme outlier prints
4 prints of `>=100k shares` (including a **single 1,000,000-share buy print** @15:53) account for
**4.1%** of total gross flow in just 4 ticks. This is what makes CVD spike vertically on the chart.
→ Winsorizing at p99 moves the level correlation from −0.158 → −0.047, so it **eases the drift**
but does not flip it positive.

### Visual check
See `scripts/cvd_price_overlay.png`. Note especially the **afternoon of 7/9**: price plunges from
204.2 → 203.0, yet CVD stays high / rises — a textbook divergence (price ≠ order flow).

---

## 3. Direct answers to your questions

**Q. Do I need to fix the CVD logic?**
→ Partly yes. The underlying formula isn't wrong, but the current **size-weighted cumulative
approach is vulnerable to outliers and classification bias**. The improvements 3-a~d below are
worth A/B testing.

**Q. As data accumulates and I move to 1-min / 3-min bars, will accuracy improve?**
→ **The level divergence is not fixed by timeframe.** Aggregation preserves the delta sum, so
drift created at the 1-sec bar carries straight into the 1-min / 3-min bars. The **per-bar change
signal (B)** improves slightly around 1-min. The amount of data is **decisively insufficient** —
the correlation sign flips between the two sessions, so the estimate is too unstable to declare
the logic "right or wrong" from the current numbers.

---

## 4. Recommended actions (in priority order)

1. **Accumulate more data first (mandatory).** At least 10–20 regular sessions. The current
   1.3-session correlations are statistically meaningless.
2. **Verify quote-lag.** Log the bid/ask freshness at classification time (quote timestamp lag
   vs the trade) in `tick_collector` to check whether the mid-size sell bias is misclassification
   or real flow. If it's misclassification, apply Lee-Ready quote alignment / lag correction.
3. **Improve size handling, A/B:** (a) p99 winsorize, (b) filter odd-lot / 1-share prints,
   (c) handle large blocks separately. → In the experiment above, winsorizing halved the drift.
4. **Consider a session-anchored CVD.** Instead of an all-time cumsum, a CVD that resets at
   session start is better suited to intraday price tracking.
5. **Reframe the goal:** CVD was never meant to be 1:1 correlated with price — it's for
   **divergence detection**. Low correlation by itself is not "broken." That said, with a
   directional hit rate <55% (higher TFs), it is still too early to treat it as a trustworthy signal.

---

---

## 5. Follow-up (2026-07-09 afternoon)

### 5.1 "Is 5-sec / 1-sec candle decomposition more accurate?" — test results & the trap
Applying wick decomposition to the same IBKR 1-sec bars and comparing to tick-aggressor:

| | LEVEL corr (tick / wick) | Directional (tick / wick) |
|--|--|--|
| 1sec | 0.15·−0.28 / **0.89·0.23** | 59.5% / **90.7%** |
| 5sec | 0.15·−0.27 / **0.95·0.73** | 57.7% / **96.0%** |
| 1min | 0.11·−0.28 / **0.99·0.92** | 58.2% / **98.7%** |

→ Wick-decomp *appears* to track price far better, **but this is circular reasoning**. The wick
method *back-solves* buy/sell from the candle's open→close direction, so it merely restates
"price↑ = buying pressure" — it is **not order-flow information independent of price**. A 99%
directional hit rate is inevitable when delta is built from ΔP itself.
→ The whole reason for switching to IBKR ticks (the professor's directive: accurate tick-level
CVD) was to abandon wick estimation. Going back gives a pretty but meaningless line.
**Conclusion: do NOT revert to candle-decomp. Fix the tick classification instead.**
→ That said, tick-CVD's change correlation being near-zero / negative is too low to be dismissed
as mere "divergence" → there is classification noise present.

### 5.2 quote-lag instrumentation implemented (done)
Added diagnostic fields to `ibkr/tick_collector.py`: for each raw tick, store the classification-time
`bid`, `ask`, `quote_age_ms` (NBBO freshness), and `cls` (classification path: quote/tick/zerotick/none).
→ From the next collection session onward, running `scripts/analyze_quote_lag.py NVDA` will tell
whether the mid-size sell bias is stale-quote misclassification (buy% recovers to ~50 when only
fresh quotes are used) or real flow.

## 6. Building noise-removal infrastructure (2026-07-09)

**Key finding (pre-implementation validation):** offline noise removal alone — winsorize,
session-anchor — **does not fix the level tracking of cumulative CVD.** In fact, the 7/8 regular
session got *worse* under winsorize, with level correlation going +0.15 → −0.90 — that +0.15 was a
*fake* correlation created by a single 1M-share buy print, and once it was stripped out the real
sell drift surfaced. In other words, the culprit wrecking the level is not the outlier but the
**systematic sell-misclassification drift**, and that cannot be fixed without the quote-lag
classification fix (which needs instrumented data). Winsorize does, however, **consistently
improve the per-bar change signal** (7/8 regular session chg +0.03 → +0.15).

**Implementation (additive, existing `cvd_all` unchanged):** added to `cvd/calculator.py` —
- `delta_wins` — per-bar delta winsorized at session p99.5 (caps block / cross prints)
- `cvd_session` — session-anchored cumulative CVD that resets daily
- `cvd_wins` / `cvd_session_wins` — combinations of the above. `aggregate_pressure` carries them
  across all timeframes.
- Validation: the 1M-print bar was capped 999,933 → 13,379. Session resets confirmed.
  app/visualizer import OK.

→ **Next step (real level fix):** restart the instrumented collector → accumulate 1–2 weeks →
diagnose with `analyze_quote_lag.py` → fix the classification logic. Then layer these columns on
top to finish. Recommend switching the visualizer display only after the classification fix
(switching now makes some days' levels look worse).

## 7. quote-lag diagnosis + reclassification fix implemented (2026-07-10)

**Diagnosis (instrumented data 09:30–09:59, 30,295 ticks):** quote-lag confirmed real — 28.9% are
stale by 100ms+. Mid-size (100–1000 share) buy% drops from 53.7% on fresh quotes → 48.3% on stale
quotes, and net delta flips from + to −. **This confirms the hypothesis that stale quotes create
the sell misclassification.** (This session was only 29% stale, so the bias isn't as severe as
yesterday's. The collector died at 09:59, so only the first 29 minutes of regular hours were captured.)

**Validation (reclassifying with stored bid/ask):** demoting stale quotes (trust only fresh quotes,
tick-rule for the rest) improves things:
| reclassify | LEVEL(30s) | direction(30s) |
|--|--:|--:|
| none | 0.834 | 49.1% |
| >100ms | 0.899 | 61.4% |
| >50ms | 0.903 | 59.6% |
| >20ms | 0.932 | 57.9% |

**Implementation:**
- `cvd/aggressor.py::classify_demote_stale()` — stale-quote demotion reclassification (reuses
  classify_vectorized)
- `cvd/calculator.py::load_from_mongo(..., reclassify_stale_ms=50)` — reclassify on raw_tick load
  (validated, usable immediately)
- `ibkr/tick_collector.py` — store the NBBO stream into `raw_quotes` (for the proper merge_asof fix)
- `ibkr/reclassify.py` — time-aligned reclassification via merge_asof (the proper approach;
  verifiable once one session of raw_quotes is collected). Currently only 48 raw_quotes (1.5 min)
  exist → a <50% coverage warning fires → needs the next collection session.

**To do next session:** restart the (updated) collector → collect one regular session of raw_quotes →
run `python -m ibkr.reclassify --ticker NVDA` to validate the proper fix → if good, apply with
`--write` + switch the visualizer to `cvd_session_wins` / reclassified delta.

## Appendix: how to reproduce
- `scratchpad/cvd_corr.py` — A~D correlation measurements
- `scratchpad/cvd_diag.py`, `cvd_diag2.py` — classification bias / size diagnosis / alternative CVD
  definition comparison
- `scratchpad/cvd_final.py` — outlier analysis + overlay chart generation
