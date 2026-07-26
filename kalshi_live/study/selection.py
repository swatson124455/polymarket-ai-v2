#!/usr/bin/env python3
"""FILL-RISK TERM FOR SELECTION -- built and validated, not asserted.

The ranker scores markets on capture ($/day) alone. Capture rises as rival depth falls,
so among QUALIFYING markets it prefers the thinnest book -- which is also where flow is
most likely to be informed. Nothing in the rank key sees fill risk.

This tests whether any quote-time-observable feature predicts realized adverse selection,
and whether ranking on (capture - predicted_loss) beats ranking on capture alone.

HONEST VALIDATION: features are fitted on the FIRST HALF of the window and evaluated on
the SECOND HALF. An in-sample comparison would be meaningless.
"""
import json, collections, bisect, math

TICK, S, CYCLE_S = 0.01, 20.0, 120.0
H = 900.0        # 15-min markout (keeps enough events in each half)

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
    """capture $/day at the touch for one snapshot (R3+R4)."""
    out = []
    for tag in ("y", "n"):
        if not r.get(tag + "_qual"):
            return 0.0
        book = float(r.get(tag + "_book_df") or 0.0)
        if (r.get(tag + "_rest_ct") or 0) > 0:
            book = max(0.0, book - float(r.get(tag + "_score") or 0.0))
        out.append(S / (book + S) if (book + S) > 0 else 0.0)
    return (sum(out) / 2.0) * float(r.get("usd_day") or 0.0)


def window(lo, hi):
    """per-ticker (reward$, loss$, contracts, feature dict) over [lo,hi)"""
    rew = collections.defaultdict(float)
    feats = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        w = tsec(r["ts"])
        if not (lo <= w < hi):
            continue
        rew[r["ticker"]] += capture0(r) * (CYCLE_S / 86400.0)
        yb, nb = r.get("y_ref"), r.get("n_ref")
        if yb is not None and nb is not None:
            feats[r["ticker"]]["spread"].append(round(1.0 - nb - yb, 4))   # yes_ask - yes_bid
        for tag in ("y", "n"):
            if r.get(tag + "_cum_ct") is not None:
                feats[r["ticker"]]["cum"].append(float(r[tag + "_cum_ct"]))
                feats[r["ticker"]]["bookdf"].append(float(r[tag + "_book_df"] or 0))
    loss = collections.defaultdict(float)
    ct = collections.defaultdict(float)
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
            if ref is None or ref < TICK:
                continue
            if not (px < ref - 1e-9):
                continue
            entry, hy, side = ref, True, "y"
        else:
            rn = r.get("n_ref")
            if rn is None or rn < TICK:
                continue
            if not (px > round(1.0 - rn, 4) + 1e-9):
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
        ct[t] += c_
        loss[t] += (entry - (f if hy else round(1.0 - f, 4))) * c_
    return rew, loss, ct, feats


def med(a):
    return sorted(a)[len(a) // 2] if a else 0.0


r1, l1, c1, f1 = window(T0, MID)
r2, l2, c2, f2 = window(MID, TAPE_END)
print("=" * 80)
print("FIRST HALF (fit)  markets with reward:", sum(1 for v in r1.values() if v > 0),
      " with loss:", sum(1 for v in l1.values() if v != 0))
print("SECOND HALF (test) markets with reward:", sum(1 for v in r2.values() if v > 0),
      " with loss:", sum(1 for v in l2.values() if v != 0))

# --- does any quote-time feature predict first-half loss-per-contract? ---
print("\n" + "=" * 80)
print("DO QUOTE-TIME FEATURES PREDICT ADVERSE SELECTION? (first half)")
print("=" * 80)
samp = [(t, l1[t] / c1[t], med(f1[t]["spread"]), med(f1[t]["cum"]), med(f1[t]["bookdf"]))
        for t in c1 if c1[t] > 0]
print(f"n markets with fills in first half = {len(samp)}")


def corr(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


lp = [s[1] for s in samp]
for name, idx in (("spread", 2), ("cum_ct(depth)", 3), ("book_df", 4)):
    print(f"  rho(loss_per_ct, {name:<14}) = {corr(lp, [s[idx] for s in samp]):+.3f}")

# --- the actual decision: pick top-N markets by each rule, score on the SECOND half ---
print("\n" + "=" * 80)
print("RANKING RULES SCORED OUT-OF-SAMPLE (rank on first half, realise on second)")
print("=" * 80)
allm = set(r1) | set(r2)
SPREAD_MAX = 0.05


def realised(sel):
    return sum(r2.get(t, 0.0) for t in sel) - sum(l2.get(t, 0.0) for t in sel)


rules = {
    "capture only (current)": lambda t: r1.get(t, 0.0),
    "capture - measured loss": lambda t: r1.get(t, 0.0) - l1.get(t, 0.0),
    "capture, spread<=5c": lambda t: (r1.get(t, 0.0)
                                      if med(f1[t]["spread"]) <= SPREAD_MAX and f1[t]["spread"]
                                      else -1e9),
}
for N in (20, 40, 80):
    print(f"\n  top-N = {N}")
    for name, key in rules.items():
        sel = sorted(allm, key=lambda t: -key(t))[:N]
        rr = sum(r2.get(t, 0.0) for t in sel)
        ll = sum(l2.get(t, 0.0) for t in sel)
        print(f"    {name:<26} 2nd-half reward ${rr:>7.3f}  loss ${ll:>6.3f}  "
              f"NET ${rr-ll:>7.3f}")
