#!/usr/bin/env python3
"""THROTTLE A/B STUDY — sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

Answers the one question the live A/B could not: how much REWARD does the
throttle's 1-tick step-inside actually give up?

Why sandbox beats the live test (operator, 2026-07-22): both arms are scored on
the IDENTICAL book snapshot, so time-of-day, gas-ladder drift, deposits and
mid-test deploys cannot load onto one arm. The live A/B had all four confounds
and never measured rewards at all — arm B filled so constantly it never produced
a quiet interval, which is the only window the balance-residual method can read.

Method: reuse the recorder's CFTC-formula scoring (qualifying walk, DF^ticks x
size, Target Size bound, two-sided void rule) from scripts/maker_kalshi_recorder.py
— the same code path already used for the pool census — and insert OUR
hypothetical order at two prices on the same snapshot:
    arm A (deployed): accumulating side at reference - 1 tick
    arm B:            accumulating side at reference
Everything else identical. Share is what LIP pays pro-rata, so the ratio B/A is
the reward multiple of quoting at the touch.

SCOPE / HONESTY: this measures the REWARD side only. Fill rate (the cost side)
is not simulatable without queue position, and is already measured live: arm B
roughly tripled naked-inventory build (11 -> 30 USD in ~40 min, two ceiling trips).
The decision needs both halves; this supplies the half the live test missed.

Run:    python kalshi_ab_throttle_study.py [minutes]
Report: python kalshi_ab_throttle_study.py --report
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ab_throttle_samples.jsonl")
PUB = "https://api.elections.kalshi.com/trade-api/v2"
ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
         "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")
OUR_SIZE = float(os.environ.get("AB_OUR_SIZE", 15))   # ~ our real capped join size
TICK = 0.01
SPACING_S = 0.35
_last = [0.0]


def _load_scoring():
    """Import the recorder's PURE scoring functions (no side effects, no auth)."""
    import importlib.util
    for cand in (os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"),
                 os.path.join(HERE, "maker_kalshi_recorder.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("_rec", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("maker_kalshi_recorder.py not found (need its CFTC scoring core)")


REC = _load_scoring()


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-ab-study/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def sample_once():
    progs, cur = [], ""
    for _ in range(4):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    ours = [p for p in progs if (p.get("market_ticker") or "").split("-")[0] in ALLOW]
    rows = []
    for p in ours:
        t = p.get("market_ticker")
        target = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        pool = float(p.get("period_reward") or 0) / 10000.0
        if target <= 0 or df <= 0:
            continue
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            continue
        yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
        if not yl or not nl:
            continue
        best_y = max(p_ for p_, _ in yl)
        best_n = max(p_ for p_, _ in nl)
        # Arm B: at reference.  Arm A: one tick inside (the deployed throttle).
        b_share, _, _, b_in = REC.side_share(yl, [(best_y, OUR_SIZE)], target, df, TICK)
        a_share, _, _, a_in = REC.side_share(
            yl, [(round(best_y - TICK, 4), OUR_SIZE)], target, df, TICK)
        rows.append({"t": t, "target": target, "df": df, "pool": pool,
                     "best_y": best_y, "best_n": best_n,
                     "arm_b_share": b_share, "arm_a_share": a_share,
                     "arm_b_in": b_in, "arm_a_in": a_in})
    return rows


def main(minutes):
    end = time.time() + minutes * 60
    n = 0
    while time.time() < end:
        try:
            rows = sample_once()
        except Exception as e:
            print(f"sample error: {e!r}")
            time.sleep(10)
            continue
        ts = datetime.now(timezone.utc).isoformat()
        with open(OUT, "a") as fh:
            for r in rows:
                fh.write(json.dumps(dict(r, ts=ts), separators=(",", ":")) + "\n")
        n += len(rows)
        print(f"{ts[11:19]} sampled {len(rows)} markets (total rows {n})")
        time.sleep(30)
    return 0


def report():
    rows = []
    try:
        for line in open(OUT):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except FileNotFoundError:
        print("no samples yet")
        return 0
    if not rows:
        print("no samples yet")
        return 0
    a = [r["arm_a_share"] for r in rows]
    b = [r["arm_b_share"] for r in rows]
    a_in = sum(1 for r in rows if r["arm_a_in"])
    b_in = sum(1 for r in rows if r["arm_b_in"])
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    # $ value: share x pool, summed per sample row (pool is that program's period reward)
    va = sum(r["arm_a_share"] * r["pool"] for r in rows) / len(rows)
    vb = sum(r["arm_b_share"] * r["pool"] for r in rows) / len(rows)
    print(f"samples: {len(rows)} market-snapshots over "
          f"{rows[0]['ts'][11:19]}..{rows[-1]['ts'][11:19]}  (our size {OUR_SIZE:.0f} ct)")
    print()
    print(f"  arm A (1 tick inside, DEPLOYED): mean share {ma:.5f}   in qualifying set "
          f"{100*a_in/len(rows):.0f}% of snapshots   mean $/period {va:.4f}")
    print(f"  arm B (at reference)           : mean share {mb:.5f}   in qualifying set "
          f"{100*b_in/len(rows):.0f}% of snapshots   mean $/period {vb:.4f}")
    print()
    if ma > 0:
        print(f"  => quoting AT REFERENCE is worth {mb/ma:.2f}x the reward of one tick inside")
    else:
        print("  => arm A scored ZERO: one tick inside fell outside the qualifying set entirely")
    zeroed = sum(1 for r in rows if r["arm_b_share"] > 0 and r["arm_a_share"] == 0)
    print(f"  snapshots where the 1-tick step ZEROED our credit: {zeroed} "
          f"({100*zeroed/len(rows):.0f}%)")
    print()
    print("REWARD SIDE ONLY. Cost side (fill rate) is not simulatable — measured live:")
    print("arm B ~tripled naked-inventory build (11->30 USD in ~40 min, 2 ceiling trips).")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report())
    mins = next((float(x) for x in sys.argv[1:] if x.replace(".", "").isdigit()), 20.0)
    sys.exit(main(mins))
