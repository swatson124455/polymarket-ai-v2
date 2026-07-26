#!/usr/bin/env python3
"""
kalshi_rho_c_probe.py -- NEW FILE. PUBLIC READ-ONLY. No keys, no orders, no live-system contact.

Measures the two quantities the final doctrine turns on:

  (A) rho_c -- the PER-CONTRACT reward rate, $/contract/day, from
      /incentive_programs?status=active. R1 says period_reward is the TOTAL for the
      Time Period, so $/day = period_reward / (end-start in days). A contract with NO
      active program has rho_c = 0 exactly: pure inventory risk, zero reward.
      This needs NO fill data, NO P&L history, and NO in-sample fitting -- which is what
      makes it different from every price-band selector proposed so far.

  (B) the EMPTY-SIDE rate -- fraction of tradeable contracts where one side of the BOOK
      has no resting orders at all. If we hold inventory there, a "reduce quote priced off
      the reducing side's reference" has no reference to price off, so nothing rests.
      That is deadlock F1 arriving by a route that has nothing to do with the cost cap.

API notes (canon KALSHI_LIP_RULE_CANON.md + kalshi_program_fetch.py):
  * limit=10000, NOT 1000 (1000 silently caps the page)
  * >= 0.3s spacing between calls
Writes: kalshi_live/rho_c_probe.json
"""
import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
ALLOW = ["KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
         "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH"]
BOOK_CAP_PER_SERIES = 20      # keep the probe small and polite
TARGET = 1000                 # target_size_fp on our allowlist
_last = [0.0]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-rho-c-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            out = json.loads(r.read())
    except Exception as e:                                    # noqa: BLE001
        out = {"_error": str(e)}
    _last[0] = time.time()
    return out


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:                                          # noqa: BLE001
        return None


def series_of(t):
    return t.split("-")[0] if t else ""


def event_of(t):
    return "-".join(t.split("-")[:2]) if t else ""


def main():
    stamp = datetime.now(timezone.utc).isoformat()
    print(f"# kalshi_rho_c_probe  {stamp}   PUBLIC READ-ONLY")

    # ---------------------------------------------------------------- (A) rho_c
    progs, cur = [], ""
    for _ in range(10):
        q = "/incentive_programs?status=active&limit=10000"
        if cur:
            q += f"&cursor={cur}"
        d = get(q)
        if "_error" in d:
            print("FETCH ERROR:", d["_error"])
            return
        progs.extend(d.get("incentive_programs") or [])
        cur = d.get("cursor") or ""
        if not cur:
            break
    print(f"active programs fetched: {len(progs)}")

    rho = {}          # market_ticker -> $/day
    meta = {}
    for p in progs:
        mt = p.get("market_ticker") or p.get("ticker") or ""
        st, en = parse_ts(p.get("start_date")), parse_ts(p.get("end_date"))
        pr = p.get("period_reward")
        if not mt or st is None or en is None or pr is None:
            continue
        days = (en - st).total_seconds() / 86400.0
        if days <= 0:
            continue
        # UNIT TRAP (found by smell-test: naive parse gave $18,823/day vs canon R1's
        # $182.51/day). `period_reward` is FIXED POINT x10,000, same family as
        # `target_size_fp`="1000.00" and the orderbook's "0.0100" price strings.
        # 1000000 / 10000 = $100.00 -- exactly canon R1's measured GASD pool.
        pr = float(pr) / 10000.0
        rho[mt] = pr / days
        meta[mt] = {"pool": pr, "days": days,
                    "target": p.get("target_size_fp"), "df": p.get("discount_factor_bps")}

    print()
    print("=" * 76)
    print("(A) rho_c -- PER-CONTRACT REWARD RATE ACROSS OUR ALLOWLIST")
    print("=" * 76)
    print(f"{'series':13s} {'n prog':>6} {'$/day min':>10} {'median':>9} {'max':>9} {'ratio max/min':>14}")
    per_series = defaultdict(list)
    for mt, v in rho.items():
        s = series_of(mt)
        if s in ALLOW:
            per_series[s].append(v)
    allow_rho = {}
    for s in ALLOW:
        vals = sorted(per_series.get(s, []))
        if not vals:
            print(f"{s:13s} {0:>6}      -- no active programs at this instant --")
            continue
        med = vals[len(vals) // 2]
        ratio = vals[-1] / vals[0] if vals[0] > 0 else float("inf")
        allow_rho[s] = vals
        print(f"{s:13s} {len(vals):>6} {vals[0]:>10.3f} {med:>9.3f} {vals[-1]:>9.3f} {ratio:>14.1f}x")

    flat = sorted(v for vs in allow_rho.values() for v in vs)
    if flat:
        print()
        print(f"ALL allowlist contracts with a program: n={len(flat)}  "
              f"min ${flat[0]:.3f}/day  median ${flat[len(flat)//2]:.3f}  max ${flat[-1]:.3f}/day")
        print(f"  -> SPREAD WITHIN THE ALLOWLIST = {flat[-1]/flat[0]:.0f}x" if flat[0] > 0 else "")
        tot = sum(flat)
        cum, k = 0.0, 0
        for v in sorted(flat, reverse=True):
            cum += v
            k += 1
            if cum >= 0.8 * tot:
                break
        print(f"  -> {k} of {len(flat)} contracts carry 80% of the allowlist's total $/day "
              f"({100*k/len(flat):.0f}% of contracts)")

    # ---------------------------------------------------------------- (B) books
    print()
    print("=" * 76)
    print("(B) BOOK SHAPE -- can a reduce quote even be priced?  + rho_c=0 exposure")
    print("=" * 76)
    rows = []
    for s in ALLOW:
        d = get(f"/markets?series_ticker={s}&status=open&limit=200")
        mk = (d.get("markets") or [])[:BOOK_CAP_PER_SERIES]
        for m in mk:
            t = m.get("ticker")
            ob = get(f"/markets/{t}/orderbook?depth=100")
            # SHAPE TRAP (found by smell-test: naive `orderbook`->`yes`/`no` returned
            # 100% both-sides-empty, which is impossible). The live payload is
            # `orderbook_fp` -> `yes_dollars` / `no_dollars`, each a list of
            # [price_string, size_string] with 4-dp decimal STRINGS, not numbers.
            book = (ob.get("orderbook_fp") or ob.get("orderbook") or {})
            yes = book.get("yes_dollars") or book.get("yes") or []
            no = book.get("no_dollars") or book.get("no") or []
            dy = sum(float(x[1]) for x in yes) if yes else 0.0
            dn = sum(float(x[1]) for x in no) if no else 0.0
            rows.append({"ticker": t, "series": s, "event": event_of(t),
                         "yes_levels": len(yes), "no_levels": len(no),
                         "yes_depth": dy, "no_depth": dn,
                         "rho_c": rho.get(t, 0.0), "has_program": t in rho})

    n = len(rows)
    if n:
        empty_any = sum(1 for r in rows if r["yes_levels"] == 0 or r["no_levels"] == 0)
        empty_both = sum(1 for r in rows if r["yes_levels"] == 0 and r["no_levels"] == 0)
        twosided_tgt = sum(1 for r in rows if r["yes_depth"] >= TARGET and r["no_depth"] >= TARGET)
        noprog = sum(1 for r in rows if not r["has_program"])
        print(f"open contracts sampled: {n}  ({BOOK_CAP_PER_SERIES}/series cap)")
        print(f"  at least ONE side completely EMPTY : {empty_any:>4} = {100*empty_any/n:5.1f}%"
              f"   <- no reference -> a reduce quote cannot be priced")
        print(f"  BOTH sides empty                   : {empty_both:>4} = {100*empty_both/n:5.1f}%")
        print(f"  book two-sided at Target={TARGET}      : {twosided_tgt:>4} = {100*twosided_tgt/n:5.1f}%"
              f"   <- R3: only these snapshots pay ANYBODY")
        print(f"  NO ACTIVE PROGRAM (rho_c = 0)      : {noprog:>4} = {100*noprog/n:5.1f}%"
              f"   <- quotable today, pays nothing, ever")
        print()
        print("  per series:")
        print(f"  {'series':13s} {'n':>4} {'empty-side':>11} {'2sided@tgt':>11} {'rho_c=0':>9} {'med $/day':>10}")
        for s in ALLOW:
            rs = [r for r in rows if r["series"] == s]
            if not rs:
                continue
            e = sum(1 for r in rs if r["yes_levels"] == 0 or r["no_levels"] == 0)
            t2 = sum(1 for r in rs if r["yes_depth"] >= TARGET and r["no_depth"] >= TARGET)
            z = sum(1 for r in rs if not r["has_program"])
            rv = sorted(r["rho_c"] for r in rs)
            print(f"  {s:13s} {len(rs):>4} {100*e/len(rs):>10.1f}% {100*t2/len(rs):>10.1f}% "
                  f"{100*z/len(rs):>8.1f}% {rv[len(rv)//2]:>10.3f}")

    out = os.path.join(HERE, "rho_c_probe.json")
    with open(out, "w") as fh:
        json.dump({"stamp": stamp, "n_programs": len(progs),
                   "allowlist_rho": {k: v for k, v in allow_rho.items()},
                   "books": rows}, fh, indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
