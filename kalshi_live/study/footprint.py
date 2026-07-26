#!/usr/bin/env python3
"""TWO questions the frozen data CAN answer, both live-config:

Q1 FOOTPRINT WIDTH. Live KALSHI_FOOTPRINT_TOP=40. Reward is concentrated but adverse
   selection scales with breadth, so net may peak well below 40. Sweep N.

Q2 DOES SCORE_RANK EARN ITS KEEP? It is ON in production and ranks on the capture model,
   which the handoff itself calls unvalidated (n=1). Compare ranking by CAPTURE against
   ranking by POOL (usd_day, the legacy key) out-of-sample. If pool wins, SCORE_RANK is
   costing money.

Both scored BOTH WAYS round (fit 1st half -> test 2nd, and fit 2nd -> test 1st) so the
answer cannot be an artifact of one split.
"""
import json, collections, bisect

TICK, S, CYCLE_S, H = 0.01, 20.0, 120.0, 900.0
rows = [json.loads(l) for l in open("quotes_frozen.jsonl")]
tape = [json.loads(l) for l in open("tape_frozen.jsonl")]


def tsec(s):
    b = int(s[11:13]) * 3600 + int(s[14:16]) * 60 + int(s[17:19])
    f = 0.0
    if len(s) > 19 and s[19] == ".":
        d = ""
        for ch in s[20:]:
            if ch.isdigit():
                d += ch
            else:
                break
        if d:
            f = int(d) / (10.0 ** len(d))
    return b + f


TAPE_END = max(tsec(x["created_time"]) for x in tape)
T0 = min(tsec(r["ts"]) for r in rows)
MID = T0 + (TAPE_END - T0) / 2.0

by_t = collections.defaultdict(list)
for r in rows:
    by_t[r["ticker"]].append((tsec(r["ts"]), r))
for t in by_t:
    by_t[t].sort(key=lambda x: x[0])
snap_times = {t: [x[0] for x in v] for t, v in by_t.items()}
tp = collections.defaultdict(list)
for x in tape:
    tp[x["ticker"]].append((tsec(x["created_time"]), float(x["yes_price_dollars"])))
for t in tp:
    tp[t].sort()
tp_times = {t: [a for a, _ in v] for t, v in tp.items()}


def snap_idx(t, w):
    a = snap_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, w) - 1
    return i if i >= 0 else None


def last_trade_in(t, lo, hi):
    a = tp_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, hi) - 1
    if i < 0:
        return None
    ts, px = tp[t][i]
    return px if ts > lo else None


def capture0(r):
    o = []
    for tag in ("y", "n"):
        if not r.get(tag + "_qual"):
            return 0.0
        b = float(r.get(tag + "_book_df") or 0.0)
        if (r.get(tag + "_rest_ct") or 0) > 0:
            b = max(0.0, b - float(r.get(tag + "_score") or 0.0))
        o.append(S / (b + S) if (b + S) > 0 else 0.0)
    return (sum(o) / 2.0) * float(r.get("usd_day") or 0.0)


def window(lo, hi):
    rew = collections.defaultdict(float)
    pool = collections.defaultdict(float)
    for r in rows:
        w = tsec(r["ts"])
        if not (lo <= w < hi):
            continue
        rew[r["ticker"]] += capture0(r) * (CYCLE_S / 86400.0)
        pool[r["ticker"]] += float(r.get("usd_day") or 0.0) * (CYCLE_S / 86400.0)
    loss = collections.defaultdict(float)
    used = collections.defaultdict(float)
    for x in sorted(tape, key=lambda z: z["created_time"]):
        w = tsec(x["created_time"])
        if not (lo <= w < hi) or w + H > TAPE_END:
            continue
        t = x["ticker"]
        i = snap_idx(t, w)
        if i is None:
            continue
        r = by_t[t][i][1]
        px = float(x["yes_price_dollars"])
        if x["taker_side"] == "no":
            ref = r.get("y_ref")
            if ref is None or ref < TICK or not (px < ref - 1e-9):
                continue
            entry, hy, side = ref, True, "y"
        else:
            rn = r.get("n_ref")
            if rn is None or rn < TICK or not (px > round(1.0 - rn, 4) + 1e-9):
                continue
            entry, hy, side = rn, False, "n"
        key = (t, side, i)
        cap = S - used[key]
        if cap <= 0:
            continue
        f = last_trade_in(t, w, w + H)
        if f is None:
            continue
        c_ = min(cap, float(x["count_fp"]))
        used[key] += c_
        loss[t] += (entry - (f if hy else round(1.0 - f, 4))) * c_
    return rew, pool, loss


A = window(T0, MID)
B = window(MID, TAPE_END)

print("=" * 84)
print("Q1 FOOTPRINT WIDTH — net vs number of markets quoted (live setting = 40)")
print("=" * 84)
for lbl, (fit, test) in (("fit 1st half -> realise 2nd", (A, B)),
                         ("fit 2nd half -> realise 1st", (B, A))):
    rf, pf, lf = fit
    rt, pt, lt = test
    allm = set(rf) | set(rt)
    print(f"\n  {lbl}")
    print(f"    {'N':>4} {'reward$':>9} {'loss$':>8} {'NET$':>9}")
    for N in (5, 10, 20, 30, 40, 60, 100, 200, 408):
        sel = sorted(allm, key=lambda t: -rf.get(t, 0.0))[:N]
        rr = sum(rt.get(t, 0.0) for t in sel)
        ll = sum(lt.get(t, 0.0) for t in sel)
        print(f"    {N:>4} {rr:>9.3f} {ll:>8.3f} {rr-ll:>9.3f}")

print("\n" + "=" * 84)
print("Q2 SCORE_RANK (capture) vs LEGACY (pool) — is the capture model earning its keep?")
print("=" * 84)
for lbl, (fit, test) in (("fit 1st half -> realise 2nd", (A, B)),
                         ("fit 2nd half -> realise 1st", (B, A))):
    rf, pf, lf = fit
    rt, pt, lt = test
    allm = set(rf) | set(rt)
    print(f"\n  {lbl}")
    print(f"    {'N':>4}  {'CAPTURE-rank NET$':>18}  {'POOL-rank NET$':>16}  winner")
    for N in (10, 20, 40, 80):
        sc = sorted(allm, key=lambda t: -rf.get(t, 0.0))[:N]
        sp = sorted(allm, key=lambda t: -pf.get(t, 0.0))[:N]
        nc = sum(rt.get(t, 0.0) for t in sc) - sum(lt.get(t, 0.0) for t in sc)
        np_ = sum(rt.get(t, 0.0) for t in sp) - sum(lt.get(t, 0.0) for t in sp)
        print(f"    {N:>4}  {nc:>18.3f}  {np_:>16.3f}  "
              f"{'CAPTURE' if nc > np_ else 'POOL':>7}")
