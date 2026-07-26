#!/usr/bin/env python3
"""ADVERSARIAL CHECKS on the 'sit at the touch' finding.

1. CONCENTRATION: who dominates the reward pool? If one market/series carries it, the
   k-ranking is that market's property, not the venue's.
2. LEAVE-ONE-OUT: does k=0 still win with the dominant series removed?
3. BREAK-EVEN: how much extra per-contract cost (settlement tail, unmeasured here)
   would it take to flip k=0 -> k=1? That is the number that decides whether the
   unmeasured settlement risk can overturn the conclusion.
"""
import json, collections, bisect

TICK, S, CYCLE_S = 0.01, 20.0, 120.0
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
H = 1800.0
T_HI = TAPE_END - H

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


def reward_by_market(k):
    per = collections.defaultdict(float)
    for r in rows:
        if tsec(r["ts"]) > T_HI:
            continue
        sy, qy = side_share(r, "y", k)
        sn, qn = side_share(r, "n", k)
        if qy and qn:
            per[r["ticker"]] += ((sy + sn) / 2.0) * float(r.get("usd_day") or 0.0) * (CYCLE_S / 86400.0)
    return per


def fills(k, mode="swept"):
    nf = collections.defaultdict(float)
    loss = collections.defaultdict(float)
    used = collections.defaultdict(float)
    for x in sorted(tape, key=lambda z: z["created_time"]):
        t = x["ticker"]
        w = tsec(x["created_time"])
        i = snap_idx(t, w)
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
            side, entry, hy = "y", q, True
        else:
            rn = r.get("n_ref")
            if rn is None:
                continue
            qn = round(rn - k * TICK, 4)
            if qn < TICK:
                continue
            ask = round(1.0 - qn, 4)
            hit = (px >= ask - 1e-9) if mode == "touched" else (px > ask + 1e-9)
            side, entry, hy = "n", qn, False
        if not hit:
            continue
        key = (t, side, i)
        cap = S - used[key]
        if cap <= 0 or w + H > TAPE_END:
            continue
        f = last_trade_in(t, w, w + H)
        if f is None:
            continue
        ct = min(cap, float(x["count_fp"]))
        used[key] += ct
        nf[t] += ct
        loss[t] += (entry - (f if hy else round(1.0 - f, 4))) * ct
    return nf, loss


R = {k: reward_by_market(k) for k in range(4)}
F = {k: fills(k) for k in range(4)}

print("=" * 84)
print("1. CONCENTRATION OF REWARD AT k=0 (denominator = 408 quoted markets)")
print("=" * 84)
r0 = R[0]
tot = sum(r0.values())
ser = collections.defaultdict(float)
for t, v in r0.items():
    ser[t.split("-")[0]] += v
print(f"total reward$ at k=0 over matched window = ${tot:.3f}")
srt = sorted(r0.values(), reverse=True)
for n in (1, 3, 5, 10):
    print(f"  top {n:>2} MARKETS = {100.0*sum(srt[:n])/tot:>5.1f}% of reward")
sv = sorted(ser.items(), key=lambda x: -x[1])
print("  top SERIES:")
for s, v in sv[:6]:
    print(f"    {s:<26} ${v:>7.3f}  {100.0*v/tot:>5.1f}%")

print("\n" + "=" * 84)
print("2. LEAVE-ONE-OUT: drop the dominant series, does k=0 still win?")
print("=" * 84)


def net(k, excl_series=None, excl_ticker=None, extra_cost_per_ct=0.0):
    rew = sum(v for t, v in R[k].items()
              if (excl_series is None or t.split("-")[0] != excl_series)
              and (excl_ticker is None or t != excl_ticker))
    nf, ls = F[k]
    L = sum(v for t, v in ls.items()
            if (excl_series is None or t.split("-")[0] != excl_series)
            and (excl_ticker is None or t != excl_ticker))
    C = sum(v for t, v in nf.items()
            if (excl_series is None or t.split("-")[0] != excl_series)
            and (excl_ticker is None or t != excl_ticker))
    return rew - L - extra_cost_per_ct * C, rew, L, C


for label, kw in (("ALL 408 markets", {}),
                  (f"drop series {sv[0][0]}", {"excl_series": sv[0][0]}),
                  (f"drop series {sv[0][0]}+{sv[1][0]}", {"excl_series": sv[0][0]}),
                  ("drop top single market", {"excl_ticker": max(r0, key=r0.get)})):
    line = f"  {label:<34}"
    for k in range(4):
        n, *_ = net(k, **kw)
        line += f" k{k}=${n:>7.2f}"
    best = max(range(4), key=lambda k: net(k, **kw)[0])
    print(line + f"   -> best k={best}")

print("\n" + "=" * 84)
print("3. BREAK-EVEN: extra per-contract cost needed to make k=1 beat k=0")
print("=" * 84)
print("   (this is the settlement-tail risk that a 30-min markout cannot see)")
lo, hi = 0.0, 5.0
for _ in range(60):
    mid = (lo + hi) / 2
    n0 = net(0, extra_cost_per_ct=mid)[0]
    n1 = net(1, extra_cost_per_ct=mid)[0]
    if n0 > n1:
        lo = mid
    else:
        hi = mid
n0, r0v, l0, c0 = net(0)
n1, r1v, l1, c1 = net(1)
print(f"   k=0: reward ${r0v:.3f}  markout-loss ${l0:.3f}  contracts {c0:.0f}")
print(f"   k=1: reward ${r1v:.3f}  markout-loss ${l1:.3f}  contracts {c1:.0f}")
print(f"   BREAK-EVEN extra cost = ${lo:.4f} per contract filled")
print(f"   For scale: a contract costs at most $1.00 to be totally wrong about.")
print(f"   So k=1 only wins if EVERY filled contract additionally loses "
      f"${lo:.3f} beyond its 30-min markout")
print(f"   i.e. {100.0*lo:.1f}% of maximum possible per-contract loss, on every fill.")
