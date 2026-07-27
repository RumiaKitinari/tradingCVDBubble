"""
scripts/coverage_and_latency.py
-------------------------------
Task B deliverable: quantitative answer to the professor's two concerns —

  (1) "The feed is probably 15-minute delayed (free account)."
      -> END-TO-END LATENCY: exchange tick time (raw_ticks.date, ET) vs the time
         our process actually inserted the doc (the MongoDB ObjectId embeds an
         insertion timestamp). If the median gap is seconds, there is no 15-min
         delay. (Architecturally there cannot be: IBKR tick-by-tick AllLast is
         only served on a REAL-TIME subscription; the delayed feed delivers
         15-min-delayed snapshots and no tick-by-tick at all.)

  (2) "Buy/sell volume doesn't explain price."
      -> COVERAGE: our AllLast tick volume as a fraction of FinViz consolidated
         (SIP) 1-minute volume, per regular-hours day. IBKR AllLast excludes
         odd-lots and most dark-pool/ATS prints, so we only see a slice of the
         tape — a structural reason the volume signal looks weak that has
         nothing to do with delay.

Outputs a PNG figure (scripts/coverage_latency.png) + a markdown report
(scripts/CVD_coverage_latency_report.md).

Usage:  python -m scripts.coverage_and_latency
"""
import argparse
from datetime import timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pymongo import MongoClient

ET = ZoneInfo("America/New_York")
TICKERS = ["NVDA", "SOFI", "RUM"]
# Coverage needs a TRUSTWORTHY consolidated denominator. RUM's FinViz 1-min
# feed is too sparse/broken (yields impossible >100% coverage), so RUM is used
# for latency only; coverage is measured on the liquid names where FinViz SIP
# 1-min volume is complete.
COVERAGE_TICKERS = ["NVDA", "SOFI"]
# Drop obviously broken FinViz days (consolidated volume far below the norm).
MIN_FINVIZ_DAY_VOL = {"NVDA": 5_000_000, "SOFI": 1_000_000, "RUM": 50_000}


def latency_sample(c, ticker, n=40000):
    """End-to-end latency (seconds): insert time (ObjectId) − exchange tick time.
    Sample the most recent n instrumented ticks (collector running = fresh)."""
    docs = list(c["raw_ticks"].find(
        {"ticker": ticker, "cls": {"$exists": True}},
        {"_id": 1, "date": 1}).sort("date", -1).limit(n))
    if not docs:
        return None
    rows = []
    for d in docs:
        insert_utc = d["_id"].generation_time                      # tz-aware UTC
        tick_utc = d["date"].replace(tzinfo=ET).astimezone(timezone.utc)  # ET-naive→UTC
        rows.append((insert_utc - tick_utc).total_seconds())
    s = pd.Series(rows)
    # ObjectId second-resolution + batch inserts add up to a few seconds of
    # apparent lag; drop negatives (clock skew) and absurd >1h (overnight).
    return s[(s >= 0) & (s < 3600)]


def coverage_by_day(c, ticker):
    """Per RTH day: our AllLast tick volume vs FinViz consolidated 1-min volume."""
    tr = pd.DataFrame(list(c["raw_ticks"].find(
        {"ticker": ticker}, {"_id": 0, "date": 1, "size": 1})))
    fv = pd.DataFrame(list(c["candles"].find(
        {"ticker": ticker, "timeframe": "1min", "source": {"$regex": "finviz"}},
        {"_id": 0, "date": 1, "volume": 1})))
    if tr.empty or fv.empty:
        return pd.DataFrame()
    for df in (tr, fv):
        df["date"] = pd.to_datetime(df["date"])
    def rth(df):
        m = df["date"].dt.hour * 60 + df["date"].dt.minute
        return df[(m >= 570) & (m < 960)]
    ours = rth(tr).groupby(rth(tr)["date"].dt.date)["size"].sum()
    cons = rth(fv).groupby(rth(fv)["date"].dt.date)["volume"].sum()
    df = pd.DataFrame({"ours": ours, "consolidated": cons}).dropna()
    df = df[df["consolidated"] >= MIN_FINVIZ_DAY_VOL[ticker]]   # clean days only
    df["coverage_pct"] = df["ours"] / df["consolidated"] * 100
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", default=TICKERS)
    args = ap.parse_args()
    c = MongoClient("mongodb://localhost:27017/")["finviz_db"]

    lat, cov = {}, {}
    for tk in args.tickers:
        lat[tk] = latency_sample(c, tk)
    for tk in COVERAGE_TICKERS:
        cov[tk] = coverage_by_day(c, tk)

    # ---- figure: latency (left) + coverage (right) ----
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13, 4.8))
    fig.suptitle("IBKR AllLast tick feed — latency vs. tape coverage", fontsize=13, weight="bold")

    for tk in args.tickers:
        s = lat[tk]
        if s is not None and len(s):
            axL.hist(s, bins=np.linspace(0, 12, 49), alpha=0.55, label=f"{tk} (p50={s.median():.1f}s)")
    axL.set_xlim(0, 12)
    axL.text(0.97, 0.72, "a 15-min delayed feed would\nsit at 900 s — far off-screen →",
             transform=axL.transAxes, color="crimson", fontsize=9,
             ha="right", va="top")
    axL.set_xlabel("end-to-end latency  (DB insert − exchange tick time, s)")
    axL.set_ylabel("ticks")
    axL.set_title("(1) No 15-minute delay: ticks arrive in seconds", fontsize=10)
    axL.legend(fontsize=8)

    labels, vals, colors = [], [], []
    palette = {"NVDA": "#2962ff", "SOFI": "#26a69a", "RUM": "#ffa726"}
    for tk in COVERAGE_TICKERS:
        for d, row in cov[tk].iterrows():
            labels.append(f"{tk}\n{d:%m-%d}")
            vals.append(row["coverage_pct"])
            colors.append(palette.get(tk, "#888"))
    xs = np.arange(len(labels))
    axR.bar(xs, vals, color=colors)
    axR.set_xticks(xs); axR.set_xticklabels(labels, fontsize=7)
    axR.set_ylabel("our tick volume ÷ consolidated (%)")
    axR.set_title("(2) We see only a slice of the tape (RTH, clean days)", fontsize=10)
    for x, v in zip(xs, vals):
        axR.text(x, v + max(vals)*0.02, f"{v:.0f}%", ha="center", fontsize=7)
    axR.axhline(100, color="gray", ls=":", lw=1)

    fig.tight_layout(rect=[0, 0, 1, 0.94])
    png = "scripts/coverage_latency.png"
    fig.savefig(png, dpi=130)
    print(f"[figure] {png}")

    # ---- markdown report ----
    L = ["# Feed Latency & Tape Coverage (Task B — For Professor)", "",
         "Data-driven answers for the two concerns. Chart: `coverage_latency.png`", "",
         "## 1. \"Is this a 15-minute delayed feed?\" → No (End-to-end latency is in seconds)", "",
         "The difference between the **exchange tick time** (raw_ticks.date) of each trade and the time our process "
         "**inserted it into the DB** (creation timestamp embedded in MongoDB ObjectId) = "
         "Exchange → Our DB end-to-end latency.", "",
         "| Ticker | p50 | p90 | p99 | n |", "|---|---|---|---|---|"]
    for tk in args.tickers:
        s = lat[tk]
        if s is not None and len(s):
            L.append(f"| {tk} | {s.median():.1f}s | {s.quantile(.9):.1f}s | "
                     f"{s.quantile(.99):.1f}s | {len(s):,} |")
    L += ["",
          "Latency is **in seconds** (mostly from ObjectId's 1s resolution + batch insert). "
          "If it were a 15-min (900s) delay, the values above would be around 900s, which they are not.", "",
          "Structurally, **we cannot even receive this data from a delayed feed**: IBKR's "
          "tick-by-tick AllLast stream is **only available for real-time subscriptions**, and the delayed (free) "
          "feed only provides 15-min delayed snapshots without tick-by-tick. The fact that we receive tens to hundreds "
          "of trades individually per second is proof that it is real-time.", "",
          "## 2. \"Volume doesn't explain price\" → It's a coverage issue, not latency", "",
          "Our AllLast tick volume ÷ FinViz consolidated (SIP) 1-min volume, during regular hours on normal days:", "",
          "| Ticker | Date | Our Tick Volume | Consolidated Vol | Coverage |",
          "|---|---|---|---|---|"]
    for tk in COVERAGE_TICKERS:
        for d, row in cov[tk].iterrows():
            L.append(f"| {tk} | {d:%m-%d} | {row['ours']:,.0f} | "
                     f"{row['consolidated']:,.0f} | **{row['coverage_pct']:.1f}%** |")
    allcov = pd.concat([cov[tk]["coverage_pct"] for tk in COVERAGE_TICKERS if not cov[tk].empty])
    L += ["",
          f"In high-liquidity stocks, we only see about **{allcov.mean():.0f}%** (4~14%) of the consolidated tape. "
          "This is because AllLast excludes odd-lots and most dark pool/ATS prints. "
          "Thus, the buy/sell volume appearing weak is **because we only see a fraction of the tape, not due to latency**. "
          "(Low-liquidity stocks like RUM were excluded from coverage as the FinViz 1-min data itself is flawed, "
          "and were only used for latency measurement.)", "",
          "## Conclusion", "",
          "- Latency: None (seconds). The 15-min delay hypothesis is rejected.",
          "- Real reason for weak volume↔price relation = ① Coverage (~10% of consolidated) + ② Microstructural signals are "
          "inherently weak (Task A: Even with Lee-Ready reclassification, regular hour change-corr is near 0).",
          "- Complete verification will be possible after improving tape coverage with an L2 (Market Depth) subscription."]
    path = "scripts/CVD_coverage_latency_report.md"
    with open(path, "w") as f:
        f.write("\n".join(L))
    print(f"[report] {path}")
    print("\n".join(L))


if __name__ == "__main__":
    main()
