#!/usr/bin/env python3
"""REWARD SIDE, exact, over the frozen telemetry.

For each snapshot and each k (ticks back from reference on BOTH sides), compute the
R4/R3 capture $/day we would have earned resting JOIN_SIZE there.

Rules applied (KALSHI_LIP_RULE_CANON R3/R4):
  - our score on a side = DF^k * S, but ONLY if our price is inside the qualifying
    set (ref - k*tick >= lowq). Outside the walk -> score 0 -> that side earns 0.
  - share = our_score / (rival_book_df + our_score)
  - R3: the snapshot pays only if BOTH sides qualify, else $0 to everyone.
  - capture$/day = ((share_y + share_n)/2) * pool_usd_day

APPROXIMATION (stated, not hidden): rival_book_df is taken from the logged walk, which
was computed WITHOUT our order in 4120/4159 rows (we rested in only 39). Where we did
rest, our own logged score is subtracted out. Adding our size can also terminate the
walk one level earlier, shrinking the rival denominator slightly -> this estimate is
mildly CONSERVATIVE (understates our share).
"""
import json, collections

TICK = 0.01
S = 20.0            # KALSHI_JOIN_SIZE from live.env
CYCLE_S = 120.0     # median observed cycle gap

rows = [json.loads(l) for l in open("quotes_frozen.jsonl")]


def side_share(r, tag, k):
    """(share, qualifies_for_us) for one side at k ticks back."""
    if not r.get(tag + "_qual"):
        return 0.0, False
    ref, lowq = r.get(tag + "_ref"), r.get(tag + "_lowq")
    if ref is None or lowq is None:
        return 0.0, False
    book = float(r.get(tag + "_book_df") or 0.0)
    # remove our own logged order from the denominator where we were resting
    if (r.get(tag + "_rest_ct") or 0) > 0:
        book = max(0.0, book - float(r.get(tag + "_score") or 0.0))
    our_px = round(ref - k * TICK, 4)
    if our_px < lowq - 1e-9:
        return 0.0, True          # side qualifies for the MARKET, but we are outside the walk
    if our_px < TICK:
        return 0.0, True
    score = (float(r["df"]) ** k) * S
    return (score / (book + score) if (book + score) > 0 else 0.0), True


out = {}
for k in range(0, 5):
    tot_usd_day = 0.0
    paid_rows = 0
    zero_rows = 0
    per_series = collections.defaultdict(float)
    for r in rows:
        sy, qy = side_share(r, "y", k)
        sn, qn = side_share(r, "n", k)
        if not (qy and qn):          # R3: market must be two-sided
            zero_rows += 1
            continue
        cap = ((sy + sn) / 2.0) * float(r.get("usd_day") or 0.0)
        if cap > 0:
            paid_rows += 1
        else:
            zero_rows += 1
        tot_usd_day += cap
        per_series[r["series"]] += cap
    # each row is one market-snapshot representing CYCLE_S seconds of resting
    usd = tot_usd_day * (CYCLE_S / 86400.0)
    out[k] = (usd, paid_rows, zero_rows, per_series)

print("=== REWARD vs TICKS BACK FROM TOUCH (frozen: 4159 snapshots, 408 mkts, 3.58h) ===")
print("k = ticks back on BOTH sides; S=20ct; DF=0.5 (4159/4159 rows)")
print()
base = out[0][0]
print(f"{'k':>2} {'reward_$_over_window':>20} {'vs_k0':>8} {'rows_paying':>12} {'rows_zero':>10}")
for k in range(0, 5):
    usd, paid, zero, _ = out[k]
    print(f"{k:>2} {usd:>20.4f} {(usd/base if base else 0):>8.3f} {paid:>12} {zero:>10}")

print()
print("NAIVE DF-ONLY EXPECTATION would be 0.5^k =", [round(0.5**k, 3) for k in range(5)])
print("The gap between the measured ratio and 0.5^k is the QUALIFYING-WALK CLIFF.")

print()
print("=== where the reward is concentrated at k=0 (top 10 series) ===")
ps = out[0][3]
tot = sum(ps.values())
for s, v in sorted(ps.items(), key=lambda x: -x[1])[:10]:
    print(f"  {s:<28} ${v*(CYCLE_S/86400.0):>8.4f}  {100.0*v/tot:>5.1f}% of capture$/day")
