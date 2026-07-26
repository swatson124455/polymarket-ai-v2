#!/usr/bin/env python3
"""PER-SURVIVOR VERDICT — joins the four measurement passes. READ-ONLY, no API calls.

Inputs (all produced by the read-only studies in this directory):
  horizon_census.json       ratio + headline pool          (kalshi_horizon_census.py)
  survivor_qualify.json     R3 two-sided, OUR admit rate, capture, capital, structure/fee
                                                           (kalshi_survivor_qualify.py)
  ladder_safety_probe.json  does the LIVE risk code net non-additive tickers
                                                           (kalshi_ladder_safety_probe.py)
  survivor_duty.json        forward 24h program coverage + 04-15Z trough coverage

THE THREE NUMBERS THAT DECIDE IT, in order:

1. STRUCTURE. If the live `_is_ladder_event` NETS an event that is mutually-exclusive, bucketed
   ('between'), or inverted-polarity ('below/before X'), then `ladder_pairing` will declare a
   FLOORED PAIR that has no floor and `event_deltas` will read FLAT on live exposure. Paired
   quantity is excluded from unwind targeting, throttle direction, the settle-taker and the STOP
   offsets — so the mis-classification removes every de-risking path at once. That is a hard
   REJECT regardless of economics.

2. EARNABILITY, ours not the venue's. `two_sided_pct` is R3 (does the snapshot pay ANYBODY).
   `admit_pct` is whether OUR selection gate would quote it at all. A series can be 100%
   two-sided and 0% admissible; that series earns us nothing.

3. MARGINAL VALUE. `basket_delta` is the measured change in the greedy $85 basket's modelled
   $/day when the series is offered alongside gas.
   ⚠ CORRECTION TO THE PREMISE I STARTED WITH. `kalshi_depth_capacity_study.py` found capital
   binding at K~7 — but that was measured with MIN_DEPTH_SYM RELAXED. At the LIVE gate the
   gas-only basket commits only $47.96 of $85 (K=7, and 1 of those 7 is a $0 KXAAAGASW slot).
   So capital does NOT bind today and a new series is ADDITIVE, not displacing. That makes the
   deltas below real additions — and it also means the correct rejection test is "does it add
   more than the model's own noise", not "does it beat gas per dollar".

4. NOBODY GETS AN ADMIT FROM THIS PASS. Toxicity is UNMEASURED for every series we have never
   traded, and canon §M8 is the standing precedent: KXTEMP* scored well on exactly this kind of
   reward-side model, earned 91% of all reward income, and was 100% of the realised loss. A
   reward-side score is a reason to PROBE, never a reason to trade.

DUTY ADJUSTMENT. `$cap/d` is per day OF THE PROGRAM WINDOW (canon R1 normalises period_reward by
window length). For a program that only exists part of the day that OVERSTATES calendar earnings:
KXAAAGASD's window is 12.75h, so its window-day rate is ~2x its calendar-day rate. The bias
flatters short-window programs — i.e. it flatters our own incumbent — so the calendar column is
the fair comparison and it moves the answer TOWARD the challengers, not away.

NOT COVERED, and it is the whole trading side:
  * fill rate, queue position, adverse selection — invisible in public data (no queue position).
  * settlement toxicity (the FIGHTMENTION shape: in-window positive, gutted at settlement) —
    needs settled positions in the series. UNMEASURED for everything we have never traded.
    Prior receipts exist only for the mention family and for our own allowlist (canon §M8).
  * the 04:00-15:00Z admissibility trough — the qualify run window does not cover it.
  * capture is a reward-side UPPER BOUND (canon §M7d: over-predicts ~2-6x). Ratios only.

Run:  python kalshi_survivor_verdict.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = ("KXAAAGASD", "KXAAAGASW")
PRIMARY = "KXAAAGASD"
TOXIC_FAMILY = ("MENTION",)          # canon: FIGHTMENTION +745 in-window / -1338 settled
NOISE_FLOOR = 1.00                   # $/day modelled added to the basket; see §M7d 2-6x


def load(n):
    p = os.path.join(HERE, n)
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    q = load("survivor_qualify.json")
    duty = load("survivor_duty.json") or {}
    probe = load("ladder_safety_probe.json")
    basket = load("survivor_basket.json")
    if not q:
        raise SystemExit("survivor_qualify.json missing — run kalshi_survivor_qualify.py first")

    danger = {}
    if probe:
        for r in probe["rows"]:
            if r["DANGEROUS"]:
                danger.setdefault(r["series"], []).append(r)
    marg = {m["series"]: m for m in (basket or {}).get("marginal", [])}

    bench_eff = None
    for r in q["rows"]:
        if r["series"] == PRIMARY:
            d24 = (duty.get(PRIMARY) or [100.0, 0.0])[0] / 100.0
            bench_eff = (r["cap_per_dollar"] or 0) * d24

    rows = []
    for r in q["rows"]:
        s = r["series"]
        d24, dtr = (duty.get(s) or [None, None])[:2]
        eff = r["cap_per_dollar"]
        eff_cal = (eff * d24 / 100.0) if (eff is not None and d24 is not None) else None
        m = marg.get(s)
        dg = danger.get(s)

        if dg:
            v, why = "REJECT", (f"live ladder logic NETS {len(dg)} non-additive event(s) "
                                f"({dg[0]['event']}: strike_types={','.join(dg[0]['strike_types'])}, "
                                f"mutually_exclusive={dg[0]['mutually_exclusive']}) — "
                                f"ladder_pairing declares a floored pair that has no floor")
        elif r["maker_fee"] != "FREE":
            v, why = "REJECT", f"maker fee {r['maker_fee']} ({r['fee_type']})"
        elif r["admit_pct"] == 0.0:
            v, why = "REJECT", (f"our selection gate admits 0 of {r['obs']} book-snapshots "
                                f"(R3 two-sided {r['two_sided_pct']:.0f}%) — unearnable BY US")
        elif m is not None and m["delta"] < NOISE_FLOOR:
            # canon §M7d: the model over-predicts 2-6x, so anything under ~$1/day modelled is
            # under ~$0.2-0.5/day real — indistinguishable from zero, and not worth new risk.
            v, why = "REJECT", (f"adds only {m['delta']:+.2f}/day modelled to the $85 basket; "
                                f"canon §M7d says the model over-predicts 2-6x, so that is "
                                f"~${m['delta']/6:.2f}-{m['delta']/2:.2f}/day real — noise")
        elif m is None:
            v, why = "NEEDS-PROBE", "no marginal-basket measurement available for this series"
        else:
            v, why = "NEEDS-PROBE", (
                f"clears structure, fee, earnability and materiality (+{m['delta']:.2f}/day "
                f"modelled, {m['slots_taken']} slot(s)); NOT an admit — toxicity UNMEASURED")

        if s in BENCH:
            v, why = "INCUMBENT", "benchmark"

        rows.append({
            "series": s, "verdict": v, "reason": why,
            "ratio": r["median_ratio"], "structure": r["structure_verdict"],
            "strike_types": r["strike_types"], "mut_ex": r["any_mutually_exclusive"],
            "code_nets": r["events_code_nets"], "code_abstains": r["events_code_abstains"],
            "fee": r["maker_fee"], "fee_type": r["fee_type"],
            "two_sided_pct": r["two_sided_pct"], "admit_pct": r["admit_pct"],
            "obs": r["obs"], "instants": r["instants"], "contracts": r["programs"],
            "pool_day": r["pool_day"],
            "cap_day_window": r["cap_day_admitted_per_instant"],
            "cap_day_calendar": (r["cap_day_admitted_per_instant"] * d24 / 100.0
                                 if d24 is not None else None),
            "capital": r["capital_per_instant"],
            "eff_window": eff, "eff_calendar": eff_cal,
            "duty_24h_pct": d24, "trough_0415Z_pct": dtr,
            "basket_delta": (m["delta"] if m else None),
            "toxicity": ("PRIOR-NEGATIVE (mention family: canon FIGHTMENTION +745 in-window / "
                         "-1338 settled)" if any(k in s for k in TOXIC_FAMILY)
                         else ("MEASURED (canon §M8 receipts)" if s in BENCH else "UNMEASURED")),
            "gate_hist": r["gate_hist"],
        })

    order = {"NEEDS-PROBE": 0, "INCUMBENT": 1, "REJECT": 2}
    rows.sort(key=lambda x: (order.get(x["verdict"], 3), -(x["eff_calendar"] or 0)))
    json.dump({"source": q["generated_utc"], "instants": q["instants"],
               "bench_eff_calendar": bench_eff, "rows": rows},
              open(os.path.join(HERE, "survivor_verdict.json"), "w"), indent=1)

    print(f"source {q['generated_utc']}  instants={q['instants']}  "
          f"benchmark {PRIMARY} eff_calendar={bench_eff:.3f} $/day/$\n")
    hdr = (f"{'series':26s} {'verdict':12s} {'ratio':>5} {'2s%':>6} {'adm%':>6} "
           f"{'$/d win':>8} {'$/d cal':>8} {'$cmt':>7} {'eff cal':>8} {'duty':>6} "
           f"{'trough':>7} {'bskt':>7}  structure")
    print(hdr)
    for x in rows:
        f = lambda v, w, p=2: (f"{v:{w}.{p}f}" if v is not None else " " * (w - 1) + "-")
        print(f"{x['series']:26s} {x['verdict']:12s} {f(x['ratio'],5)} "
              f"{f(x['two_sided_pct'],5,1)}% {f(x['admit_pct'],5,1)}% "
              f"{f(x['cap_day_window'],8)} {f(x['cap_day_calendar'],8)} "
              f"{f(x['capital'],7)} {f(x['eff_calendar'],8,3)} {f(x['duty_24h_pct'],5,0)}% "
              f"{f(x['trough_0415Z_pct'],6,0)}% {f(x['basket_delta'],7)}  {x['structure']}")
    print("\nREASONS")
    for x in rows:
        print(f"  {x['series']:26s} {x['verdict']:12s} {x['reason']}")
    return rows


if __name__ == "__main__":
    main()
