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

from .calculator import run_pipeline, TIMEFRAME_RULE, DAILY_OR_ABOVE, WEEK_OR_ABOVE, TIMEFRAME_RULE_IBKR


# Injected into the saved HTML (via write_html post_script).
#
# Plotly does NOT re-scale the y-axis when the visible x-window or the set of
# shown traces changes, so this script keeps every left y-axis fitted to
# whatever is actually on screen. `refitY()` scans the *currently shown* traces
# (visible===true, so legend-toggled-off traces are excluded), takes the
# min/max of their values inside the visible x-window, and sets each left axis
# (candles=y, panel A=y2, panel B=y4) to that range +/- 10% padding. The
# Buy-Ratio axes (y3/y5) stay fixed at 0-100%.
#
# refitY() is triggered on three events:
#   * plotly_relayout  -> pan / zoom the x-axis (Y follows the visible bars)
#   * plotly_restyle   -> a legend toggle (or timeframe button) shows/hides a
#                         trace, so the visible set changed
#   * dblclick (DOM)    -> Plotly's native double-click ("autorange to ALL
#                         data") is disabled via config doubleClick:false; we
#                         catch the DOM dblclick and restore the active
#                         timeframe's ~N_VISIBLE-candle default window (dfltX).
#
# NOTE on braces: write_html inserts this verbatim and only substitutes the
# literal token {plot_id}, so this string uses normal single braces.
REFIT_JS = """
var gd = document.getElementById('{plot_id}');
var YPAD = 0.10, busy = false;
var LEFT = {'y':'yaxis', 'y2':'yaxis2', 'y4':'yaxis4'};
// The "nice" default x-window for the active timeframe, restored on double-click.
var dfltX = (gd._fullLayout.xaxis.range || []).slice();

function visibleX() {
    var ax = gd._fullLayout.xaxis;
    if (!ax || !ax.range) return null;
    return [new Date(ax.range[0]).getTime(), new Date(ax.range[1]).getTime()];
}
function refitY() {
    var xr = visibleX(); if (!xr) return;
    var t0 = xr[0], t1 = xr[1], upd = {};
    Object.keys(LEFT).forEach(function(yref) {
        var lo = Infinity, hi = -Infinity;
        gd.data.forEach(function(tr, idx) {
            if (tr.visible !== true) return;            // skip hidden / legendonly
            if ((tr.yaxis || 'y') !== yref) return;
            // IMPORTANT: Plotly 6.x serializes numeric arrays as base64 typed-array
            // specs ({dtype, bdata}) on gd.data, which are NOT indexable. The decoded
            // Float64Arrays live on gd._fullData, so read x/y/low/high from there.
            var f = gd._fullData[idx]; if (!f) return;
            var xs = f.x; if (!xs) return;
            var isC = (tr.type === 'candlestick');
            var ys = isC ? null : f.y, lows = isC ? f.low : null, highs = isC ? f.high : null;
            for (var i = 0; i < xs.length; i++) {
                var tx = new Date(xs[i]).getTime();
                if (tx < t0 || tx > t1) continue;
                if (isC) {
                    if (lows[i]  < lo) lo = lows[i];
                    if (highs[i] > hi) hi = highs[i];
                } else {
                    var v = ys[i];
                    if (v == null || isNaN(v)) continue;
                    if (v < lo) lo = v;
                    if (v > hi) hi = v;
                }
            }
        });
        if (lo < hi) {
            var pad = (hi - lo) * YPAD;
            upd[LEFT[yref] + '.range'] = [lo - pad, hi + pad];
        }
    });
    if (Object.keys(upd).length) {
        busy = true;
        Plotly.relayout(gd, upd).then(function() { busy = false; });
    }
}
// Snap x back to the active timeframe's default ~100-candle window, then re-fit Y.
function resetView() {
    if (!dfltX || !dfltX.length) return;
    busy = true;
    Plotly.relayout(gd, {
        'xaxis.range': dfltX.slice(), 'xaxis2.range': dfltX.slice(), 'xaxis3.range': dfltX.slice(),
        'xaxis.autorange': false, 'xaxis2.autorange': false, 'xaxis3.autorange': false
    }).then(function() { busy = false; refitY(); });
}
gd.on('plotly_relayout', function(ev) {
    if (busy) return;
    var keys = Object.keys(ev);
    // fallback safety: if anything still autoranges, snap back to the default window
    if (keys.some(function(k) { return k.indexOf('.autorange') > -1; })) { resetView(); return; }
    // a timeframe button carries title/rangebreaks -> remember its window as the new default
    if (keys.some(function(k) { return k.indexOf('rangebreaks') > -1 || k === 'title'; })) {
        var r = gd._fullLayout.xaxis.range;
        if (r) dfltX = [r[0], r[1]];
    }
    // pan / zoom the x-axis -> re-fit Y to the visible bars
    if (keys.some(function(k) { return k.indexOf('xaxis') === 0; })) refitY();
});
// legend toggle / timeframe-button visibility change -> re-fit Y to what's shown now
gd.on('plotly_restyle', function() { if (!busy) refitY(); });
// Double-click handling. Plotly's native double-click is disabled via config
// (doubleClick:false) because it autoranges X to the WHOLE dataset; instead we
// catch the DOM dblclick and restore the default window. Skip clicks on the legend
// so its built-in double-click-to-isolate still works.
gd.addEventListener('dblclick', function(e) {
    try { if (e.target && e.target.closest && e.target.closest('.legend')) return; } catch (_) {}
    resetView();
});
refitY();
"""


def write_chart_html(fig, path: str):
    """Save the figure to HTML with the y-axis auto-refit script attached.
    doubleClick:false disables Plotly's native 'autorange to all data' so our
    dblclick handler in REFIT_JS can restore the default window instead."""
    fig.write_html(path, post_script=REFIT_JS, config={"doubleClick": False})


# How many candles to show by default, regardless of timeframe/interval.
# The view is NOT locked — the user can pan/zoom freely; this is only the
# initial window (and what each timeframe button snaps back to) so that any
# timeframe opens at a comfortable ~50-60 candles instead of being squashed.
N_VISIBLE = 100

PAD = 0.10   # y-axis padding (fraction) so candles aren't flush against edges


def _count_window(df, n: int = N_VISIBLE):
    """Return (x_start, x_end) covering the last ~n bars, padded ~0.7 bar on
    each side so the edge candles aren't clipped. Works on any timeframe
    because it counts bars, not wall-clock time. Padding uses the *median* bar
    spacing so an overnight/weekend gap inside the window doesn't blow it up."""
    if df.empty:
        return None, None
    n = min(n, len(df))
    sub = df.index[-n:]
    x_start, x_end = sub[0], sub[-1]
    if len(sub) > 1:
        step = sub.to_series().diff().median()   # robust to gaps
        x_start = x_start - step * 0.7
        x_end = x_end + step * 0.7
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

    # Gray out bars whose volume is dominated by the closing auction (>50%):
    # their buy/sell split is neutralized (50/50) so the direction isn't real.
    GRAY = "rgba(150,150,150,0.80)"
    frac = df["auction_frac"] if "auction_frac" in df.columns else pd.Series(0.0, index=df.index)
    buy_colors  = [GRAY if a > 0.5 else "rgba(38,166,154,0.85)" for a in frac]
    sell_colors = [GRAY if a > 0.5 else "rgba(239,83,80,0.85)"  for a in frac]

    def vis(name):
        if not on:
            return False
        return True if name in default_on else "legendonly"

    fig.add_trace(go.Bar(
        x=df.index, y=df["buy_pressure"], name="Buy Volume",
        marker_color=buy_colors, visible=vis("Buy Volume"), showlegend=on,
        legend=legend_id,
        hovertemplate="<b>%{x}</b><br>Buy: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Bar(
        x=df.index, y=-df["sell_pressure"], name="Sell Volume",
        marker_color=sell_colors, visible=vis("Sell Volume"), showlegend=on,
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
        x=df.index, y=df["cvd_all_raw_end"], mode="lines", name="CVD raw (incl. auction)",
        line=dict(color="#9575cd", width=1.4, dash="dot"), connectgaps=True,
        visible=vis("CVD raw (incl. auction)"), showlegend=on, legend=legend_id,
        hovertemplate="<b>%{x}</b><br>CVD raw: %{y:,.0f}<extra></extra>",
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


def _add_source_annotations(fig: go.Figure, df_base: pd.DataFrame) -> None:
    """
    Step 3: Add shaded background zones marking wick-estimated data regions.

    For each source that uses wick decomposition (finviz_wick, ibkr_hist,
    alpaca_wick) a light semi-transparent rectangle is drawn across the full
    chart height, plus a small text label at the left edge of the zone.
    Real-tick sources (ibkr_tick, alpaca_tick) are left unmarked — they are
    the ground truth and need no disclaimer.

    Uses xref='x' (shared x-axis) and yref='paper' (full height) so the shape
    spans all three rows simultaneously.
    """
    if "source" not in df_base.columns:
        return

    EST_SOURCES = {
        "finviz_wick": dict(
            fillcolor="rgba(255,210,100,0.07)",
            label="FinViz (est.)",
            font_color="rgba(220,180,80,0.75)",
        ),
        "ibkr_hist": dict(
            fillcolor="rgba(100,160,255,0.07)",
            label="IBKR hist (est.)",
            font_color="rgba(100,160,220,0.75)",
        ),
        "alpaca_wick": dict(
            fillcolor="rgba(200,120,255,0.07)",
            label="Alpaca wick (est.)",
            font_color="rgba(180,100,220,0.75)",
        ),
    }

    for src, style in EST_SOURCES.items():
        mask = df_base["source"] == src
        if not mask.any():
            continue
        x0 = df_base.index[mask].min()
        x1 = df_base.index[mask].max()

        # Shaded rectangle spanning the full chart height
        fig.add_shape(
            type="rect",
            x0=x0, x1=x1,
            y0=0, y1=1,
            yref="paper", xref="x",
            fillcolor=style["fillcolor"],
            line_width=0,
            layer="below",
        )
        # Text label at the left edge of the shaded zone
        fig.add_annotation(
            x=x0,
            y=0.98,
            yref="paper",
            xref="x",
            text=style["label"],
            showarrow=False,
            xanchor="left",
            yanchor="top",
            font=dict(size=8, color=style["font_color"]),
        )


def build_chart(df_1min, frames: dict, ticker: str) -> go.Figure:

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.50, 0.25, 0.25],
        vertical_spacing=0.06,
        specs=[[{}], [{"secondary_y": True}], [{"secondary_y": True}]],
        subplot_titles=(
            f"{ticker} — Candlestick",
            "Indicator Panel A — Buy/Sell Volume (toggle in upper legend)",
            "Indicator Panel B — CVD (toggle in lower legend)",
        )
    )

    # Use the actual keys present in frames (FinViz: 9 TFs, IBKR: 11 TFs with 1sec/5sec).
    timeframes = list(frames.keys())
    default_tf = "1hr" if "1hr" in timeframes else timeframes[-1]
    default_idx = timeframes.index(default_tf)

    # Per timeframe: 1 candle + 8 (panel A) + 8 (panel B) = 17 traces
    # panel order: buy, sell, CVD all, CVD raw, cum total, cum buy, cum sell, ratio
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
                             default_on={"CVD (all-time)"})

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

        # panel order: buy, sell, CVD all, CVD raw, cum total, cum buy, cum sell, ratio
        # panel A default = buy/sell bars; panel B default = CVD (all-time, neutralized)
        LO = "legendonly"
        panelA = [True, True, LO, LO, LO, LO, LO, LO]
        panelB = [LO, LO, True, LO, LO, LO, LO, LO]
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

        x0, x1 = _count_window(df)
        in_view = df.loc[x0:x1]
        # candle y from high/low; panel A y from buy/sell; panel B y from CVD (session)
        if not in_view.empty:
            y1 = _yrange(pd.concat([in_view["high"], in_view["low"]]))
            y_pa = _yrange(pd.concat([in_view["buy_pressure"], -in_view["sell_pressure"]]))
            y_pb = _yrange(in_view["cvd_all_end"])
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
    x0, x1 = _count_window(df0)
    in0 = df0.loc[x0:x1]
    y1_0 = _yrange(pd.concat([in0["high"], in0["low"]]))
    y_pa_0 = _yrange(pd.concat([in0["buy_pressure"], -in0["sell_pressure"]]))
    y_pb_0 = _yrange(in0["cvd_all_end"])

    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b> — {default_tf}", font=dict(size=18)),
        template="plotly_dark",
        height=1150,
        barmode="overlay",
        bargap=0.1,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        # two independent legends, one per indicator panel
        legend=dict(orientation="h", yanchor="bottom", y=0.25, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        legend2=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0,
                     bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=60, t=120, b=60),
        updatemenus=[
            dict(type="buttons", direction="right", x=0.0, y=1.09, xanchor="left",
                 buttons=buttons, bgcolor="#2d2d2d", bordercolor="#888",
                 font=dict(color="#aaaaaa", size=12), active=default_idx),
            dict(type="buttons", direction="right", x=0.0, y=1.04, xanchor="left",
                 buttons=session_buttons, bgcolor="#1e1e1e", bordercolor="#888",
                 font=dict(color="#aaaaaa", size=11), active=0),
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

    # default rangebreaks + initial ~55-candle window. x is NOT fixed: the user
    # can pan/zoom freely, and the injected JS (see REFIT_JS) re-fits the y-axes
    # to whatever bars are in view as they scroll.
    fig.update_xaxes(rangebreaks=breaks_for(default_tf), range=[x0, x1])
    if y1_0:
        fig.update_yaxes(range=y1_0, row=1, col=1)
    if y_pa_0:
        fig.update_yaxes(range=y_pa_0, row=2, col=1, secondary_y=False)
    if y_pb_0:
        fig.update_yaxes(range=y_pb_0, row=3, col=1, secondary_y=False)

    # Step 3: Source-aware annotations — shade wick-estimated data regions.
    _add_source_annotations(fig, df_1min)

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
        write_chart_html(fig, path)
        print(f"[Visualizer] Saved → {path}")

    fig.show()


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    show_chart(ticker)
