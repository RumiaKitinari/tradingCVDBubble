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

## Known issues & current limitations (handoff)

An honest running list of what is incomplete or behaves imperfectly right now,
so it is clear what still needs attention.

### Data completeness
1. **Level-2 depth is not the full order book.** We request 20 levels per side
   from IBKR, and IBKR **SMART depth** returns up to ~20 levels within roughly
   ±5% of price. This is much deeper than before and reads Bookmap-style, but it
   is **not** the full Bookmap book (hundreds of levels). Going deeper would
   require single-exchange TotalView deep book (which trades cross-exchange
   aggregation for depth) or a dedicated market-depth vendor.
2. **Backfilled 1-second data is incomplete (~10–15% of the consolidated tape,
   IEX-biased).** Historical 1-second bars pulled from IBKR are thin, and
   after-hours backfill bars are flat / zero-volume filler, so backfilled
   1-second candles differ noticeably from TradingView. **Live-collected**
   1-second data during regular hours is accurate; only the historical backfill
   is limited. Practical effect: price/trend over backfilled regions is usable,
   but **CVD there is a BVC/wick estimate, not measured aggressor flow** (the
   chart shades these regions — see USAGE.md).
3. **The collector must run continuously for complete 1-second history.** Any
   downtime is a **permanent gap** in tick-level data (full 1-second history
   cannot be re-backfilled after the fact). After-hours tick data is also thin
   (odd lots; prices can lag the consolidated tape).

### Level-2 support/resistance display
4. **Sometimes only one S&R line shows (e.g. support but no resistance).** Two
   causes: (a) the **L2 Depth** selector clips the heatmap to N levels around the
   current price, so a wall on the other side that sits just outside the clip is
   dropped — most visible at **10 / 20 levels**; and (b) a side only draws a line
   if it has a wall that passes the persistence + size threshold, so a one-sided
   book at that moment yields a one-sided line. **Workaround:** switch L2 Depth to
   **Full book** to see both sides. *(Follow-up idea: compute S&R on the full
   book even when the heatmap is clipped, so both lines always reflect the real
   strongest walls regardless of the depth zoom.)*
5. **S&R side classification depends on the bid/ask dominance in the captured
   window.** If the last close drifts away from the resting book, the
   classification can skew everything to one side.

### Closing-auction detection
6. **The auction condition code is confirmed for NVDA only.** The closing-cross
   token `'6'` was verified against the real NVDA 16:00 print; other tickers or
   venues may stamp a different token (`'M'` / `'X'`). The volume heuristic still
   catches those, but code-based precision is NVDA-verified only. Run
   `python -m scripts.inspect_auction_conditions --ticker <SYM>` after a close to
   confirm a new ticker's code.

### App / operational
7. **A brand-new 1-second ticker shows "Fetching…" for up to ~1 minute** while
   its backfill runs asynchronously. An unrecognized symbol now shows
   "Unknown ticker" (via a FinViz probe), but a symbol that exists on IBKR yet is
   not listed by FinViz could be misflagged.
8. **After restarting the app/server, hard-refresh the browser** — a stale Dash
   callback spec otherwise leaves requests stalled.

## Credit

Based on initial code by Aisiri Cherrimane Narendra —
[github.com/aisiricherrimane](https://github.com/aisiricherrimane)
