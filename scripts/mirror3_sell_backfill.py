#!/usr/bin/env python3
"""One-time SELL-sink backfill for the 2026-09-06 permission outage.

WHY: the 03:16Z sell-sink migration left the file root-owned; the watcher
(service user polymarket) got PermissionError on every roster SELL append
until the 15:33Z chown — 806 failed writes logged. The events are on
chain; this replays the outage window with the WATCHER'S OWN primitives
(decode_fill_v2 / merge_same_tx / side_from_receipt_logs / sell_record —
imported, never re-implemented) and appends the missing records.

METHOD:
  1. eth_getLogs over the window (900-block chunks, both V2 exchanges,
     roster as topic-2 — the watcher's exact query shape).
  2. decode + same-tx merge, exactly as live.
  3. RECALL pre-filter: data word[0] == 1 (chain-verified SELL flag on
     txs 0x0f422cdb + 0x31f7d3b9 — 2-tx evidence, so it is only a
     pre-filter). PRECISION: every candidate is receipt-confirmed via
     side_from_receipt_logs before it is written — no false SELLs.
     w0-vs-receipt concordance is measured and printed.
  4. Dedup against the existing sink by (tx, trader, token).
  5. Records carry backfill_20260906=true and detect_ts = BLOCK time
     (detection time is unknowable retroactively — disclosed).

Appends only (never creates/replaces the sink — the ownership lesson);
refuses to run if the sink is missing. Dry-run by default; --write to
append.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "mirror_v3"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))
from mirror_v3.copy_watcher import (  # noqa: E402
    EXCHANGE_V2, FILL_TOPIC_V2, NEGRISK_EXCHANGE_V2, WatcherConfig,
    _hex, _words, addr_topic, decode_fill_v2, load_roster, rpc_call,
    sell_record, side_from_receipt_logs)
from mirror_v3.sizing import merge_same_tx  # noqa: E402

SINK = "/opt/pa2-shared/mirror3_shadow_sells.jsonl"
CHUNK = 900


async def block_at(w3, target_ts: int, lo: int, hi: int) -> int:
    """Smallest block with timestamp >= target_ts (bisection)."""
    while lo < hi:
        mid = (lo + hi) // 2
        blk = await rpc_call(w3.eth.get_block(mid))
        if int(blk["timestamp"]) < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


async def run(args) -> int:
    if not os.path.exists(SINK):
        print(f"FATAL: sink missing ({SINK}) — backfill only appends")
        return 2
    existing = set()
    with open(SINK) as f:
        for ln in f:
            try:
                r = json.loads(ln)
                existing.add((r.get("tx"), r.get("trader"),
                              r.get("token_id")))
            except ValueError:
                continue
    print(f"sink holds {len(existing)} existing (tx,trader,token) keys")

    env = {k: v for k, v in os.environ.items()}
    cfg = WatcherConfig.from_env(env)
    roster = load_roster(cfg.roster_path)
    roster_set = set(roster)
    from web3 import AsyncWeb3
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(cfg.rpc_url))
    # Polygon RPCs return oversized extraData — same POA middleware the
    # production blockchain_client injects (base_engine/data)
    from web3.middleware import ExtraDataToPOAMiddleware
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    t0 = int(datetime.fromisoformat(args.frm.replace("Z", "+00:00"))
             .timestamp())
    t1 = int(datetime.fromisoformat(args.to.replace("Z", "+00:00"))
             .timestamp())
    head = await rpc_call(w3.eth.get_block("latest"))
    head_n, head_ts = int(head["number"]), int(head["timestamp"])
    # Polygon ~2s blocks: seed the bisection bracket generously
    span = max(int((head_ts - t0) / 1.8) + 5000, 10)
    b0 = await block_at(w3, t0, max(head_n - span, 0), head_n)
    b1 = await block_at(w3, t1, b0, head_n) - 1
    print(f"window {args.frm}..{args.to} -> blocks {b0}..{b1} "
          f"({b1 - b0 + 1} blocks)")

    topics = [FILL_TOPIC_V2, None, [addr_topic(a) for a in roster]]
    decoded: list[dict] = []
    n_events = 0
    lo = b0
    while lo <= b1:
        hi = min(lo + CHUNK - 1, b1)
        logs = await rpc_call(w3.eth.get_logs({
            "address": [EXCHANGE_V2, NEGRISK_EXCHANGE_V2],
            "topics": topics, "fromBlock": lo, "toBlock": hi}))
        for lg in logs:
            n_events += 1
            d = dict(lg)
            sig = decode_fill_v2(d, roster_set)
            if sig is None:
                continue
            sig["tx"] = _hex(d.get("transactionHash", ""))
            sig["_block"] = int(d.get("blockNumber", 0))
            sig["_w0"] = _words(d.get("data", "0x"))[0]
            decoded.append(sig)
        lo = hi + 1
    print(f"{n_events} roster fill events, {len(decoded)} decoded")

    merged = merge_same_tx(decoded)
    cand = [s for s in merged if s.get("_w0") == 1]
    n_w0_buy = sum(1 for s in merged if s.get("_w0") == 0)
    print(f"{len(merged)} merged wagers: w0==1 (SELL candidates) "
          f"{len(cand)}, w0==0 {n_w0_buy}")

    concord = {"agree": 0, "disagree": 0, "unknown": 0}
    new_records = []
    for sig in cand:
        key = (sig["tx"], sig["trader"], sig["token_id"])
        if key in existing:
            continue
        try:
            rcpt = await rpc_call(
                w3.eth.get_transaction_receipt(sig["tx"]))
            side = side_from_receipt_logs(
                [dict(lg) for lg in rcpt["logs"]],
                sig["trader"], sig["token_id"])
        except Exception as e:
            print(f"  receipt error {sig['tx'][:18]}…: {e!r} — SKIPPED "
                  f"(precision over recall)")
            concord["unknown"] += 1
            continue
        if side == "SELL":
            concord["agree"] += 1
            blk = await rpc_call(w3.eth.get_block(sig["_block"]))
            rec = sell_record(sig, float(int(blk["timestamp"])))
            rec["backfill_20260906"] = True
            rec["block_ts_is_detect_ts"] = True
            new_records.append(rec)
        elif side is None:
            concord["unknown"] += 1
        else:
            concord["disagree"] += 1
    print(f"w0-vs-receipt on candidates: agree={concord['agree']} "
          f"disagree={concord['disagree']} unknown={concord['unknown']}")
    print(f"{len(new_records)} SELL records to append "
          f"(receipt-confirmed, deduped)")
    for r in new_records[:3]:
        print("  sample:", json.dumps(r)[:160])
    if not args.write:
        print("\nDRY RUN — sink untouched. Re-run with --write to append.")
        return 0
    if not new_records:
        print("nothing to append")
        return 0
    with open(SINK, "a") as f:   # append-only: ownership untouched
        for r in new_records:
            f.write(json.dumps(r) + "\n")
    print(f"APPENDED {len(new_records)} records to {SINK}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="backfill lost roster SELLs")
    ap.add_argument("--from", dest="frm", required=True,
                    help="window start ISO Z (outage start)")
    ap.add_argument("--to", dest="to", required=True,
                    help="window end ISO Z (chown time)")
    ap.add_argument("--write", action="store_true")
    raise SystemExit(asyncio.run(run(ap.parse_args())))
