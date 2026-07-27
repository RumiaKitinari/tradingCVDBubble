# CVD Bubble Dashboard — Reading Guide

A short guide to every visual on the chart: what each color means, how the data
sources are merged and marked, how the CVD lines are computed, and how to use
the Level-2 depth, the volume pies, and the "Jump to" time navigation.

The dashboard runs at `http://127.0.0.1:8050` (start it with `./start_all.sh`).

---

## 1. Quick start

1. **Ticker** — type a symbol (e.g. `NVDA`) in the top-left box.
   - If the symbol is not recognized anywhere (FinViz or IBKR), the chart says
     **"'XYZ' is not a recognized ticker"** instead of spinning forever.
   - A valid but brand-new 1-second ticker shows **"Fetching … backfill in
     progress"** while its history is pulled in the background, then fills in.
2. **Data source** — the radio toggle picks the base feed:
   - **Tick (IBKR)** → true trade-by-trade data (1-second and up).
   - **FinViz** → 1-minute consolidated bars and up (used for longer history).
3. **Active Timeframe** — the dropdown (1sec, 5sec, 1min, 5min, 1hr, 1day, …).
   The list adapts to the chosen source.

The chart has **three stacked panels** sharing one time axis:
- **Top:** price candles (+ bubbles, + L2 depth, + volume pies).
- **Middle & bottom:** the indicator panels (buy/sell volume bars, CVD lines,
  cumulative lines).

---

## 2. Data sources — how they are merged and marked

We stitch the best available feed for each time range into one continuous
chart. Because the feeds differ in quality, the **background shading tells you
how trustworthy the buy/sell split is** in each region:

| Background | Meaning | Buy/Sell quality |
|---|---|---|
| **No shading** | Real tick data (`ibkr_tick`) | **Ground truth** — every trade classified by the actual aggressor side |
| **Yellow / gold tint** | Estimated (`BVC` or `wick`) | Estimate from bar shape only — no tick detail |
| **Blue tint** | IBKR historical backfill / **mixed** | Partly estimated; a coarse bar mixing real ticks and estimated fill |

A small label at the left edge of each shaded run names the estimator
(`BVC (est.)`, `Wick (est.)`, `IBKR hist (est.)`, `Mixed (part est.)`).

**Rule of thumb: unshaded = real, shaded = estimated.**

### What happens when there is no tick data?

Without trade-by-trade ticks we cannot know the true aggressor, so we
**estimate** the buy/sell split from the bar itself using **BVC (Bulk Volume
Classification** — Easley, López de Prado & O'Hara, 2012):

```
buy_volume = Volume × Φ(ΔP / σ)
```

where `ΔP` is the close-to-close price change, `σ` is a rolling std of `ΔP`, and
`Φ` is the standard-normal CDF. In words: the more a bar closed up relative to
its recent volatility, the larger the share of its volume counted as buying.
This is the standard estimator for "bars only" data and is what the **default
CVD line** falls back to over any non-tick region.

> Closing-auction bars (the single Market-On-Close cross at ~15:59) are
> **neutralized** (buy = sell, delta = 0) and drawn as **gray** volume bars,
> because a single-priced auction has no meaningful direction. This keeps one
> giant print from dominating the whole CVD curve.

---

## 3. The three CVD lines (middle panel)

CVD = **Cumulative Volume Delta** = running sum of (buy − sell) volume. Three
versions are plotted so they can be compared; toggle each in the legend:

| Line | Color / style | How it is computed |
|---|---|---|
| **CVD (all-time)** | purple, solid | The **primary** CVD. Uses **real tick delta** where we have ticks, and **BVC** where only FinViz bars exist. This is the "best available per region" curve. |
| **CVD (BVC est.)** | cyan, dashed | **BVC everywhere** (probabilistic, from price change / volatility). Consistent method across the whole chart. |
| **CVD (wick est.)** | pink, dash-dot | **Pure wick decomposition everywhere** — buy/sell inferred from each candle's wick lengths, summed from the finest bars up. |

They agree in tick regions and diverge in estimated regions — the gap between
them is a visual measure of estimation uncertainty.

Also on this panel: **Cum Total / Cum Buy / Cum Sell** (cumulative volume lines)
and **Buy Ratio**.

---

## 4. Buy / Sell volume bars (indicator panel)

Per-bar buy volume points **up (teal)**, sell volume points **down (red)**.
Three estimation methods are available — **tick**, **wick**, and **BVC** — each
computed as `buy = (V + delta)/2`, `sell = (V − delta)/2` from that method's
delta. Each method is one legend entry that toggles its buy and sell together.

---

## 5. Z-Score Volume Bubbles (on the price panel)

A bubble marks a **statistically unusual bar**: it appears when the bar's
**delta z-score ≥ 2** OR its **volume z-score ≥ 3** (z-score = how many standard
deviations above the recent mean). Bubble **size grows with the z-score**; the
label shows the signed delta (`+3.2M`) or the total volume.

**Bubble colors:**

| Color | Meaning |
|---|---|
| 🟢 **Green** | Strong net **BUYING** (delta z ≥ 2, delta > 0) |
| 🔴 **Crimson** | Strong net **SELLING** (delta z ≥ 2, delta < 0) |
| 🟣 **Purple** | **Absorption** — a large delta (z ≥ 2) that barely moved price (candle body < 0.6× its average). Big pressure met by an opposing wall. |
| 🟡 **Gold** | **Volume spike** (volume z ≥ 3) without an extreme delta — heavy two-sided activity. |

Toggle all bubbles with the **Bubbles** button.

---

## 6. Volume Pies (on the price panel)

When **Pies** are on, the visible window is divided into equal slices and each
slice gets a pie showing its **Buy (teal) vs Sell (red)** volume split — a
quick left-to-right read of who dominated each segment. Hover a pie for the
exact buy/sell numbers and the time range it covers. The strip auto-sizes as you
zoom, so each pie always covers a whole number of bars.

---

## 7. Level-2 (L2) Depth (on the price panel)

Turn on **L2 Depth** to overlay the resting order book as a Bookmap-style
heatmap behind the candles:

- **Heatmap color = resting size** at each price: transparent/dark = thin,
  blue → cyan = ordinary depth, **yellow → white = the big walls** (top ~2% of
  resting size). Hover any cell for `price · size`.
- **Support / Resistance lines** — dashed horizontal lines mark persistent
  resting liquidity: **blue = support**, **amber = resistance**. The right-edge
  label shows the wall's average size (e.g. `R 205.58 · 1.2M`). Thicker/brighter
  lines = stronger, more persistent walls.

L2 is depth-only and available for the actively collected tickers. It also works
in **Jump-to** mode — jump to a past time and the book at that time is shown.

---

## 8. "Jump to (ET)" — go to a specific time

Type a date/time in the **Jump to (ET)** box and click **Jump**:

- `2026-07-22 19:50` → centers the **19:50 bar** of 2026-07-22 on screen.
- `2026-07-22` → jumps to that day.
- Times are **US Eastern (ET)**, matching how the data is stored.

The dashboard loads a window of history around that instant and centers the
exact bar you typed. Click **Live** to snap back to the live tail (most recent
bars, auto-updating).

---

## 9. Other controls

- **Bubbles** — show/hide the Z-Score bubbles.
- **Pies** — show/hide the volume pie strip.
- **L2 Depth** — show/hide the order-book heatmap and S&R lines.
- **Y Auto-Scale** — ON: the price axis auto-fits as you pan. OFF: your manual
  y-zoom sticks.
- **Manual Refresh** — force a data reload without waiting for the poll.

---

*Buy/sell splits over shaded (estimated) regions are approximations, not
measured aggressor flow. Treat unshaded (real-tick) regions as ground truth and
shaded regions as directional estimates.*
