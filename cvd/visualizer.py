"""
cvd/visualizer.py
-----------------
Bookmap-style Chart:
  - 상단: 캔들스틱
  - 하단: 양방향 바차트 (위=Buy, 아래=Sell)
  - 버튼: 1min / 3min / 5min / 15min / 1hr 전환
"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .calculator import run_pipeline, TIMEFRAME_RULE


def build_chart(df_1min, frames: dict, ticker: str) -> go.Figure:

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.6, 0.4],
        vertical_spacing=0.03,
        subplot_titles=(
            f"{ticker} — Candlestick",
            "Buy / Sell Volume"
        )
    )

    timeframes = list(TIMEFRAME_RULE.keys())   # ["1min","3min","5min","15min","1hr"]
    default_tf = "1hr"
    default_idx = timeframes.index(default_tf)

    # ── 각 timeframe별 trace 쌍 추가 (처음엔 default만 visible)
    # trace 순서: 각 tf마다 [캔들, buy bar, sell bar] = 3개씩
    for i, tf in enumerate(timeframes):
        df = frames[tf]
        visible = (tf == default_tf)

        # 1. 캔들스틱
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["open"], high=df["high"],
                low=df["low"],   close=df["close"],
                name=f"Candle ({tf})",
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
                increasing_fillcolor="#26a69a",
                decreasing_fillcolor="#ef5350",
                visible=visible,
                showlegend=False,
            ),
            row=1, col=1
        )

        # 2. Buy 바 (양수, 위)
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=df["buy_pressure"],
                name="Buy Volume",
                marker_color="rgba(38, 166, 154, 0.8)",
                visible=visible,
                showlegend=(tf == default_tf),
                hovertemplate="<b>%{x}</b><br>Buy: %{y:,.0f}<extra></extra>",
            ),
            row=2, col=1
        )

        # 3. Sell 바 (음수, 아래)
        fig.add_trace(
            go.Bar(
                x=df.index,
                y=-df["sell_pressure"],   # 음수로 뒤집어서 아래로
                name="Sell Volume",
                marker_color="rgba(239, 83, 80, 0.8)",
                visible=visible,
                showlegend=(tf == default_tf),
                hovertemplate="<b>%{x}</b><br>Sell: %{customdata:,.0f}<extra></extra>",
                customdata=df["sell_pressure"],
            ),
            row=2, col=1
        )

    n_traces = 3  # 캔들 + buy bar + sell bar

    # ── 버튼: 각 timeframe 선택 시 해당 3개 trace만 visible
    buttons = []
    for i, tf in enumerate(timeframes):
        visibility = []
        for j in range(len(timeframes)):
            visibility += [j == i] * n_traces   # 해당 tf만 True

        buttons.append(dict(
            label=tf,
            method="update",
            args=[
                {"visible": visibility},
                {"title": f"<b>{ticker}</b> — {tf} Bookmap-style CVD Chart"}
            ]
        ))

    # ── 레이아웃
    fig.update_layout(
        title=dict(
            text=f"<b>{ticker}</b> — {default_tf} Bookmap-style CVD Chart",
            font=dict(size=18)
        ),
        template="plotly_dark",
        height=800,
        barmode="overlay",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        bargap=0.1,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
        margin=dict(l=60, r=40, t=100, b=40),
        updatemenus=[dict(
            type="buttons",
            direction="right",
            x=0.0, y=1.12,
            xanchor="left",
            buttons=buttons,
            bgcolor="#2d2d2d",
            bordercolor="#555",
            font=dict(color="white"),
            active=default_idx,
        )]
    )

    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)

    # 0선 (buy/sell 기준선)
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)

    # 장 마감 + 주말 공백 제거
    fig.update_xaxes(
        rangebreaks=[
            dict(bounds=["sat", "mon"]),
            dict(bounds=[16, 9.5], pattern="hour"),
        ]
    )

    return fig


def show_chart(ticker: str = "NVDA", save_html: bool = True):
    df_1min, frames = run_pipeline(ticker)

    if df_1min.empty or not frames:
        print(f"[Visualizer] No data for {ticker}.")
        return

    fig = build_chart(df_1min, frames, ticker)

    if save_html:
        path = f"{ticker}_cvd_chart.html"
        fig.write_html(path)
        print(f"[Visualizer] Saved → {path}")

    fig.show()


if __name__ == "__main__":
    show_chart("NVDA")
