#!/usr/bin/env python3
"""THE MEASUREMENT v2 — corrected fill accounting.

v1 defect: every touching trade was charged as a fresh 20ct fill. In reality we rest
S=20 per side and, once filled, we are FLAT until the next cycle re-quotes (120s). So
fills are capped at S per (market, side, cycle). That cap is the difference between a
hypothetical infinite-refill maker and the actual bot.

QUEUE MODEL — reported as bounds, because intra-level queue position is unobservable:
  SWEPT (primary): we fill only when a trade prints STRICTLY through our price. As a
    joiner we sit at the BACK of the queue at our level, so this is the realistic model.
  TOUCHED (upper bound): any print at or through our price fills us.

FROZEN: quotes_frozen.jsonl md5 7d7023857c07cdb1b14bd1aab3cc73c5 (4159 snaps / 408 mkts)
        tape_frozen.jsonl 4763 public trades, same window, 0 dupes, 0 outside.
"""
import json, collections, bisect

TICK = 0.01
S = 20.0
CYCLE_S = 120.0
HORIZ = 1800.0

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


by_t = collections.defaultdict(list)
for r in rows:
    by_t[r["ticker"]].append((tsec(r["ts"]), r))
for t in by_t:
    by_t[t].sort(key=lambda x: x[0])
snap_times = {t: [x[0] for x in v] for t, v in by_t.items()}


def snap_idx(t, when):
    a = snap_times.get(t)
    if not a:
        return None
    i = bisect.bisect_right(a, when) - 1
    return i if i >= 0 else None


def snap_after(t, when):
    a = snap_times.get(t)
    if not a:
        return None
    i = bisect.bisect_left(a, when)
    return by_t[t][i][1] if i < len(a) else None


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
        sy, qy = side_share(r, "y", k)
        sn, qn = side_share(r, "n", k)
        if not (qy and qn):
            continue
        per[r["ticker"]] += ((sy + sn) / 2.0) * float(r.get("usd_day") or 0.0) * (CYCLE_S / 86400.0)
    return per


def fills_by_market(k, mode):
    """Fills capped at S per (ticker, side, cycle-index). Returns per-ticker dicts."""
    filled = collections.defaultdict(float)     # remaining capacity tracker
    nf = collections.defaultdict(float)
    loss = collections.defaultdict(float)
    n_marked = collections.defaultdict(int)
    n_unmarked = collections.defaultdict(int)
    used = collections.defaultdict(float)       # (ticker, side, idx) -> ct already filled
    for x in sorted(tape, key=lambda z: z["created_time"]):
        t = x["ticker"]
        when = tsec(x["created_time"])
        i = snap_idx(t, when)
        if i is None:
            continue
        r = by_t[t][i][1]
        px = float(x["yes_price_dollars"])
        if x["taker_side"] == "no":                         # hits YES bids
            ref = r.get("y_ref")
            if ref is None:
                continue
            q = round(ref - k * TICK, 4)
            if q < TICK:
                continue
            hit = (px <= q + 1e-9) if mode == "touched" else (px < q - 1e-9)
            side, entry, hold_yes = "y", q, True
        else:                                                # hits NO bids
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
            continue                                         # already flat this cycle
        ct = min(cap, float(x["count_fp"]))
        used[key] += ct
        nf[t] += ct
        fut = snap_after(t, when + HORIZ)
        mark = None
        if fut:
            mark = fut.get("y_ref") if hold_yes else fut.get("n_ref")
        if mark is not None:
            loss[t] += (entry - float(mark)) * ct
            n_marked[t] += 1
        else:
            n_unmarked[t] += 1
    return nf, loss, n_marked, n_unmarked


print("=" * 86)
print("REWARD vs FILL COST BY DISTANCE FROM TOUCH  (fills capped at 20ct/side/cycle)")
print("frozen 2026-07-26T00:59:58Z..04:35:01Z (3.58h) | 408 mkts | S=20 | DF=0.5 (4159/4159)")
print("=" * 86)
res = {}
for mode in ("swept", "touched"):
    print(f"\n--- {mode.upper()} "
          f"({'realistic: joiner at back of queue' if mode=='swept' else 'upper bound on fill rate'}) ---")
    print(f"{'k':>2} {'reward$':>9} {'ct_filled':>10} {'loss$@30m':>10} {'NET$':>9} "
          f"{'mkts_paid':>9} {'mkts_hit':>8} {'marked':>7} {'unmarked':>8}")
    for k in range(0, 4):
        rew = reward_by_market(k)
        nf, loss, nm, nu = fills_by_market(k, mode)
        R, L, C = sum(rew.values()), sum(loss.values()), sum(nf.values())
        res[(mode, k)] = (rew, nf, loss)
        print(f"{k:>2} {R:>9.3f} {C:>10.0f} {L:>10.3f} {R-L:>9.3f} "
              f"{sum(1 for v in rew.values() if v>0):>9} {sum(1 for v in nf.values() if v>0):>8} "
              f"{sum(nm.values()):>7} {sum(nu.values()):>8}")

# ---- the decomposition that matters: is the loss concentrated? ----------------------
print("\n" + "=" * 86)
print("PER-MARKET DECOMPOSITION at k=0, SWEPT — is the damage concentrated?")
print("=" * 86)
rew, nf, loss = res[("swept", 0)]
allt = set(rew) | set(loss)
net = {t: rew.get(t, 0.0) - loss.get(t, 0.0) for t in allt}
tot = sum(net.values())
print(f"TOTAL NET over 408 markets: ${tot:.3f}   (reward ${sum(rew.values()):.3f} "
      f"- loss ${sum(loss.values()):.3f})")
worst = sorted(net.items(), key=lambda x: x[1])[:10]
print("\nWORST 10 MARKETS BY NET:")
for t, v in worst:
    print(f"  {t:<42} net ${v:>9.3f}  (rew ${rew.get(t,0):>7.3f}  loss ${loss.get(t,0):>8.3f})")
top = sorted(net.items(), key=lambda x: -x[1])[:10]
print("\nBEST 10 MARKETS BY NET:")
for t, v in top:
    print(f"  {t:<42} net ${v:>9.3f}  (rew ${rew.get(t,0):>7.3f}  loss ${loss.get(t,0):>8.3f})")

nloss = sum(1 for t in allt if loss.get(t, 0) > 0)
print(f"\nMARKETS WITH ANY MEASURED LOSS: {nloss} of {len(allt)}")
srt = sorted((loss.get(t, 0.0) for t in allt), reverse=True)
L = sum(srt)
for n in (1, 3, 5, 10, 20):
    print(f"  top {n:>2} loss-makers = ${sum(srt[:n]):>8.3f} = {100.0*sum(srt[:n])/L:>5.1f}% of all loss")

pos = {t: v for t, v in net.items() if v > 0}
print(f"\nMARKETS WITH POSITIVE NET: {len(pos)} of {len(allt)}, summing ${sum(pos.values()):.3f}")
