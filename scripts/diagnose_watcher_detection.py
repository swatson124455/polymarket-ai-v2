#!/usr/bin/env python3
"""Why is the shadow watcher blind? — one-shot detection diagnostic.

CONTEXT (2026-07-12): the copy watcher logged ZERO shadow records in ~33h
while the data-api shows the CLEAN roster made 179 BUYs in 40h (one 0.3h
before the check). Startup is clean (roster=16, medians seeded). So
detection silently misses real events. Two candidate mechanisms, both
invisible to the retry-don't-skip guard (which only catches ERRORS):

  A) HEAD-RACE SILENT EMPTIES — the watcher queries blocks the moment
     they are announced; if the gateway's log index lags its head, those
     queries return [] WITHOUT error, the cursor advances, the window is
     never re-read. Systematic loss of everything.
  B) LIST-FILTER FAILURE — the audit (which verified 505 fills) always
     filtered by ONE address; the watcher passes all 16 as a topic list.
     A gateway that mishandles multi-address topic filters returns []
     silently.

THIS SCRIPT (read-only RPC + public data-api GETs, no writes anywhere):
  probe 1  unfiltered OrderFilled counts in 5-block windows at increasing
           distance from head (0, 5, 10, 20, 50, 100 blocks back).
           Zero near head + nonzero deeper = A confirmed (prints the
           measured indexing-lag depth).
  probe 2  over one deeper, settled window (~200 blocks): unfiltered
           count vs 16-address LIST filter count vs sum of PER-ADDRESS
           counts. single_sum > 0 with list == 0 = B confirmed.
  probe 3  ground truth: newest data-api trades for the roster (the same
           fills the activity probe saw), block located per timestamp,
           then single-address filtered query ±40 blocks around each.
           NOT FOUND here = the address/event model itself is wrong at
           the head (would contradict the audit) — escalate, don't patch.
  verdict  prints which mechanism(s) the evidence supports and the fix
           each one implies. Applies NO fix itself.

INVOCATION (VPS, from a /tmp clone; ~2-4 min):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbre \
      venv/bin/python /tmp/mbre/scripts/diagnose_watcher_detection.py \
      --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_roster_chain as ac  # noqa: E402  (audited helpers, reused as-is)

DATA_API = "https://data-api.polymarket.com/activity"
UA = "PolymarketAI/1.0 (https://github.com; data)"


def newest_api_trades(roster: list[str], hours: float,
                      timeout_s: float = 15.0) -> list[dict]:
    """Newest data-api TRADE per roster address inside the window."""
    out = []
    for a in roster:
        url = DATA_API + "?" + urllib.parse.urlencode(
            {"user": a, "limit": 10, "type": "TRADE"})
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                rows = json.load(r)
        except Exception as e:
            print(f"    data-api error {a[:12]}…: {e!r}", file=sys.stderr)
            continue
        trades = [t for t in (rows if isinstance(rows, list) else [])
                  if str(t.get("type", "")).upper() == "TRADE"
                  and t.get("timestamp") is not None]
        if not trades:
            continue
        newest = max(trades, key=lambda t: float(t["timestamp"]))
        if time.time() - float(newest["timestamp"]) < hours * 3600:
            out.append({"addr": a, "ts": float(newest["timestamp"]),
                        "side": newest.get("side")})
        time.sleep(0.3)
    return sorted(out, key=lambda t: -t["ts"])


async def run(args) -> int:
    from base_engine.data.blockchain_client import (
        BlockchainClient, EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT,
        ORDER_FILLED_EVENT_ABI)
    from web3 import Web3

    with open(args.roster) as f:
        roster = [str(a).lower() for a in json.load(f).get("clean", [])]
    assert roster, "no clean[] addresses"
    roster_cs = [Web3.to_checksum_address(a) for a in roster]

    bc = BlockchainClient(rpc_url=args.rpc_url)
    await bc.ensure_client()
    contracts = [bc.w3.eth.contract(
        address=Web3.to_checksum_address(c), abi=[ORDER_FILLED_EVENT_ABI])
        for c in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT))]

    async def count_events(b0: int, b1: int, filters: Optional[dict]) -> int:
        n = 0
        for c in contracts:
            if filters is None:
                try:
                    evs = await ac.get_logs_compat(
                        c.events.OrderFilled, b0, b1, {})
                    n += len(evs)
                except Exception as e:
                    print(f"      RPC error [{b0},{b1}] unfiltered: {e!r}")
                await asyncio.sleep(1.0 / args.rps)
            else:
                for field, vals in filters.items():
                    try:
                        evs = await ac.get_logs_compat(
                            c.events.OrderFilled, b0, b1, {field: vals})
                        n += len(evs)
                    except Exception as e:
                        print(f"      RPC error [{b0},{b1}] {field}: {e!r}")
                    await asyncio.sleep(1.0 / args.rps)
        return n

    head = int(await bc.w3.eth.get_block_number())
    print(f"head={head} rpc={args.rpc_url}\n")

    print("PROBE 1 — unfiltered OrderFilled counts near the head "
          "(zero near head + nonzero deeper = silent indexing lag = cause A)")
    lag_depth = None
    p1 = {}
    for back in (0, 5, 10, 20, 50, 100):
        b1 = head - back
        n = await count_events(b1 - 4, b1, None)
        p1[back] = n
        print(f"    blocks [{b1 - 4},{b1}] (head-{back}): {n} events")
    deeper_active = any(p1[b] > 0 for b in (20, 50, 100))
    if deeper_active:
        for back in (0, 5, 10, 20, 50, 100):
            if p1[back] > 0:
                lag_depth = back
                break

    print("\nPROBE 2 — settled window, list filter vs per-address "
          "(single_sum>0 with list=0 = cause B)")
    w1 = head - args.settle_back
    w0 = w1 - args.settle_span
    n_unf = await count_events(w0, w1, None)
    n_list = await count_events(
        w0, w1, {"maker": roster_cs, "taker": roster_cs})
    n_single = 0
    for a_cs in roster_cs:
        n_single += await count_events(w0, w1, {"maker": a_cs, "taker": a_cs})
    print(f"    window [{w0},{w1}] ({args.settle_span} blocks): "
          f"unfiltered={n_unf}  list16={n_list}  single_sum={n_single}")

    print("\nPROBE 3 — hunt the roster's newest REAL fills "
          "(from the data-api; NOT FOUND = address model broken at head)")
    recents = newest_api_trades(roster, args.hours)[:args.max_hunt]
    found = missed = 0
    latest_blk = await bc.w3.eth.get_block(head)
    latest_num, latest_ts = int(latest_blk["number"]), int(latest_blk["timestamp"])
    anchors: list[tuple[int, int]] = []

    async def _get_block_ts(b: int) -> int:
        await asyncio.sleep(1.0 / args.rps)
        return int((await bc.w3.eth.get_block(b))["timestamp"])

    for t in recents:
        center = await ac.locate_block_by_ts(
            int(t["ts"]), _get_block_ts, latest_num, latest_ts, anchors,
            tol_s=60)
        if center is None:
            print(f"    {t['addr'][:12]}…  block locate failed for ts={t['ts']}")
            continue
        a_cs = Web3.to_checksum_address(t["addr"])
        n = await count_events(center - 40, center + 40,
                               {"maker": a_cs, "taker": a_cs})
        ok = n > 0
        found += ok
        missed += (not ok)
        age_h = (time.time() - t["ts"]) / 3600
        print(f"    {t['addr'][:12]}…  {t['side']} {age_h:.1f}h ago, "
              f"block~{center}: {'FOUND ' + str(n) + ' events' if ok else 'NOT FOUND'}")

    print("\n" + "=" * 78)
    print("  DIAGNOSIS")
    if deeper_active and p1[0] == 0 and lag_depth and lag_depth > 0:
        print(f"  A CONFIRMED: logs invisible until ~{lag_depth} blocks behind "
              f"head (~{lag_depth * 2.1:.0f}s). The watcher's cursor outruns the")
        print("  index and silently drops everything. FIX: query only up to "
              "head - CONFIRM_BLOCKS (env, default > measured lag).")
    elif p1[0] > 0:
        print("  A NOT SUPPORTED: events are visible at the head in this run "
              "(lag may be intermittent — rerun a few times before ruling out).")
    else:
        print("  A INCONCLUSIVE: no events even 100 blocks back in 5-block "
              "windows this run — widen --settle-span or rerun at a busier time.")
    if n_single > 0 and n_list == 0:
        print("  B CONFIRMED: per-address filters return events, the 16-address")
        print("  LIST filter returns none. FIX: loop per-address (or chunk the "
              "topic list) in the watcher's get_logs calls.")
    elif n_list >= n_single and n_list > 0:
        print("  B NOT SUPPORTED: the list filter returns at least as much as "
              "per-address queries.")
    else:
        print(f"  B INCONCLUSIVE this run (list={n_list}, single_sum={n_single} "
              "— roster may simply not have traded in the settled window).")
    if missed and not found:
        print("  MODEL ALARM: known real fills NOT FOUND even single-filtered —")
        print("  contradicts the audit's address model. STOP; escalate to the "
              "operator before changing the watcher.")
    elif found:
        print(f"  ground truth reachable: {found}/{found + missed} known fills "
              "found on-chain via single-address filters.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Diagnose why the shadow watcher detects nothing")
    ap.add_argument("--roster", required=True)
    ap.add_argument("--rpc-url", default="https://polygon.gateway.tenderly.co",
                    dest="rpc_url")
    ap.add_argument("--hours", type=float, default=40.0,
                    help="data-api recency window for probe 3")
    ap.add_argument("--max-hunt", type=int, default=6, dest="max_hunt",
                    help="newest real fills to hunt in probe 3")
    ap.add_argument("--settle-back", type=int, default=300, dest="settle_back",
                    help="probe 2 window ends this many blocks behind head")
    ap.add_argument("--settle-span", type=int, default=200, dest="settle_span")
    ap.add_argument("--rps", type=float, default=4.0)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))
