#!/usr/bin/env python3
"""THE MEASUREMENT v3 — tape-based markout, apples-to-apples window.

v2 defect: adverse selection was marked off the TELEMETRY ref, but a market only appears
in telemetry on cycles where it was in the footprint (4159 rows / 408 markets = ~10 of
104 cycles each). So 70 of 84 fill events had no forward row and were charged ZERO loss.
That understated the cost side by ~6x and is why v2's net looked clean.

v3 marks off the PUBLIC TAPE instead (standard microstructure markout), which exists for
every market independent of our sampling:
    markout(H) = P_last_trade(t..t+H) - entry     for a long YES
Fills are only counted when at least H of forward tape exists AND a forward trade prints,
so every counted fill is genuinely marked. Reward is restricted to the same sub-window so
the two sides are measured over identical time.

SETTLEMENT RISK IS NOT IN THIS NUMBER. A 3.58h window cannot see a binary resolving
against us; markout is a floor on the true cost, not the whole of it.
"""
import json, collections, bisect

TICK = 0.01
S = 20.0
CYCLE_S = 120.0

rows = [json.loads(l) for l in open("quotes_frozen.jsonl")]
tape = [json.loads(l) for l in open("tape_frozen.jsonl")]


def tsec(s):
    base = int(s[11:13]) * 3600 + int(s[14:16]) * 60 + int(s[17:19])
    frac = 0.0
    if len(s) > 19 and s[19] == ".":
        d = ""
        for ch in s[20:]:
            if ch.isdigit():
                d += ch
            else:
                break
        if d:
            frac = int(d) / (10.0 ** len(d))
    return base + frac


TAPE_END = max(tsec(x["created_time"]) for x in tape)

by_t = collections.defaultdict(list)
for r in rows:
    by_t[r["ticker"]].append((tsec(r["ts"]), r))
for t in by_t:
    by_t[t].sort(key=lambda x: x[0])
snap_times = {t: [x[0] for x in v] for t, v in by_t.items()}

# tape indexed per ticker, time-sorted, for markout lookups
tp = collections.defaultdict(list)
for x in tape:
    tp[x["ticker"]].append((tsec(x["created_time"]), float(x["yes_price_dollars"])))
for t in tp:
    tp[t].sort()
tp_times = {t: [a for a, _ in v] for t, v in tp.items()}


def snap_idx(t, when):
    a = snap_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, when) - 1
    return i if i >= 0 else None


def last_trade_in(t, lo, hi):
    """yes-price of the last trade in (lo, hi], else None"""
    a = tp_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, hi) - 1
    if i < 0:
        return None
    ts, px = tp[t][i]
    return px if ts > lo else None


def side_share(r, tag, k):
    if not r.get(tag + "_qual"):
        return 0.0, False
    ref, lowq = r.get(tag + "_ref"), r.get(tag + "_lowq")
    if ref is None or lowq is None:
        return 0.0, False
    book = float(r.get(tag + "_book_df") or 0.0)
    if (r.get(tag + "_rest_ct") or 0) > 0:
        book = max(0.0, book - float(r.get(tag + "_score") or 0.0))
    q = round(ref - k * TICK, 4)
    if q < lowq - 1e-9 or q < TICK:
        return 0.0, True
    sc = (float(r["df"]) ** k) * S
    return (sc / (book + sc) if (book + sc) > 0 else 0.0), True


def reward_by_market(k, t_hi):
    per = collections.defaultdict(float)
    for r in rows:
        if tsec(r["ts"]) > t_hi:
            continue
        sy, qy = side_share(r, "y", k)
        sn, qn = side_share(r, "n", k)
        if not (qy and qn):
            continue
        per[r["ticker"]] += ((sy + sn) / 2.0) * float(r.get("usd_day") or 0.0) * (CYCLE_S / 86400.0)
    return per


def fills(k, mode, H):
    """Fills capped at S per (ticker, side, cycle). Only fills with a forward markout
    inside the tape are counted, on BOTH the count and the loss, so rate and cost share
    one denominator."""
    nf = collections.defaultdict(float)
    loss = collections.defaultdict(float)
    used = collections.defaultdict(float)
    n_ev = 0
    n_drop_noforward = 0
    for x in sorted(tape, key=lambda z: z["created_time"]):
        t = x["ticker"]
        when = tsec(x["created_time"])
        i = snap_idx(t, when)
        if i is None:
            continue
        r = by_t[t][i][1]
        px = float(x["yes_price_dollars"])
        if x["taker_side"] == "no":
            ref = r.get("y_ref")
            if ref is None:
                continue
            q = round(ref - k * TICK, 4)
            if q < TICK:
                continue
            hit = (px <= q + 1e-9) if mode == "touched" else (px < q - 1e-9)
            side, entry, hold_yes = "y", q, True
        else:
            refn = r.get("n_ref")
            if refn is None:
                continue
            qn = round(refn - k * TICK, 4)
            if qn < TICK:
                continue
            ask = round(1.0 - qn, 4)
            hit = (px >= ask - 1e-9) if mode == "touched" else (px > ask + 1e-9)
            side, entry, hold_yes = "n", qn, False
        if not hit:
            continue
        key = (t, side, i)
        cap = S - used[key]
        if cap <= 0:
            continue
        ct = min(cap, float(x["count_fp"]))
        if when + H > TAPE_END:
            n_drop_noforward += 1
            continue
        fwd_yes = last_trade_in(t, when, when + H)
        if fwd_yes is None:
            n_drop_noforward += 1
            continue
        used[key] += ct
        n_ev += 1
        mark = fwd_yes if hold_yes else round(1.0 - fwd_yes, 4)
        nf[t] += ct
        loss[t] += (entry - mark) * ct
    return nf, loss, n_ev, n_drop_noforward


print("=" * 92)
print("REWARD vs FILL COST BY DISTANCE FROM TOUCH — tape markout, matched window")
print("frozen 2026-07-26T00:59:58Z..04:35:01Z | 408 mkts | S=20ct | DF=0.5 on 4159/4159 rows")
print("=" * 92)

for H, hl in ((300.0, "5 min"), (900.0, "15 min"), (1800.0, "30 min")):
    t_hi = TAPE_END - H
    print(f"\n########## MARKOUT HORIZON {hl} "
          f"(fills must be <= {int(t_hi//3600):02d}:{int(t_hi%3600//60):02d}Z to be counted) ##########")
    for mode in ("swept", "touched"):
        print(f"\n  --- {mode.upper()} "
              f"{'(joiner at back of queue - realistic)' if mode=='swept' else '(upper bound)'} ---")
        print(f"  {'k':>2} {'reward$':>9} {'ct_fill':>8} {'loss$':>9} {'NET$':>9} "
              f"{'$loss/ct':>9} {'events':>7} {'nofwd':>6}")
        for k in range(0, 4):
            rew = reward_by_market(k, t_hi)
            nf, loss, nev, nd = fills(k, mode, H)
            R, L, C = sum(rew.values()), sum(loss.values()), sum(nf.values())
            print(f"  {k:>2} {R:>9.3f} {C:>8.0f} {L:>9.3f} {R-L:>9.3f} "
                  f"{(L/C if C else 0):>9.4f} {nev:>7} {nd:>6}")

print("""
READING THIS TABLE
  reward$  = R4/R3 capture over the matched sub-window, resting 20ct both sides, all 408 mkts
  loss$    = sum over fills of (entry - forward mark) x contracts. Positive = adverse.
  NET$     = reward - loss. The business question is: which k maximises NET?
  $loss/ct = adverse selection per contract filled -- the 'loss per fill' term.

CAVEAT THAT BOUNDS EVERY NUMBER ABOVE: markout over <=30min on a 3.58h window cannot see
settlement. A fill that rides into an adverse resolution costs far more than its 30min
markout. These NET figures are therefore an UPPER bound on the true net.""")
