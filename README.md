# Trading CVD Bubble (Temporary Project Name)

A **Cumulative Volume Delta (CVD)** visualization tool for spotting short-squeeze
pressure. It stitches IBKR tick data and FinViz consolidated bars into one chart
(candles + volume-delta bubbles + Level-2 depth heatmap, Bookmap-style) and shows
how buying vs. selling pressure accumulates over time.

## Running it

The whole system is just **two processes**:

```bash
./start_all.sh      # 1) unified collector  2) Dash app (http://127.0.0.1:8050)
./stop_all.sh       # stop both
```

- **Collector** (`python -m ibkr.dynamic_collector`) — one IB connection that
  handles ticks → 1-second bars, one-time 1-second catch-up backfill, and L2
  depth. It is **on-demand**: nothing is collected until you search a ticker in
  the app. The queue keeps the most-recently-searched tickers (up to 5) and
  evicts the oldest. Requires IB Gateway running and logged in (API on port 7497).
- **App** (`python -m app`) — the dashboard.

## How to read the chart

See **[USAGE.md](USAGE.md)** for a full reading guide: data-source shading
(real vs. estimated), the three CVD lines, the Z-Score bubble colors, buy/sell
volume bars, the volume pies, the Level-2 depth heatmap, and the "Jump to (ET)"
time navigation.

## Credit

Based on initial code by Aisiri Cherrimane Narendra —
[github.com/aisiricherrimane](https://github.com/aisiricherrimane)
