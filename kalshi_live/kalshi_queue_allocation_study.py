"""QUEUE-DEPTH ALLOCATION STUDY — the reward-per-unit-fill-risk law.

NEW FILE. Read-only. No live-system contact, no keys, no orders.
Imports the CFTC LIP scoring core from scripts/maker_kalshi_recorder.py (qualifying_walk,
side_share) exactly as kalshi_skew_cost_study.py does.

QUESTION
--------
The skew study established that `depth_at_ref >= target` is BOTH the trigger that zeroes a
price-skew's reward credit AND the state where we are queued behind >=Target and almost never
fill. That means external depth at the reference price is a single observable that moves reward
and fill-risk in OPPOSITE directions. This study measures the exchange rate as a continuous
function of that depth, so an allocation law can be derived from it instead of guessed.

For each contract-side snapshot:
    D      = external depth at the reference (best) price, contracts
    share  = our normalised LIP score share on that side with JOIN ct resting AT reference
             (this is the reward per snapshot, in units of "fraction of that side's pool")
    fills  = expected contracts filled per taker order that hits this side, us at the BACK of
             the reference level:  fill = min(JOIN, max(0, V0 - D))  where V0 is the volume the
             taker order consumed at depth 0 (the touch)
    ratio  = share / fills   -- reward earned per contract of adverse-selection exposure

NOT COVERED
-----------
* Reward side is exact (rulebook formula on real books). The fill side is a MODEL: it assumes
  strict price-time priority with our order last in its level and no cancel/replace race.
* Gas only. KXTEMP* had no active programs in either capture window (the known snapshot
  artefact). Temp is where the money is lost, and it is NOT in this sample.
* Two capture windows only (02:25-02:48Z frozen, 16:35-16:42Z fresh) on one day.
* share is computed with the public book treated as external. If our own 20ct was already in
  the book at capture time, D is overstated by up to 20 and share understated slightly.
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from maker_kalshi_recorder import side_share, qualifying_walk  # noqa: E402

JOIN = 20.0
TICK = 0.01


def load_rows(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            snap = json.loads(line)
            for r in snap.get("rows", []):
                out.append((snap.get("ts"), r))
    return out


def sweeps_by_ticker_side(path):
    d = json.load(open(path))
    idx = defaultdict(list)
    for s in d["sweeps"]:
        v0 = float(s.get("bydepth", {}).get("0", 0.0))
        idx[(s["t"], s["side"])].append(v0)
    return idx


def analyse(name, rows, sweeps):
    recs = []
    for ts, r in rows:
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
            sh, _ref, _tot, _in = side_share(lv, [(ref, JOIN)], target, df, TICK)
            if not _in:
                continue
            v0s = sweeps.get((r["t"], side), [])
            if v0s:
                fills = sum(min(JOIN, max(0.0, v - D)) for v in v0s) / len(v0s)
                nsw = len(v0s)
            else:
                fills, nsw = None, 0
            recs.append(dict(t=r["t"], side=side, D=D, target=target, share=sh,
                             fills=fills, nsw=nsw, price=ref))
    print(f"\n{'='*78}\n{name}: {len(recs)} contract-side snapshots"
          f"  ({len({x['t'] for x in recs})} contracts)")

    buckets = [(0, 50), (50, 250), (250, 1000), (1000, 5000), (5000, 10 ** 9)]
    print(f"{'depth@ref D':>16} {'n':>5} {'mean share':>11} {'med share':>10} "
          f"{'mean fills':>11} {'ratio':>10} {'D>=tgt':>7}")
    for lo, hi in buckets:
        sel = [x for x in recs if lo <= x["D"] < hi]
        if not sel:
            continue
        ms = sum(x["share"] for x in sel) / len(sel)
        srt = sorted(x["share"] for x in sel)
        md = srt[len(srt) // 2]
        wf = [x for x in sel if x["fills"] is not None]
        mf = (sum(x["fills"] for x in wf) / len(wf)) if wf else float("nan")
        ratio = (ms / mf) if wf and mf > 0 else float("inf")
        cliff = sum(1 for x in sel if x["D"] >= x["target"]) / len(sel)
        print(f"{lo:>7}-{hi if hi < 10**9 else 'inf':>8} {len(sel):>5} {ms:>11.4f} {md:>10.4f} "
              f"{mf:>11.3f} {ratio:>10.3f} {cliff:>6.0%}")

    # binary cliff split — the deployed observable
    for label, sel in (("CLIFF  D>=target", [x for x in recs if x["D"] >= x["target"]]),
                       ("THIN   D< target", [x for x in recs if x["D"] < x["target"]])):
        if not sel:
            continue
        wf = [x for x in sel if x["fills"] is not None]
        ms = sum(x["share"] for x in sel) / len(sel)
        mf = (sum(x["fills"] for x in wf) / len(wf)) if wf else float("nan")
        print(f"  {label:18s} n={len(sel):4d}  share={ms:.4f}  fills/order={mf:.3f}  "
              f"reward-per-fill-risk={ms / mf if mf > 0 else float('inf'):.4f}")
    return recs


def main():
    sweeps = sweeps_by_ticker_side(os.path.join(HERE, "skew_sweeps.json"))
    for name, path in (("FRESH  (unfiltered 16:35-16:42Z)", "skew_samples.jsonl"),
                       ("FROZEN (filtered 02:25-02:48Z)", "concentration_samples.jsonl")):
        p = os.path.join(HERE, path)
        if not os.path.exists(p):
            print(f"missing {p}")
            continue
        analyse(name, load_rows(p), sweeps)


if __name__ == "__main__":
    main()
