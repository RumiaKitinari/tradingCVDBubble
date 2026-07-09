import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np

from cvd.calculator import run_pipeline, TIMEFRAME_RULE, DAILY_OR_ABOVE, WEEK_OR_ABOVE, TIMEFRAME_RULE_IBKR
from cvd.visualizer import (
    REFIT_JS, N_VISIBLE, PAD, _count_window, _yrange, 
    _add_indicator_panel, _add_source_annotations
)
from .data_provider import fetch_and_aggregate_l2_data


def build_chart(df_1min, frames: dict, ticker: str, active_timeframe: str = None) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.50, 0.25, 0.25],
        vertical_spacing=0.06,
        specs=[[{"secondary_y": True}], [{"secondary_y": True}], [{"secondary_y": True}]],
        subplot_titles=(
            f"{ticker} — Candlestick + Level 2 Heatmap",
            "Indicator Panel A — Buy/Sell Volume",
            "Indicator Panel B — CVD",
        )
    )

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
    
    # Pre-process all frames and fetch L2 data
    processed_frames = {}
    l2_data_cache = {}
    
    for i, tf in enumerate(timeframes):
        df = frames[tf].copy()
        if len(df) > MAX_CANDLES:
            df = df.iloc[-MAX_CANDLES:]
            
        df['x_idx'] = np.arange(len(df)) + current_idx
        if tf in DAILY_OR_ABOVE:
            df['time_str'] = df.index.strftime('%Y-%m-%d')
        else:
            df['time_str'] = df.index.strftime('%Y-%m-%d %H:%M')
            
        tf_offsets[tf] = current_idx
        current_idx += len(df)
        
        # Fetch L2 data
        # To avoid performance issues, only fetch heatmap for the active timeframe, or default
        df, y_levels, z_matrix = fetch_and_aggregate_l2_data(ticker, df, max_candles=300)
        
        processed_frames[tf] = df
        l2_data_cache[tf] = (y_levels, z_matrix)
        
    frames = processed_frames

    df_active = frames[default_tf]
    if not df_active.empty and "source" in df_active.columns:
        top_src = df_active["source"].mode()
        src_str = top_src.iloc[0] if not top_src.empty else "unknown"
    else:
        src_str = "unknown"

    N_TRACES_PER_TF = 25  # We will have more traces now per timeframe

    for tf in timeframes:
        df = frames[tf]
        on = (tf == default_tf)
        y_levels, z_matrix = l2_data_cache[tf]

        # 1. Heatmap Trace
        if z_matrix is not None and y_levels is not None:
            # We align the z_matrix to the end of the x_idx
            # The z_matrix covers the last `z_matrix.shape[1]` candles
            start_x_idx = df['x_idx'].iloc[-z_matrix.shape[1]]
            end_x_idx = df['x_idx'].iloc[-1]
            x_vals = df['x_idx'].iloc[-z_matrix.shape[1]:].values
            
            fig.add_trace(go.Heatmap(
                x=x_vals, y=y_levels, z=z_matrix,
                colorscale="Blues", showscale=False, opacity=0.6, hoverinfo="skip",
                name=f"L2 Heatmap ({tf})", visible=on
            ), row=1, col=1, secondary_y=False)
        else:
            # Add dummy trace to keep indices aligned
            fig.add_trace(go.Scatter(x=[None], y=[None], name=f"L2 Heatmap ({tf})", visible=False), row=1, col=1, secondary_y=False)

        # 2. Candlestick
        if tf == "raw_tick":
            fig.add_trace(go.Scattergl(
                x=df['x_idx'], y=df["close"],
                mode="lines+markers", name=f"Raw Ticks ({tf})",
                line=dict(color="#29b6f6", width=1), marker=dict(size=3, color="#29b6f6"),
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

        # 3. Center of Gravity Lines
        if 'bid_cog' in df.columns:
            fig.add_trace(go.Scattergl(
                x=df['x_idx'], y=df['bid_cog'], mode="lines", name=f"Bid CoG ({tf})",
                line=dict(color="#00e676", width=1.5, dash="dot"),
                visible=on, showlegend=False, hoverinfo="skip"
            ), row=1, col=1, secondary_y=False)
            
            fig.add_trace(go.Scattergl(
                x=df['x_idx'], y=df['ask_cog'], mode="lines", name=f"Ask CoG ({tf})",
                line=dict(color="#ff1744", width=1.5, dash="dot"),
                visible=on, showlegend=False, hoverinfo="skip"
            ), row=1, col=1, secondary_y=False)
        else:
            fig.add_trace(go.Scatter(x=[None], y=[None], visible=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[None], y=[None], visible=False), row=1, col=1)
            
        # 4. Spoofing / Pressure Signals (OBI based)
        # If OBI > 0.3 (30% more bids than asks) -> Strong Bid Pressure (Spoof Buy)
        if 'obi' in df.columns:
            buy_sig = df[df['obi'] > 0.3]
            sell_sig = df[df['obi'] < -0.3]
            
            fig.add_trace(go.Scattergl(
                x=buy_sig['x_idx'], y=buy_sig['low'] - (buy_sig['high'] - buy_sig['low'])*0.5,
                mode='markers', name=f"Strong Bid OBI ({tf})",
                marker=dict(symbol='triangle-up', size=10, color='#00ff00', line=dict(width=1, color='white')),
                visible=on, showlegend=False, customdata=buy_sig['time_str'],
                hovertemplate="<b>%{customdata}</b><br>Strong Bid Pressure<extra></extra>"
            ), row=1, col=1, secondary_y=False)
            
            fig.add_trace(go.Scattergl(
                x=sell_sig['x_idx'], y=sell_sig['high'] + (sell_sig['high'] - sell_sig['low'])*0.5,
                mode='markers', name=f"Strong Ask OBI ({tf})",
                marker=dict(symbol='triangle-down', size=10, color='#ff0000', line=dict(width=1, color='white')),
                visible=on, showlegend=False, customdata=sell_sig['time_str'],
                hovertemplate="<b>%{customdata}</b><br>Strong Ask Pressure<extra></extra>"
            ), row=1, col=1, secondary_y=False)
        else:
            fig.add_trace(go.Scatter(x=[None], y=[None], visible=False), row=1, col=1)
            fig.add_trace(go.Scatter(x=[None], y=[None], visible=False), row=1, col=1)

        # Indicator Panels
        _add_indicator_panel(fig, df, row=2, legend_id="legend",  on=on,
                             default_on={"Buy Volume", "Sell Volume"})
        _add_indicator_panel(fig, df, row=3, legend_id="legend2", on=on,
                             default_on={"CVD (all-time)"})

    # Timeframe buttons
    buttons = []
    for i, tf in enumerate(timeframes):
        df = frames[tf]
        offset = tf_offsets[tf]
        
        LO = "legendonly"
        panelA = [True, True, LO, LO, LO, LO, LO, LO, LO, LO]
        panelB = [LO, LO, True, LO, LO, LO, LO, LO, LO, LO]
        visibility = []
        
        # Traces per TF: Heatmap, Candle, Bid CoG, Ask CoG, Buy Sig, Sell Sig, PanelA (10), PanelB (10)
        # Wait, panelA and panelB trace counts from _add_indicator_panel:
        # Buy Vol, Sell Vol, CVD all, CVD raw, Cum Total, Cum Buy, Cum Sell, Buy Ratio, Buy% Strip, Sell% Strip
        # That's 10 traces per panel. So 20 traces for panels.
        # Plus 6 traces on Row 1 (Heatmap, Candle, Bid CoG, Ask CoG, BuySig, SellSig)
        # Total 26 traces per timeframe.
        N_TRACES = 26
        
        for j in range(len(timeframes)):
            if j == i:
                visibility += [True, True, True, True, True, True] + panelA + panelB
            else:
                visibility += [False] * N_TRACES

        showlegend = []
        for j in range(len(timeframes)):
            if j == i:
                showlegend += [False]*6 + [True]*20
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
        if y_pa: layout["yaxis2.range"] = y_pa
        if y_pb: layout["yaxis4.range"] = y_pb

        buttons.append(dict(
            label=tf, method="update",
            args=[{"visible": visibility, "showlegend": showlegend}, layout],
        ))

    # Layout
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
        dragmode="pan",
        legend=dict(orientation="h", yanchor="bottom", y=0.25, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        legend2=dict(orientation="h", yanchor="top", y=-0.02, xanchor="left", x=0,
                     bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        margin=dict(l=60, r=60, t=120, b=60),
        uirevision="constant",
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

    if not df_1min.empty:
        last_close = df_1min["close"].iloc[-1]
        last_cvd = df_1min["cvd_all_end"].iloc[-1] if "cvd_all_end" in df_1min.columns else df_1min["cvd_all"].iloc[-1]
        
        fig.add_annotation(
            xref="paper", yref="y",
            x=1, y=last_close,
            text=f"{last_close:.2f}",
            showarrow=False,
            xanchor="left",
            bgcolor="rgba(38,166,154,0.9)" if df_1min["close"].iloc[-1] >= df_1min["open"].iloc[-1] else "rgba(239,83,80,0.9)",
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

    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=3, col=1)

    fig.update_xaxes(range=[x0, x1])
    if y1_0:
        fig.update_yaxes(range=y1_0, row=1, col=1)
    if y_pa_0:
        fig.update_yaxes(range=y_pa_0, row=2, col=1, secondary_y=False)
    if y_pb_0:
        fig.update_yaxes(range=y_pb_0, row=3, col=1, secondary_y=False)

    _add_source_annotations(fig, frames)

    return fig
