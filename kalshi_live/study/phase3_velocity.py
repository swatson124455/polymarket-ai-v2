#!/usr/bin/env python3
"""PHASE 3 — does the "sit at the touch" answer FLIP when the reference price is MOVING?

study3.py established the UNCONDITIONAL answer: k=0 maximises net in 6/6 configurations.
That is an average over all market-states. The open operator question is different:
when price is CLIMBING, should we lead it, sit, or stand down?

This is study3.py's machinery VERBATIM (same tsec / snap_idx / side_share / fills, same
frozen inputs) with ONE addition: every telemetry snapshot is bucketed by the observed
REFERENCE VELOCITY at that snapshot, and reward + fill-cost are then accumulated PER
BUCKET. Reusing the original functions is deliberate — two of study3's conventions had
already silently inverted a result once each (fill DIRECTION, and fill SIZE capping), so
they are not re-derived here.

VELOCITY DEFINITION
  For snapshot i of ticker t:  v = |y_ref(i) - y_ref(i-1)| / dt , in TICKS PER MINUTE.

SAMPLING CAVEAT THAT BOUNDS EVERY NUMBER BELOW — telemetry only records a market on the
cycles where it was in the footprint (study3: 4159 rows / 408 markets ~ 10 of 104 cycles
each), so consecutive snapshots of one ticker can be far apart. A velocity computed across
a long gap is not a velocity, it is a stale difference. Snapshots whose gap exceeds
MAX_GAP_S are therefore bucketed UNKNOWN and reported separately, never folded into a
regime. This is the main reason the result may be underpowered.

SETTLEMENT RISK IS NOT IN THIS NUMBER (inherited from study3): markout <=30min over a
3.58h window cannot see a binary resolving against us. Every NET below is an UPPER bound.
"""
import json, collections, bisect

TICK = 0.01
S = 20.0
CYCLE_S = 120.0
MAX_GAP_S = 300.0          # beyond this a "velocity" is a stale difference, not a rate

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

tp = collections.defaultdict(list)
for x in tape:
    tp[x["ticker"]].append((tsec(x["created_time"]), float(x["yes_price_dollars"])))
for t in tp:
    tp[t].sort()
tp_times = {t: [a for a, _ in v] for t, v in tp.items()}

# ---- velocity per (ticker, snapshot index) -------------------------------------------
VEL = {}
for t, seq in by_t.items():
    for i, (ts_i, r) in enumerate(seq):
        if i == 0:
            VEL[(t, i)] = None
            continue
        ts_p, rp = seq[i - 1]
        dt = ts_i - ts_p
        a, b = r.get("y_ref"), rp.get("y_ref")
        if a is None or b is None or dt <= 0 or dt > MAX_GAP_S:
            VEL[(t, i)] = None
            continue
        VEL[(t, i)] = (abs(a - b) / TICK) / (dt / 60.0)


def bucket(v):
    if v is None:
        return "UNKNOWN"
    if v <= 1e-9:
        return "QUIET"        # reference did not move
    if v < 0.5:
        return "DRIFT"        # < 0.5 tick/min
    return "FAST"             # >= 0.5 tick/min


IDX = {}
for t, seq in by_t.items():
    for i, (_ts, r) in enumerate(seq):
        IDX[id(r)] = (t, i)


def snap_idx(t, when):
    a = snap_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, when) - 1
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


def reward_by_bucket(k, t_hi):
    per = collections.defaultdict(float)
    for t, seq in by_t.items():
        for i, (ts_i, r) in enumerate(seq):
            if ts_i > t_hi:
                continue
            sy, qy = side_share(r, "y", k)
            sn, qn = side_share(r, "n", k)
            if not (qy and qn):
                continue
            per[bucket(VEL[(t, i)])] += ((sy + sn) / 2.0) * float(r.get("usd_day") or 0.0) \
                * (CYCLE_S / 86400.0)
    return per


def fills_by_bucket(k, mode, H):
    nf = collections.defaultdict(float)
    loss = collections.defaultdict(float)
    used = collections.defaultdict(float)
    ev = collections.defaultdict(int)
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
            continue
        fwd = last_trade_in(t, when, when + H)
        if fwd is None:
            continue
        used[key] += ct
        b = bucket(VEL[(t, i)])
        ev[b] += 1
        mark = fwd if hold_yes else round(1.0 - fwd, 4)
        nf[b] += ct
        loss[b] += (entry - mark) * ct
    return nf, loss, ev


print("=" * 100)
print("PHASE 3 — IS 'SIT AT THE TOUCH' CONDITIONAL ON REFERENCE VELOCITY?")
print("frozen 2026-07-26T00:59:58Z..04:35:01Z | study3.py conventions verbatim | S=20ct")
print(f"buckets: QUIET = ref unmoved | DRIFT < 0.5 tick/min | FAST >= 0.5 tick/min | "
      f"UNKNOWN = gap > {MAX_GAP_S:.0f}s")
print("=" * 100)

cnt = collections.Counter(bucket(VEL[(t, i)]) for t, seq in by_t.items()
                          for i in range(len(seq)))
tot = sum(cnt.values())
print("\nSNAPSHOT COUNTS BY BUCKET (denominator for everything below):")
for b in ("QUIET", "DRIFT", "FAST", "UNKNOWN"):
    print(f"  {b:<8} {cnt[b]:>5} / {tot}  ({100.0*cnt[b]/tot:>5.1f}%)")

H, hl = 1800.0, "30 min"
t_hi = TAPE_END - H
for mode in ("swept", "touched"):
    print(f"\n########## MARKOUT {hl} — {mode.upper()} ##########")
    print(f"  {'bucket':<8} {'k':>2} {'reward$':>9} {'ct_fill':>8} {'loss$':>9} "
          f"{'NET$':>9} {'$loss/ct':>9} {'events':>7}")
    for b in ("QUIET", "DRIFT", "FAST", "UNKNOWN"):
        best_k, best_net = None, None
        for k in range(0, 4):
            rew = reward_by_bucket(k, t_hi)
            nf, loss, ev = fills_by_bucket(k, mode, H)
            R, L, C = rew.get(b, 0.0), loss.get(b, 0.0), nf.get(b, 0.0)
            net = R - L
            if best_net is None or net > best_net:
                best_k, best_net = k, net
            print(f"  {b:<8} {k:>2} {R:>9.3f} {C:>8.0f} {L:>9.3f} {net:>9.3f} "
                  f"{(L/C if C else 0):>9.4f} {ev.get(b,0):>7}")
        print(f"  {'':<8} -> best k = {best_k}  (net ${best_net:.3f})\n")

print("""
HOW TO READ THIS
  If k=0 wins in EVERY bucket, velocity-conditional placement is NOT justified and Phase 3
  should not be built -- the unconditional study3 answer already covers it.
  If k flips in FAST, that is the first evidence for a regime policy -- but it must hold
  under BOTH swept and touched, and the bucket must carry enough fill events to mean
  anything. A flip on a handful of events is noise, not a finding.""")
