"""ADVERSARIAL REFUTATION OF INVENTORY DOCTRINE v1 -- selector robustness + the R2 reward cliff.

NEW FILE. READ-ONLY. Local receipts only (kalshi_transactions_2026-07-23.csv).

Tests four load-bearing claims of the proposal:

  C1  "SELECTOR sets the sign."  The temp ADMIT cells (price 0.20-0.40 a=0.74; 0.80-1.00
      a=3.82, both < a*=5.8c) are the doctrine's only positive claim about temp.  The proposal
      reports leave-one-out AFTER DROPPING THE WORST ticker -- the direction that supports a
      REFUSE, not an ADMIT.  This runs the LOO that actually tests an ADMIT: drop the BEST
      contributor.

  C2  "Inventory control cannot change the sign; every within-market lever multiplies reward
      and fills by the same factor."  R2 makes the whole-Time-Period payout a THRESHOLD at
      $1.00, so reward is max(0, .) in size, not linear.  This applies a uniform size haircut
      to the 10 receipt-grade credit rows and measures the elasticity.

  C3  the MIN_QUOTE_CT floor rests size that still bears fill risk.  Under R2, does a floored
      quote earn anything at all?

  C4  what the SELECTOR would actually have saved on the 07-22 worthless-expiry cluster, and
      how much of that is simply "stop quoting KXTEMP*" (already proposed in canon M8, needs
      no doctrine).

NOT COVERED
  * 244 lots / 3 days / 2 families / 10 credit rows.  Everything here is in-sample against the
    same window the doctrine's parameters were fitted on -- that is the point of C1.
  * credit rows carry an EMPTY market_ticker, so row -> family attribution is NOT CSV-verified
    (canon M8 attributes the FAMILY TOTALS from operator screenshots: TEMP $23.06 / GAS $2.15
    of $25.21).  C2's row-level haircut is an ILLUSTRATION of the threshold mechanism, not a
    per-family forecast.
  * the size->share->payout map is treated as linear (our ~20ct against a 1000ct Target Size
    moves the denominator by ~2%; checked: 20/1020 vs 15/1015 = 0.753 vs a naive 0.750).
  * says nothing about whether the doctrine's reward parameter rho is right; rho is a
    self-declared LOWER bound from a partial screenshot scroll.
"""
import csv
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CSVP = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")
BANDS = ((0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01))
ADMIT_TEMP = {(.2, .4), (.8, 1.01)}
A_STAR = 5.8            # c/ct, = rho/h from the proposal


def load():
    tr, cr = [], []
    with open(CSVP, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["type"] == "trade":
                r["_q"] = float(r["quantity_fp"])
                r["_p"] = float(r["realized_pnl_with_fees_dollars"])
                r["_e"] = float(r["entry_price_dollars"])
                r["_x"] = float(r["exit_price_dollars"])
                t = r["market_ticker"]
                r["_f"] = "TEMP" if t.startswith("KXTEMP") else (
                    "GAS" if t.startswith("KXAAAGAS") else "OTHER")
                tr.append(r)
            else:
                cr.append(float(r["realized_pnl_with_fees_dollars"]))
    return tr, cr


def band(p):
    for lo, hi in BANDS:
        if lo <= p < hi:
            return (lo, hi)
    return None


def c1(tr):
    print("=" * 96)
    print("C1  SELECTOR ROBUSTNESS -- the LOO that tests an ADMIT (drop the BEST contributor)")
    print(f"    a* = {A_STAR} c/ct.  ADMIT iff a < a*.")
    for lo, hi in BANDS:
        b = [r for r in tr if r["_f"] == "TEMP" and lo <= r["_e"] < hi]
        if not b:
            continue
        ct = sum(r["_q"] for r in b)
        pn = sum(r["_p"] for r in b)
        a = -pn / ct * 100
        byt = defaultdict(lambda: [0.0, 0.0])
        for r in b:
            byt[r["market_ticker"]][0] += r["_q"]
            byt[r["market_ticker"]][1] += r["_p"]
        best = max(byt.items(), key=lambda kv: kv[1][1])
        bc, bp = best[1]
        a_drop = -(pn - bp) / (ct - bc) * 100 if ct - bc > 0 else float("nan")
        verdict = "ADMIT " if a < A_STAR else "refuse"
        flip = ""
        if verdict == "ADMIT ":
            flip = "  <-- FLIPS TO REFUSE" if a_drop >= A_STAR else "  (holds)"
        print(f"  TEMP [{lo:.2f},{hi:.2f})  lots {len(b):>3}  ct {ct:>6.1f}  "
              f"a {a:>7.2f}  {verdict} | tickers {len(byt)}  "
              f"top-ct {max(v[0] for v in byt.values()) / ct:>4.0%}  "
              f"drop-best({best[0]} {bp:+.2f} on {bc:.0f}ct) -> a {a_drop:>7.2f}{flip}")


def c2(cr):
    print("=" * 96)
    print("C2  R2 THRESHOLD -- reward is NOT linear in size, so a size lever CAN change the sign")
    print(f"    receipt-grade credit rows (n={len(cr)}, CSV-verified total ${sum(cr):.2f}):")
    print("    " + "  ".join(f"${x:.2f}" for x in sorted(cr)))
    print(f"    {'haircut':>8} {'paid rows':>10} {'zeroed':>7} {'credit $':>9} "
          f"{'vs full':>8} {'elasticity':>11}")
    full = sum(cr)
    for lam in (1.0, 0.92, 0.79, 0.70, 0.50, 0.10):
        paid = [x * lam for x in cr if x * lam >= 1.00]
        tot = sum(paid)
        el = ((full - tot) / full) / (1 - lam) if lam < 1 else 1.0
        print(f"    {lam:>8.2f} {len(paid):>10} {len(cr) - len(paid):>7} {tot:>9.2f} "
              f"{tot / full:>7.1%} {el:>11.2f}")
    print("    lam=0.79 = the proposal's own g_time integrated over a 60-min temp life")
    print("               (0.92@60min, 0.90@29, 0.75@18, 0.50@3, 0@0 -> time-weighted 0.79)")
    print("    lam=0.50 = the reward cost of being ONE-SIDED (R4: score = norm_yes + norm_no)")
    print("    elasticity > 1 == the size lever destroys reward FASTER than it destroys risk.")


def c3(cr):
    print("=" * 96)
    print("C3  MIN_QUOTE_CT FLOOR (=2 vs JOIN_SIZE=20 -> 10% of normal resting share)")
    lam = 2 / 20
    print(f"    largest receipt credit ${max(cr):.2f} x {lam:.2f} = ${max(cr) * lam:.2f} < $1.00")
    print("    => a contract held at the floor for a WHOLE Time Period pays EXACTLY $0 under R2,")
    print("       while still resting fillable size.  Strictly dominated by resting nothing.")
    print("       (13h gas program floored for part of the period: loss is pro-rata.  ~1h temp")
    print("        program at the floor for the whole period: total.)")


def c4(tr):
    print("=" * 96)
    print("C4  REPLAY vs the real losers")
    z = [r for r in tr if r["_x"] == 0.0 and r["close_timestamp"][:10] == "2026-07-22"]
    kept = [r for r in z if r["_f"] != "TEMP" or band(r["_e"]) in ADMIT_TEMP]
    print(f"    07-22 worthless-expiry cluster: {len(z)} lots, ${sum(r['_p'] for r in z):.2f}")
    print(f"    SELECTOR still admits {len(kept)} lots, ${sum(r['_p'] for r in kept):.2f} "
          f"-> avoided ${sum(r['_p'] for r in z) - sum(r['_p'] for r in kept):.2f}")
    tmp = [r for r in tr if r["_f"] == "TEMP"]
    ad = [r for r in tmp if band(r["_e"]) in ADMIT_TEMP]
    tct, act = sum(r["_q"] for r in tmp), sum(r["_q"] for r in ad)
    print(f"    but it does so by refusing {tct - act:.0f} of {tct:.0f} temp contracts "
          f"({1 - act / tct:.0%}).")
    print(f"    residual ADMITTED temp is still negative: ${sum(r['_p'] for r in ad):.2f} on "
          f"{act:.0f} ct, n={len(ad)} lots, IN-SAMPLE.")
    print("    'drop KXTEMP* from KALSHI_SERIES_ALLOW' (canon M8, already proposed) avoids "
          f"${sum(r['_p'] for r in z):.2f} of the same cluster with no control law at all.")
    print("-" * 96)
    print("C4b ONE-SIDEDNESS IS FORCED, not chosen: no_price ~ 1 - yes_price, so an ADMIT set of")
    print("    [0.20,0.40) u [0.80,1.00) can NEVER admit both sides of the same contract, and")
    print("    admits NEITHER when yes in [0.40,0.60).  Every admitted temp contract is quoted")
    print("    one-sided => lam=0.50 in the C2 table, compounding with g_time's 0.79.")
    print("    combined lam = 0.50 x 0.79 = 0.40 -> see the C2 row nearest it.")


def main():
    tr, cr = load()
    print(f"receipts: {len(tr)} trades, {len(cr)} credits, "
          f"window {min(r['close_timestamp'][:10] for r in tr)}"
          f"..{max(r['close_timestamp'][:10] for r in tr)}")
    c1(tr)
    c2(cr)
    c3(cr)
    c4(tr)


if __name__ == "__main__":
    main()
