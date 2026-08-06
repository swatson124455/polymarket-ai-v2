#!/usr/bin/env python3
"""0a standing recorder — the venue's PER-USER reward-estimate feed, sampled on a timer.

Discovered 2026-08-06 (session evidence, frontend bundle 40naw_64skudx.js):
  GET {exchangeApiUrl}/v1/incentives/users/{user_id}/estimates
returns [{program_id, reward_centicents}] — the EXACT data behind the web UI's
per-market reward chip (queryKey ["incentiveEstimates", userId], 60s refetch,
units 10000 centicents = $1, same scale as period_reward/10000 pool canon).
Verified with the bot's own key 2026-08-06T03:25:29Z: 63 rows, $5.5793 total.

What the time series settles (the questions 0a left open):
  * update cadence (two reads 5 min apart were byte-identical -> batch recompute)
  * cumulative-accrual vs instantaneous-projection (monotonicity within a program)
  * estimate -> credit mapping at payout (incl. whether the $1 credit floor and
    final pool scaling are already inside the estimate) — join vs credit_history
  * FIX-H payment timing confirmation on 2026-08-09 program end (KXTOPMODEL-26AUG31,
    KXCHIPBURRITO-26SEP02)

Appends {"ts", "estimates"} to estimates-YYYYMM.jsonl (raw venue rows, no
interpretation at write time). Also maintains kalshi_program_map.json, a
MERGE-ONLY program_id -> {market_ticker, end_date, period_reward} cache so rows
from programs that have since left the active list stay resolvable.

READ-ONLY against the venue. Run by kalshi-estimates-recorder.timer (5 min).
"""
import datetime as dt
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from maker_kalshi_client import KalshiOrderClient, CREDIT_BASE, CREDIT_USER_ID  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
MAP_PATH = os.path.join(HERE, "kalshi_program_map.json")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    c = KalshiOrderClient(mode="live")
    est = c._request("GET", f"/v1/incentives/users/{CREDIT_USER_ID}/estimates",
                     base=CREDIT_BASE).get("estimates") or []

    out = os.path.join(HERE, f"estimates-{now:%Y%m}.jsonl")
    with open(out, "a") as fh:
        fh.write(json.dumps({"ts": now.isoformat(),
                             "estimates": est}, separators=(",", ":")) + "\n")

    # merge-only program map: ended programs leave the active feed but their
    # estimate rows persist until payout — the map must remember them.
    try:
        pmap = json.load(open(MAP_PATH))
    except Exception:
        pmap = {}
    progs, cursor = [], ""
    try:
        for _ in range(5):
            d = json.load(urllib.request.urlopen(
                BASE + "/incentive_programs?status=active&limit=10000"
                + (f"&cursor={cursor}" if cursor else ""), timeout=30))
            progs += d.get("incentive_programs") or []
            cursor = d.get("next_cursor") or ""
            if not cursor:
                break
        for p in progs:
            pid = p.get("id")
            if pid:
                pmap[pid] = {"market_ticker": p.get("market_ticker"),
                             "end_date": p.get("end_date"),
                             "period_reward": p.get("period_reward")}
        json.dump(pmap, open(MAP_PATH, "w"))
    except Exception as e:
        # map refresh is best-effort; the raw estimate rows are the record
        print(f"program-map refresh failed (rows still recorded): {e}")

    total = sum(e.get("reward_centicents") or 0 for e in est)
    unmapped = sum(1 for e in est if e.get("program_id") not in pmap)
    print(f"estimates {len(est)} rows, total {total} cc = ${total/10000.0:.4f}, "
          f"unmapped {unmapped}, map {len(pmap)} programs")


if __name__ == "__main__":
    main()
