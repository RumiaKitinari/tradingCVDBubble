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

from .calculator import run_pipeline, TIMEFRAME_RULE, DAILY_OR_ABOVE, WEEK_OR_ABOVE


def build_chart(df_1min, frames: dict, ticker: str) -> go.Figure:

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        row_heights=[0.34, 0.15, 0.22, 0.15, 0.14],
        vertical_spacing=0.03,
        specs=[[{}], [{}], [{}], [{}], [{"secondary_y": True}]],  # row5 보조축
        subplot_titles=(
            f"{ticker} — Candlestick",
            "Buy / Sell Volume (per bar)",
            "Cumulative Volume — session-reset (solid) vs all-time (toggle in legend)",
            "CVD (Cumulative Volume Delta, session-reset)",
            "Buy Ratio (left) + Pressure ROC / Momentum (right, toggle in legend)"
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

        # ── Cumulative lines (row 3)
        # 세션(하루)별 리셋 누적 — 기본 표시
        d = df.index.date
        cumR_total = df["volume"].groupby(d).cumsum()
        cumR_buy   = df["buy_pressure"].groupby(d).cumsum()
        cumR_sell  = df["sell_pressure"].groupby(d).cumsum()
        # 전체 누적 — legend에서 토글
        cumA_total = df["volume"].cumsum()
        cumA_buy   = df["buy_pressure"].cumsum()
        cumA_sell  = df["sell_pressure"].cumsum()

        # 표시 상태: 선택된 tf의 세션리셋은 보이고, 전체누적은 legendonly(숨김)
        vis_reset = visible
        vis_all   = "legendonly" if visible else False

        cum_specs = [
            ("Total (reset)", cumR_total, "#42a5f5", vis_reset),
            ("Buy (reset)",   cumR_buy,   "#26a69a", vis_reset),
            ("Sell (reset)",  cumR_sell,  "#ef5350", vis_reset),
            ("Total (all)",   cumA_total, "#90caf9", vis_all),
            ("Buy (all)",     cumA_buy,   "#80cbc4", vis_all),
            ("Sell (all)",    cumA_sell,  "#ef9a9a", vis_all),
        ]
        for name, series, color, vis in cum_specs:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=series,
                    mode="lines", name=f"Cum {name}",
                    line=dict(color=color, width=2), connectgaps=True,
                    visible=vis,
                    showlegend=(tf == default_tf),
                    hovertemplate=f"<b>%{{x}}</b><br>Cum {name}: %{{y:,.0f}}<extra></extra>",
                ),
                row=3, col=1
            )

        # ── CVD (row 4) — 세션 리셋된 누적 델타, 0 기준 등락
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["cvd_end"],
                mode="lines", name="CVD",
                line=dict(color="#b39ddb", width=2), connectgaps=True,
                visible=visible,
                showlegend=(tf == default_tf),
                hovertemplate="<b>%{x}</b><br>CVD: %{y:,.0f}<extra></extra>",
            ),
            row=4, col=1
        )

        # ── Buy Ratio (row 5, 왼쪽 축) — buy / (buy+sell), 0~1
        ratio = df["buy_pressure"] / (df["buy_pressure"] + df["sell_pressure"])
        fig.add_trace(
            go.Scatter(
                x=df.index, y=ratio,
                mode="lines", name="Buy Ratio",
                line=dict(color="#ffb74d", width=2), connectgaps=True,
                visible=visible,
                showlegend=(tf == default_tf),
                hovertemplate="<b>%{x}</b><br>Buy Ratio: %{y:.1%}<extra></extra>",
            ),
            row=5, col=1, secondary_y=False
        )

        # ── Pressure ROC / Momentum (row 5, 오른쪽 축) — 기본 숨김(legend 토글)
        # 전체(all-hours) 2개 + 정규장(regular-hours) 2개
        vis_mom = "legendonly" if visible else False
        mom_specs = [
            ("Buy ROC % (all)",  "buy_pressure_roc",  "#26a69a"),
            ("Sell ROC % (all)", "sell_pressure_roc", "#ef5350"),
            ("Buy ROC % (reg)",  "buy_roc_reg",       "#80cbc4"),
            ("Sell ROC % (reg)", "sell_roc_reg",      "#ef9a9a"),
        ]
        for name, col, color in mom_specs:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df[col],
                    mode="lines", name=name,
                    line=dict(color=color, width=1.2, dash="dot"), connectgaps=True,
                    visible=vis_mom,
                    showlegend=(tf == default_tf),
                    hovertemplate=f"<b>%{{x}}</b><br>{name}: %{{y:.1f}}%<extra></extra>",
                ),
                row=5, col=1, secondary_y=True
            )

    n_traces = 3 + 6 + 2 + 4  # 캔들 + buy/sell bar + cumulative 6 + CVD + ratio + momentum 4

    # x축 공백 제거 규칙 (intraday는 주말+장외, daily 이상은 주말만)
    # FinViz는 프리마켓(04:00) ~ 애프터마켓(20:00)까지 제공.
    # 데이터 없는 야간(20:00~04:00)만 숨겨 공백 제거.
    intraday_breaks = [
        dict(bounds=["sat", "mon"]),
        dict(bounds=[20, 4], pattern="hour"),
    ]
    daily_breaks = [dict(bounds=["sat", "mon"])]
    no_breaks = []   # 주봉/월봉: 라벨이 주말에 걸릴 수 있어 break 미적용

    def breaks_for(tf):
        if tf in WEEK_OR_ABOVE:
            return no_breaks
        if tf in DAILY_OR_ABOVE:
            return daily_breaks
        return intraday_breaks

    # ── 버튼: 각 timeframe 선택 시 해당 3개 trace만 visible + rangebreak 전환
    buttons = []
    for i, tf in enumerate(timeframes):
        visibility = []
        for j in range(len(timeframes)):
            if j == i:
                # candle + buy/sell bar + 3 reset lines = True (6),
                # 3 all-time lines = legendonly (3), CVD + ratio = True (2),
                # 4 momentum lines = legendonly (4)
                visibility += [True] * 6 + ["legendonly"] * 3 + [True] * 2 + ["legendonly"] * 4
            else:
                visibility += [False] * n_traces

        # 선택된 tf의 trace만 legend에 표시 (candle 제외한 14개)
        showlegend = []
        for j in range(len(timeframes)):
            if j == i:
                showlegend += [False] + [True] * 14   # candle=False, 나머지 14개=True
            else:
                showlegend += [False] * n_traces

        breaks = breaks_for(tf)

        buttons.append(dict(
            label=tf,
            method="update",
            args=[
                {"visible": visibility, "showlegend": showlegend},
                {
                    "title": f"<b>{ticker}</b> — {tf} Bookmap-style CVD Chart",
                    "xaxis.rangebreaks":  breaks,
                    "xaxis2.rangebreaks": breaks,
                    "xaxis3.rangebreaks": breaks,
                    "xaxis4.rangebreaks": breaks,
                    "xaxis5.rangebreaks": breaks,
                }
            ]
        ))

    # ── 시간대 필터 버튼 (rangebreak만 전환, intraday 전용)
    weekend = dict(bounds=["sat", "mon"])
    session_break_sets = {
        "All hours":  [weekend, dict(bounds=[20, 4], pattern="hour")],                                    # 04:00~20:00
        "Regular":    [weekend, dict(bounds=[16, 9.5], pattern="hour")],                                  # 09:30~16:00
        "Extended":   [weekend, dict(bounds=[9.5, 16], pattern="hour"), dict(bounds=[20, 4], pattern="hour")],  # 프리+애프터만
    }
    session_buttons = []
    for label, brk in session_break_sets.items():
        session_buttons.append(dict(
            label=label,
            method="relayout",
            args=[{
                "xaxis.rangebreaks":  brk,
                "xaxis2.rangebreaks": brk,
                "xaxis3.rangebreaks": brk,
                "xaxis4.rangebreaks": brk,
                "xaxis5.rangebreaks": brk,
            }]
        ))

    # ── 레이아웃
    fig.update_layout(
        title=dict(
            text=f"<b>{ticker}</b> — {default_tf} Bookmap-style CVD Chart",
            font=dict(size=18)
        ),
        template="plotly_dark",
        height=1150,
        barmode="overlay",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        bargap=0.1,
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="left", x=0),
        margin=dict(l=60, r=40, t=130, b=120),
        updatemenus=[
            # 1) Timeframe 선택
            dict(
                type="buttons",
                direction="right",
                x=0.0, y=1.12,
                xanchor="left",
                buttons=buttons,
                bgcolor="#2d2d2d",
                bordercolor="#555",
                font=dict(color="white"),
                active=default_idx,
            ),
            # 2) 시간대 필터 (intraday 전용) — rangebreak만 전환
            dict(
                type="buttons",
                direction="right",
                x=0.0, y=1.06,
                xanchor="left",
                buttons=session_buttons,
                bgcolor="#1e1e1e",
                bordercolor="#555",
                font=dict(color="#bbbbbb"),
                active=0,
            ),
        ]
    )

    fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="Cum. Volume", row=3, col=1)
    fig.update_yaxes(title_text="CVD", row=4, col=1)
    fig.update_yaxes(title_text="Buy Ratio", row=5, col=1, tickformat=".0%", range=[0, 1], secondary_y=False)
    fig.update_yaxes(title_text="ROC %", row=5, col=1, secondary_y=True)
    fig.update_xaxes(title_text="Time", row=5, col=1)

    # 기준선들
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=2, col=1)   # buy/sell
    fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=4, col=1)   # CVD 0
    fig.add_hline(y=0.5, line=dict(color="gray", width=0.8, dash="dot"), row=5, col=1) # ratio 50%

    # 장 마감 + 주말 공백 제거 (default = 1hr, intraday)
    fig.update_xaxes(rangebreaks=breaks_for(default_tf))

    return fig


def show_chart(ticker: str = "NVDA", save_html: bool = True, auto_fetch: bool = True):
    ticker = ticker.upper()
    df_1min, frames = run_pipeline(ticker)

    # 데이터가 없으면 FinViz에서 자동으로 받아온 뒤 재시도
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
    # 사용법: python -m cvd.visualizer [TICKER]
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    show_chart(ticker)
