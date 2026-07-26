#!/usr/bin/env python3
"""SIGNAL INVENTORY — AUDIT of the fair-value construction used by signal_book_drift.py.

signal_book_drift.py reported TEMP 2-minute mid changes with sd = 0.1167 and a single
2-minute bin of 0.970. Those are not credible price moves; they are the signature of a
MIXED-SOURCE series (book mid on some bins, last-trade fallback on others) plus one-sided
books near expiry. This script isolates the cause before any of those numbers are used.

Three fair-value definitions, scored on the SAME candles:
  STRICT   both sides present, 0 < bid, ask < 1, spread <= 20c   -> mid
  WIDE     as STRICT but spread <= 60c
  MIXED    STRICT, else last-trade price   (what signal_book_drift.py used)

Reports per family: coverage of each definition, source mix, and the resulting sd, so the
inflation can be attributed. READ-ONLY. New file; edits nothing.
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict


def parts(c):
    try:
        b = float(c["yes_bid"]["close_dollars"])
        a = float(c["yes_ask"]["close_dollars"])
    except Exception:  # noqa: BLE001
        return None, None, None
    p = c.get("price") or {}
    lt = None
    for k in ("close_dollars", "mean_dollars", "previous_dollars"):
        if p.get(k):
            lt = float(p[k])
            break
    return b, a, lt


def fv(c, mode):
    b, a, lt = parts(c)
    if b is None:
        return None, "none"
    ok = b > 0.0 and a < 1.0
    if mode == "STRICT" and ok and (a - b) <= 0.20:
        return (a + b) / 2.0, "book"
    if mode == "WIDE" and ok and (a - b) <= 0.60:
        return (a + b) / 2.0, "book"
    if mode == "MIXED":
        if ok and (a - b) <= 0.20:
            return (a + b) / 2.0, "book"
        if lt is not None:
            return lt, "trade"
    return None, "drop"


def fam(tk):
    return "TEMP" if tk.startswith("KXTEMP") else "GAS"


def main():
    cache = json.load(open(sys.argv[1]))

    print("=" * 100)
    print("A. SOURCE MIX AND COVERAGE, per family, on 2-minute grid points")
    print("=" * 100)
    for mode in ["STRICT", "WIDE", "MIXED"]:
        src = defaultdict(Counter)
        deltas = defaultdict(list)
        for tk, v in cache.items():
            f = fam(tk)
            pts = []
            for c in v["candles"]:
                if c["end_period_ts"] % 120:
                    continue
                x, s = fv(c, mode)
                src[f][s] += 1
                pts.append((c["end_period_ts"], x, s))
            for i in range(1, len(pts)):
                if pts[i][0] - pts[i - 1][0] != 120:
                    continue
                if pts[i][1] is None or pts[i - 1][1] is None:
                    continue
                deltas[f].append((pts[i][1] - pts[i - 1][1], pts[i - 1][2], pts[i][2]))
        for f in ["GAS", "TEMP"]:
            tot = sum(src[f].values())
            d = [x for x, _, _ in deltas[f]]
            if not d:
                continue
            ad = sorted(abs(x) for x in d)
            same_src = [x for x, s1, s2 in deltas[f] if s1 == s2 == "book"]
            cross = [x for x, s1, s2 in deltas[f] if s1 != s2]
            print(f"  {mode:<7}{f:<6} grid_pts={tot:<6} "
                  f"src={{'book':{src[f]['book']},'trade':{src[f]['trade']},"
                  f"'drop':{src[f]['drop']}}}  usable_deltas={len(d):<5} "
                  f"sd={st.pstdev(d):.4f}  p90|d|={ad[int(.9*len(ad))]:.4f}  "
                  f"max|d|={ad[-1]:.3f}")
            if mode == "MIXED":
                print(f"           book->book deltas n={len(same_src)} "
                      f"sd={st.pstdev(same_src) if len(same_src) > 1 else float('nan'):.4f}"
                      f"   CROSS-SOURCE deltas n={len(cross)} "
                      f"sd={st.pstdev(cross) if len(cross) > 1 else float('nan'):.4f}"
                      f"  <- inflation source")

    print()
    print("=" * 100)
    print("B. THE 0.970 BIN — dump the raw candles around the worst TEMP move")
    print("=" * 100)
    tk = "KXTEMPLAXH-26JUL2212-T70.99"
    cs = [c for c in cache[tk]["candles"] if c["end_period_ts"] % 120 == 0]
    seq = []
    for c in cs:
        x, s = fv(c, "MIXED")
        seq.append((c["end_period_ts"], x, s, c))
    worst_i, worst = None, 0
    for i in range(1, len(seq)):
        if seq[i][1] is None or seq[i - 1][1] is None:
            continue
        if abs(seq[i][1] - seq[i - 1][1]) > worst:
            worst, worst_i = abs(seq[i][1] - seq[i - 1][1]), i
    print(f"  {tk} worst MIXED 2-min delta = {worst:.3f} at index {worst_i}")
    for i in range(max(0, worst_i - 2), min(len(seq), worst_i + 3)):
        t, x, s, c = seq[i]
        b, a, lt = parts(c)
        print(f"    ts={t} fair={x} src={s:<6} bid={b} ask={a} last={lt} "
              f"vol={c.get('volume_fp')} oi={c.get('open_interest_fp')}")

    print()
    print("=" * 100)
    print("C. HOW OFTEN IS A TEMP BOOK EVEN TWO-SIDED AND TIGHT? (per-minute, all candles)")
    print("=" * 100)
    for f in ["GAS", "TEMP"]:
        n = tight = onesided = wide = 0
        for tk, v in cache.items():
            if fam(tk) != f:
                continue
            for c in v["candles"]:
                b, a, _ = parts(c)
                if b is None:
                    continue
                n += 1
                if not (b > 0 and a < 1):
                    onesided += 1
                elif (a - b) <= 0.20:
                    tight += 1
                else:
                    wide += 1
        if n:
            print(f"  {f}: minutes={n}  two-sided&tight(<=20c)={tight/n:.3f}  "
                  f"two-sided&wide={wide/n:.3f}  ONE-SIDED/empty={onesided/n:.3f}")

    print()
    print("=" * 100)
    print("D. RE-RUN OF THE HEADLINE NUMBER ON BOOK-ONLY (STRICT) DATA")
    print("=" * 100)
    for f in ["GAS", "TEMP"]:
        d = []
        for tk, v in cache.items():
            if fam(tk) != f:
                continue
            pts = []
            for c in v["candles"]:
                if c["end_period_ts"] % 120:
                    continue
                x, s = fv(c, "STRICT")
                pts.append((c["end_period_ts"], x))
            for i in range(1, len(pts)):
                if pts[i][0] - pts[i - 1][0] == 120 and pts[i][1] is not None \
                        and pts[i - 1][1] is not None:
                    d.append(pts[i][1] - pts[i - 1][1])
        if len(d) < 5:
            print(f"  {f}: too few ({len(d)})")
            continue
        ad = sorted(abs(x) for x in d)
        n = len(d)
        m = st.mean(d)
        num = sum((d[i] - m) * (d[i - 1] - m) for i in range(1, n))
        den = sum((v - m) ** 2 for v in d)
        print(f"  {f}: n={n} sd={st.pstdev(d):.4f} p50|d|={ad[n//2]:.4f} "
              f"p90|d|={ad[int(.9*n)]:.4f} p99|d|={ad[min(n-1,int(.99*n))]:.4f} "
              f"max|d|={ad[-1]:.3f} P(|d|>=5c)={sum(1 for x in ad if x>=.05)/n:.3f} "
              f"ac1={num/den if den else float('nan'):+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
