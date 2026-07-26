#!/usr/bin/env python3
"""GATE ATTRIBUTION — why is `gated_out` eating 83% of the footprint? (read-only, no keys)

THE HOLE THIS FILLS: the live plan log reports `gated_out = len(footprint) - len(desired)`
(maker_kalshi_quoter.py:1320) as ONE undifferentiated number. `desired_quotes` has EIGHT
early `return []` paths and only ONE of them (`unqualifiable`, :493) increments a counter.
Measured over the last 24h of plans-*.jsonl: gated_out mean 12.29 of footprint 14.81, and
`unqualifiable` was 0.00 in all 592 rows that carry it -- so ~100% of the attrition lands in
paths that emit NO telemetry at all. This script re-derives each predicate against the live
public book so the bucket can be split.

The predicates are RE-IMPLEMENTED here, not imported: maker_kalshi_quoter.py is being edited
by a concurrent workflow and must not be touched or depended on. Constants are read from the
live env values (printed by the operator), defaulting to the shipped defaults. If the quoter's
logic changes, this file drifts -- it is a diagnostic, not a source of truth. Line refs pinned
at 2026-07-23.

ASSUMES FLAT (inv=0, event_delta=0), i.e. it answers "why can we not OPEN here", which is the
under-deployment question. It does NOT model the unwind/wind-down branches that only apply
when inventory is already held.

CANNOT SEE: fill rate, queue position, adverse selection, our eventual normalised share.
Reward-side and gate-side only.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
_last = [0.0]

ALLOW = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
         "KXAAAGASD", "KXAAAGASW"]
# live.env @ 2026-07-23T15:19Z
FOOTPRINT_TOP = 40
PER_SERIES_CAP = 10
JOIN_SIZE = 20
MAX_ACTIVATE_CAPITAL = 15.0
MAX_MARKET_CAPITAL = 15.0
MAX_PRICE_DOLLARS = 0.96
MIN_PRICE_DOLLARS = 0.04
WIND_DOWN_MIN = 20
MAX_SPREAD_TICKS = 8
MIN_DEPTH_SYM = 0.25
INV_SOFT_CT = 15
TICK = 0.01
# footprint late-life gate (quoter :304-311) -- these are code defaults, not in live.env
LATE_LIFE_FRAC = 0.30
MAX_ENTRY_CUTOFF_MIN = 1440


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-gate-attrib/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    _last[0] = time.time()
    return out


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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


def select_footprint(progs, now):
    """mirror of maker_kalshi_quoter.select_footprint (:282-330) incl. round-robin"""
    rows, drops = [], Counter()
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            drops["drop_not_liquidity"] += 1
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            drops["drop_null_fields"] += 1
            continue
        t = p.get("market_ticker")
        if not t:
            continue
        if t.split("-")[0] not in ALLOW:
            drops["drop_allowlist"] += 1
            continue
        try:
            end, start = parse_iso(p["end_date"]), parse_iso(p["start_date"])
        except Exception:
            drops["drop_date_parse"] += 1
            continue
        life_min = max((end - start).total_seconds() / 60.0, 1.0)
        cutoff = min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC * life_min))
        if end < now + timedelta(minutes=cutoff):
            drops["drop_late_life"] += 1
            continue
        days = max((end - start).total_seconds() / 86400, 1 / 24)
        rows.append({"ticker": t, "usd_day": ((p.get("period_reward") or 0) / 10000) / days,
                     "target": float(p["target_size_fp"]), "end": end})
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))
    by_series = defaultdict(list)
    for r in rows:
        by_series[r["ticker"].split("-")[0]].append(r)
    order = sorted(by_series, key=lambda s: (-by_series[s][0]["usd_day"], s))
    picked, per = [], Counter()
    while len(picked) < FOOTPRINT_TOP:
        added = False
        for s in order:
            if len(picked) >= FOOTPRINT_TOP or per[s] >= PER_SERIES_CAP:
                continue
            if per[s] < len(by_series[s]):
                picked.append(by_series[s][per[s]])
                per[s] += 1
                added = True
        if not added:
            break
    return picked, drops


def classify(m, yl, nl, now):
    """re-derivation of desired_quotes' early returns, FLAT case. Returns (reason, detail)."""
    best_y = max((p for p, _ in yl), default=None)
    best_n = max((p for p, _ in nl), default=None)
    if m["end"] < now + timedelta(minutes=WIND_DOWN_MIN):
        return "wind_down", {}
    if best_y is None or best_n is None:
        return "unpriceable_side", {"best_y": best_y, "best_n": best_n}
    if not (MIN_PRICE_DOLLARS < best_y <= MAX_PRICE_DOLLARS) or \
       not (MIN_PRICE_DOLLARS < best_n <= MAX_PRICE_DOLLARS):
        return "spread_sanity", {"best_y": best_y, "best_n": best_n}
    if best_y + best_n >= 1.0:
        return "crossed_book", {"sum": round(best_y + best_n, 4)}
    ext_y = sum(s for _, s in yl)
    ext_n = sum(s for _, s in nl)
    target = m["target"]
    void = ext_y < target or ext_n < target
    addable = MAX_ACTIVATE_CAPITAL / max(best_y, best_n, 0.01)
    if not ((ext_y + addable >= target) and (ext_n + addable >= target)):
        return "unqualifiable", {"ext_y": ext_y, "ext_n": ext_n, "target": target,
                                 "addable": round(addable, 1)}
    if not void:
        spread_ticks = (1.0 - best_n - best_y) / TICK
        sym = min(ext_y, ext_n) / max(ext_y, ext_n, 1e-9)
        if spread_ticks > MAX_SPREAD_TICKS:
            return "sel_gate_wide", {"spread_ticks": round(spread_ticks, 1),
                                     "sym": round(sym, 3)}
        if sym < MIN_DEPTH_SYM:
            return "sel_gate_asym", {"spread_ticks": round(spread_ticks, 1),
                                     "sym": round(sym, 3), "ext_y": ext_y, "ext_n": ext_n}
        return "QUOTE_join", {"spread_ticks": round(spread_ticks, 1), "sym": round(sym, 3)}
    add_y = max(JOIN_SIZE, target - ext_y)
    add_n = max(JOIN_SIZE, target - ext_n)
    cap = best_y * add_y + best_n * add_n
    if cap > MAX_ACTIVATE_CAPITAL:
        return "activate_too_expensive", {"cap": round(cap, 2),
                                          "limit": MAX_ACTIVATE_CAPITAL,
                                          "ext_y": ext_y, "ext_n": ext_n}
    return "QUOTE_activate", {"cap": round(cap, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    progs, cur = [], ""
    for _ in range(5):
        d = get("/incentive_programs?status=active&limit=10000" + (f"&cursor={cur}" if cur else ""))
        progs.extend(d.get("incentive_programs") or [])
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    fp, drops = select_footprint(progs, now)
    print(f"{now:%Y-%m-%dT%H:%M:%SZ}  programs_seen={len(progs)}  "
          f"drop_allowlist={drops['drop_allowlist']}  drop_late_life={drops['drop_late_life']}  "
          f"footprint={len(fp)}")
    by_series = Counter(m["ticker"].split("-")[0] for m in fp)
    print(f"footprint by series: {dict(by_series)}")

    reasons, rows = Counter(), []
    print(f"\n{'ticker':<34}{'reason':<24}detail")
    for m in fp:
        try:
            ob = get(f"/markets/{m['ticker']}/orderbook").get("orderbook_fp") or {}
        except Exception as e:
            reasons["FETCH_FAIL"] += 1
            print(f"{m['ticker']:<34}{'FETCH_FAIL':<24}{e!r}")
            continue
        yl = levels(ob.get("yes_dollars"))
        nl = levels(ob.get("no_dollars"))
        r, det = classify(m, yl, nl, now)
        reasons[r] += 1
        rows.append({"ticker": m["ticker"], "reason": r, "usd_day": m["usd_day"], **det})
        print(f"{m['ticker']:<34}{r:<24}{det}")

    print(f"\n=== ATTRIBUTION (n={len(fp)} footprint markets, ONE snapshot) ===")
    tot = sum(reasons.values()) or 1
    for r, n in reasons.most_common():
        print(f"  {r:<26}{n:>4}  {n/tot:>6.1%}")
    q = sum(n for r, n in reasons.items() if r.startswith("QUOTE_"))
    print(f"\n  would QUOTE: {q}/{len(fp)} = {q/tot:.1%}   would GATE: {tot-q}/{len(fp)} = {(tot-q)/tot:.1%}")
    pool_q = sum(r["usd_day"] for r in rows if r["reason"].startswith("QUOTE_"))
    pool_g = sum(r["usd_day"] for r in rows if not r["reason"].startswith("QUOTE_"))
    print(f"  pool $/day reachable={pool_q:.2f}  gated away={pool_g:.2f}  "
          f"(pool SIZE, not our capture)")
    if a.out:
        json.dump({"ts": now.isoformat(), "footprint": len(fp),
                   "reasons": dict(reasons), "rows": rows,
                   "pool_usd_day_quotable": pool_q, "pool_usd_day_gated": pool_g},
                  open(a.out, "w"), indent=2)
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
