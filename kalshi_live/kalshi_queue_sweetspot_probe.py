"""SWEET-SPOT PROBE — is the D in [250,1000) reward/fill bump real, or a few tickers?

NEW FILE. Read-only. Follow-up to kalshi_queue_allocation_study.py, which found
reward-per-unit-fill-risk essentially FLAT across the cliff/thin split (0.034-0.041 both sides)
but with a 15-20x bump in the single bucket 250 <= D < 1000. Protocol 14 says check who dominates
a pooled bucket BEFORE presenting the number.

Reports, per fine depth bucket:  n, distinct tickers, top-ticker share of the bucket,
mean/median share, mean fills, ratio, and leave-one-ticker-out ratio range.
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
from maker_kalshi_recorder import side_share  # noqa: E402

JOIN, TICK = 20.0, 0.01
BUCKETS = [(0, 25), (25, 100), (100, 250), (250, 500), (500, 750), (750, 1000),
           (1000, 2000), (2000, 10 ** 9)]


def load(path):
    out = []
    for line in open(path):
        if not line.strip():
            continue
        snap = json.loads(line)
        for r in snap.get("rows", []):
            out.append(r)
    return out


def sweeps(path):
    idx = defaultdict(list)
    for s in json.load(open(path))["sweeps"]:
        idx[(s["t"], s["side"])].append(float(s.get("bydepth", {}).get("0", 0.0)))
    return idx


def build(rows, sw):
    recs = []
    for r in rows:
        target = float(r.get("target") or 0)
        df = float(r.get("df") or 0.5)
        if target <= 0:
            continue
        for side, key in (("yes", "yl"), ("no", "nl")):
            lv = [(float(p), float(s)) for p, s in (r.get(key) or [])]
            if not lv:
                continue
            ref = max(p for p, _ in lv)
            if ref >= 1.0:
                continue
            D = sum(s for p, s in lv if abs(p - ref) < TICK / 2)
            sh, _rf, _tt, inset = side_share(lv, [(ref, JOIN)], target, df, TICK)
            if not inset:
                continue
            v0s = sw.get((r["t"], side), [])
            if not v0s:
                continue
            recs.append(dict(t=r["t"], side=side, D=D, share=sh,
                             fills=sum(min(JOIN, max(0.0, v - D)) for v in v0s) / len(v0s),
                             pen=sum(1 for v in v0s if v > D) / len(v0s), n_sw=len(v0s)))
    return recs


def ratio_of(sel):
    if not sel:
        return float("nan")
    mf = sum(x["fills"] for x in sel) / len(sel)
    ms = sum(x["share"] for x in sel) / len(sel)
    return (ms / mf) if mf > 0 else float("inf")


def report(name, recs):
    print(f"\n{'='*100}\n{name}  n={len(recs)} contract-side snapshots")
    print(f"{'D bucket':>14} {'n':>5} {'tick':>5} {'top%':>5} {'meanShare':>10} "
          f"{'meanFills':>10} {'pen%':>6} {'ratio':>9}  LOO-ratio range")
    for lo, hi in BUCKETS:
        sel = [x for x in recs if lo <= x["D"] < hi]
        if not sel:
            continue
        by_t = defaultdict(list)
        for x in sel:
            by_t[x["t"]].append(x)
        top = max(len(v) for v in by_t.values()) / len(sel)
        loo = []
        for drop in by_t:
            rest = [x for x in sel if x["t"] != drop]
            if rest:
                loo.append(ratio_of(rest))
        rng = f"{min(loo):.3f}..{max(loo):.3f}" if loo else "n/a"
        print(f"{lo:>6}-{hi if hi < 10**9 else 'inf':>7} {len(sel):>5} {len(by_t):>5} "
              f"{top:>5.0%} {sum(x['share'] for x in sel)/len(sel):>10.4f} "
              f"{sum(x['fills'] for x in sel)/len(sel):>10.3f} "
              f"{sum(x['pen'] for x in sel)/len(sel):>5.1%} {ratio_of(sel):>9.3f}  {rng}")


def main():
    sw = sweeps(os.path.join(HERE, "skew_sweeps.json"))
    for nm, p in (("FRESH  skew_samples.jsonl", "skew_samples.jsonl"),
                  ("FROZEN concentration_samples.jsonl", "concentration_samples.jsonl")):
        fp = os.path.join(HERE, p)
        if os.path.exists(fp):
            report(nm, build(load(fp), sw))


if __name__ == "__main__":
    main()
