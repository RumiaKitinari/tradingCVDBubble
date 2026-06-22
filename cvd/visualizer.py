"""
cvd/visualizer.py
-----------------
Three-screen Bookmap-style chart:
  Screen 1: candlestick
  Screen 2: indicator panel A (Buy/Sell volume default + CVD/Cumulative/Ratio toggles)
  Screen 3: indicator panel B — identical set, independently toggled (own legend)

So you can show Buy/Sell volume on one panel and, say, CVD on the other and
compare them at the same time. Timeframe buttons auto-scale the x-axis (and the
candle y-axis); a second button row filters trading hours.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .calculator import run_pipeline, TIMEFRAME_RULE, DAILY_OR_ABOVE, WEEK_OR_ABOVE


# Default visible span per timeframe (how far back from the last bar to show).
DEFAULT_SPAN = {
    "1min":   pd.Timedelta(hours=6),
    "3min":   pd.Timedelta(hours=18),
    "5min":   pd.Timedelta(hours=30),
    "15min":  pd.Timedelta(hours=90),
    "1hr":    pd.Timedelta(days=7),
    "3hr":    pd.Timedelta(days=21),
    "1day":   pd.Timedelta(days=90),
    "1week":  pd.Timedelta(days=365),
    "1month": pd.Timedelta(days=1095),
}

PAD = 0.05   # y-axis padding for the candle panel


def _span_window(df, tf):
    x_end = df.index.max()
    x_start = x_end - DEFAULT_SPAN[tf]
    if x_start < df.index.min():
        x_start = df.index.min()
    return x_start, x_end


def _yrange(series, extra=PAD):
    s = series.dropna()
    if s.empty:
        return None
    lo, hi = float(s.min()), float(s.max())
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    rng = hi - lo
    return [lo - rng * extra, hi + rng * extra]


# One indicator panel's worth of traces (6): buy bar, sell bar, CVD all,
# CVD session, cumulative volume, buy ratio (last one on the right axis).
def _add_indicator_panel(fig, df, row, legend_id, on, default_on):
    """default_on: set of trace names shown by default in this panel."""
    ratio = df["buy_pressure"] / (df["buy_pressure"] + df["sell_pressure"])

    def vis(name):
        if not on:
            return False
        return True if name in default_on else "legendonly"

    fig.add_trace(go.Bar(
        x=df.index, y=df["buy_pressure"], name="Buy Volume",
        marker_color="rgba(38,166,154,0.85)", visible=vis("Buy Volume"), showlegend=on,
        legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Buy: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Bar(
        x=df.index, y=-df["sell_pressure"], name="Sell Volume",
        marker_color="rgba(239,83,80,0.85)", visible=vis("Sell Volume"), showlegend=on,
        legend=legend_id, customdata=df["sell_pressure"],
        hovertemplate="<b>%{x}</b><br>Sell: %{customdata:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["cvd_all_end"], mode="lines", name="CVD (all-time)",
        line=dict(color="#ba68c8", width=2), connectgaps=True,
        visible=vis("CVD (all-time)"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>CVD all: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["cvd_end"], mode="lines", name="CVD (session)",
        line=dict(color="#4fc3f7", width=2), connectgaps=True,
        visible=vis("CVD (session)"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>CVD session: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["volume"].cumsum(), mode="lines", name="Cum Total",
        line=dict(color="#ffd54f", width=2), connectgaps=True,
        visible=vis("Cum Total"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Cum Total: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["buy_pressure"].cumsum(), mode="lines", name="Cum Buy",
        line=dict(color="#66bb6a", width=1.6), connectgaps=True,
        visible=vis("Cum Buy"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Cum Buy: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["sell_pressure"].cumsum(), mode="lines", name="Cum Sell",
        line=dict(color="#e57373", width=1.6), connectgaps=True,
        visible=vis("Cum Sell"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Cum Sell: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=df.index, y=ratio, mode="lines", name="Buy Ratio",
        line=dict(color="#ff9800", width=1.6, dash="dot"), connectgaps=True,
        visible=vis("Buy Ratio"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Buy Ratio: %{y:.1%}<extra></extra>",
    ), row=row, col=1, secondary_y=True)


def build_chart(df_1min, frames: dict, ticker: str) -> go.Figure:

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.32, 0.34, 0.34],
        vertical_spacing=0.06,
        specs=[[{}], [{"secondary_y": True}], [{"secondary_y": True}]],
        subplot_titles=(
            f"{ticker} — Candlestick",
            "Indicator Panel A — Buy/Sell Volume (toggle in upper legend)",
            "Indicator Panel B — CVD (toggle in lower legend)",
        )
    )

    timeframes = list(TIMEFRAME_RULE.keys())
    default_tf = "1hr"
    default_idx = timeframes.index(default_tf)

    # Per timeframe: 1 candle + 8 (panel A) + 8 (panel B) = 17 traces
    # panel order: buy, sell, CVD all, CVD session, cum total, cum buy, cum sell, ratio
    N_TRACES = 17

    for tf in timeframes:
        df = frames[tf]
        on = (tf == default_tf)

        # Screen 1: candle
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name=f"Candle ({tf})",
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
            visible=on, showlegend=False,
        ), row=1, col=1)

        # Screen 2: defaults to Buy/Sell volume; Screen 3: defaults to CVD (session)
        _add_indicator_panel(fig, df, row=2, legend_id="legend",  on=on,
                             default_on={"Buy Volume", "Sell Volume"})
        _add_indicator_panel(fig, df, row=3, legend_id="legend2", on=on,
                             default_on={"CVD (session)"})

    # ── Rangebreak rules ──
    intraday_breaks = [dict(bounds=["sat", "mon"]), dict(bounds=[20, 4], pattern="hour")]
    daily_breaks = [dict(bounds=["sat", "mon"])]
    no_breaks = []

    def breaks_for(tf):
        if tf in WEEK_OR_ABOVE:  return no_breaks
        if tf in DAILY_OR_ABOVE: return daily_breaks
        return intraday_breaks

    # ── Timeframe buttons ──
    buttons = []
    for i, tf in enumerate(timeframes):
        df = frames[tf]

        # panel order: buy, sell, CVD all, CVD session, cum total, cum buy, cum sell, ratio
        # panel A default = buy/sell bars; panel B default = CVD (session)
        LO = "legendonly"
        panelA = [True, True, LO, LO, LO, LO, LO, LO]
        panelB = [LO, LO, LO, True, LO, LO, LO, LO]
        visibility = []
        for j in range(len(timeframes)):
            if j == i:
                visibility += [True] + panelA + panelB
            else:
                visibility += [False] * N_TRACES

        showlegend = []
        for j in range(len(timeframes)):
            if j == i:
                showlegend += [False] + [True] * 16
            else:
                showlegend += [False] * N_TRACES

        x0, x1 = _span_window(df, tf)
        in_view = df.loc[x0:x1]
        # candle y from high/low; panel A y from buy/sell; panel B y from CVD (session)
        if not in_view.empty:
            y1 = _yrange(pd.concat([in_view["high"], in_view["low"]]))
            y_pa = _yrange(pd.concat([in_view["buy_pressure"], -in_view["sell_pressure"]]))
            y_pb = _yrange(in_view["cvd_end"])
        else:
            y1 = y_pa = y_pb = None

        breaks = breaks_for(tf)
        layout = {
            "title": f"<b>{ticker}</b> — {tf}",
            "xaxis.rangebreaks":  breaks,
            "xaxis2.rangebreaks": breaks,
            "xaxis3.rangebreaks": breaks,
            "xaxis.range":  [x0, x1],
            "xaxis2.range": [x0, x1],
            "xaxis3.range": [x0, x1],
        }
        if y1:  layout["yaxis.range"]  = y1
        if y_pa: layout["yaxis2.range"] = y_pa   # panel A (buy/sell scale)
        if y_pb: layout["yaxis4.range"] = y_pb   # panel B (CVD scale)

        buttons.append(dict(
            label=tf, method="update",
            args=[{"visible": visibility, "showlegend": showlegend}, layout],
        ))

    # ── Session-hours filter buttons ──
    weekend = dict(bounds=["sat", "mon"])
    session_break_sets = {
        "All hours": [weekend, dict(bounds=[20, 4], pattern="hour")],
        "Regular":   [weekend, dict(bounds=[16, 9.5], pattern="hour")],
        "Extended":  [weekend, dict(bounds=[9.5, 16], pattern="hour"), dict(bounds=[20, 4], pattern="hour")],
    }
    session_buttons = [
        dict(label=lbl, method="relayout",
             args=[{"xaxis.rangebreaks": brk, "xaxis2.rangebreaks": brk, "xaxis3.rangebreaks": brk}])
        for lbl, brk in session_break_sets.items()
    ]

    # ── Layout ──
    df0 = frames[default_tf]
    x0, x1 = _span_window(df0, default_tf)
    in0 = df0.loc[x0:x1]
    y1_0 = _yrange(pd.concat([in0["high"], in0["low"]]))
    y_pa_0 = _yrange(pd.concat([in0["buy_pressure"], -in0["sell_pressure"]]))
    y_pb_0 = _yrange(in0["cvd_end"])

    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b> — {default_tf}", font=dict(size=18)),
        template="plotly_dark",
        height=1150,
        barmode="overlay",
        bargap=0.1,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        # two independent legends, one per indicator panel
        legend=dict(orientation="h", yanchor="bottom", y=0.34, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        legend2=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0,
                     bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=60, t=120, b=60),
        updatemenus=[
            dict(type="buttons", direction="right", x=0.0, y=1.09, xanchor="left",
                 buttons=buttons, bgcolor="#2d2d2d", bordercolor="#888",
                 font=dict(color="white", size=12), active=default_idx),
            dict(type="buttons", direction="right", x=0.0, y=1.04, xanchor="left",
                 buttons=session_buttons, bgcolor="#1e1e1e", bordercolor="#888",
                 font=dict(color="#cccccc", size=11), active=0),
        ],
    )

    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume / CVD", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Ratio", row=2, col=1, secondary_y=True, tickformat=".0%", range=[0, 1])
    fig.update_yaxes(title_text="Volume / CVD", row=3, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Ratio", row=3, col=1, secondary_y=True, tickformat=".0%", range=[0, 1])
    fig.update_xaxes(title_text="Time", row=3, col=1)

    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=3, col=1)

    # default rangebreaks + initial view; lock x so only y reacts to double-click
    fig.update_xaxes(rangebreaks=breaks_for(default_tf), range=[x0, x1], fixedrange=True)
    if y1_0:
        fig.update_yaxes(range=y1_0, row=1, col=1)
    if y_pa_0:
        fig.update_yaxes(range=y_pa_0, row=2, col=1, secondary_y=False)
    if y_pb_0:
        fig.update_yaxes(range=y_pb_0, row=3, col=1, secondary_y=False)

    return fig


def show_chart(ticker: str = "NVDA", save_html: bool = True, auto_fetch: bool = True):
    ticker = ticker.upper()
    df_1min, frames = run_pipeline(ticker)

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
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    show_chart(ticker)
