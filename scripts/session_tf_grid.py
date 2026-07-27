"""
scripts/session_tf_grid.py
--------------------------
CVD <-> price tracking grid on the CLEAN (post-subscription) tick feed.

    3 tickers (Mega NVDA / Micro SOFI / Nano RUM)
  x 3 sessions (Pre 04:00-09:30, Regular 09:30-16:00, After 16:00-20:00)
  x 3 timeframes (1sec, 1min, 1hr)

Metric per cell = within-day, within-session change-correlation of per-bar
buy/sell delta vs per-bar price change, plus directional hit% and n paired bars
(same definition as scripts/corr_common.py). We correlate CHANGES, not levels,
because two cumulative series give a spurious (Granger-Newbold) correlation.

Only --days that were collected under the consolidated subscription should be
passed (default 2026-07-21, 2026-07-22): the earlier free "non-consolidated"
feed captured <10% of the tape and is directionally biased (see
scripts/CVD_full_tape_verification_report.md).

Usage:
    python scripts/session_tf_grid.py
    python scripts/session_tf_grid.py --days 2026-07-21 2026-07-22 --out report.md
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from cvd.calculator import run_pipeline
from corr_common import session_change_corr, SESSIONS

TIERS = [("Mega", "NVDA"), ("Micro", "SOFI"), ("Nano", "RUM")]
TFS = ["1sec", "1min", "5min", "15min", "1hr"]
# The consolidated market-data subscription went live on this date; earlier days
# are the free non-consolidated feed (<10% capture, biased) and are excluded.
SUBSCRIPTION_LIVE = "2026-07-21"


def clean_days_since(since: str) -> list:
    """Every calendar day (ET) >= `since` that has NVDA raw_ticks — auto-grows
    as the collector keeps running, so a daily re-run needs no arg changes."""
    c = MongoClient("mongodb://localhost:27017/")["finviz_db"]["raw_ticks"]
    lo = datetime.strptime(since, "%Y-%m-%d")
    days = c.distinct("date", {"ticker": "NVDA", "date": {"$gte": lo}})
    return sorted({d.date() for d in days})


def cell(triple):
    corr, hit, n = triple
    if n == 0 or (isinstance(corr, float) and np.isnan(corr)):
        return "—"
    hs = f"{hit:.0f}%" if not (isinstance(hit, float) and np.isnan(hit)) else "·"
    return f"{corr:+.2f} / {hs} / {n}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="+", default=None,
                    help="Explicit calendar days (ET). Default: auto — every day "
                         f"from --since onward that has data.")
    ap.add_argument("--since", default=SUBSCRIPTION_LIVE,
                    help="Include all clean-feed days from this date onward.")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                    "CVD_session_tf_grid_report.md"))
    args = ap.parse_args()
    if args.days:
        keep = sorted({datetime.strptime(d, "%Y-%m-%d").date() for d in args.days})
    else:
        keep = clean_days_since(args.since)
    if not keep:
        print(f"No clean-feed days found since {args.since}. Is the collector running?")
        return
    print(f"[grid] clean days: {', '.join(str(d) for d in keep)}")

    # rows[(tier,ticker)][ (session, tf) ] = (corr,hit,n)
    rows = {}
    per_ticker_frames = {}
    for tier, tk in TIERS:
        _, frames = run_pipeline(tk, base_timeframe="raw_tick", only=TFS)
        per_ticker_frames[tk] = frames
        cells = {}
        for tf in TFS:
            fr = frames.get(tf)
            if fr is not None and not fr.empty:
                fr = fr[np.isin(fr.index.date, list(keep))]   # clean days only
            for sname, (lo, hi) in SESSIONS.items():
                cells[(sname, tf)] = (session_change_corr(fr, lo, hi)
                                      if fr is not None else (np.nan, np.nan, 0))
        rows[(tier, tk)] = cells

    # ── Console + markdown ────────────────────────────────────────────────────
    days_str = ", ".join(str(d) for d in keep)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    L = [f"# CVD ↔ Price Grid — clean consolidated feed",
         f"_Days: {days_str} · generated {stamp}_", "",
         "Cell = **change-corr / directional-hit% / n bars** "
         "(per-bar delta vs per-bar price change, within session & day). "
         "Higher corr and hit% = CVD tracks price better. Watch **n**: at 1hr a "
         "session holds only a few bars, so those cells are noisy by construction.",
         "", "Feed: post-subscription tick data (~71% tape capture, direction "
         "unbiased — verified in CVD_full_tape_verification_report.md). "
         "The pre-subscription free feed is excluded (it saw <10% of the tape).",
         ""]

    for tf in TFS:
        L += [f"## {tf}",
              "| Tier | Ticker | Pre | Regular | After |",
              "|---|---|---|---|---|"]
        for (tier, tk), cells in rows.items():
            r = " | ".join(cell(cells[(s, tf)]) for s in SESSIONS)
            L.append(f"| {tier} | {tk} | {r} |")
        # session-mean corr across the 3 tickers
        means = []
        for s in SESSIONS:
            vals = [rows[k][(s, tf)][0] for k in rows
                    if not np.isnan(rows[k][(s, tf)][0])]
            means.append(f"{np.mean(vals):+.2f}" if vals else "—")
        L.append(f"| **mean** | — | **{means[0]}** | **{means[1]}** | **{means[2]}** |")
        L.append("")

    report = "\n".join(L)
    with open(args.out, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"\n[written] {args.out}")


if __name__ == "__main__":
    main()
