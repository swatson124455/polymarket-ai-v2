"""DOCTRINE PARAMETER DERIVATION — every constant in the inventory doctrine, measured.

NEW FILE. Read-only; consumes only local caches:
  kalshi_live/kalshi_transactions_2026-07-23.csv     (receipt-grade, operator export)
  <scratchpad>/kdata/tape/*.json                      (public Kalshi trade tape, 54 contracts)
  <scratchpad>/kdata/plans-2026072*.jsonl             (our own cycle telemetry)

DERIVES
  T1  tau_unwind      median minutes from OUR fill to the first opposite-direction taker trade
                      (= a 1-tick-through reducing quote has price priority, so the first
                      opposite taker fills it). Per family. This is the ONLY time constant the
                      settlement endgame needs.
  T2  rho             $ of LIP credit per $-hour of size resting AT the reference price,
                      from receipts / our own at_ref_usd telemetry.
  T3  a               adverse selection $ per contract filled, per family, from the receipts.
  T4  h               fill hazard: contracts filled per $-hour of resting, per family.

NOT COVERED
  * 3 days, 244 lots, 54 contracts, 2 families. No cross-validation window.
  * tau_unwind assumes price priority and ignores our own size vs the taker's size; a taker
    smaller than our residual partially fills. It is therefore a LOWER bound on full-exit time
    and an accurate estimate of time-to-FIRST-fill.
  * rho's numerator (credits) lags its denominator (quoting) by one Time Period, and the
    export ends 07-22 while gas-weekly credits had not yet posted. rho is therefore a LOWER
    bound, biased against gas.
"""
import csv
import glob
import json
import os
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KDATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "kdata")
CSVP = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")


def fam(t):
    return "TEMP" if t.startswith("KXTEMP") else ("GAS" if t.startswith("KXAAAGAS") else "OTHER")


def parse(ts):
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def load_trades():
    rows = []
    with open(CSVP, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] != "trade":
                continue
            r["_open"] = parse(r["open_timestamp"])
            r["_close"] = parse(r["close_timestamp"])
            r["_qty"] = float(r["quantity_fp"])
            r["_ep"] = float(r["entry_price_dollars"])
            r["_pnl"] = float(r["realized_pnl_with_fees_dollars"])
            r["_fam"] = fam(r["market_ticker"])
            rows.append(r)
    return rows


def load_credits():
    out = []
    with open(CSVP, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] == "credit":
                out.append((parse(r["close_timestamp"]),
                            float(r["realized_pnl_with_fees_dollars"])))
    return out


def load_tape():
    tp = {}
    for f in glob.glob(os.path.join(KDATA, "tape", "*.json")):
        d = json.load(open(f))
        tr = []
        for x in d.get("trades", []):
            tr.append((parse(x["created_time"].replace("Z", "+00:00")),
                       x.get("taker_side"), float(x["count_fp"]),
                       float(x["yes_price_dollars"])))
        tr.sort()
        tp[d["ticker"]] = tr
    return tp


def t1_tau_unwind(trades, tape):
    print("\n=== T1  tau_unwind : minutes from OUR fill to first opposite-direction taker ===")
    per = defaultdict(list)
    miss = defaultdict(int)
    for r in trades:
        tr = tape.get(r["market_ticker"])
        if not tr:
            miss[r["_fam"]] += 1
            continue
        # long yes -> we post a yes ask -> a taker BUYING yes fills us, and vice versa
        want = "yes" if r["side"] == "yes" else "no"
        t0 = r["_open"]
        nxt = next((ts for ts, side, _c, _p in tr if ts > t0 and side == want), None)
        if nxt is None:
            miss[r["_fam"]] += 1
            continue
        per[r["_fam"]].append((nxt - t0).total_seconds() / 60.0)
    for f in sorted(per):
        v = sorted(per[f])
        print(f"  {f:5s} n={len(v):4d} (no opposite taker ever: {miss[f]})  "
              f"p25={v[len(v)//4]:7.2f}  MEDIAN={st.median(v):7.2f}  "
              f"p75={v[3*len(v)//4]:7.2f}  p90={v[int(.9*len(v))]:8.2f} min")
    return {f: st.median(v) for f, v in per.items()}


def t2_rho(credits, trades):
    """rho and the fill hazard h, in matched units of EXPOSURE ($-hours of resting size).

    ⚠ rho is NOT receipt-measurable from local data: at_ref_usd telemetry begins
    2026-07-22T18:52Z, and the credits in the CSV posted 07-21/07-22 pay for quoting that
    happened BEFORE that (R1: credits post at period end, §M13). The two windows do not
    overlap. What IS computable is (a) exposure by day, (b) the fill hazard h in the same
    units, and (c) rho back-solved from the §M4-vs-receipt anchor. All three are printed and
    labelled."""
    print("\n=== T2  exposure, fill hazard h, and what rho would have to be ===")
    per_day = defaultdict(lambda: [0.0, 0.0, 0])
    for f in sorted(glob.glob(os.path.join(KDATA, "plans-2026072*.jsonl"))):
        for line in open(f):
            if not line.strip():
                continue
            d = json.loads(line)
            if "committed_usd" not in d:
                continue
            day = d["ts"][:10]
            per_day[day][0] += float(d["committed_usd"])
            per_day[day][1] += float(d.get("at_ref_usd", 0.0))
            per_day[day][2] += 1
    cyc_h = 2.0 / 60.0
    print(f"  {'day':12s} {'cycles':>7} {'committed $-h':>14} {'at-ref $-h':>11} "
          f"{'ct filled':>10} {'h (ct/$-h)':>11}")
    for day in sorted(per_day):
        c, a, n = per_day[day]
        ct = sum(r["_qty"] for r in trades if r["_open"].strftime("%Y-%m-%d") == day)
        dh = c * cyc_h
        print(f"  {day:12s} {n:>7} {dh:>14.1f} {a*cyc_h:>11.1f} {ct:>10.1f} "
              f"{(ct/dh if dh else float('nan')):>11.3f}")
    # 07-22 is the only day with near-complete cycle coverage (595 of a possible 720)
    c22, a22, n22 = per_day["2026-07-22"]
    dh22 = c22 * cyc_h
    ct22 = sum(r["_qty"] for r in trades if r["_open"].strftime("%Y-%m-%d") == "2026-07-22")
    pnl22 = sum(r["_pnl"] for r in trades if r["_open"].strftime("%Y-%m-%d") == "2026-07-22")
    print(f"\n  BEST-COVERED DAY 07-22: {n22} cycles ({n22/720:.0%} of a 2-min day), "
          f"{dh22:.1f} committed $-hours")
    print(f"    h = {ct22/dh22:.3f} contracts filled per committed $-hour")
    print(f"    realised cost = ${-pnl22/dh22:.4f} per committed $-hour  "
          f"(a x h, measured directly)")
    print(f"    => rho must exceed ${-pnl22/dh22:.4f}/$-hour for the book to break even")
    print(f"    => on ${c22/n22:.2f} mean committed, that is "
          f"${-pnl22/dh22*c22/n22*24:.2f}/day of credit needed")
    return -pnl22 / dh22


def t3_t4(trades):
    print("\n=== T3/T4  adverse selection and fill hazard, by family ===")
    for f in ("GAS", "TEMP"):
        sel = [r for r in trades if r["_fam"] == f]
        if not sel:
            continue
        qty = sum(r["_qty"] for r in sel)
        notional = sum(r["_qty"] * r["_ep"] for r in sel)
        pnl = sum(r["_pnl"] for r in sel)
        print(f"  {f:5s} lots={len(sel):4d} contracts={qty:8.1f} notional=${notional:8.2f} "
              f"P&L=${pnl:8.2f}")
        print(f"        a = ${-pnl/qty:.4f} loss per contract filled "
              f"({-pnl/qty*100:.2f}c)   |   {-pnl/notional*100:6.2f}% of notional")


def main():
    trades, credits, tape = load_trades(), load_credits(), load_tape()
    print(f"loaded {len(trades)} lots, {len(credits)} credits (${sum(c for _, c in credits):.2f}), "
          f"{len(tape)} taped contracts")
    t1_tau_unwind(trades, tape)
    t2_rho(credits, trades)
    t3_t4(trades)


if __name__ == "__main__":
    main()
