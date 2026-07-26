#!/usr/bin/env python3
"""PROBE (read-only, public API, no keys, never trades): size the per-series contract
census for the 14 allowlisted series. Replicates the DEPLOYED late-life gate to count how
many contracts survive per series, so the full census knows how many books to fetch."""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACING_S = 0.32
_last = [0.0]

ALLOW = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
         "KXAAAGASD", "KXAAAGASW", "KXB200MON", "KXAMSAVO", "KXH100MON",
         "KXMUSKNW", "KXCHIPBURRITO", "KXTRUMPENDORSEMENTS", "KXGENERICBALLOTVOTEHUB"]
# DEPLOYED late-life gate params (task-documented, verified this session)
WIND_DOWN_MIN = 45
LATE_LIFE_FRAC = 0.6
MAX_ENTRY_CUTOFF_MIN = 120


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path,
                                 headers={"User-Agent": "kalshi-allowlist-census-probe/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_programs():
    progs, cur, seen = [], "", set()
    for _ in range(20):
        d = get("/incentive_programs?status=active&limit=10000"
                + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur or cur in seen:
            break
        seen.add(cur)
    return progs


def main():
    now = datetime.now(timezone.utc)
    progs = fetch_programs()
    want = set(ALLOW)
    per = defaultdict(lambda: {"raw": 0, "liq": 0, "hasfields": 0, "post_gate": 0,
                               "usd_days": [], "example": None})
    for p in progs:
        t = p.get("market_ticker") or ""
        s = t.split("-")[0]
        if s not in want:
            continue
        per[s]["raw"] += 1
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        per[s]["liq"] += 1
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            continue
        per[s]["hasfields"] += 1
        try:
            st, en = parse_iso(p["start_date"]), parse_iso(p["end_date"])
        except Exception:
            continue
        life_min = max((en - st).total_seconds() / 60.0, 1.0)
        cutoff = min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC * life_min))
        if en < now + timedelta_min(cutoff):
            continue
        per[s]["post_gate"] += 1
        days = max((en - st).total_seconds() / 86400.0, 1 / 24.0)
        ud = ((p.get("period_reward") or 0) / 10000.0) / days
        per[s]["usd_days"].append(ud)
        if per[s]["example"] is None:
            per[s]["example"] = {"ticker": t, "target_size_fp": p.get("target_size_fp"),
                                 "discount_factor_bps": p.get("discount_factor_bps"),
                                 "period_reward": p.get("period_reward"),
                                 "start": p["start_date"], "end": p["end_date"],
                                 "days": round(days, 4), "usd_day": round(ud, 4)}

    print(f"census probe @ {now.isoformat()}  total active programs: {len(progs)}")
    print(f"{'series':24s}{'raw':>5}{'liq':>5}{'flds':>6}{'gated':>7}{'bestUD':>9}{'sumUD':>9}")
    for s in ALLOW:
        d = per[s]
        uds = d["usd_days"]
        print(f"{s:24s}{d['raw']:>5}{d['liq']:>5}{d['hasfields']:>6}{d['post_gate']:>7}"
              f"{(max(uds) if uds else 0):>9.3f}{(sum(uds) if uds else 0):>9.3f}")
    out = {"generated": now.isoformat(), "total_programs": len(progs),
           "per_series": {s: {k: v for k, v in per[s].items() if k != "usd_days"}
                          | {"n_usd": len(per[s]["usd_days"])} for s in ALLOW}}
    json.dump(out, open(os.path.join(HERE, "allowlist_census_probe.json"), "w"), indent=1)
    print("\nexamples:")
    for s in ALLOW:
        print(f"  {s:24s} {per[s]['example']}")


def timedelta_min(m):
    from datetime import timedelta
    return timedelta(minutes=m)


if __name__ == "__main__":
    sys.exit(main() or 0)
