#!/usr/bin/env python3
"""Which Polygon RPC actually serves OrderFilled logs? — stdlib shootout.

CONTEXT (2026-07-12): diagnose_watcher_detection.py returned ZERO OrderFilled
events on https://polygon.gateway.tenderly.co everywhere — unfiltered, settled
windows, and around six fills the data-api proves happened. Polymarket fills
constantly; a zero unfiltered count over 200 blocks is not quiet traders, it
is the gateway answering eth_getLogs with [] instead of data. The 505-fill
chain audit that DID work ran on a different endpoint. The watcher has been
blind since deploy.

THIS SCRIPT (pure stdlib urllib — runs with system python3, no venv):
per candidate RPC:
  1. eth_blockNumber
  2. unfiltered OrderFilled count on the MAIN exchange over a settled
     40-block window [head-100, head-60]  → nonzero = the RPC serves logs
  3. same, near-head [head-6, head-1]     → measures head indexing lag
  4. (--roster) the SAME settled window filtered by the 16 roster
     addresses as an OR topic list (maker=topic2, taker=topic3) → proves
     multi-address list filters work on that endpoint (the watcher's
     query shape). Roster counts are usually 0 in a 40-block window —
     what matters is that the call SUCCEEDS; the unfiltered count is the
     data check.
Topic0 is keccak256 of the OrderFilled signature from
base_engine/data/blockchain_client.ORDER_FILLED_EVENT_ABI (precomputed —
no web3 import needed; the audit's 505 verified fills pin the ABI as
correct for the deployed contracts).

READ-ONLY: JSON-RPC GETs/POSTs only. Prints a WINNER line: first RPC with
nonzero settled count, no getLogs error, and a working list filter.

INVOCATION (VPS):
    python3 scripts/rpc_logs_probe.py \
        --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

# keccak256("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
TOPIC0 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"          # main CTF exchange
NEGRISK = "0xC5d563A36AE78145C45a50134d48A1215220f80a"           # NegRisk exchange

DEFAULT_RPCS = [
    "https://polygon.gateway.tenderly.co",   # current .env.mirror3 (suspect)
    "https://polygon-rpc.com",               # audit example endpoint
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-bor-rpc.publicnode.com",  # known: 403s getLogs (control)
]


def call(url: str, method: str, params: list, timeout_s: float = 20.0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "PolymarketAI/1.0 (https://github.com; data)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def get_logs(url: str, b0: int, b1: int, address: str,
             topics: list) -> tuple[int, str]:
    """(count, note). count=-1 on error, note carries the error."""
    try:
        res = call(url, "eth_getLogs", [{
            "fromBlock": hex(b0), "toBlock": hex(b1),
            "address": address, "topics": topics}])
    except Exception as e:
        return -1, repr(e)
    if "error" in res:
        return -1, str(res["error"])
    return len(res.get("result", [])), ""


def pad_addr(a: str) -> str:
    return "0x" + "0" * 24 + a.lower().replace("0x", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Polygon RPC getLogs shootout")
    ap.add_argument("--roster", default="",
                    help="chain_audit.json; enables the list-filter check")
    ap.add_argument("--rpcs", default=",".join(DEFAULT_RPCS),
                    help="comma-separated candidate endpoints")
    args = ap.parse_args()

    roster_topics = None
    if args.roster:
        with open(args.roster) as f:
            roster = [str(a).lower() for a in json.load(f).get("clean", [])]
        roster_topics = [pad_addr(a) for a in roster]
        print(f"roster list-filter check enabled ({len(roster)} addresses)")

    winner = None
    for rpc in [r.strip() for r in args.rpcs.split(",") if r.strip()]:
        try:
            head = int(call(rpc, "eth_blockNumber", [])["result"], 16)
        except Exception as e:
            print(f"\n{rpc}\n    eth_blockNumber ERROR {e!r}")
            continue
        s0, s1 = head - 100, head - 60          # settled 40 blocks
        n_settled, err_s = get_logs(rpc, s0, s1, EXCHANGE, [TOPIC0])
        n_head, err_h = get_logs(rpc, head - 6, head - 1, EXCHANGE, [TOPIC0])
        print(f"\n{rpc}\n    head={head}")
        print(f"    settled [{s0},{s1}]: "
              f"{n_settled if n_settled >= 0 else 'ERROR ' + err_s}")
        print(f"    near-head [head-6,head-1]: "
              f"{n_head if n_head >= 0 else 'ERROR ' + err_h}")
        list_ok = True
        if roster_topics is not None and n_settled > 0:
            nm, em = get_logs(rpc, s0, s1, EXCHANGE,
                              [TOPIC0, None, roster_topics])
            nt, et = get_logs(rpc, s0, s1, EXCHANGE,
                              [TOPIC0, None, None, roster_topics])
            list_ok = nm >= 0 and nt >= 0
            print(f"    16-addr list filter: maker="
                  f"{nm if nm >= 0 else 'ERROR ' + em}  taker="
                  f"{nt if nt >= 0 else 'ERROR ' + et}"
                  f"  ({'call shape OK' if list_ok else 'BROKEN'})")
        if winner is None and n_settled > 0 and list_ok:
            winner = rpc

    print("\n" + "=" * 78)
    if winner:
        print(f"  WINNER: {winner}")
        print("  Fix: set MIRROR3_RPC_URL to the winner in "
              "/opt/pa2-shared/.env.mirror3 and restart polymarket-mirror3.")
        print("  (A zero near-head count with a nonzero settled count means "
              "the winner also lags at head — the watcher's canary + retry "
              "absorb that; only a settled-zero endpoint is disqualifying.)")
    else:
        print("  NO WINNER: no candidate returned settled OrderFilled logs — "
              "try paid endpoints (Alchemy/Infura key) before touching the "
              "watcher code.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
