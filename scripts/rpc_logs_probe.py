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
import os
import sys
from typing import Optional
import urllib.request

# keccak256("OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)")
TOPIC0 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"
# V1 exchanges — blockchain_client's constants, what the watcher queries
# today. WI-24 (WORK_PROGRAM, on-chain verified 2026-06-11) shows trading
# moved to the V2 exchanges; recent fills may not emit here at all.
EXCHANGE_V1 = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEGRISK_V1 = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
# V2 main exchange — full address in base_engine/execution/contract_manager.py:34
EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
# V2 NegRisk (0xe2222d279d…0F59) — full address only exists on the VPS in
# py_clob_client_v2; find_negrisk_v2() greps site-packages for it.
NEGRISK_V2_PREFIX = "0xe2222d279d"

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


def find_negrisk_v2(venv_lib: str = "/opt/polymarket-ai-v2/venv/lib") -> Optional[str]:
    """Grep the venv's py_clob_client(_v2) sources for the full NegRisk V2
    address (only its 0xe2222d279d… prefix is recorded in the repo)."""
    import re
    pat = re.compile(NEGRISK_V2_PREFIX[2:] + r"[0-9a-fA-F]{30}", re.I)
    for root, _dirs, files in os.walk(venv_lib):
        if "clob" not in root.lower():
            continue
        for fn in files:
            if not fn.endswith((".py", ".json")):
                continue
            try:
                with open(os.path.join(root, fn), errors="ignore") as f:
                    m = pat.search(f.read())
            except OSError:
                continue
            if m:
                return "0x" + m.group(0)
    return None


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

    exchanges = [("V1-main", EXCHANGE_V1), ("V1-negrisk", NEGRISK_V1),
                 ("V2-main", EXCHANGE_V2)]
    nr2 = find_negrisk_v2()
    if nr2:
        exchanges.append(("V2-negrisk", nr2))
        print(f"NegRisk V2 resolved from venv: {nr2}")
    else:
        print("NegRisk V2 NOT found in venv site-packages — testing 3 contracts")

    winner = None          # (rpc, exchange_label, addr)
    for rpc in [r.strip() for r in args.rpcs.split(",") if r.strip()]:
        try:
            head = int(call(rpc, "eth_blockNumber", [])["result"], 16)
        except Exception as e:
            print(f"\n{rpc}\n    eth_blockNumber ERROR {e!r}")
            continue
        s0, s1 = head - 100, head - 60          # settled 40 blocks
        print(f"\n{rpc}\n    head={head}  settled window [{s0},{s1}]")
        for label, addr in exchanges:
            n_settled, err_s = get_logs(rpc, s0, s1, addr, [TOPIC0])
            n_head, _ = get_logs(rpc, head - 6, head - 1, addr, [TOPIC0])
            print(f"    {label:<11} settled="
                  f"{n_settled if n_settled >= 0 else 'ERROR ' + err_s}"
                  f"  near-head={n_head if n_head >= 0 else 'ERR'}")
            list_ok = True
            if roster_topics is not None and n_settled > 0:
                nm, em = get_logs(rpc, s0, s1, addr,
                                  [TOPIC0, None, roster_topics])
                nt, et = get_logs(rpc, s0, s1, addr,
                                  [TOPIC0, None, None, roster_topics])
                list_ok = nm >= 0 and nt >= 0
                print(f"      16-addr list filter: maker="
                      f"{nm if nm >= 0 else 'ERROR ' + em}  taker="
                      f"{nt if nt >= 0 else 'ERROR ' + et}"
                      f"  ({'call shape OK' if list_ok else 'BROKEN'})")
            if winner is None and n_settled > 0 and list_ok:
                winner = (rpc, label, addr)

    print("\n" + "=" * 78)
    if winner:
        rpc, label, addr = winner
        print(f"  WINNER: {rpc}  via  {label} ({addr})")
        print("  READ: nonzero on V2 with zero on V1 = the watcher queries")
        print("  DEAD contracts — fix the watcher's exchange constants (and")
        print("  the topic0/event shape is confirmed compatible, since these")
        print("  counts are topic0-filtered). RPC itself may be fine.")
    else:
        print("  NO WINNER: no candidate returned settled OrderFilled logs on "
              "any contract — try paid endpoints before touching watcher code.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
