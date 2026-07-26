#!/usr/bin/env python3
"""
kalshi_doctrine_final_verify.py  -- NEW FILE, read-only, no live-system contact.

Independent re-verification of every load-bearing number that goes into
docs/maker_handoffs/KALSHI_INVENTORY_DOCTRINE_2026-07-23.md

Source: kalshi_live/kalshi_transactions_2026-07-23.csv  (receipt-grade, 244 trades + 10 credits)
Nothing here writes to the repo, contacts the live bot, or edits an existing module.

Run:  python kalshi_live/kalshi_doctrine_final_verify.py
"""
import csv
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "kalshi_transactions_2026-07-23.csv")


def load():
    with open(CSV, newline="") as fh:
        return list(csv.DictReader(fh))


def fam(t):
    if t.startswith("KXAAAGAS"):
        return "GAS"
    if t.startswith("KXTEMP"):
        return "TEMP"
    return "OTHER"


def event(t):
    return "-".join(t.split("-")[:2])


def main():
    rows = load()
    trades = [r for r in rows if r["type"] == "trade"]
    credits = [r for r in rows if r["type"] == "credit"]

    print("=" * 78)
    print("V0. FILE INTEGRITY")
    print("=" * 78)
    cvals = sorted(float(r["realized_pnl_with_fees_dollars"]) for r in credits)
    tot_c = sum(cvals)
    pnl_nofee = sum(float(r["realized_pnl_without_fees_dollars"]) for r in trades)
    pnl_fee = sum(float(r["realized_pnl_with_fees_dollars"]) for r in trades)
    print(f"trades={len(trades)}  credits={len(credits)}")
    print(f"credit values: {[round(v,2) for v in cvals]}")
    print(f"credit total  = ${tot_c:.2f}   (canon SM8: $25.21)")
    print(f"trading P&L before fees = ${pnl_nofee:.4f}  (canon: -77.4108)")
    print(f"trading P&L after  fees = ${pnl_fee:.4f}  (canon: -79.9931)")

    # ---------------------------------------------------------------- V1
    print()
    print("=" * 78)
    print("V1. THE R2 CLIFF -- reward elasticity to a uniform size haircut")
    print("    R2: whole-Time-Period payout < $1.00 pays ZERO.")
    print("    Assumes payout scales linearly in our resting size BEFORE the floor")
    print("    (that is the doctrine's own 'cost/control=1.000' claim; if it is")
    print("     superlinear the cliff is WORSE, so this is the optimistic case).")
    print("=" * 78)
    print(f"{'lambda':>7} {'paid $':>9} {'% of full':>10} {'zeroed':>8} {'elasticity':>11}")
    base = tot_c
    for lam in (1.0, 0.95, 0.92, 0.90, 0.79, 0.75, 0.60, 0.53, 0.50, 0.35, 0.10):
        paid = sum(v * lam for v in cvals if v * lam >= 1.00)
        zero = sum(1 for v in cvals if v * lam < 1.00)
        if lam < 1.0 and paid > 0:
            import math
            el = math.log(paid / base) / math.log(lam)
        else:
            el = float("nan")
        print(f"{lam:>7.2f} {paid:>9.2f} {100*paid/base:>9.1f}% {zero:>5}/10 {el:>11.2f}")
    gcrit = sorted(1.00 / v for v in cvals)
    print()
    print("g_crit = size fraction at which each observed credit hits the $1.00 floor:")
    print("   " + "  ".join(f"{g:.2f}" for g in gcrit))
    print(f"   MEDIAN g_crit = {gcrit[4]:.2f}/{gcrit[5]:.2f}  -> half our credits die by ~0.44-0.45x size")

    # ---------------------------------------------------------------- V2
    print()
    print("=" * 78)
    print("V2. THE F2 MASSACRE -- 07-22 settlement deaths")
    print("=" * 78)
    dead = [r for r in trades
            if float(r["exit_price_dollars"]) == 0.0
            and r["close_timestamp"].startswith("2026-07-22")]
    tot = sum(float(r["realized_pnl_with_fees_dollars"]) for r in dead)
    ct = sum(float(r["quantity_fp"]) for r in dead)
    print(f"n rows = {len(dead)}   contracts = {ct:.2f}   P&L = ${tot:.2f}  (canon: 20 rows, -$40.62)")
    byfam = defaultdict(float)
    for r in dead:
        byfam[fam(r["market_ticker"])] += float(r["realized_pnl_with_fees_dollars"])
    for k, v in sorted(byfam.items()):
        print(f"   {k:6s} ${v:8.2f}")
    print()
    print("  DOMINANCE TEST: drop KXTEMP* from KALSHI_SERIES_ALLOW (one env line, Tier-2)")
    print(f"     -> avoids ${-byfam.get('TEMP',0):.2f} of ${-tot:.2f} = "
          f"{100*byfam.get('TEMP',0)/tot:.2f}% of the massacre, with zero new code.")

    # ---------------------------------------------------------------- V3
    print()
    print("=" * 78)
    print("V3. WHERE THE MONEY WENT -- settled-vs-traded-out, by family")
    print("    (is the EXIT the problem, or is ENTRY the problem?)")
    print("=" * 78)
    # A Kalshi settlement exits at EXACTLY 0.00 or 1.00. Anything strictly between
    # is a trade-out. Classifying "settled" as exit==0.0 only would keep the losing
    # settles and drop the winning ones -- a selection bias that inflates a_settle.
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0])  # n, ct, basis, pnl
    for r in trades:
        f = fam(r["market_ticker"])
        xp = float(r["exit_price_dollars"])
        settled = (xp == 0.0 or xp == 1.0)
        k = (f, "SETTLED" if settled else "TRADED OUT")
        q = float(r["quantity_fp"])
        agg[k][0] += 1
        agg[k][1] += q
        agg[k][2] += q * float(r["entry_price_dollars"])
        agg[k][3] += float(r["realized_pnl_with_fees_dollars"])
    print(f"{'cell':22s} {'lots':>5} {'ct':>8} {'basis $':>9} {'P&L $':>9} {'a c/ct':>8}")
    for k in sorted(agg):
        n, q, b, p = agg[k]
        print(f"{k[0]+' '+k[1]:22s} {n:>5} {q:>8.1f} {b:>9.2f} {p:>9.2f} {-100*p/q:>8.2f}")
    print()
    print("  READ: if a_settled >> a_tradedout, the exit rule is the lever.")
    print("        if they are comparable, the loss is priced in AT THE FILL and")
    print("        no exit doctrine can recover it -- selection is the only lever.")

    # ---------------------------------------------------------------- V4
    print()
    print("=" * 78)
    print("V4. REWARD PER DOLLAR OF CAPITAL vs TOKEN PRICE")
    print("    LIP scores CONTRACTS (DF^N x size). Capital buys contracts at price p.")
    print("    So reward-contracts per dollar committed ~ 1/p.")
    print("=" * 78)
    JOIN, PER_SIDE = 20, 7.50   # _capped_join: min(JOIN, int((MAX_MARKET_CAPITAL/2)/p))
    print(f"{'price':>6} {'join ct':>8} {'$ used':>8} {'reward-ct per $':>16}")
    for p in (0.05, 0.10, 0.14, 0.20, 0.30, 0.50, 0.70, 0.86, 0.95):
        j = min(JOIN, int(PER_SIDE / p))
        used = j * p
        print(f"{p:>6.2f} {j:>8} {used:>8.2f} {j/used if used else 0:>16.2f}")
    print()
    print("  0.14 -> 7.14 reward-ct/$ ;  0.86 -> 1.16 reward-ct/$  =  6.2x")
    print("  ANY rule that moves us from the cheap token to its complement pays 6.2x more")
    print("  capital for the same LIP credit. Price-band selectors do exactly that.")

    # ---------------------------------------------------------------- V5
    print()
    print("=" * 78)
    print("V5. EVENT-LEVEL CONCENTRATION (canon SS-T: event = ONE correlated risk)")
    print("=" * 78)
    for f in ("GAS", "TEMP"):
        ev = defaultdict(float)
        for r in trades:
            if fam(r["market_ticker"]) == f:
                ev[event(r["market_ticker"])] += float(r["realized_pnl_with_fees_dollars"])
        tot_f = sum(ev.values())
        srt = sorted(ev.items(), key=lambda kv: kv[1])
        print(f"\n{f}: {len(ev)} events, total ${tot_f:.2f}")
        for k, v in srt[:4]:
            share = 100 * v / tot_f if tot_f else 0
            print(f"    {k:32s} ${v:8.2f}   {share:5.1f}% of family loss")
        if srt:
            print(f"    -> leave-one-out (drop worst event): ${tot_f - srt[0][1]:.2f}")

    # ---------------------------------------------------------------- V6
    print()
    print("=" * 78)
    print("V6. rho -- THE ADMISSION THRESHOLD'S NUMERATOR IS NOT PINNED")
    print("=" * 78)
    COMMITTED_0722 = 689.8   # $-hours, plans-20260722.jsonl, 595 cycles (prior session)
    ATREF_0722 = 51.2        # $-hours at reference, same file
    print(f"  credits in the ONLY window where credits+trades are both complete (07-21..22)")
    print(f"    = ${tot_c:.2f} over ~704 committed $-hours   -> rho = ${tot_c/704.1:.4f}/$-h")
    print(f"  screenshot route (~$42 on 07-23, PARTIAL SCROLL = LOWER BOUND)")
    print(f"    / {COMMITTED_0722} committed $-h            -> rho = ${42/COMMITTED_0722:.4f}/$-h")
    print(f"    / {ATREF_0722} AT-REFERENCE $-h             -> rho = ${42/ATREF_0722:.4f}/$-h")
    h = 1.049  # ct filled per committed $-hour, 07-22
    print(f"\n  a* = rho / h  with h = {h} ct per committed $-hour:")
    for label, rho in (("matched window", tot_c/704.1),
                       ("screenshot/committed", 42/COMMITTED_0722),
                       ("screenshot/at-ref", 42/ATREF_0722)):
        print(f"    {label:24s} rho=${rho:.4f}  ->  a* = {100*rho/h:7.2f} c/ct")
    print("\n  SPREAD = 23x. Any ADMIT/REFUSE call on a cell with a in [3.4c, 78c]")
    print("  is undetermined by existing data. That is every cell in the prior table.")


if __name__ == "__main__":
    main()
