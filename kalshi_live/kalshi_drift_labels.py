#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — build the labelled test set from our real receipts.

NEW FILE. Edits nothing. Reads only kalshi_transactions_2026-07-23.csv (receipt-grade,
244 trade rows + 10 credit rows, 07-20..07-22).

UNIT OF ANALYSIS = a POSITION = (ticker, side).  The CSV is lot-level: the same
(ticker, side) can appear several times because each entry lot settles separately.
A detector fires on a CONTRACT at a MOMENT, not on a lot, so lots must be folded up.

LABELS  (reconciled to canon, see RECONCILIATION below)
  positive     = position with >=1 lot that expired worthless (exit 0.00) AND net P&L < 0
  neg_strict   = GAS position with net P&L > 0                      ("the thing that works")
  neg_broad    = any GAS position with no worthless lot            (wider FP denominator)
  temp_control = TEMP position with no worthless lot   (the WITHIN-FAMILY negative set --
                 the only negatives that are not confounded with family)

RECONCILIATION to the canonical loss set (must hold, asserted in main()):
  canon = 20 CSV lots, exit 0.00, P&L<0, close date 2026-07-22, sum -$40.62
  those 20 lots live in 18 distinct (ticker, side) positions
  those 18 positions total -$46.21, i.e. -$40.62 of worthless lots plus -$5.59 of
  PARTIAL-EXIT lots on the same contracts (we got some size off before expiry).
  The extra -$5.59 is not double counting -- it is the rest of the same run-over.

CONFOUND WARNING (measured, see module docstring of kalshi_drift_detect.py):
  19 of the 20 canonical 07-22 positives are KXTEMP*; every profitable-gas negative is
  KXAAAGAS*. Family alone therefore scores TPR .95 / FPR .00 with zero market data.
  Any pooled TEMP-vs-GAS result is uninformative. The load-bearing test is WITHIN TEMP.
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")


def family(tk):
    if tk.startswith("KXTEMP"):
        return "TEMP"
    if tk.startswith("KXAAAGAS"):
        return "GAS"
    return "OTHER"


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def load_positions(csv_path=CSV_PATH):
    rows = [r for r in csv.DictReader(open(csv_path)) if r["type"] == "trade"]
    agg = defaultdict(lambda: {"lots": [], "qty": 0.0, "pnl": 0.0, "cost": 0.0})
    for r in rows:
        key = (r["market_ticker"], r["side"])
        q = float(r["quantity_fp"])
        ep = float(r["entry_price_dollars"])
        xp = float(r["exit_price_dollars"])
        pnl = float(r["realized_pnl_with_fees_dollars"])
        a = agg[key]
        a["lots"].append({
            "qty": q, "entry": ep, "exit": xp, "pnl": pnl,
            "open_ts": ts(r["open_timestamp"]), "close_ts": ts(r["close_timestamp"]),
            "open_iso": r["open_timestamp"], "close_iso": r["close_timestamp"],
        })
        a["qty"] += q
        a["pnl"] += pnl
        a["cost"] += q * ep

    out = []
    for (tk, side), a in agg.items():
        lots = sorted(a["lots"], key=lambda l: l["open_ts"])
        all_zero = all(l["exit"] == 0.0 for l in lots)
        any_zero = any(l["exit"] == 0.0 for l in lots)
        fam = family(tk)
        if any_zero and a["pnl"] < 0:
            label = "positive"
        elif fam == "GAS" and a["pnl"] > 0:
            label = "neg_strict"
        elif fam == "GAS":
            label = "neg_broad"
        elif fam == "TEMP":
            label = "temp_control"
        else:
            label = "other"
        out.append({
            "ticker": tk, "side": side, "family": family(tk), "label": label,
            "qty": round(a["qty"], 2), "pnl": round(a["pnl"], 6),
            "cost": round(a["cost"], 4),
            "n_lots": len(lots), "any_zero_exit": any_zero,
            "first_entry_ts": lots[0]["open_ts"], "last_entry_ts": lots[-1]["open_ts"],
            "first_entry_iso": lots[0]["open_iso"], "last_entry_iso": lots[-1]["open_iso"],
            "close_ts": max(l["close_ts"] for l in lots),
            "close_iso": max(l["close_iso"] for l in lots),
            "vwap_entry": round(a["cost"] / a["qty"], 4) if a["qty"] else None,
            "lots": lots,
        })
    return sorted(out, key=lambda p: p["first_entry_ts"])


def main():
    pos = load_positions()
    if len(sys.argv) > 1 and sys.argv[1] == "--tickers":
        json.dump(sorted({p["ticker"] for p in pos}), open(sys.argv[2], "w"))
        print(f"{len({p['ticker'] for p in pos})} tickers -> {sys.argv[2]}")
        return
    json.dump(pos, open(os.path.join(HERE, "kalshi_drift_labels.json"), "w"))

    print(f"positions: {len(pos)}   contracts: {len({p['ticker'] for p in pos})}")
    hdr = f"{'family':<7}{'label':<10}{'n':>4}{'qty':>9}{'pnl':>10}"
    print(hdr)
    print("-" * len(hdr))
    tot = defaultdict(lambda: [0, 0.0, 0.0])
    for p in pos:
        k = (p["family"], p["label"])
        tot[k][0] += 1
        tot[k][1] += p["qty"]
        tot[k][2] += p["pnl"]
    for k in sorted(tot):
        n, q, pl = tot[k]
        print(f"{k[0]:<7}{k[1]:<10}{n:>4}{q:>9.1f}{pl:>10.2f}")

    print("\n-- RECONCILIATION vs canon (assert) --")
    rows = [r for r in csv.DictReader(open(CSV_PATH)) if r["type"] == "trade"]
    canon = [r for r in rows
             if float(r["exit_price_dollars"]) == 0.0
             and float(r["realized_pnl_with_fees_dollars"]) < 0
             and r["close_timestamp"][:10] == "2026-07-22"]
    canon_pnl = sum(float(r["realized_pnl_with_fees_dollars"]) for r in canon)
    print(f"canon worthless lots        : {len(canon)}   pnl {canon_pnl:.2f}")
    assert len(canon) == 20, len(canon)
    assert abs(canon_pnl + 40.62) < 0.01, canon_pnl

    p22 = [p for p in pos if p["label"] == "positive"
           and p["close_iso"][:10] == "2026-07-22"]
    print(f"positions holding those lots: {len(p22)}   lots {sum(p['n_lots'] for p in p22)}"
          f"   pnl {sum(p['pnl'] for p in p22):.2f}")
    assert len(p22) == 18, len(p22)
    canon_keys = {(r["market_ticker"], r["side"]) for r in canon}
    assert canon_keys == {(p["ticker"], p["side"]) for p in p22}
    print(f"  -> {canon_pnl:.2f} worthless + "
          f"{sum(p['pnl'] for p in p22) - canon_pnl:.2f} partial-exit on same contracts")
    print("RECONCILED.")


if __name__ == "__main__":
    main()
