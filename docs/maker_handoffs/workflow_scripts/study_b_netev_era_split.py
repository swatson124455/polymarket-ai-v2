#!/usr/bin/env python3
"""STUDY B (read-only, $0): NETEV era-split for the gas/diesel families.

Question: are KXDIESELW / KXAAAGASW / KXAAAGASD net-negative on CLEAN-era data
(on/after 2026-08-10, the R0a fixed-build boundary), or only on launch-defect-era
data? NETEV_GATE currently blocks the diesel tails on its all-era family table.

Method (canonical models only):
  fill cash    kalshi_attribution_ledger.replay_fills over the FULL tape (the tape
               must replay from flat), then each event attributed to an era by its
               fill created_time; family by ticker prefix.
  settlements  kalshi_attribution_ledger.settlement_revenue per row, era by
               settled_time.
  credits      credit_history rows (KalshiOrderClient mode=live), event via the
               reward_pnl regex (for event <X>), family by event prefix, era by
               credit created_at (payment date; labeled).
"""
import datetime
import json
import re
import sys

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import kalshi_attribution_ledger as kal            # noqa: E402
from maker_kalshi_client import KalshiOrderClient  # noqa: E402

ERA = "2026-08-10T00:00:00Z"
FAMS = ("KXDIESELW", "KXAAAGASW", "KXAAAGASD")
EVENT_RE = re.compile(r"for event (\S+)")


def fam(t):
    return (t or "").split("-")[0]


print("read_ts:", kal.utcnow().isoformat(), " era_boundary:", ERA,
      " families:", ",".join(FAMS))

fills = kal.get_paginated("/trade-api/v2/portfolio/fills", "fills")
events, _pos = kal.replay_fills(fills)
buckets = {}


def bucket(family, era_key):
    return buckets.setdefault((family, era_key), {"fill_cash": 0.0, "n_fills": 0,
                                                  "sett_rev": 0.0, "n_setts": 0,
                                                  "credits": 0.0, "n_credits": 0})


for e in events:
    f = e["fill"]
    t = f.get("ticker") or f.get("market_ticker")
    if fam(t) not in FAMS:
        continue
    era_key = "clean" if (f.get("created_time") or "") >= ERA else "defect_era"
    b = bucket(fam(t), era_key)
    b["fill_cash"] += e["cash"]
    b["n_fills"] += 1

setts = kal.get_paginated("/trade-api/v2/portfolio/settlements", "settlements")
for s in setts:
    if fam(s.get("ticker")) not in FAMS:
        continue
    era_key = "clean" if (s.get("settled_time") or "") >= ERA else "defect_era"
    b = bucket(fam(s.get("ticker")), era_key)
    b["sett_rev"] += kal.settlement_revenue(s)
    b["n_setts"] += 1

credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
for c in credits:
    m = EVENT_RE.search(c.get("reason") or "")
    if not m or fam(m.group(1)) not in FAMS:
        continue
    era_key = "clean" if (c.get("created_at") or "") >= ERA else "defect_era"
    b = bucket(fam(m.group(1)), era_key)
    b["credits"] += (c.get("amount_cents") or 0) / 100.0
    b["n_credits"] += 1

print("%-11s %-10s | fills n/cash      | setts n/rev     | credits n/$   | NET"
      % ("family", "era"))
tot = {}
for (family, era_key), b in sorted(buckets.items()):
    net = b["fill_cash"] + b["sett_rev"] + b["credits"]
    tot.setdefault(era_key, 0.0)
    tot[era_key] += net
    print("%-11s %-10s | %4d  %+9.4f | %3d  %+9.4f | %2d  %+8.2f | %+9.4f"
          % (family, era_key, b["n_fills"], b["fill_cash"], b["n_setts"],
             b["sett_rev"], b["n_credits"], b["credits"], net))
print("ERA TOTALS (all three families):",
      {k: round(v, 4) for k, v in tot.items()})
json.dump({str(k): v for k, v in buckets.items()},
          open("/tmp/STUDY_B_ERA_SPLIT.json", "w"), indent=1)
print("wrote /tmp/STUDY_B_ERA_SPLIT.json")
