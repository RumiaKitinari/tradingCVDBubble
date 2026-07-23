"""
cvd/ofi.py
----------
Order-Flow Imbalance (OFI) — Cont, Kukanov & Stoikov (2014), "The Price Impact
of Order Book Events".

CVD signs *trades* by aggressor. OFI instead measures net pressure from changes
in the best bid/ask QUEUES themselves (size added at the bid = demand, size
pulled from the ask = supply removed), which the literature finds explains
short-horizon price moves better than trade volume alone. It needs top-of-book
sizes — stored in raw_quotes as bid_size/ask_size since 2026-07-22.

Per consecutive best-quote update n, the event contribution is
    e_n =  q^b_n · 1{P^b_n ≥ P^b_{n-1}}  −  q^b_{n-1} · 1{P^b_n ≤ P^b_{n-1}}
         − q^a_n · 1{P^a_n ≤ P^a_{n-1}}  +  q^a_{n-1} · 1{P^a_n ≥ P^a_{n-1}}
and OFI over a bar = Σ e_n in that bar. Price change over the bar is expected to
be ~ linear in OFI.
"""
import numpy as np
import pandas as pd


def ofi_events(bid, ask, bid_size, ask_size) -> np.ndarray:
    """Per-update OFI contribution e_n (first element is 0 — no predecessor)."""
    Pb = np.asarray(bid, float);  Pa = np.asarray(ask, float)
    qb = np.asarray(bid_size, float); qa = np.asarray(ask_size, float)
    n = len(Pb)
    e = np.zeros(n)
    if n < 2:
        return e
    Pb0, Pb1 = Pb[:-1], Pb[1:]
    Pa0, Pa1 = Pa[:-1], Pa[1:]
    qb0, qb1 = qb[:-1], qb[1:]
    qa0, qa1 = qa[:-1], qa[1:]
    dW = qb1 * (Pb1 >= Pb0) - qb0 * (Pb1 <= Pb0)     # bid-queue (demand) change
    dV = qa1 * (Pa1 <= Pa0) - qa0 * (Pa1 >= Pa0)     # ask-queue (supply) change
    e[1:] = dW - dV
    # a missing quote/size on either side makes the event undefined -> 0
    bad = np.isnan(Pb1) | np.isnan(Pa1) | np.isnan(qb1) | np.isnan(qa1) \
        | np.isnan(Pb0) | np.isnan(Pa0) | np.isnan(qb0) | np.isnan(qa0)
    e[1:][bad] = 0.0
    return e


def ofi_frame(quotes: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample a sized-quote stream to `rule`, returning per-bar OFI and mid.

    quotes: columns date, bid, ask, bid_size, ask_size (raw_quotes with sizes).
    Returns index=bar, columns 'ofi' (Σ e_n) and 'mid' (last mid in the bar).
    """
    q = quotes.sort_values("date").reset_index(drop=True)
    e = ofi_events(q["bid"], q["ask"], q["bid_size"], q["ask_size"])
    mid = (pd.to_numeric(q["bid"], errors="coerce")
           + pd.to_numeric(q["ask"], errors="coerce")) / 2.0
    g = pd.DataFrame({"ofi": e, "mid": mid.to_numpy()},
                     index=pd.to_datetime(q["date"]))
    out = g.resample(rule).agg(ofi=("ofi", "sum"), mid=("mid", "last"))
    return out.dropna(subset=["mid"])
