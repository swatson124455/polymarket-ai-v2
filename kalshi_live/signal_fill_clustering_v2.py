#!/usr/bin/env python3
"""SIGNAL INVENTORY part 2 (v2) — INVENTORY-AWARE fill clustering.

READ-ONLY. New file; edits nothing. Supersedes the aggregate half of
signal_fill_clustering.py, which had a construction defect worth recording:

  v1 measured contract-level |net|/gross "one-sidedness". That is not a run-over
  signal, because a maker's EXIT fill lands on the opposite book side from the entry
  and therefore CANCELS the imbalance. A contract we were run over in, exited, and
  re-entered scores 0.000 "one-sided" — perfectly balanced — which is why v1's sweep
  collapsed to TPR 0.09 @ FPR 0.25 and then to 0/0. The defect is in the metric, not
  in the data.

v2 replays a running inventory per contract and classifies each fill:
    BUILD  = |inventory| increased  (a new bet)
    REDUCE = |inventory| decreased  (an exit / self-hedge)
Clustering is then measured over BUILD fills only, which is what "being run over"
actually looks like: repeated one-way accumulation with no offsetting flow.

Labels:
  fill-level  POS = fill followed by a >=5c adverse book move within 10 min
  contract-level POS = a lot expired at 0.00 in kalshi_transactions_2026-07-23.csv

Usage: python kalshi_live/signal_fill_clustering_v2.py <fills.json> <hist_cache.json>
"""
import csv
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fair(c):
    try:
        b = float(c["yes_bid"]["close_dollars"])
        a = float(c["yes_ask"]["close_dollars"])
    except Exception:  # noqa: BLE001
        return None
    return (a + b) / 2.0 if (b > 0.0 and a < 1.0 and (a - b) <= 0.20) else None


def main():
    fills = json.load(open(sys.argv[1]))
    hist = json.load(open(sys.argv[2]))

    pnl, worthless = defaultdict(float), set()
    with open(CSV_PATH, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] != "trade":
                continue
            pnl[r["market_ticker"]] += float(r["realized_pnl_with_fees_dollars"])
            if float(r["exit_price_dollars"]) == 0.0:
                worthless.add(r["market_ticker"])

    idx = {tk: {c["end_period_ts"]: fair(c) for c in v["candles"]}
           for tk, v in hist.items()}

    def f_at(tk, t):
        d = idx.get(tk) or {}
        t = int(t) // 60 * 60
        for k in range(0, 6):
            for cand in (t - k * 60, t + k * 60):
                if d.get(cand) is not None:
                    return d[cand]
        return None

    by = defaultdict(list)
    for f in fills:
        by[f["ticker"]].append((ts(f["created_time"]),
                                1 if f["book_side"] == "bid" else -1,
                                float(f["count_fp"]), bool(f["is_taker"])))
    for k in by:
        by[k].sort()

    # ---- classify BUILD vs REDUCE by replaying inventory --------------------
    tagged = defaultdict(list)
    for tk, seq in by.items():
        inv = 0.0
        for t, s, q, tk_ in seq:
            new = inv + s * q
            kind = "BUILD" if abs(new) > abs(inv) + 1e-9 else "REDUCE"
            tagged[tk].append((t, s, q, kind, inv, new, tk_))
            inv = new

    print("=" * 104)
    print("A. BUILD vs REDUCE fill mix (inventory replay)")
    print("=" * 104)
    for fam in ["GAS", "TEMP"]:
        sel = [tk for tk in tagged if (tk.startswith("KXTEMP")) == (fam == "TEMP")]
        allf = [x for tk in sel for x in tagged[tk]]
        b = [x for x in allf if x[3] == "BUILD"]
        print(f"  {fam}: contracts={len(sel)} fills={len(allf)} "
              f"BUILD={len(b)} ({len(b)/len(allf):.0%}) REDUCE={len(allf)-len(b)}")

    print()
    print("=" * 104)
    print("B. BUILD-FILL CLUSTERING per contract — the actual 'run over' shape")
    print("   build_run = longest streak of consecutive BUILD fills in the same direction")
    print("   gap = seconds between consecutive same-direction BUILD fills")
    print("=" * 104)
    rows = []
    for tk, seq in tagged.items():
        b = [x for x in seq if x[3] == "BUILD"]
        if not b:
            continue
        gaps, run, best, prev = [], 0, 0, None
        for i, x in enumerate(b):
            if prev is not None and x[1] == prev[1]:
                gaps.append(x[0] - prev[0])
                run += 1
            else:
                run = 1
            best = max(best, run)
            prev = x
        peak_inv = max(abs(x[5]) for x in seq)
        rows.append(dict(tk=tk, fam="TEMP" if tk.startswith("KXTEMP") else "GAS",
                         nb=len(b), best=best,
                         medgap=st.median(gaps) if gaps else None,
                         mingap=min(gaps) if gaps else None,
                         peak=peak_inv, w=tk in worthless,
                         pnl=pnl.get(tk), gross=sum(x[2] for x in b)))

    def agg(sel, name):
        s = [r for r in rows if sel(r)]
        if not s:
            print(f"  {name:<34} n=0")
            return
        g = [r["medgap"] for r in s if r["medgap"] is not None]
        print(f"  {name:<34} contracts={len(s):<4} build_fills={sum(r['nb'] for r in s):<5} "
              f"med_build_run={st.median([r['best'] for r in s]):.1f}  "
              f"med_peak_inv={st.median([r['peak'] for r in s]):.1f}ct  "
              f"med_gap_between_same_dir_builds="
              f"{(st.median(g) if g else float('nan')):.0f}s (n={len(g)})")

    agg(lambda r: r["fam"] == "GAS", "GAS all")
    agg(lambda r: r["fam"] == "TEMP", "TEMP all")
    print()
    agg(lambda r: r["w"], "EXPIRED-WORTHLESS contracts")
    agg(lambda r: r["pnl"] is not None and not r["w"], "labelled, NOT worthless")
    print()
    agg(lambda r: r["fam"] == "TEMP" and r["w"], "TEMP worthless  <- pos control")
    agg(lambda r: r["fam"] == "GAS" and r["pnl"] is not None and not r["w"],
        "GAS non-worthless <- neg control")

    print()
    print("  contract-level sweep: fire if longest BUILD run >= X")
    P = [r for r in rows if r["w"]]
    N = [r for r in rows if r["pnl"] is not None and not r["w"]]
    print(f"  positives={len(P)} negatives={len(N)}")
    print(f"    {'X':>4}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}")
    for X in [2, 3, 4, 5]:
        tp = sum(1 for r in P if r["best"] >= X)
        fp = sum(1 for r in N if r["best"] >= X)
        print(f"    {X:>4}{tp/len(P):>7.2f}{fp/len(N):>7.2f}{tp:>5}{fp:>5}")
    print("  contract-level sweep: fire if peak |inventory| >= X contracts")
    print(f"    {'X':>4}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}")
    for X in [15, 20, 25, 30, 40]:
        tp = sum(1 for r in P if r["peak"] >= X)
        fp = sum(1 for r in N if r["peak"] >= X)
        print(f"    {X:>4}{tp/len(P):>7.2f}{fp/len(N):>7.2f}{tp:>5}{fp:>5}")

    # ---- fill-level: recent BUILD pressure as an online detector ------------
    print()
    print("=" * 104)
    print("C. FILL-LEVEL ONLINE DETECTOR — signed BUILD contracts acquired in the")
    print("   PRIOR 4 min (2 cycles), scored against 'this fill got run over'")
    print("=" * 104)
    for fam in ["GAS", "TEMP"]:
        P, N = [], []
        for tk, seq in tagged.items():
            if (tk.startswith("KXTEMP")) != (fam == "TEMP"):
                continue
            for i, x in enumerate(seq):
                t, s, q, kind, inv, new, _ = x
                f0, f10 = f_at(tk, t), f_at(tk, t + 600)
                if f0 is None or f10 is None:
                    continue
                fwd = s * (f10 - f0)
                prior = sum(y[1] * y[2] for y in seq[:i]
                            if y[3] == "BUILD" and t - y[0] <= 240)
                rec = dict(prior_same_dir=s * prior, inv_before=abs(inv), q=q)
                (P if fwd <= -0.05 else N).append(rec)
        if not P or not N:
            continue
        print(f"\n  {fam}: run-over fills={len(P)}  other fills={len(N)}")
        print(f"    mean prior-4min BUILD in the SAME direction: "
              f"run-over={st.mean([r['prior_same_dir'] for r in P]):+.1f}ct  "
              f"other={st.mean([r['prior_same_dir'] for r in N]):+.1f}ct")
        print(f"    mean |inventory| already held at the fill: "
              f"run-over={st.mean([r['inv_before'] for r in P]):.1f}ct  "
              f"other={st.mean([r['inv_before'] for r in N]):.1f}ct")
        print(f"    {'X':>5}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}   "
              f"(fire if prior-4min same-dir BUILD >= X ct)")
        for X in [1, 10, 20, 30]:
            tp = sum(1 for r in P if r["prior_same_dir"] >= X)
            fp = sum(1 for r in N if r["prior_same_dir"] >= X)
            print(f"    {X:>5}{tp/len(P):>7.2f}{fp/len(N):>7.2f}{tp:>5}{fp:>5}")
        print(f"    {'X':>5}{'TPR':>7}{'FPR':>7}{'TP':>5}{'FP':>5}   "
              f"(fire if |inventory| already held >= X ct  <- TODAY'S CONTROL)")
        for X in [5, 15, 30, 60]:
            tp = sum(1 for r in P if r["inv_before"] >= X)
            fp = sum(1 for r in N if r["inv_before"] >= X)
            print(f"    {X:>5}{tp/len(P):>7.2f}{fp/len(N):>7.2f}{tp:>5}{fp:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
