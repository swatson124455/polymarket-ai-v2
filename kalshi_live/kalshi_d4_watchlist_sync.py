#!/usr/bin/env python3
"""D4 watchlist sync — point the always-on book recorder at what matters TODAY.

Operator directive 2026-08-24 ("watching from the dark"): the D4 recorder ran on a
stale hand-picked list. This generates d4_tickers.json (cap 40, the recorder's
sweep budget) every run from three pools, in priority order:
  1. FOOTPRINT — allowlist-class markets with open books and future close
     (what we are in or may enter),
  2. CANDIDATES — remaining allowlist-class actives by pool desc,
  3. PAROLE — evicted classes under review (recover markets disposed of too
     early; operator ruling #4, 2026-08-24).
Read-only; atomic tmp+rename write; the recorder re-reads the file every sweep.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import kalshi_attribution_ledger as kal

ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTOPMODEL", "KXCLAYTONDNI",
         "KXDIESELW", "KXCLARITYVOTE")
PAROLE = ("KXUSDJPY",)
PAROLE_SLOTS = 8
CAP = 40
OUT = "/opt/pa2-maker-kalshi-live/d4_tickers.json"


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    progs, cur = [], ""
    for _ in range(50):
        q = kal.P + "/incentive_programs?status=active&limit=10000" + (
            f"&cursor={cur}" if cur else "")
        d = kal.get(q)
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or d.get("cursor") or ""
        if not cur:
            break
    rows = {}
    for p in progs:
        t = p.get("market_ticker") or ""
        c = t.split("-")[0]
        if c not in ALLOW + PAROLE:
            continue
        pool = (p.get("period_reward") or 0) / 10000.0
        end = p.get("end_date") or ""
        r = rows.setdefault(t, {"cls": c, "pool": 0.0, "end": end})
        r["pool"] = max(r["pool"], pool)
    allow_rows = sorted((t for t, r in rows.items() if r["cls"] in ALLOW),
                        key=lambda t: (-rows[t]["pool"], rows[t]["end"], t))
    parole_rows = sorted((t for t, r in rows.items() if r["cls"] in PAROLE),
                         key=lambda t: (-rows[t]["pool"], t))[:PAROLE_SLOTS]
    picked = (allow_rows[:CAP - len(parole_rows)] + parole_rows)[:CAP]
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(picked, f, indent=1)
    os.replace(tmp, OUT)
    print(f"{now.isoformat()} d4 watchlist: {len(picked)} tickers "
          f"({len(picked) - len(parole_rows)} allowlist + {len(parole_rows)} parole) "
          f"of {len(rows)} eligible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
