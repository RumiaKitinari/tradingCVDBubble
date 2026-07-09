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
import numpy as np

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
var dfltX = (gd._fullLayout.xaxis.range || []).slice();

function visibleX() {
    var ax = gd._fullLayout.xaxis;
    if (!ax || !ax.range) return null;
    return [ax.range[0], ax.range[1]];
}

function refitY() {
    var xr = visibleX(); if (!xr) return;
    var upd = {};
    Object.keys(LEFT).forEach(function(yref) {
        var lo = Infinity, hi = -Infinity;
        gd._fullData.forEach(function(f) {
            if (f.visible === false || f.visible === 'legendonly') return;
            if ((f.yaxis || 'y') !== yref) return;
            var xs = f.x; if (!xs || !xs.length) return;
            
            var offset = xs[0];
            var len = xs.length;
            var i0 = Math.max(0, Math.floor(xr[0] - offset));
            var i1 = Math.min(len - 1, Math.ceil(xr[1] - offset));
            if (i0 > i1 || i0 >= len || i1 < 0) return; 
            
            if (f.low && f.high) {
                var lows = f.low, highs = f.high;
                for (var i = i0; i <= i1; i++) {
                    if (lows[i]  < lo) lo = lows[i];
                    if (highs[i] > hi) hi = highs[i];
                }
            } else if (f.y) {
                var ys = f.y;
                for (var i = i0; i <= i1; i++) {
                    var v = ys[i];
                    if (v == null || isNaN(v)) continue;
                    if (v < lo) lo = v;
                    if (v > hi) hi = v;
                }
            }
        });
        if (lo < hi && lo !== Infinity) {
            var pad = (hi - lo) * YPAD;
            upd[LEFT[yref] + '.range'] = [lo - pad, hi + pad];
        }
    });
    if (Object.keys(upd).length) {
        busy = true;
        Plotly.relayout(gd, upd).then(function() { busy = false; });
    }
}

var refitTimer = null;
function requestRefit() {
    if (refitTimer) clearTimeout(refitTimer);
    refitTimer = setTimeout(refitY, 150);
}

gd.on('plotly_relayout', function(ev) {
    if (busy) return;
    var keys = Object.keys(ev);
    if (keys.some(function(k) { return k.indexOf('title') > -1; })) {
        var r = gd._fullLayout.xaxis.range;
        if (r) dfltX = [r[0], r[1]];
    }
    if (keys.some(function(k) { return k.indexOf('xaxis') === 0; })) requestRefit();
});

// ── Toggle Bubble Button ──
var toggleBtn = document.createElement('button');
toggleBtn.innerHTML = 'Toggle Bubble Chart';
toggleBtn.style.position = 'absolute';
toggleBtn.style.top = '10px';
toggleBtn.style.left = '10px';
toggleBtn.style.zIndex = '1000';
toggleBtn.style.padding = '8px 12px';
toggleBtn.style.backgroundColor = '#444';
toggleBtn.style.color = '#fff';
toggleBtn.style.border = '1px solid #777';
toggleBtn.style.borderRadius = '4px';
toggleBtn.style.cursor = 'pointer';
toggleBtn.style.fontFamily = 'sans-serif';
toggleBtn.style.fontSize = '12px';

var bubble_state = false;
var enforcing_bubbles = false;

function applyBubbleState() {
    if (enforcing_bubbles) return;
    enforcing_bubbles = true;
    
    var update_indices = [];
    var new_visibility = [];
    
    for(var i=0; i<gd.data.length; i++) {
        if(gd.data[i].name && gd.data[i].name.indexOf('Bubble') > -1) {
            var is_outer = gd.data[i].name.indexOf('Outer') > -1;
            // The candle trace is 1 or 2 indices before the bubble trace
            var candle_idx = is_outer ? i - 1 : i - 2;
            var candle_visible = gd.data[candle_idx].visible === true;
            
            var target_visible = bubble_state && candle_visible;
            if (gd.data[i].visible !== target_visible) {
                update_indices.push(i);
                new_visibility.push(target_visible);
            }
        }
    }
    
    if (update_indices.length > 0) {
        Plotly.restyle(gd, {'visible': new_visibility}, update_indices).then(function() {
            enforcing_bubbles = false;
            requestRefit();
        });
    } else {
        enforcing_bubbles = false;
    }
}

toggleBtn.onclick = function() {
    bubble_state = !bubble_state;
    toggleBtn.style.backgroundColor = bubble_state ? '#26a69a' : '#444';
    applyBubbleState();
};

document.body.appendChild(toggleBtn);

gd.on('plotly_restyle', function() { 
    if (!busy) requestRefit(); 
    applyBubbleState();
});
document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    // Timeframe hotkeys: 1-9 = buttons 1-9, 0 = 10th, - = 11th
    var keyInt = parseInt(e.key);
    var tfIdx = null;
    if (!isNaN(keyInt) && keyInt >= 1 && keyInt <= 9) tfIdx = keyInt - 1;
    else if (e.key === '0') tfIdx = 9;
    else if (e.key === '-') tfIdx = 10;
    if (tfIdx !== null) {
        var btnGrp = document.querySelectorAll('.updatemenu-container')[0];
        if (btnGrp) {
            var btns = btnGrp.querySelectorAll('.updatemenu-button');
            if (tfIdx < btns.length) {
                btns[tfIdx].dispatchEvent(new MouseEvent('click', {bubbles: true}));
            }
        }
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        var xr = visibleX(); if (!xr) return;
        var span = xr[1] - xr[0];
        var shift = span * 0.05 * (e.key === 'ArrowLeft' ? -1 : 1);
        busy = true;
        Plotly.relayout(gd, {
            'xaxis.range': [xr[0] + shift, xr[1] + shift],
            'xaxis2.range': [xr[0] + shift, xr[1] + shift],
            'xaxis3.range': [xr[0] + shift, xr[1] + shift]
        }).then(function() { busy = false; requestRefit(); });
    }
    if (e.key.toLowerCase() === 'r') {
        if (!dfltX || !dfltX.length) return;
        busy = true;
        Plotly.relayout(gd, {
            'xaxis.range': dfltX.slice(), 'xaxis2.range': dfltX.slice(), 'xaxis3.range': dfltX.slice(),
        }).then(function() { busy = false; requestRefit(); });
    }
});

requestRefit();
"""



def write_chart_html(fig, path: str):
    """Save the figure to HTML with the y-axis auto-refit script attached.
    doubleClick:false disables Plotly's native 'autorange to all data' so our
    dblclick handler in REFIT_JS can restore the default window instead."""
    fig.write_html(path, post_script=REFIT_JS, config={"doubleClick": False, "scrollZoom": True})


# How many candles to show by default, regardless of timeframe/interval.
# The view is NOT locked — the user can pan/zoom freely; this is only the
# initial window (and what each timeframe button snaps back to) so that any
# timeframe opens at a comfortable ~50-60 candles instead of being squashed.
N_VISIBLE = 100

PAD = 0.10   # y-axis padding (fraction) so candles aren't flush against edges


def _count_window(df, offset, n: int = N_VISIBLE):
    """Return (x_start_idx, x_end_idx) covering the last ~n bars."""
    if df.empty:
        return None, None
    n = min(n, len(df))
    start_idx = offset + len(df) - n
    end_idx = offset + len(df) - 1
    # add some padding on edges
    return max(0, start_idx - 1), end_idx + 1


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

    # Determine if this panel contains estimated data
    has_wick = "source" in df.columns and df["source"].astype(str).str.contains("wick").any()
    buy_name = "Buy Volume (est.)" if has_wick else "Buy Volume"
    sell_name = "Sell Volume (est.)" if has_wick else "Sell Volume"

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
        
    sell_custom = np.stack((df['time_str'], df["sell_pressure"]), axis=-1)

    fig.add_trace(go.Bar(
        x=df['x_idx'], y=df["buy_pressure"], name=buy_name,
        marker_color=buy_colors, visible=vis("Buy Volume"), showlegend=on,
        legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>Buy: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Bar(
        x=df['x_idx'], y=-df["sell_pressure"], name=sell_name,
        marker_color=sell_colors, visible=vis("Sell Volume"), showlegend=on,
        legend=legend_id, customdata=sell_custom,
        hovertemplate="<b>%{customdata[0]}</b><br>Sell: %{customdata[1]:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=df["cvd_all_end"], mode="lines", name="CVD (all-time)",
        line=dict(color="#ba68c8", width=2), connectgaps=True,
        visible=vis("CVD (all-time)"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>CVD all: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=df["cvd_all_raw_end"], mode="lines", name="CVD raw (incl. auction)",
        line=dict(color="#9575cd", width=1.4, dash="dot"), connectgaps=True,
        visible=vis("CVD raw (incl. auction)"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>CVD raw: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=df["volume"].cumsum(), mode="lines", name="Cum Total",
        line=dict(color="#ffd54f", width=2), connectgaps=True,
        visible=vis("Cum Total"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>Cum Total: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=df["buy_pressure"].cumsum(), mode="lines", name="Cum Buy",
        line=dict(color="#66bb6a", width=1.6), connectgaps=True,
        visible=vis("Cum Buy"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>Cum Buy: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=df["sell_pressure"].cumsum(), mode="lines", name="Cum Sell",
        line=dict(color="#e57373", width=1.6), connectgaps=True,
        visible=vis("Cum Sell"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>Cum Sell: %{y:,.0f}<extra></extra>",
    ), row=row, col=1, secondary_y=False)

    fig.add_trace(go.Scattergl(
        x=df['x_idx'], y=ratio, mode="lines", name="Buy Ratio",
        line=dict(color="#ff9800", width=1.6, dash="dot"), connectgaps=True,
        visible=vis("Buy Ratio"), showlegend=on, legend=legend_id, customdata=df['time_str'],
        hovertemplate="<b>%{customdata}</b><br>Buy Ratio: %{y:.1%}<extra></extra>",
    ), row=row, col=1, secondary_y=True)
    
    # ── Delta Ratio Strip (Stacked Bar on Secondary Y) ──
    tot_vol = df["buy_pressure"] + df["sell_pressure"]
    tot_vol = np.where(tot_vol == 0, 1, tot_vol)
    buy_pct = (df["buy_pressure"] / tot_vol)
    sell_pct = (df["sell_pressure"] / tot_vol)
    
    fig.add_trace(go.Bar(
        x=df['x_idx'], y=buy_pct, name="Buy % (Strip)",
        marker_color="rgba(38,166,154,0.6)", visible=vis("Buy % (Strip)"), showlegend=on,
        legend=legend_id, hoverinfo="y", offsetgroup=f"strip_{row}"
    ), row=row, col=1, secondary_y=True)

    fig.add_trace(go.Bar(
        x=df['x_idx'], y=sell_pct, base=buy_pct, name="Sell % (Strip)",
        marker_color="rgba(239,83,80,0.6)", visible=vis("Sell % (Strip)"), showlegend=on,
        legend=legend_id, hoverinfo="y", offsetgroup=f"strip_{row}"
    ), row=row, col=1, secondary_y=True)


def _add_source_annotations(fig: go.Figure, frames: dict) -> None:
    """
    Step 3: Add shaded background zones marking wick-estimated data regions.

    For each source that uses wick decomposition (finviz_wick, ibkr_hist,
    alpaca_wick) a light semi-transparent rectangle is drawn across the full
    chart height, plus a small text label at the left edge of each zone.
    Real-tick sources (ibkr_tick, alpaca_tick) are left unmarked — they are
    the ground truth and need no disclaimer.

    Every timeframe occupies its own disjoint x_idx interval, so rects for
    all timeframes can be drawn at once: only the active timeframe's region
    is ever inside the visible x-window. Mixed-source data is handled by
    splitting into contiguous runs per source (instead of one first→last
    rect that would also cover tick regions in between).

    Uses xref='x' (shared x-axis) and yref='paper' (full height) so the shape
    spans all three rows simultaneously.
    """
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

    for df in frames.values():
        if "source" not in df.columns or "x_idx" not in df.columns:
            continue
        src_col = df["source"]
        # Contiguous runs of the same source value
        run_id = (src_col != src_col.shift()).cumsum()
        for _, run in df.groupby(run_id):
            src = run["source"].iloc[0]
            style = EST_SOURCES.get(src)
            if style is None:
                continue
            x0 = run["x_idx"].iloc[0]
            x1 = run["x_idx"].iloc[-1]

            fig.add_shape(
                type="rect",
                x0=x0, x1=x1,
                y0=0, y1=1,
                yref="paper", xref="x",
                fillcolor=style["fillcolor"],
                line_width=0,
                layer="below",
            )
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




def build_chart(df: pd.DataFrame, frames: dict, ticker: str, active_timeframe: str = "1sec", pie_chart_count: int = 0, x_range: tuple = None) -> go.Figure:

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.50, 0.25, 0.25],
        vertical_spacing=0.06,
        specs=[[{}], [{"secondary_y": True}], [{"secondary_y": True}]],
        subplot_titles=(f"{ticker} — Candlestick", "", "")
    )
    
    # ── Add zero lines for Indicator Panels (Must be done before Pie traces to avoid Plotly bugs) ──
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=3, col=1)

    timeframes = list(frames.keys())
    if active_timeframe and active_timeframe in timeframes:
        timeframes = [active_timeframe]
        default_tf = active_timeframe
        default_idx = 0
    else:
        default_tf = "1hr" if "1hr" in timeframes else timeframes[-1]
        default_idx = timeframes.index(default_tf)

    tf_offsets = {}
    current_idx = 0
    MAX_CANDLES = 6000
    for i, tf in enumerate(timeframes):
        # Copy so x_idx/time_str additions never mutate the caller's frames
        # (and never trigger SettingWithCopyWarning on the truncated slice).
        df = frames[tf].copy()

        # ── DOM Optimization: Limit short timeframes to the most recent 6000 bars
        if len(df) > MAX_CANDLES:
            df = df.iloc[-MAX_CANDLES:]

        frames[tf] = df
        df['x_idx'] = np.arange(len(df)) + current_idx
        if tf in DAILY_OR_ABOVE:
            df['time_str'] = df.index.strftime('%Y-%m-%d')
        else:
            df['time_str'] = df.index.strftime('%Y-%m-%d %H:%M')
        tf_offsets[tf] = current_idx
        current_idx += len(df)
        
    # Get predominant source for title
    df_active = frames[default_tf]
    if not df_active.empty and "source" in df_active.columns:
        top_src = df_active["source"].mode()
        src_str = top_src.iloc[0] if not top_src.empty else "unknown"
    else:
        src_str = "unknown"

    N_TRACES = 19

    for tf in timeframes:
        df = frames[tf]
        on = (tf == default_tf)

        # Screen 1: candle or scatter (for raw ticks)
        if tf == "raw_tick":
            # For raw ticks, OHLC are all the same, so we plot a simple line or scatter.
            # Using markers allows seeing individual trades clearly.
            fig.add_trace(go.Scattergl(
                x=df['x_idx'], y=df["close"],
                mode="lines+markers",
                name=f"Raw Ticks",
                line=dict(color="#29b6f6", width=1),
                marker=dict(size=3, color="#29b6f6"),
                visible=on, showlegend=False, customdata=df['time_str'],
                hovertemplate="<b>%{customdata}</b><br>Price: %{y:.2f}<extra></extra>",
            ), row=1, col=1, secondary_y=False)
        else:
            fig.add_trace(go.Candlestick(
                x=df['x_idx'], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                name=f"Candle ({tf})",
                increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
                increasing_fillcolor="#26a69a", decreasing_fillcolor="#ef5350",
                visible=on, showlegend=False, customdata=df['time_str'],
                hovertemplate="<b>%{customdata}</b><br>O: %{open:.2f}<br>H: %{high:.2f}<br>L: %{low:.2f}<br>C: %{close:.2f}<extra></extra>",
            ), row=1, col=1, secondary_y=False)

        # ── Fixed Domain Pie Charts ──
        if pie_chart_count > 0 and len(timeframes) == 1:
            N_total = len(df)
            if x_range is not None:
                x0_init = x_range[0]
                x1_init = x_range[1]
            else:
                x0_init = max(0, N_total - 100)
                x1_init = N_total
                
            df_vis = df[(df['x_idx'] >= x0_init) & (df['x_idx'] <= x1_init)]
            
            n_pies = int(pie_chart_count)
            overall_width = 0.94 # Matches secondary y-axis domain in Plotly
            width = overall_width / n_pies
            pad = width * 0.05
            
            chunk_size = (x1_init - x0_init) / n_pies if n_pies > 0 else 1
            
            # Pre-compute max volume for sqrt scaling
            chunks = []
            vols = []
            for k in range(n_pies):
                c_start = x0_init + k * chunk_size
                c_end = x0_init + (k + 1) * chunk_size
                chunk = df_vis[(df_vis['x_idx'] >= c_start) & (df_vis['x_idx'] < c_end)]
                chunks.append(chunk)
                if len(chunk) > 0:
                    vols.append(float(chunk["buy_pressure"].sum() + chunk["sell_pressure"].sum()))
                else:
                    vols.append(0.0)
            max_vol = max(vols) if vols and max(vols) > 0 else 1.0
            
            for k in range(n_pies):
                chunk = chunks[k]
                if len(chunk) > 0:
                    buy = float(chunk["buy_pressure"].sum())
                    sell = float(chunk["sell_pressure"].sum())
                else:
                    buy, sell = 0, 0
                
                if (buy + sell) > 0:
                    factor = ((buy + sell) / max_vol) ** 0.5
                    factor = max(0.15, factor) # min size
                    
                    x_center = k * width + (width / 2.0)
                    d_x = [x_center - width * 0.45 * factor, x_center + width * 0.45 * factor]
                    d_y = [0.52 - 0.04 * factor, 0.52 + 0.04 * factor] # Slightly higher than bottom
                    
                    fig.add_trace(go.Pie(
                        values=[buy, sell],
                        labels=["Buy", "Sell"],
                        marker=dict(colors=["rgba(38,166,154,0.7)", "rgba(239,83,80,0.7)"]),
                        textinfo="none",
                        hoverinfo="text",
                        hovertext=f"Buy: {buy:,.0f}<br>Sell: {sell:,.0f}",
                        hole=0.0,
                        domain=dict(x=d_x, y=d_y),
                        sort=False,
                        direction="clockwise",
                        showlegend=False
                    ))
                else:
                    # Invisible placeholder
                    x_center = k * width + (width / 2.0)
                    d_x = [x_center - width * 0.45 * 0.15, x_center + width * 0.45 * 0.15]
                    d_y = [0.52 - 0.04 * 0.15, 0.52 + 0.04 * 0.15]
                    
                    fig.add_trace(go.Pie(
                        values=[1.0, 1.0],
                        labels=["Buy", "Sell"],
                        marker=dict(colors=["rgba(0,0,0,0)", "rgba(0,0,0,0)"]),
                        textinfo="none",
                        hoverinfo="none",
                        hole=0.0,
                        domain=dict(x=d_x, y=d_y),
                        sort=False,
                        direction="clockwise",
                        showlegend=False
                    ))



        # Screen 2: defaults to Buy/Sell volume; Screen 3: defaults to CVD (session)
        _add_indicator_panel(fig, df, row=2, legend_id="legend",  on=on,
                             default_on={"Buy Volume", "Sell Volume"})
        _add_indicator_panel(fig, df, row=3, legend_id="legend2", on=on,
                             default_on={"CVD (all-time)"})

    # ── Timeframe buttons ──
    buttons = []
    for i, tf in enumerate(timeframes):
        df = frames[tf]
        offset = tf_offsets[tf]
        
        # panel order: buy, sell, CVD all, CVD raw, cum total, cum buy, cum sell, ratio, buy%, sell%
        LO = "legendonly"
        panelA = [True, True, LO, LO, LO, LO, LO, LO, LO, LO]
        panelB = [LO, LO, True, LO, LO, LO, LO, LO, LO, LO]
        visibility = []
        
        # In static HTML mode, we skip dynamic traces (pie/strip) for updatemenus
        # because their trace count is variable. This loop is only used if not active_timeframe.
        N_TRACES = 21 # 1 Candle + 20 indicators (10 per panel)
        for j in range(len(timeframes)):
            if j == i:
                # Candle + panels
                visibility += [True] + panelA + panelB
            else:
                visibility += [False] * N_TRACES

        showlegend = []
        for j in range(len(timeframes)):
            if j == i:
                showlegend += [False] + [True] * 20
            else:
                showlegend += [False] * N_TRACES

        x0, x1 = _count_window(df, offset)
        in_view = df.iloc[-min(N_VISIBLE, len(df)):]
        if not in_view.empty:
            y1 = _yrange(pd.concat([in_view["high"], in_view["low"]]))
            y_pa = _yrange(pd.concat([in_view["buy_pressure"], -in_view["sell_pressure"]]))
            y_pb = _yrange(in_view["cvd_all_end"])
        else:
            y1 = y_pa = y_pb = None

        layout = {
            "title": f"<b>{ticker}</b> — {tf}",
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

    # ── Layout ──
    df0 = frames[default_tf]
    offset0 = tf_offsets[default_tf]
    x0, x1 = _count_window(df0, offset0)
    in0 = df0.iloc[-min(N_VISIBLE, len(df0)):]
    y1_0 = _yrange(pd.concat([in0["high"], in0["low"]]))
    y_pa_0 = _yrange(pd.concat([in0["buy_pressure"], -in0["sell_pressure"]]))
    y_pb_0 = _yrange(in0["cvd_all_end"])

    layout_kwargs = dict(
        title=dict(text=f"<b>{ticker}</b> — {default_tf} <span style='font-size:12px;color:gray;'>(Source: {src_str})</span>", font=dict(size=18)),
        template="plotly_dark",
        height=1150,
        barmode="overlay",
        bargap=0.1,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        hoverlabel=dict(bgcolor="rgba(25, 25, 25, 0.95)", font_size=12, font_color="white", bordercolor="#444"),
        dragmode="pan",  # Left-click drags to pan instead of selecting a zoom box
        # two independent legends, one per indicator panel
        legend=dict(orientation="h", yanchor="bottom", y=0.25, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        legend2=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0,
                     bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=60, t=120, b=150 if pie_chart_count > 0 else 60),
        uirevision="constant",  # Prevents Plotly from resetting UI state on relayout
    )
    if not active_timeframe:
        layout_kwargs["updatemenus"] = [
            dict(type="buttons", direction="right", x=0.0, y=1.09, xanchor="left",
                 buttons=buttons, bgcolor="#2d2d2d", bordercolor="#888",
                 font=dict(color="#aaaaaa", size=12), active=default_idx),
        ]
        
    fig.update_layout(**layout_kwargs)

    fig.update_xaxes(type="linear", showticklabels=False, showspikes=True, spikemode="across", spikedash="solid", spikecolor="gray", spikethickness=1)
    fig.update_yaxes(title_text="Price (USD)", fixedrange=True, row=1, col=1, showspikes=True, spikemode="across", spikedash="solid", spikecolor="gray", spikethickness=1)
    fig.update_yaxes(title_text="Volume / CVD", fixedrange=True, row=2, col=1, secondary_y=False, showspikes=True, spikemode="across", spikedash="solid", spikecolor="gray", spikethickness=1)
    fig.update_yaxes(title_text="Ratio", fixedrange=True, row=2, col=1, secondary_y=True, tickformat=".0%", range=[0, 1])
    fig.update_yaxes(title_text="Volume / CVD", fixedrange=True, row=3, col=1, secondary_y=False, showspikes=True, spikemode="across", spikedash="solid", spikecolor="gray", spikethickness=1)
    fig.update_yaxes(title_text="Ratio", fixedrange=True, row=3, col=1, secondary_y=True, tickformat=".0%", range=[0, 1])
    fig.update_xaxes(title_text="Time", row=3, col=1)

    # ── Current Value Labels ──
    if not df.empty:
        last_close = df["close"].iloc[-1]
        last_cvd = df["cvd_all_end"].iloc[-1] if "cvd_all_end" in df.columns else df["cvd_all"].iloc[-1]
        
        fig.add_annotation(
            xref="paper", yref="y",
            x=1, y=last_close,
            text=f"{last_close:.2f}",
            showarrow=False,
            xanchor="left",
            bgcolor="rgba(38,166,154,0.9)" if df["close"].iloc[-1] >= df["open"].iloc[-1] else "rgba(239,83,80,0.9)",
            font=dict(color="white", size=11),
            borderpad=3
        )
        fig.add_annotation(
            xref="paper", yref="y4",
            x=1, y=last_cvd,
            text=f"{last_cvd:,.0f}",
            showarrow=False,
            xanchor="left",
            bgcolor="#ba68c8",
            font=dict(color="white", size=11),
            borderpad=3
        )

    fig.update_xaxes(range=[x0, x1])
    if y1_0:
        fig.update_yaxes(range=y1_0, row=1, col=1)
    if y_pa_0:
        fig.update_yaxes(range=y_pa_0, row=2, col=1, secondary_y=False)
    if y_pb_0:
        fig.update_yaxes(range=y_pb_0, row=3, col=1, secondary_y=False)

    # Step 3: Source-aware annotations — shade wick-estimated data regions.
    _add_source_annotations(fig, frames)

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
        path = "chart.html"
        write_chart_html(fig, path)
        print(f"[Visualizer] Saved → {path}")
        
        import webbrowser
        import os
        webbrowser.open(f"file://{os.path.abspath(path)}")
    else:
        fig.show()


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    show_chart(ticker)
