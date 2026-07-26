#!/usr/bin/env python3
"""FOOTPRINT SLOT DISTRIBUTION vs PURE-DENSITY COUNTERFACTUAL (read-only, GET only).

NEW FILE. Does NOT import or edit maker_kalshi_quoter.py. select_footprint is REPLICATED
from the task-documented deployed logic (md5 727ca7c5): 14-series allowlist, FOOTPRINT_TOP=40,
PER_SERIES_CAP=100, late-life gate cutoff_min = min(120, max(45, 0.6*life_min)), round-robin.

Public API for /incentive_programs + /markets/*/orderbook (no keys, 0.35s spacing).
Authed resting-order cross-check via the ledger module L (0.6s spacing).

Outputs a JSON dump + prints tables. Writes nothing else, sends no orders.
"""
import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
_last = [0.0]

# ---- DEPLOYED CONFIG (task-verified live.env @ 2026-07-23, md5 727ca7c5) ----
FOOTPRINT_TOP = 40
PER_SERIES_CAP = 100
SERIES_ALLOW = {
    "KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",  # 5 temp
    "KXAAAGASD", "KXAAAGASW",                                              # 2 gas
    "KXB200MON", "KXAMSAVO", "KXH100MON", "KXMUSKNW", "KXCHIPBURRITO",
    "KXTRUMPENDORSEMENTS", "KXGENERICBALLOTVOTEHUB",                       # 7 new
}
GAS_SERIES = {"KXAAAGASD", "KXAAAGASW"}
# late-life gate constants (deployed): cutoff_min = min(120, max(45, 0.6*life_min))
LATE_LIFE_FRAC = 0.6
LATE_LIFE_MIN = 45
LATE_LIFE_MAX = 120


def pub_get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-dilution/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    _last[0] = time.time()
    return out


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_active_programs():
    progs, cur = [], ""
    for _ in range(8):
        q = "/incentive_programs?status=active&limit=10000" + (f"&cursor={cur}" if cur else "")
        d = pub_get(q)
        got = d.get("incentive_programs") or []
        progs.extend(got)
        cur = d.get("next_cursor") or ""
        if not cur or not got:
            break
    return progs


def eligible_rows(progs, now):
    rows, drops = [], Counter()
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            drops["not_liquidity"] += 1
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            drops["null_fields"] += 1
            continue
        t = p.get("market_ticker")
        if not t:
            drops["no_ticker"] += 1
            continue
        series = t.split("-")[0]
        if series not in SERIES_ALLOW:
            drops["allowlist"] += 1
            continue
        try:
            end, start = parse_iso(p["end_date"]), parse_iso(p["start_date"])
        except Exception:
            drops["date_parse"] += 1
            continue
        life_min = max((end - start).total_seconds() / 60.0, 1.0)
        cutoff = min(LATE_LIFE_MAX, max(LATE_LIFE_MIN, LATE_LIFE_FRAC * life_min))
        if end < now + timedelta(minutes=cutoff):
            drops["late_life"] += 1
            continue
        days = max((end - start).total_seconds() / 86400.0, 1 / 24)
        usd_day = ((p.get("period_reward") or 0) / 10000.0) / days
        rows.append({
            "ticker": t, "series": series, "usd_day": usd_day,
            "target": float(p["target_size_fp"]),
            "period_reward": p.get("period_reward"),
            "days": days, "life_min": life_min, "cutoff_min": cutoff,
            "end": end.isoformat(), "discount_bps": p.get("discount_factor_bps"),
        })
    return rows, drops


def round_robin_pick(rows):
    """DEPLOYED pick: sort by (-usd_day, ticker), round-robin across series."""
    srt = sorted(rows, key=lambda r: (-r["usd_day"], r["ticker"]))
    by_series = defaultdict(list)
    for r in srt:
        by_series[r["series"]].append(r)
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
    return picked


def pure_pick(rows):
    """COUNTERFACTUAL: top-FOOTPRINT_TOP by pure usd_day (per-series cap 100 non-binding)."""
    return sorted(rows, key=lambda r: (-r["usd_day"], r["ticker"]))[:FOOTPRINT_TOP]


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


def book_two_sided(ticker, target):
    """R3: does the current public book reach target size on BOTH sides?
    Uses FULL-BOOK depth per side (generous upper bound; not band-limited)."""
    try:
        ob = pub_get(f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    except Exception as e:
        return None, {"err": repr(e)}
    yl = levels(ob.get("yes_dollars"))
    nl = levels(ob.get("no_dollars"))
    ext_y = sum(s for _, s in yl)
    ext_n = sum(s for _, s in nl)
    two = (ext_y >= target) and (ext_n >= target)
    return two, {"ext_y": ext_y, "ext_n": ext_n, "target": target,
                 "best_y": max((p for p, _ in yl), default=None),
                 "best_n": max((p for p, _ in nl), default=None)}


def dist(picks):
    c = Counter(p["series"] for p in picks)
    usd = defaultdict(float)
    for p in picks:
        usd[p["series"]] += p["usd_day"]
    return c, usd


def main():
    now = datetime.now(timezone.utc)
    print(f"NOW = {now:%Y-%m-%dT%H:%M:%SZ}")
    progs = fetch_active_programs()
    print(f"active programs fetched: {len(progs)}")
    rows, drops = eligible_rows(progs, now)
    print(f"eligible rows (post gate/allowlist): {len(rows)}   drops={dict(drops)}")

    # per-series eligible counts
    elig_by_series = Counter(r["series"] for r in rows)
    print(f"\neligible contracts per allowed series (in universe, pre-pick):")
    for s in sorted(SERIES_ALLOW, key=lambda s: -elig_by_series.get(s, 0)):
        best = max((r["usd_day"] for r in rows if r["series"] == s), default=0.0)
        print(f"  {s:<24} eligible={elig_by_series.get(s,0):>3}  best_usd_day=${best:7.2f}")

    picked = round_robin_pick(rows)
    pure = pure_pick(rows)

    pc, pu = dist(picked)
    cc, cu = dist(pure)
    picked_total = sum(p["usd_day"] for p in picked)
    pure_total = sum(p["usd_day"] for p in pure)

    print(f"\n=== PICKED (deployed round-robin) n={len(picked)} total_usd_day=${picked_total:.2f} ===")
    for s in sorted(pc, key=lambda s: -pu[s]):
        print(f"  {s:<24} slots={pc[s]:>2}  usd_day=${pu[s]:8.2f}")

    print(f"\n=== COUNTERFACTUAL (pure top-{FOOTPRINT_TOP} usd_day) n={len(pure)} total_usd_day=${pure_total:.2f} ===")
    for s in sorted(cc, key=lambda s: -cu[s]):
        print(f"  {s:<24} slots={cc[s]:>2}  usd_day=${cu[s]:8.2f}")

    dilution = pure_total - picked_total
    print(f"\n=== DILUTION (raw, pre-R3) ===")
    print(f"  counterfactual_total=${pure_total:.2f}  picked_total=${picked_total:.2f}")
    print(f"  dilution=${dilution:.2f}  ({dilution/pure_total*100:.1f}% of achievable)"
          if pure_total else "  n/a")

    # which tickers are in pure-but-not-picked (displacers vs displaced)
    picked_t = {p["ticker"] for p in picked}
    pure_t = {p["ticker"] for p in pure}
    only_pure = [p for p in pure if p["ticker"] not in picked_t]   # high-density dropped by RR
    only_pick = [p for p in picked if p["ticker"] not in pure_t]   # low-density added by RR
    print(f"\n  in PURE but NOT picked (high-density displaced): {len(only_pure)}")
    for p in sorted(only_pure, key=lambda r: -r["usd_day"]):
        print(f"    +${p['usd_day']:7.2f}/day  {p['ticker']}  ({p['series']})")
    print(f"  in PICKED but NOT pure (low-density displacers): {len(only_pick)}")
    for p in sorted(only_pick, key=lambda r: -r["usd_day"]):
        print(f"    -${p['usd_day']:7.2f}/day  {p['ticker']}  ({p['series']})")

    # ---- R3 THINNESS CHECK on GAS: how many two-sided strikes does gas actually have? ----
    print(f"\n=== R3 TWO-SIDED CHECK on GAS eligible contracts ===")
    gas_rows = sorted([r for r in rows if r["series"] in GAS_SERIES],
                      key=lambda r: -r["usd_day"])
    print(f"gas eligible contracts: {len(gas_rows)} (fetching books)")
    gas_two = []
    for r in gas_rows:
        two, det = book_two_sided(r["ticker"], r["target"])
        r["_two_sided"] = two
        r["_book"] = det
        flag = "2SIDED" if two else ("1SIDED" if two is False else "ERR")
        print(f"  {r['ticker']:<30} ${r['usd_day']:7.2f}/day  {flag:<7} "
              f"ext_y={det.get('ext_y')} ext_n={det.get('ext_n')} tgt={det.get('target')}")
        if two:
            gas_two.append(r)
    n_gas_two = len(gas_two)
    gas_picked = [p for p in picked if p["series"] in GAS_SERIES]
    print(f"\ngas eligible={len(gas_rows)}  gas two-sided={n_gas_two}  "
          f"gas slots the bot PICKS now={len(gas_picked)}")

    # R3-adjusted counterfactual: pure top-40 but a gas row only counts if two-sided.
    # Non-gas rows kept as-is (their two-sidedness not measured here; caveat in verdict).
    two_map = {r["ticker"]: r.get("_two_sided") for r in gas_rows}
    r3_pool = []
    for r in rows:
        if r["series"] in GAS_SERIES and two_map.get(r["ticker"]) is not True:
            continue  # drop non-two-sided gas from the achievable set
        r3_pool.append(r)
    r3_pure = pure_pick(r3_pool)
    r3_total = sum(p["usd_day"] for p in r3_pure)
    r3c, r3u = dist(r3_pure)
    print(f"\n=== R3-ADJUSTED COUNTERFACTUAL (gas must be two-sided) n={len(r3_pure)} "
          f"total_usd_day=${r3_total:.2f} ===")
    for s in sorted(r3c, key=lambda s: -r3u[s]):
        print(f"  {s:<24} slots={r3c[s]:>2}  usd_day=${r3u[s]:8.2f}")
    r3_dilution = r3_total - picked_total
    print(f"\n  R3 dilution = ${r3_dilution:.2f}  (counterfactual-with-R3 minus picked)")

    out = {
        "now": now.isoformat(),
        "n_active_programs": len(progs),
        "n_eligible": len(rows),
        "drops": dict(drops),
        "eligible_by_series": dict(elig_by_series),
        "picked_total_usd_day": picked_total,
        "pure_total_usd_day": pure_total,
        "dilution_usd_day": dilution,
        "picked_dist": {s: {"slots": pc[s], "usd_day": pu[s]} for s in pc},
        "pure_dist": {s: {"slots": cc[s], "usd_day": cu[s]} for s in cc},
        "only_pure": [{"ticker": p["ticker"], "series": p["series"], "usd_day": p["usd_day"]} for p in only_pure],
        "only_pick": [{"ticker": p["ticker"], "series": p["series"], "usd_day": p["usd_day"]} for p in only_pick],
        "gas_eligible": len(gas_rows),
        "gas_two_sided": n_gas_two,
        "gas_slots_picked_now": len(gas_picked),
        "gas_rows": [{"ticker": r["ticker"], "usd_day": r["usd_day"], "two_sided": r.get("_two_sided"),
                      "book": r.get("_book")} for r in gas_rows],
        "r3_total_usd_day": r3_total,
        "r3_dilution_usd_day": r3_dilution,
        "r3_dist": {s: {"slots": r3c[s], "usd_day": r3u[s]} for s in r3c},
        "picked_full": [{"ticker": p["ticker"], "series": p["series"], "usd_day": p["usd_day"],
                         "target": p["target"], "end": p["end"]} for p in picked],
    }
    outp = os.path.join(HERE, "footprint_dilution.json")
    with open(outp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nwrote {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
