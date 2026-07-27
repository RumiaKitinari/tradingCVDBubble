"""
scripts/regression_eventstudy.py
--------------------------------
Task C: does the volume signal explain price beyond a raw correlation, once we
(a) tame the heavy-tailed size distribution, and (b) look at what price does
AROUND the big prints (the bubbles)?

Uses the Lee-Ready time-aligned delta (ibkr/reclassify.py) so classification is
the best available; regular hours only; pooled within-day so returns never span
the overnight gap.

1) REGRESSION  — per timeframe, OLS of bar return on the volume delta, three
   specs, to see whether size-taming helps:
     raw     : ret ~ delta
     signed  : ret ~ sign(delta)*log1p(|delta|)     (compresses whales)
     winsor  : signed, with both sides winsorized at 1/99 pct
   reports beta, t-stat, R², n.

2) EVENT STUDY — flag bars whose within-day delta z-score exceeds ±2 (the
   "bubbles"), then average the forward cumulative return over the next
   1/3/5/10 bars, split by the sign of the event delta. If buy-bubbles are
   followed by higher prices and sell-bubbles by lower, the signal has forward
   information; if the forward paths are flat or crossed, it does not.

Usage:  python -m scripts.regression_eventstudy [--tickers NVDA SOFI RUM]
"""
import argparse
import numpy as np
import pandas as pd

from ibkr.reclassify import load_streams, reclassify

TFS = ["1min", "5min"]
REG_LO, REG_HI = 570, 960          # 09:30–16:00 ET in minutes


def rth_bars(merged: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule = {"1min": "1min", "5min": "5min"}[tf]
    d = merged.set_index("date")
    bar = d.resample(rule).agg(close=("price", "last"),
                               delta=("delta_new", "sum"),
                               vol=("size", "sum")).dropna(subset=["close"])
    m = bar.index.hour * 60 + bar.index.minute
    return bar[(m >= REG_LO) & (m < REG_HI)]


def within_day_returns(bar: pd.DataFrame):
    """Return a frame with per-bar return + delta, computed within each day."""
    parts = []
    for _, g in bar.groupby(bar.index.date):
        g = g.sort_index().copy()
        g["ret"] = g["close"].diff()
        parts.append(g)
    out = pd.concat(parts).dropna(subset=["ret"])
    return out


def ols(y, x):
    """Simple OLS y = a + b x. Returns (beta, t_stat, r2, n)."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 10 or x.std() == 0:
        return np.nan, np.nan, np.nan, n
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = n - 2
    sigma2 = (resid @ resid) / dof
    xtx_inv = np.linalg.inv(X.T @ X)
    se = np.sqrt(sigma2 * np.diag(xtx_inv))
    t = beta[1] / se[1] if se[1] > 0 else np.nan
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return beta[1], t, r2, n


def winsorize(s, lo=0.01, hi=0.99):
    ql, qh = s.quantile(lo), s.quantile(hi)
    return s.clip(ql, qh)


def regression_block(df: pd.DataFrame):
    d = df["delta"]
    signed = np.sign(d) * np.log1p(np.abs(d))
    specs = {
        "raw     (ret~delta)": d,
        "signed  (ret~slog Δ)": signed,
        "winsor  (slog, 1/99)": winsorize(signed),
    }
    rows = []
    for name, x in specs.items():
        beta, t, r2, n = ols(df["ret"], x)
        rows.append((name, beta, t, r2, n))
    return rows


def event_study(bar: pd.DataFrame, horizons=(1, 3, 5, 10), z=2.0):
    """Forward cumulative return after ±z delta z-score bars, by event sign."""
    buy_paths = {h: [] for h in horizons}
    sell_paths = {h: [] for h in horizons}
    n_buy = n_sell = 0
    for _, g in bar.groupby(bar.index.date):
        g = g.sort_index().reset_index(drop=True)
        if len(g) < max(horizons) + 5:
            continue
        dz = (g["delta"] - g["delta"].mean()) / (g["delta"].std() or 1)
        for i in range(len(g) - max(horizons)):
            if abs(dz.iloc[i]) < z:
                continue
            base = g["close"].iloc[i]
            sign = np.sign(dz.iloc[i])
            for h in horizons:
                fwd = g["close"].iloc[i + h] - base
                (buy_paths if sign > 0 else sell_paths)[h].append(fwd)
            if sign > 0:
                n_buy += 1
            else:
                n_sell += 1
    def avg(paths):
        return {h: (np.mean(v) if v else np.nan) for h, v in paths.items()}
    return avg(buy_paths), avg(sell_paths), n_buy, n_sell


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=["NVDA", "SOFI", "RUM"])
    ap.add_argument("--write-report", action="store_true", default=True)
    args = ap.parse_args()

    L = ["# Regression & Event Study (Task C)", "",
         "Using Lee-Ready time-aligned delta, during regular hours, calculated intraday. "
         "Regression explains bar returns with volume delta (three specs: original / signed-log / winsorized). "
         "Event study averages the cumulative returns after a delta z-score > ±2 bar (bubble) by event sign.", ""]

    for tk in args.tickers:
        trades, quotes = load_streams(tk)
        if trades.empty or quotes.empty:
            L.append(f"## {tk}\n_No streams — skipped_\n")
            print(f"[{tk}] skip (no streams)")
            continue
        q_lo, q_hi = quotes["date"].min(), quotes["date"].max()
        trades = trades[trades["date"].between(q_lo, q_hi)].reset_index(drop=True)
        merged = reclassify(trades, quotes)
        L.append(f"## {tk}")
        print(f"[{tk}] analyzing…")

        for tf in TFS:
            bar = rth_bars(merged, tf)
            df = within_day_returns(bar)
            L.append(f"### {tf} — Regression (n={len(df)})")
            L.append("| Spec | beta | t | R² |")
            L.append("|---|---|---|---|")
            for name, beta, t, r2, n in regression_block(df):
                bs = f"{beta:+.4g}" if np.isfinite(beta) else "—"
                ts = f"{t:+.2f}" if np.isfinite(t) else "—"
                rs = f"{r2:.4f}" if np.isfinite(r2) else "—"
                L.append(f"| {name} | {bs} | {ts} | {rs} |")
            L.append("")

        # event study on 1min
        bar1 = rth_bars(merged, "1min")
        buy, sell, nb, ns = event_study(bar1)
        L.append(f"### Event Study (1min, |z|>2 delta bar)")
        L.append(f"Buy bubble n={nb}, Sell bubble n={ns}. Cumulative price change ($) after event:")
        L.append("| Event | +1 bar | +3 bars | +5 bars | +10 bars |")
        L.append("|---|---|---|---|---|")
        L.append(f"| Buy (+Δ) | " + " | ".join(f"{buy[h]:+.4f}" for h in (1,3,5,10)) + " |")
        L.append(f"| Sell (−Δ) | " + " | ".join(f"{sell[h]:+.4f}" for h in (1,3,5,10)) + " |")
        L.append("")
        print(f"  {tf} done; events buy={nb} sell={ns}")

    report = "\n".join(L)
    print("\n" + report)
    if args.write_report:
        with open("scripts/CVD_regression_eventstudy_report.md", "w") as f:
            f.write(report)
        print("\n[report] scripts/CVD_regression_eventstudy_report.md")


if __name__ == "__main__":
    main()
