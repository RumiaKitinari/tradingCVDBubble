"""
cvd/visualizer.py
-----------------
Two-screen Bookmap-style chart:
  Screen 1 (large): candlestick only
  Screen 2:         Buy/Sell volume (default) + CVD all-time + CVD session-reset
                    + Total cumulative volume + Buy ratio (right axis), all legend-toggled

Timeframe buttons auto-scale the x-axis (and y-axis) to a sensible default span
per timeframe. A second button row filters trading hours (All / Regular / Extended).
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .calculator import run_pipeline, TIMEFRAME_RULE, DAILY_OR_ABOVE, WEEK_OR_ABOVE


# Default visible span per timeframe (how far back from the last bar to show).
DEFAULT_SPAN = {
    "1min":   pd.Timedelta(hours=12),
    "3min":   pd.Timedelta(days=1),
    "5min":   pd.Timedelta(days=1),
    "15min":  pd.Timedelta(days=2),
    "1hr":    pd.Timedelta(days=7),
    "3hr":    pd.Timedelta(days=21),
    "1day":   pd.Timedelta(days=90),
    "1week":  pd.Timedelta(days=365),
    "1month": pd.Timedelta(days=1095),
}

PAD = 0.05   # y-axis padding (5% above/below the data in view)


def _span_window(df, tf):
    """Return (x_start, x_end) for the default view of this timeframe."""
    x_end = df.index.max()
    x_start = x_end - DEFAULT_SPAN[tf]
    # don't go before the first bar
    if x_start < df.index.min():
        x_start = df.index.min()
    return x_start, x_end


def _yrange(series, lo_extra=PAD, hi_extra=PAD):
    """Min/max of a series with padding; returns None if empty/all-NaN."""
    s = series.dropna()
    if s.empty:
        return None
    lo, hi = float(s.min()), float(s.max())
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    rng = hi - lo
    return [lo - rng * lo_extra, hi + rng * hi_extra]


def build_chart(df_1min, frames: dict, ticker: str) -> go.Figure:

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.40],
        vertical_spacing=0.05,
        specs=[[{}], [{"secondary_y": True}]],   # screen 2 has a right axis (Buy Ratio)
        subplot_titles=(
            f"{ticker} — Candlestick",
            "Buy/Sell Volume (default) · CVD · Total Cumulative · Buy Ratio (toggle in legend)",
        )
    )

    timeframes = list(TIMEFRAME_RULE.keys())
    default_tf = "1hr"
    default_idx = timeframes.index(default_tf)

    # Each timeframe contributes this many traces, in this fixed order:
    #   0 candle | 1 buy bar | 2 sell bar | 3 CVD all | 4 CVD reset
    #   5 cum total | 6 buy ratio
    N_TRACES = 7

    for tf in timeframes:
        df = frames[tf]
        on = (tf == default_tf)
        # default-ON traces: candle + buy/sell bars; the rest start legend-only
        v_main = on                       # candle + bars
        v_opt  = "legendonly" if on else False

        # 1. Candlestick (screen 1)
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name=f"Candle ({tf})",
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
            visible=v_main, showlegend=False,
        ), row=1, col=1)

        # 2. Buy bar (screen 2, up)
        fig.add_trace(go.Bar(
            x=df.index, y=df["buy_pressure"], name="Buy Volume",
            marker_color="rgba(38,166,154,0.85)", visible=v_main, showlegend=on,
            hovertemplate="<b>%{x}</b><br>Buy: %{y:,.0f}<extra></extra>",
        ), row=2, col=1, secondary_y=False)

        # 3. Sell bar (screen 2, down)
        fig.add_trace(go.Bar(
            x=df.index, y=-df["sell_pressure"], name="Sell Volume",
            marker_color="rgba(239,83,80,0.85)", visible=v_main, showlegend=on,
            customdata=df["sell_pressure"],
            hovertemplate="<b>%{x}</b><br>Sell: %{customdata:,.0f}<extra></extra>",
        ), row=2, col=1, secondary_y=False)

        # 4. CVD all-time
        fig.add_trace(go.Scatter(
            x=df.index, y=df["cvd_all_end"], mode="lines", name="CVD (all-time)",
            line=dict(color="#ba68c8", width=2), connectgaps=True,
            visible=v_opt, showlegend=on,
            hovertemplate="<b>%{x}</b><br>CVD all: %{y:,.0f}<extra></extra>",
        ), row=2, col=1, secondary_y=False)

        # 5. CVD session-reset
        fig.add_trace(go.Scatter(
            x=df.index, y=df["cvd_end"], mode="lines", name="CVD (session)",
            line=dict(color="#4fc3f7", width=2), connectgaps=True,
            visible=v_opt, showlegend=on,
            hovertemplate="<b>%{x}</b><br>CVD session: %{y:,.0f}<extra></extra>",
        ), row=2, col=1, secondary_y=False)

        # 6. Total cumulative volume
        fig.add_trace(go.Scatter(
            x=df.index, y=df["volume"].cumsum(), mode="lines", name="Cumulative Volume",
            line=dict(color="#ffd54f", width=2), connectgaps=True,
            visible=v_opt, showlegend=on,
            hovertemplate="<b>%{x}</b><br>Cum Vol: %{y:,.0f}<extra></extra>",
        ), row=2, col=1, secondary_y=False)

        # 7. Buy ratio (right axis)
        ratio = df["buy_pressure"] / (df["buy_pressure"] + df["sell_pressure"])
        fig.add_trace(go.Scatter(
            x=df.index, y=ratio, mode="lines", name="Buy Ratio",
            line=dict(color="#ff9800", width=1.6, dash="dot"), connectgaps=True,
            visible=v_opt, showlegend=on,
            hovertemplate="<b>%{x}</b><br>Buy Ratio: %{y:.1%}<extra></extra>",
        ), row=2, col=1, secondary_y=True)

    # ── Rangebreak rules (hide empty x gaps) ──
    intraday_breaks = [dict(bounds=["sat", "mon"]), dict(bounds=[20, 4], pattern="hour")]
    daily_breaks = [dict(bounds=["sat", "mon"])]
    no_breaks = []

    def breaks_for(tf):
        if tf in WEEK_OR_ABOVE:  return no_breaks
        if tf in DAILY_OR_ABOVE: return daily_breaks
        return intraday_breaks

    # ── Timeframe buttons: toggle visibility + auto x/y range + rangebreaks ──
    buttons = []
    for i, tf in enumerate(timeframes):
        df = frames[tf]

        # visibility for all timeframes' traces
        visibility = []
        for j, tf2 in enumerate(timeframes):
            if j == i:
                visibility += [True, True, True, "legendonly", "legendonly", "legendonly", "legendonly"]
            else:
                visibility += [False] * N_TRACES
        showlegend = []
        for j in range(len(timeframes)):
            showlegend += ([False, True, True, True, True, True, True] if j == i
                           else [False] * N_TRACES)

        # auto x-range for the default view of this timeframe
        x0, x1 = _span_window(df, tf)
        in_view = df.loc[x0:x1]

        # y-range: screen 1 (candle) fixed to high/low of the view;
        # screen 2 uses autorange so it re-fits whenever you toggle a series.
        y1 = _yrange(pd.concat([in_view["high"], in_view["low"]])) if not in_view.empty else None

        breaks = breaks_for(tf)
        layout = {
            "title": f"<b>{ticker}</b> — {tf}",
            "xaxis.rangebreaks":  breaks,
            "xaxis2.rangebreaks": breaks,
            "xaxis.range":  [x0, x1],
            "xaxis2.range": [x0, x1],
            "yaxis2.autorange": True,   # screen 2 left axis re-fits on legend toggle
        }
        if y1: layout["yaxis.range"] = y1

        buttons.append(dict(
            label=tf, method="update",
            args=[{"visible": visibility, "showlegend": showlegend}, layout],
        ))

    # ── Session-hours filter buttons (rangebreaks only) ──
    weekend = dict(bounds=["sat", "mon"])
    session_break_sets = {
        "All hours": [weekend, dict(bounds=[20, 4], pattern="hour")],
        "Regular":   [weekend, dict(bounds=[16, 9.5], pattern="hour")],
        "Extended":  [weekend, dict(bounds=[9.5, 16], pattern="hour"), dict(bounds=[20, 4], pattern="hour")],
    }
    session_buttons = [
        dict(label=lbl, method="relayout",
             args=[{"xaxis.rangebreaks": brk, "xaxis2.rangebreaks": brk}])
        for lbl, brk in session_break_sets.items()
    ]

    # ── Layout ──
    df0 = frames[default_tf]
    x0, x1 = _span_window(df0, default_tf)
    in0 = df0.loc[x0:x1]
    y1_0 = _yrange(pd.concat([in0["high"], in0["low"]]))

    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b> — {default_tf}", font=dict(size=18)),
        template="plotly_dark",
        height=900,
        barmode="overlay",
        bargap=0.1,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.06, xanchor="left", x=0),
        margin=dict(l=60, r=60, t=120, b=80),
        updatemenus=[
            dict(type="buttons", direction="right", x=0.0, y=1.10, xanchor="left",
                 buttons=buttons, bgcolor="#2d2d2d", bordercolor="#888",
                 font=dict(color="white", size=12), active=default_idx),
            dict(type="buttons", direction="right", x=0.0, y=1.04, xanchor="left",
                 buttons=session_buttons, bgcolor="#1e1e1e", bordercolor="#888",
                 font=dict(color="#cccccc", size=11), active=0),
        ],
    )

    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume / CVD", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Buy Ratio", row=2, col=1, secondary_y=True,
                     tickformat=".0%", range=[0, 1])
    fig.update_xaxes(title_text="Time", row=2, col=1)

    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)

    # apply default rangebreaks + initial x/y view
    fig.update_xaxes(rangebreaks=breaks_for(default_tf), range=[x0, x1])
    if y1_0: fig.update_yaxes(range=y1_0, row=1, col=1)
    fig.update_yaxes(autorange=True, row=2, col=1, secondary_y=False)  # re-fits on toggle

    return fig


def show_chart(ticker: str = "NVDA", save_html: bool = True, auto_fetch: bool = True):
    ticker = ticker.upper()
    df_1min, frames = run_pipeline(ticker)

    # If there's no data, auto-fetch from FinViz then retry once
    if (df_1min.empty or not frames) and auto_fetch:
        print(f"[Visualizer] No data for {ticker} — fetching from FinViz...")
        try:
            from finviz.new_finviz import fetch_and_save
            fetch_and_save(ticker, timeframe="i1")
            df_1min, frames = run_pipeline(ticker)
        except Exception as e:
            print(f"[Visualizer] Auto-fetch failed: {e}")

    if df_1min.empty or not frames:
        print(f"[Visualizer] No data for {ticker}. Aborting.")
        return

    fig = build_chart(df_1min, frames, ticker)

    if save_html:
        path = f"{ticker}_cvd_chart.html"
        fig.write_html(path)
        print(f"[Visualizer] Saved → {path}")

    fig.show()


if __name__ == "__main__":
    import sys
    # Usage: python -m cvd.visualizer [TICKER]
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    show_chart(ticker)
