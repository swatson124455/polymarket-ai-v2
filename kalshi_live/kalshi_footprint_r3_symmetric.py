#!/usr/bin/env python3
"""R3-SYMMETRIC: two-sided-check EVERY picked + every counterfactual contract (read-only).
Loads the footprint_dilution.json produced by kalshi_footprint_dilution.py, then fetches the
public book for each PICKED and each PURE contract and marks two-sided (full-book depth per
side >= target). Reports picked_earnable vs counterfactual_earnable so the dilution is
apples-to-apples under R3. No auth, GET only."""
import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
_last = [0.0]


def pub_get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-r3sym/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    _last[0] = time.time()
    return out


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


BOOK = {}


def two_sided(ticker, target):
    if ticker in BOOK:
        ey, en = BOOK[ticker]
    else:
        try:
            ob = pub_get(f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
        except Exception:
            BOOK[ticker] = (None, None)
            return None
        ey = sum(s for _, s in levels(ob.get("yes_dollars")))
        en = sum(s for _, s in levels(ob.get("no_dollars")))
        BOOK[ticker] = (ey, en)
    if ey is None:
        return None
    return (ey >= target) and (en >= target)


def main():
    d = json.load(open(os.path.join(HERE, "footprint_dilution.json"), encoding="utf-8"))
    # rebuild picked + pure with targets: picked_full has target; pure we reconstruct from
    # eligible not stored, so we re-derive pure earnable from picked+only_pure sets we DO have.
    picked = d["picked_full"]  # has ticker, series, usd_day, target
    # PURE set = picked minus only_pick PLUS only_pure. Reconstruct usd_day/target per ticker.
    tgt = {p["ticker"]: p["target"] for p in picked}
    usd = {p["ticker"]: p["usd_day"] for p in picked}
    ser = {p["ticker"]: p["series"] for p in picked}
    for r in d["only_pure"]:
        usd[r["ticker"]] = r["usd_day"]
        ser[r["ticker"]] = r["series"]
        # target for only_pure not stored; gas target=1000, others unknown -> fetch via book only
        tgt.setdefault(r["ticker"], None)
    only_pick_t = {r["ticker"] for r in d["only_pick"]}
    only_pure = d["only_pure"]
    picked_tickers = [p["ticker"] for p in picked]
    pure_tickers = [t for t in picked_tickers if t not in only_pick_t] + [r["ticker"] for r in only_pure]

    # We need targets for only_pure tickers. Gas target = 1000 (from dilution run). For the
    # rest, pull target_size_fp fresh from active programs so the R3 test is exact.
    prog_target = {}
    cur = ""
    for _ in range(8):
        q = "/incentive_programs?status=active&limit=10000" + (f"&cursor={cur}" if cur else "")
        pg = pub_get(q)
        for p in (pg.get("incentive_programs") or []):
            if p.get("target_size_fp") is not None:
                prog_target[p.get("market_ticker")] = float(p["target_size_fp"])
        cur = pg.get("next_cursor") or ""
        if not cur:
            break
    for t in list(usd):
        if tgt.get(t) is None:
            tgt[t] = prog_target.get(t)

    def eval_set(tickers, label):
        rows = []
        for t in tickers:
            target = tgt.get(t)
            if target is None:
                rows.append((t, ser.get(t), usd.get(t, 0.0), None))
                continue
            ts = two_sided(t, target)
            rows.append((t, ser.get(t), usd.get(t, 0.0), ts))
        nom = sum(r[2] for r in rows)
        earn = sum(r[2] for r in rows if r[3] is True)
        n_two = sum(1 for r in rows if r[3] is True)
        n_one = sum(1 for r in rows if r[3] is False)
        n_unk = sum(1 for r in rows if r[3] is None)
        print(f"\n=== {label} n={len(rows)} ===")
        print(f"  nominal usd_day=${nom:.2f}   EARNABLE (two-sided) usd_day=${earn:.2f}")
        print(f"  two-sided={n_two}  one-sided={n_one}  unknown_target={n_unk}")
        by = defaultdict(lambda: [0, 0.0, 0, 0.0])  # slots, nom, two_slots, two_usd
        for t, s, u, ts in rows:
            by[s][0] += 1
            by[s][1] += u
            if ts is True:
                by[s][2] += 1
                by[s][3] += u
        for s in sorted(by, key=lambda s: -by[s][3]):
            slots, n, tw, tu = by[s]
            print(f"    {s:<24} slots={slots:>2} nom=${n:8.2f}  two_sided={tw:>2} earn=${tu:8.2f}")
        return nom, earn, rows

    pnom, pearn, prows = eval_set(picked_tickers, "PICKED (deployed) R3-symmetric")
    cnom, cearn, crows = eval_set(pure_tickers, "COUNTERFACTUAL (pure top-40) R3-symmetric")

    print("\n==================== R3-SYMMETRIC DILUTION ====================")
    print(f"  picked nominal   =${pnom:8.2f}   picked earnable   =${pearn:8.2f}")
    print(f"  pure   nominal   =${cnom:8.2f}   pure   earnable   =${cearn:8.2f}")
    print(f"  nominal dilution =${cnom-pnom:8.2f}")
    print(f"  EARNABLE dilution=${cearn-pearn:8.2f}   <-- the real, R3-correct gap")

    json.dump({"picked_nominal": pnom, "picked_earnable": pearn,
               "pure_nominal": cnom, "pure_earnable": cearn,
               "earnable_dilution": cearn - pearn,
               "picked_rows": [[t, s, u, ts] for t, s, u, ts in prows],
               "pure_rows": [[t, s, u, ts] for t, s, u, ts in crows]},
              open(os.path.join(HERE, "footprint_r3_symmetric.json"), "w"), indent=2)
    print("\nwrote footprint_r3_symmetric.json")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
