#!/usr/bin/env python3
"""TAIL CAPITAL-LOCK + DIRECTION-CORRECT FLATTEN FEASIBILITY — READ-ONLY, PUBLIC API, NO KEYS.

Two corrections to kalshi_tail_exit_probe.py, plus the measurement the proposal is missing.

CORRECTION 1 — FLATTEN DIRECTION. A Kalshi orderbook_fp is BID-ONLY on both sides. The live
`flatten_to_zero` (maker_kalshi_quoter.py:1396-1420) does:
      yb, ya = _touch(ob)
      price, side = (yb, "ask") if long_yes else (ya, "bid")
      if price is None or not (0.01 <= price <= 0.99): break        # <- NO FLATTEN AT ALL
so:  long YES  -> needs a YES BID   (depth = yes_dollars)
     long NO   -> needs a YES ASK, i.e. 1 - best NO bid (depth = no_dollars)
My first pass walked the wrong book. Redone here against the real semantics, and the
0.01<=p<=0.99 guard is applied, because that guard is a SILENT no-flatten.

CORRECTION 2 — CAPITAL LOCK, not reward rate. The proposal ranks by $/day where the day is a
program-window day. But committed capital is released at SETTLEMENT, not at program end:
  * committed = surviving standing + held_cost, and held_cost is GROSS (quoter:1251-1259,
    _held_cost) — PAIRED inventory counts in full against MAX_TOTAL_CAPITAL;
  * naked_held_cost() strips paired inventory from the BREAKER, so paired inventory is
    invisible to HELD_MAX_USD/velocity while still consuming the $85 cap;
  * neither the strand unwind (:1088) nor the settlement taker (:975) can even SEE paired
    inventory — both iterate naked_by.
So the honest denominator for a series with an uncompensated tail is reward-days over
CAPITAL-LOCK-days = (program window) + (tail). This computes that haircut.

WHAT THIS DOES NOT COVER: fill rate, queue position, adverse selection, settlement toxicity.
One instant. Reward-side/book-shape only. It does NOT prove a program will not recur — it
measures the state a position sits in while the darkness lasts.

Run: python kalshi_tail_capital_lock.py   Out: tail_capital_lock.json
"""
import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(HERE, "tail_capital_lock.json")
PROBE = os.path.join(HERE, "tail_exit_probe.json")
SPACING_S = 0.32
PAGES = 8
JOIN = 20.0
_last = [0.0]

SERIES = ["KXNETFLIXTOPVIEWSMOVIE", "KXNETFLIXTOPVIEWSTV", "KXTRUMPENDORSEMENTS", "KXEOWEEK",
          "KXAMSAVO", "KXACTBLUETOP", "KXB200MON", "KXBIGBROTHERELIMINATION", "KXNHSALES",
          "KXMUSKNW", "KXTRUTHSOCIAL", "KXRTX5090MON", "KXFEDMENTION", "KXTRUMPACT",
          "KXAAAGASD", "KXAAAGASW"]


def get(path):
    w = SPACING_S - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-tail-cap/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_programs():
    out, cur, seen = [], "", set()
    for _ in range(PAGES):
        d = get(f"/incentive_programs?status=active&limit=10000"
                + (f"&cursor={cur}" if cur else ""))
        out += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    return out


def main():
    now = datetime.now(timezone.utc)
    print(f"TAIL CAPITAL-LOCK  {now.isoformat()}\n{'='*100}")

    # ---------- part 1: direction-correct flatten feasibility on the DARK books already sampled
    try:
        d = json.load(open(PROBE))
    except OSError:
        d = {"dark_samples": []}
    ds = d.get("dark_samples") or []
    fl = []
    for s in ds:
        by, bn = s["best_yes"], s["best_no"]
        # long YES -> sell YES at the YES bid
        py = by
        okY = py is not None and 0.01 <= py <= 0.99 and s["yes_depth_ct"] >= JOIN
        # long NO -> buy YES at the YES ask == 1 - best NO bid
        pa = (1.0 - bn) if bn is not None else None
        okN = pa is not None and 0.01 <= pa <= 0.99 and s["no_depth_ct"] >= JOIN
        fl.append({"series": s["series"], "ticker": s["ticker"], "h_to_close": s["h_to_close"],
                   "strand_unwind_possible": s["strand_unwind_possible"],
                   "flat_longYES_ok": bool(okY), "flat_longYES_px": py,
                   "flat_longNO_ok": bool(okN), "flat_longNO_px": pa})
    n = len(fl)
    if n:
        print(f"DARK BOOKS (open market, NO active program)  n={n}")
        print(f"  strand unwind possible          {sum(1 for x in fl if x['strand_unwind_possible']):3d}/{n}"
              f"  ({100.0*sum(1 for x in fl if x['strand_unwind_possible'])/n:5.1f}%)")
        print(f"  taker CAN flatten a long YES    {sum(1 for x in fl if x['flat_longYES_ok']):3d}/{n}"
              f"  ({100.0*sum(1 for x in fl if x['flat_longYES_ok'])/n:5.1f}%)")
        print(f"  taker CAN flatten a long NO     {sum(1 for x in fl if x['flat_longNO_ok']):3d}/{n}"
              f"  ({100.0*sum(1 for x in fl if x['flat_longNO_ok'])/n:5.1f}%)")
        both = sum(1 for x in fl if x["flat_longYES_ok"] and x["flat_longNO_ok"])
        nei = sum(1 for x in fl if not x["flat_longYES_ok"] and not x["flat_longNO_ok"])
        print(f"  BOTH directions exitable        {both:3d}/{n}  ({100.0*both/n:5.1f}%)")
        print(f"  NEITHER direction exitable      {nei:3d}/{n}  ({100.0*nei/n:5.1f}%)")
        ex = [x for x in fl if x["series"] not in ("KXAAAGASD", "KXAAAGASW")]
        if ex:
            print(f"  [proposed survivors only, n={len(ex)}] strand "
                  f"{100.0*sum(1 for x in ex if x['strand_unwind_possible'])/len(ex):5.1f}%  "
                  f"bothTaker {100.0*sum(1 for x in ex if x['flat_longYES_ok'] and x['flat_longNO_ok'])/len(ex):5.1f}%")

    # ---------- part 2: capital-lock haircut
    progs = fetch_programs()
    pbt = {}
    for p in progs:
        pbt[p.get("market_ticker") or ""] = p
    print(f"\n{'='*100}\nCAPITAL-LOCK HAIRCUT  (reward accrues over the PROGRAM window; capital is "
          f"released at SETTLEMENT)\n{'='*100}")
    print(f"{'series':26s} {'progH':>7} {'tailH':>7} {'lockH':>7} {'comp%':>7} {'poolTot$':>9} "
          f"{'$/progDay':>10} {'$/lockDay':>10}")
    rows = []
    for s in SERIES:
        ps = [p for t, p in pbt.items() if t.split("-")[0] == s]
        if not ps:
            continue
        # one representative covered market per series (they share the pot value; see the
        # degenerate-sampling warning — this reports the WINDOW geometry, not a ranking)
        p = ps[0]
        t = p["market_ticker"]
        try:
            m = get(f"/markets/{t}").get("market") or {}
            ct = parse_iso(m["close_time"])
            a, b = parse_iso(p["start_date"]), parse_iso(p["end_date"])
        except Exception as e:
            print(f"  {s:26s} FAILED {e!r}")
            continue
        progH = (b - a).total_seconds() / 3600.0
        tailH = (ct - b).total_seconds() / 3600.0
        lockH = progH + max(tailH, 0.0)
        pool = (p.get("period_reward") or 0) / 10000.0
        pdays = max(progH / 24.0, 1e-9)
        ldays = max(lockH / 24.0, 1e-9)
        rows.append({"series": s, "n_programs": len(ps), "rep_ticker": t,
                     "program_h": progH, "tail_h": tailH, "lock_h": lockH,
                     "compensated_pct": 100.0 * progH / lockH if lockH else None,
                     "pool_period_usd": pool,
                     "pool_per_program_day": pool / pdays, "pool_per_lock_day": pool / ldays})
        print(f"  {s:24s} {progH:7.2f} {tailH:7.2f} {lockH:7.2f} "
              f"{100.0*progH/lockH if lockH else 0:6.1f}% {pool:9.2f} "
              f"{pool/pdays:10.2f} {pool/ldays:10.2f}")

    json.dump({"generated": now.isoformat(), "flatten": fl, "lock": rows,
               "join_ct": JOIN,
               "caveats": "read-only public API; one instant; reward-side/book-shape only; "
                          "no fill rate, queue position, adverse selection or settlement toxicity"},
              open(OUT, "w"), indent=2)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
