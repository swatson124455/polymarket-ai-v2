#!/usr/bin/env python3
"""SIGNAL INVENTORY part 2 — is our OWN FILL STREAM a usable run-over detector?

READ-ONLY analysis of an already-fetched /portfolio/fills dump. New file; edits nothing.

WHY: the bot throttles on inventory LEVEL (INV_SOFT_CT=15 / INV_HARD_CT=60). It never looks
at fill RATE or fill ONE-SIDEDNESS. Fills are free, already in hand, and arrive with zero
extra latency. This measures whether they DISCRIMINATE.

FILL SIDE SEMANTICS (decoded from the live dump, 333 fills, 2026-07-20..23):
  Kalshi normalises every fill to YES terms, so `action`/`side` are degenerate
  (only ('buy','yes') and ('sell','no') ever occur). The informative field is `book_side`:
      book_side == 'bid'  -> our resting YES bid was hit   -> we got LONGER yes  (+count)
      book_side == 'ask'  -> our resting YES ask was hit   -> we got SHORTER yes (-count)
  That is the signed order-flow we take, per contract.

LABELS come from kalshi_live/kalshi_transactions_2026-07-23.csv (receipt-grade, 07-20..22):
  LOSER   = contract with a lot that exited at 0.00 (expired worthless)
  WINNER  = contract with realised P&L > 0
  FLAT    = neither

Usage:
  python kalshi_live/signal_fill_clustering.py <fills.json>
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


def load_labels():
    """Per-contract realised P&L and 'expired worthless' flag from the receipt CSV."""
    pnl, worthless, notional = defaultdict(float), set(), defaultdict(float)
    with open(CSV_PATH, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] != "trade":
                continue
            tk = r["market_ticker"]
            pnl[tk] += float(r["realized_pnl_with_fees_dollars"])
            notional[tk] += float(r["quantity_fp"]) * float(r["entry_price_dollars"])
            if float(r["exit_price_dollars"]) == 0.0:
                worthless.add(tk)
    return pnl, worthless, notional


def episodes(fills):
    """Group fills by contract; return per-contract signed fill sequences."""
    by = defaultdict(list)
    for f in fills:
        by[f["ticker"]].append((
            ts(f["created_time"]),
            +1 if f["book_side"] == "bid" else -1,
            float(f["count_fp"]),
            float(f["yes_price_dollars"]),
            bool(f["is_taker"]),
        ))
    for k in by:
        by[k].sort()
    return by


def runs(sign_seq):
    """Lengths of maximal same-sign runs."""
    out, cur, prev = [], 0, None
    for s in sign_seq:
        if s == prev:
            cur += 1
        else:
            if prev is not None:
                out.append(cur)
            cur, prev = 1, s
    if prev is not None:
        out.append(cur)
    return out


def describe(seq):
    """Metrics computable from the fill stream ALONE (no book, no external data)."""
    n = len(seq)
    signs = [s for _, s, _, _, _ in seq]
    qty = [q for _, _, q, _, _ in seq]
    net = sum(s * q for _, s, q, _, _ in seq)
    gross = sum(qty)
    # one-sidedness of CONTRACT VOLUME, 0 = perfectly balanced, 1 = all one way
    onesided = abs(net) / gross if gross else 0.0
    # inter-arrival between CONSECUTIVE SAME-SIDE fills
    same_gaps = []
    for i in range(1, n):
        if signs[i] == signs[i - 1]:
            same_gaps.append(seq[i][0] - seq[i - 1][0])
    all_gaps = [seq[i][0] - seq[i - 1][0] for i in range(1, n)]
    rl = runs(signs)
    # peak signed contracts acquired inside any 4-minute (2 cycle) sliding window
    peak4 = 0.0
    for i in range(n):
        acc = 0.0
        for k in range(i, n):
            if seq[k][0] - seq[i][0] > 240:
                break
            acc += seq[k][1] * seq[k][2]
            peak4 = max(peak4, abs(acc))
    span = seq[-1][0] - seq[0][0] if n > 1 else 0.0
    return dict(
        n=n, gross=round(gross, 2), net=round(net, 2), onesided=round(onesided, 3),
        max_run=max(rl) if rl else 0, mean_run=round(st.mean(rl), 2) if rl else 0,
        med_same_gap=round(st.median(same_gaps), 1) if same_gaps else None,
        n_same_gap=len(same_gaps),
        med_gap=round(st.median(all_gaps), 1) if all_gaps else None,
        peak4=round(peak4, 1), span_min=round(span / 60, 1),
        takers=sum(1 for _, _, _, _, t in seq if t),
        px_first=seq[0][3], px_last=seq[-1][3],
    )


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "fills.json"
    fills = json.load(open(path))
    pnl, worthless, notional = load_labels()
    eps = episodes(fills)

    rows = []
    for tk, seq in eps.items():
        d = describe(seq)
        d["ticker"] = tk
        d["series"] = tk.split("-")[0]
        d["fam"] = "TEMP" if tk.startswith("KXTEMP") else "GAS"
        d["pnl"] = round(pnl[tk], 2) if tk in pnl else None
        d["worthless"] = tk in worthless
        rows.append(d)
    rows.sort(key=lambda r: (r["pnl"] is None, r["pnl"] if r["pnl"] is not None else 0))

    print("=" * 118)
    print(f"FILL-STREAM EPISODES  n_fills={len(fills)}  n_contracts={len(rows)}  "
          f"labelled_by_CSV={sum(1 for r in rows if r['pnl'] is not None)}")
    print("=" * 118)
    hdr = (f"{'ticker':<30}{'fam':<5}{'pnl':>7}{'wl':>3}{'n':>4}{'gross':>8}{'net':>8}"
           f"{'1sided':>7}{'maxrun':>7}{'medgap':>8}{'sgap':>8}{'peak4':>7}{'span':>7}{'tk':>4}")
    print(hdr)
    for r in rows:
        print(f"{r['ticker']:<30}{r['fam']:<5}"
              f"{(r['pnl'] if r['pnl'] is not None else float('nan')):>7.2f}"
              f"{'W' if r['worthless'] else '.':>3}{r['n']:>4}{r['gross']:>8.1f}{r['net']:>8.1f}"
              f"{r['onesided']:>7.3f}{r['max_run']:>7}"
              f"{(r['med_gap'] if r['med_gap'] is not None else -1):>8.1f}"
              f"{(r['med_same_gap'] if r['med_same_gap'] is not None else -1):>8.1f}"
              f"{r['peak4']:>7.1f}{r['span_min']:>7.1f}{r['takers']:>4}")

    def agg(sel, name):
        s = [r for r in rows if sel(r)]
        if not s:
            print(f"  {name:<34} n=0")
            return
        multi = [r for r in s if r["n"] >= 2]
        print(f"  {name:<34} contracts={len(s):<4} fills={sum(r['n'] for r in s):<5} "
              f"med_1sided={st.median([r['onesided'] for r in s]):.3f}  "
              f"med_maxrun={st.median([r['max_run'] for r in s]):.1f}  "
              f"med_peak4={st.median([r['peak4'] for r in s]):.1f}  "
              f"med_samegap_s="
              f"{st.median([r['med_same_gap'] for r in multi if r['med_same_gap'] is not None]) if any(r['med_same_gap'] is not None for r in multi) else float('nan'):.1f}")

    print()
    print("=" * 118)
    print("AGGREGATES")
    print("=" * 118)
    agg(lambda r: True, "ALL")
    agg(lambda r: r["fam"] == "GAS", "GAS (all)")
    agg(lambda r: r["fam"] == "TEMP", "TEMP (all)")
    print()
    agg(lambda r: r["worthless"], "LOSERS (expired worthless)")
    agg(lambda r: r["pnl"] is not None and r["pnl"] > 0, "WINNERS (csv pnl > 0)")
    agg(lambda r: r["pnl"] is not None and not r["worthless"], "NON-LOSERS (labelled)")
    print()
    agg(lambda r: r["fam"] == "GAS" and r["pnl"] is not None and r["pnl"] > 0,
        "GAS WINNERS  <- neg. control")
    agg(lambda r: r["fam"] == "TEMP" and r["worthless"], "TEMP LOSERS  <- pos. control")

    # ---- detector sweep: fire on one-sidedness, report TPR and FPR ----------
    print()
    print("=" * 118)
    print("DETECTOR SWEEP — fire if (onesided >= X) AND (n_fills >= 2). "
          "POS = expired-worthless contract, NEG = labelled contract with pnl > 0")
    print("=" * 118)
    pos = [r for r in rows if r["worthless"]]
    neg = [r for r in rows if r["pnl"] is not None and r["pnl"] > 0]
    print(f"  positives(losers)={len(pos)}   negatives(winners)={len(neg)}")
    print(f"  {'thresh':>8}{'TPR':>8}{'FPR':>8}{'TP':>5}{'FN':>5}{'FP':>5}{'TN':>5}")
    for thr in [0.5, 0.7, 0.8, 0.9, 0.95, 1.0]:
        def fire(r):
            return r["n"] >= 2 and r["onesided"] >= thr
        tp = sum(1 for r in pos if fire(r))
        fp = sum(1 for r in neg if fire(r))
        print(f"  {thr:>8.2f}{(tp / len(pos) if pos else 0):>8.2f}"
              f"{(fp / len(neg) if neg else 0):>8.2f}"
              f"{tp:>5}{len(pos) - tp:>5}{fp:>5}{len(neg) - fp:>5}")

    print()
    print("  same sweep on PEAK 4-MINUTE SIGNED ACQUISITION (contracts):")
    print(f"  {'thresh':>8}{'TPR':>8}{'FPR':>8}{'TP':>5}{'FN':>5}{'FP':>5}{'TN':>5}")
    for thr in [10, 15, 20, 25, 30, 40]:
        tp = sum(1 for r in pos if r["peak4"] >= thr)
        fp = sum(1 for r in neg if r["peak4"] >= thr)
        print(f"  {thr:>8.0f}{(tp / len(pos) if pos else 0):>8.2f}"
              f"{(fp / len(neg) if neg else 0):>8.2f}"
              f"{tp:>5}{len(pos) - tp:>5}{fp:>5}{len(neg) - fp:>5}")

    # ---- venue-wide base rate of one-sidedness in a MAKER's fill stream -----
    print()
    print("=" * 118)
    print("BASE RATE — one-sidedness by family, all contracts with >=3 fills")
    print("=" * 118)
    for fam in ["GAS", "TEMP"]:
        s = [r for r in rows if r["fam"] == fam and r["n"] >= 3]
        if s:
            v = sorted(r["onesided"] for r in s)
            print(f"  {fam}: n={len(s)} onesided deciles "
                  f"p10={v[len(v)//10]:.2f} p50={v[len(v)//2]:.2f} "
                  f"p90={v[max(0,(9*len(v))//10)]:.2f}  "
                  f"frac_at_1.00={sum(1 for x in v if x >= 0.999)/len(v):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
