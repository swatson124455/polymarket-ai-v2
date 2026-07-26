#!/usr/bin/env python3
"""SIGNAL INVENTORY part 3 — how much does the book move between 2-minute cycles,
per series, and is a drift detector even POSSIBLE at that cadence?

READ-ONLY offline analysis of signal_history_cache.json. New file; edits nothing.

The question that decides everything for weather:
  a detector at cycle cadence can only work if a move is SPREAD OVER SEVERAL CYCLES.
  If the move that kills us completes inside ONE 2-minute bin, no amount of book
  cleverness at 2-minute cadence can see it coming, and the answer has to come from
  somewhere else (don't be there at all / smaller size / external signal).

METRICS
  1. per series: distribution of signed 2-minute mid changes
  2. PERSISTENCE: lag-1 autocorrelation of 2-min changes, and E[next move | this move]
     -> this is the thing that makes or breaks a drift detector
  3. CONCENTRATION: what fraction of a contract's entire terminal journey happens in
     its single biggest 2-minute bin
  4. POSITIVE/NEGATIVE CONTROL at our own fills: at each fill, the drift over the
     PRIOR 2 cycles vs the adverse move over the FOLLOWING cycles.

Usage: python kalshi_live/signal_book_drift.py <cache.json> <fills.json>
"""
import json
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime


def fair(c):
    """BOOK-ONLY mid from a 1-min candle. None when the book is unusable.

    ⚠ CORRECTED. The first version of this function fell back to the last TRADE price
    when the book was one-sided. signal_fair_value_audit.py proved that fallback
    MANUFACTURED the headline numbers: it inflated TEMP 2-min sd 0.0847 -> 0.1167
    (+38%) and produced a fake 0.970 single-bin move on KXTEMPLAXH-26JUL2212-T70.99
    (a 0.98 last-trade print sitting against a 0.20/0.99 book, two minutes before
    expiry). Cross-source deltas had sd 0.2189 vs 0.0847 for book->book. Mixing a
    mid with a last-trade print is not a price change, so the fallback is removed.
    Cost of removing it: TEMP 2-min grid coverage 960 -> 800 deltas (-17%).
    """
    try:
        b = float(c["yes_bid"]["close_dollars"])
        a = float(c["yes_ask"]["close_dollars"])
    except Exception:  # noqa: BLE001
        return None
    if b > 0.0 and a < 1.0 and (a - b) <= 0.20:
        return (a + b) / 2.0
    return None


def series_of(tk):
    return tk.split("-")[0]


def fam(tk):
    return "TEMP" if tk.startswith("KXTEMP") else "GAS"


def to_2min(candles):
    """(ts, fair) sampled on a 2-minute grid — our actual cycle cadence."""
    out = []
    for c in candles:
        t = c["end_period_ts"]
        if t % 120 != 0:
            continue
        f = fair(c)
        if f is not None:
            out.append((t, f))
    return out


def autocorr(x):
    if len(x) < 4:
        return None
    m = st.mean(x)
    num = sum((x[i] - m) * (x[i - 1] - m) for i in range(1, len(x)))
    den = sum((v - m) ** 2 for v in x)
    return num / den if den else None


def main():
    cache = json.load(open(sys.argv[1]))
    fills = json.load(open(sys.argv[2]))

    # ---------------- 1 & 2: per-series 2-minute move distribution -------------
    per_series = defaultdict(list)      # series -> list of signed 2-min deltas
    per_series_n = defaultdict(int)
    coverage = {}
    for tk, v in cache.items():
        pts = to_2min(v["candles"])
        coverage[tk] = len(pts)
        if len(pts) < 3:
            continue
        s = series_of(tk)
        for i in range(1, len(pts)):
            if pts[i][0] - pts[i - 1][0] != 120:
                continue           # skip gaps so a delta is always exactly one cycle
            per_series[s].append(pts[i][1] - pts[i - 1][1])
        per_series_n[s] += 1

    print("=" * 104)
    print("1. SIGNED 2-MINUTE MID CHANGE, BY SERIES  (one cycle of the live bot)")
    print("   contracts = our own traded set only; deltas are consecutive-bin only (gaps dropped)")
    print("=" * 104)
    print(f"{'series':<13}{'ctr':>4}{'n_2min':>8}{'sd':>8}{'p50|d|':>8}{'p90|d|':>8}"
          f"{'p99|d|':>8}{'P>=3c':>8}{'P>=5c':>8}{'P>=10c':>8}{'ac1':>8}")
    order = sorted(per_series, key=lambda s: -len(per_series[s]))
    for s in order:
        d = per_series[s]
        ad = sorted(abs(x) for x in d)
        n = len(d)
        if n < 10:
            continue
        print(f"{s:<13}{per_series_n[s]:>4}{n:>8}{st.pstdev(d):>8.4f}"
              f"{ad[n // 2]:>8.4f}{ad[int(.9 * n)]:>8.4f}{ad[min(n - 1, int(.99 * n))]:>8.4f}"
              f"{sum(1 for x in ad if x >= .03) / n:>8.3f}"
              f"{sum(1 for x in ad if x >= .05) / n:>8.3f}"
              f"{sum(1 for x in ad if x >= .10) / n:>8.3f}"
              f"{(autocorr(d) if autocorr(d) is not None else float('nan')):>8.3f}")

    # family pooled
    print()
    fam_d = defaultdict(list)
    for s, d in per_series.items():
        fam_d["TEMP" if s.startswith("KXTEMP") else "GAS"].extend(d)
    for f, d in fam_d.items():
        ad = sorted(abs(x) for x in d)
        n = len(d)
        print(f"  POOLED {f:<6} n={n:<6} sd={st.pstdev(d):.4f}  p50|d|={ad[n // 2]:.4f}  "
              f"p90|d|={ad[int(.9 * n)]:.4f}  P(|d|>=5c)={sum(1 for x in ad if x >= .05) / n:.3f}  "
              f"ac1={autocorr(d):.3f}")

    # ---------------- 2b: PERSISTENCE — the make-or-break number ---------------
    print()
    print("=" * 104)
    print("2b. PERSISTENCE — given a 2-min move of size >= X, what happens NEXT?")
    print("    E[next move, SAME direction] and P(next move continues). ")
    print("    A drift detector needs continuation. ~0 continuation => the move is over")
    print("    by the time we see it, and NO 2-minute book detector can work.")
    print("=" * 104)
    print(f"{'family':<8}{'trigger':>9}{'n_trig':>8}{'P(cont)':>9}{'E[next|same dir]':>18}"
          f"{'E[|next|]':>11}{'E[sum next 3 cyc]':>19}")
    for f in ["GAS", "TEMP"]:
        seq_all = []
        for tk, v in cache.items():
            if fam(tk) != f:
                continue
            pts = to_2min(v["candles"])
            ds = []
            for i in range(1, len(pts)):
                ds.append(pts[i][1] - pts[i - 1][1] if pts[i][0] - pts[i - 1][0] == 120
                          else None)
            seq_all.append(ds)
        for trig in [0.02, 0.03, 0.05, 0.10]:
            nxt, cont, nxt3 = [], 0, []
            for ds in seq_all:
                for i in range(len(ds) - 1):
                    if ds[i] is None or abs(ds[i]) < trig:
                        continue
                    sgn = 1 if ds[i] > 0 else -1
                    if ds[i + 1] is None:
                        continue
                    nxt.append(sgn * ds[i + 1])
                    cont += 1 if sgn * ds[i + 1] > 0 else 0
                    tail = [ds[i + k] for k in range(1, 4)
                            if i + k < len(ds) and ds[i + k] is not None]
                    nxt3.append(sgn * sum(tail))
            if len(nxt) >= 8:
                print(f"{f:<8}{trig:>9.2f}{len(nxt):>8}{cont / len(nxt):>9.3f}"
                      f"{st.mean(nxt):>18.4f}{st.mean([abs(x) for x in nxt]):>11.4f}"
                      f"{st.mean(nxt3):>19.4f}")
            else:
                print(f"{f:<8}{trig:>9.2f}{len(nxt):>8}   (too few)")

    # ---------------- 3: how concentrated is the terminal journey? -------------
    print()
    print("=" * 104)
    print("3. IS THE MOVE COMPLETE WITHIN ONE CYCLE? Per contract: share of the total")
    print("   absolute 2-min path taken by the single largest bin, and the largest bin size.")
    print("=" * 104)
    rows = []
    for tk, v in cache.items():
        pts = to_2min(v["candles"])
        if len(pts) < 6:
            continue
        ds = [pts[i][1] - pts[i - 1][1] for i in range(1, len(pts))
              if pts[i][0] - pts[i - 1][0] == 120]
        if len(ds) < 5:
            continue
        tot = sum(abs(x) for x in ds)
        mx = max(abs(x) for x in ds)
        net = abs(pts[-1][1] - pts[0][1])
        rows.append((fam(tk), tk, len(ds), tot, mx, mx / tot if tot else 0, net,
                     mx / net if net else 0))
    for f in ["GAS", "TEMP"]:
        s = [r for r in rows if r[0] == f]
        if not s:
            continue
        print(f"  {f}: contracts={len(s)}  "
              f"median max-bin={st.median([r[4] for r in s]):.3f}  "
              f"median max-bin / total-path={st.median([r[5] for r in s]):.3f}  "
              f"median max-bin / NET move={st.median([r[7] for r in s]):.3f}  "
              f"median total-path={st.median([r[3] for r in s]):.3f}")
    print()
    print("  worst single 2-min bins observed (any contract):")
    for r in sorted(rows, key=lambda r: -r[4])[:10]:
        print(f"    {r[1]:<32} {r[0]:<5} bins={r[2]:<4} max_bin={r[4]:.3f} "
              f"total_path={r[3]:.3f} net={r[6]:.3f} maxbin/path={r[5]:.2f}")

    # ---------------- 4: POSITIVE / NEGATIVE CONTROL AT OUR OWN FILLS ----------
    print()
    print("=" * 104)
    print("4. AT OUR OWN FILLS — was there a VISIBLE PRIOR DRIFT before we got run over?")
    print("   For each fill: sgn = +1 if we got long yes (book_side=bid), -1 if short.")
    print("   prior  = sgn * (fair at fill - fair 2 cycles=4min earlier)   [<0 = moving against us already]")
    print("   fwd10  = sgn * (fair 10 min later - fair at fill)            [<0 = ran us over]")
    print("=" * 104)
    idx = {}
    for tk, v in cache.items():
        idx[tk] = {c["end_period_ts"]: fair(c) for c in v["candles"]}

    def f_at(tk, t):
        d = idx.get(tk) or {}
        t = int(t) // 60 * 60
        for k in range(0, 6):
            for cand in (t - k * 60, t + k * 60):
                if d.get(cand) is not None:
                    return d[cand]
        return None

    recs = []
    for fl in fills:
        tk = fl["ticker"]
        t = datetime.fromisoformat(fl["created_time"].replace("Z", "+00:00")).timestamp()
        sgn = 1 if fl["book_side"] == "bid" else -1
        f0, fm4, fp10 = f_at(tk, t), f_at(tk, t - 240), f_at(tk, t + 600)
        mkt = (cache.get(tk) or {}).get("market") or {}
        try:
            close = datetime.fromisoformat(
                mkt["close_time"].replace("Z", "+00:00")).timestamp()
            life = (close - datetime.fromisoformat(
                mkt["open_time"].replace("Z", "+00:00")).timestamp())
            ttc, lifefrac = (close - t) / 60.0, 1 - (close - t) / life if life else None
        except Exception:  # noqa: BLE001
            ttc = lifefrac = None
        if f0 is None:
            continue
        recs.append(dict(tk=tk, fam=fam(tk), sgn=sgn, qty=float(fl["count_fp"]),
                         prior=(sgn * (f0 - fm4)) if fm4 is not None else None,
                         fwd10=(sgn * (fp10 - f0)) if fp10 is not None else None,
                         ttc=ttc, lifefrac=lifefrac, taker=fl["is_taker"],
                         result=mkt.get("result")))

    for f in ["GAS", "TEMP"]:
        s = [r for r in recs if r["fam"] == f and r["fwd10"] is not None]
        if not s:
            continue
        bad = [r for r in s if r["fwd10"] <= -0.05]
        good = [r for r in s if r["fwd10"] > -0.05]
        print(f"\n  {f}: fills with fwd10 available = {len(s)}   "
              f"RUN-OVER (fwd10 <= -5c) = {len(bad)} ({len(bad)/len(s):.1%})")
        for label, grp in (("RUN-OVER  ", bad), ("not run-over", good)):
            p = [r["prior"] for r in grp if r["prior"] is not None]
            if not p:
                continue
            print(f"    {label} n={len(grp):<4} "
                  f"mean prior-4min drift = {st.mean(p):+.4f}   "
                  f"median = {st.median(p):+.4f}   "
                  f"P(prior<0) = {sum(1 for x in p if x < 0)/len(p):.2f}   "
                  f"P(prior<=-2c) = {sum(1 for x in p if x <= -.02)/len(p):.2f}   "
                  f"median ttc_min = "
                  f"{st.median([r['ttc'] for r in grp if r['ttc'] is not None]):.0f}   "
                  f"median life-frac = "
                  f"{st.median([r['lifefrac'] for r in grp if r['lifefrac'] is not None]):.2f}")

        # detector sweep on prior drift
        print(f"    DETECTOR: block the fill if prior-4min drift <= -X. "
              f"POS = run-over fill, NEG = not-run-over fill")
        print(f"      {'X':>6}{'TPR':>8}{'FPR':>8}{'TP':>5}{'FN':>5}{'FP':>5}{'TN':>5}")
        P = [r for r in bad if r["prior"] is not None]
        N = [r for r in good if r["prior"] is not None]
        for X in [0.01, 0.02, 0.03, 0.05]:
            tp = sum(1 for r in P if r["prior"] <= -X)
            fp = sum(1 for r in N if r["prior"] <= -X)
            print(f"      {X:>6.2f}{(tp/len(P) if P else 0):>8.2f}"
                  f"{(fp/len(N) if N else 0):>8.2f}{tp:>5}{len(P)-tp:>5}"
                  f"{fp:>5}{len(N)-fp:>5}")
        # life-fraction alternative (free, no book at all)
        print(f"    ALTERNATIVE (no book needed): block the fill if life-frac >= X")
        print(f"      {'X':>6}{'TPR':>8}{'FPR':>8}{'TP':>5}{'FN':>5}{'FP':>5}{'TN':>5}")
        P2 = [r for r in bad if r["lifefrac"] is not None]
        N2 = [r for r in good if r["lifefrac"] is not None]
        for X in [0.4, 0.5, 0.6, 0.7, 0.8]:
            tp = sum(1 for r in P2 if r["lifefrac"] >= X)
            fp = sum(1 for r in N2 if r["lifefrac"] >= X)
            print(f"      {X:>6.2f}{(tp/len(P2) if P2 else 0):>8.2f}"
                  f"{(fp/len(N2) if N2 else 0):>8.2f}{tp:>5}{len(P2)-tp:>5}"
                  f"{fp:>5}{len(N2)-fp:>5}")

    print()
    print("=" * 104)
    print("CANDLE COVERAGE (2-min usable points per contract)")
    print("=" * 104)
    for f in ["GAS", "TEMP"]:
        v = sorted(coverage[t] for t in coverage if fam(t) == f)
        print(f"  {f}: contracts={len(v)} zero-coverage={sum(1 for x in v if x == 0)} "
              f"min={v[0]} median={v[len(v)//2]} max={v[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
