#!/usr/bin/env python3
"""Prove the watcher's EXACT production query returns known real fills.

WHY (2026-07-12): the redeployed watcher's canary verified UNFILTERED V2
fill detection (25k events/300 blocks), but the production query adds a
16-address topic-2 OR-list — a shape some gateways mishandle by returning
[] without error, and one that tonight's probes never actually exercised
against a live event. This script replays the exact watcher filter over
the block range containing four receipt-verified roster fills (traced
2026-07-12, txs pinned below). Finding them = the full production path is
proven end-to-end. Not finding them = the list filter is broken on this
endpoint and the watcher needs per-address queries.

STDLIB ONLY, READ-ONLY.

INVOCATION (VPS):
    python3 scripts/verify_roster_filter.py \
        --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json
"""
from __future__ import annotations

import argparse
import json
import urllib.request

FILL_TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEGRISK_V2 = "0xe2222d279d744050d28e00520010520000310F59"
UA = "PolymarketAI/1.0 (https://github.com; data)"
CHUNK = 900

# Receipt-verified roster fills from 2026-07-12 (scripts/trace_real_fill.py):
KNOWN = {
    "0xee0355b05a2168184f37e0309cc1139947820345fe55910e29f4393c77acf523": 90126118,
    "0x8ffb45ae60d807a56f68a1b2164ab0b94c26eb370edc9e85a9dcabe0c995bcda": 90125826,
    "0x264a319beba7a160f44603ac5ff5a85bdc5bbca90bad4b7fc82b8a6f26dfb5e7": 90121681,
    "0xe463a4c01b37fdeaac7c2e69e177e9e4d3c53b3f1906e62b3d330a039f90ef54": 90118820,
}


def rpc(url, method, params, timeout_s=30.0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def pad(a: str) -> str:
    return "0x" + "0" * 24 + a.lower().replace("0x", "")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Replay the watcher's roster filter over known fills")
    ap.add_argument("--roster", required=True)
    ap.add_argument("--rpc", default="https://polygon.gateway.tenderly.co")
    args = ap.parse_args()

    with open(args.roster) as f:
        roster = [str(a).lower() for a in json.load(f).get("clean", [])]
    topics = [FILL_TOPIC, None, [pad(a) for a in roster]]

    b0 = min(KNOWN.values()) - 5
    b1 = max(KNOWN.values()) + 5
    print(f"watcher-shape query: 2 exchanges, {len(roster)}-addr topic2 list, "
          f"blocks [{b0},{b1}] ({b1 - b0} blocks, chunked {CHUNK})")
    found_tx = set()
    n_total = 0
    lo = b0
    while lo <= b1:
        hi = min(lo + CHUNK - 1, b1)
        res = rpc(args.rpc, "eth_getLogs", [{
            "fromBlock": hex(lo), "toBlock": hex(hi),
            "address": [EXCHANGE_V2, NEGRISK_V2], "topics": topics}])
        if "error" in res:
            print(f"  [{lo},{hi}] ERROR: {res['error']}")
        else:
            logs = res.get("result", [])
            n_total += len(logs)
            for lg in logs:
                found_tx.add(str(lg.get("transactionHash", "")).lower())
        lo = hi + 1

    hits = {tx: (tx in found_tx) for tx in KNOWN}
    per = {a: 0 for a in roster}
    print(f"\n  roster-filtered fill events found: {n_total}")
    for tx, blk in KNOWN.items():
        print(f"  known fill @block {blk}: "
              f"{'FOUND' if hits[tx] else 'MISSING'}  {tx[:22]}…")
    n_hit = sum(hits.values())
    print("\n" + "=" * 70)
    if n_hit == len(KNOWN):
        print("  PASS: the exact production filter returns every known fill.")
        print("  The watcher's detection path is proven end-to-end.")
    elif n_hit == 0 and n_total == 0:
        print("  FAIL: list filter returns NOTHING over blocks with verified")
        print("  fills — the gateway mishandles the 16-address topic list.")
        print("  Watcher needs per-address queries (or a different RPC).")
    else:
        print(f"  PARTIAL ({n_hit}/{len(KNOWN)}): investigate before trusting"
              " — paste this output back.")
    print("=" * 70)
    return 0 if n_hit == len(KNOWN) else 1


if __name__ == "__main__":
    raise SystemExit(main())
