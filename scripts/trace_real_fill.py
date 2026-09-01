#!/usr/bin/env python3
"""Trace a KNOWN Polymarket fill to its on-chain receipt — no guessing.

WHY (2026-07-12): every getLogs probe returned zero OrderFilled events on
BOTH the V1 and V2 exchange contracts across multiple RPCs, while the
data-api shows the roster filling constantly. Either the V2 exchanges emit
a different event signature (our topic0 filter is blind to it) or trades
no longer settle on-chain per-fill at all (pUSD internal ledger). Instead
of guessing signatures, this script takes trades the data-api PROVES
happened, reads their transactionHash, and fetches the receipts: the
receipt shows exactly which contract addresses and which event topics a
real Polymarket fill touches today — or that the tx does not exist on
chain, which would close the question the other way.

OUTPUT per traced fill: tx status, every log's contract address and
topic0 (labeled when known: OrderFilled v1, ERC-1155 transfers, ERC-20
Transfer, CTF split/merge, ...), topic count and data size — everything
needed to point the watcher at the right (contract, event) pair next.

STDLIB ONLY, READ-ONLY (data-api GETs + JSON-RPC). System python3.

INVOCATION (VPS):
    python3 scripts/trace_real_fill.py \
        --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json \
        --rpc https://polygon.gateway.tenderly.co
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

DATA_API = "https://data-api.polymarket.com/activity"
UA = "PolymarketAI/1.0 (https://github.com; data)"

KNOWN_TOPICS = {
    "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6":
        "OrderFilled (V1 sig)",
    "0x63bf4d16b7fa898ef4c4b2b6d90fd201e9c56313b65638af6088d149d2ce956c":
        "OrdersMatched (V1 sig)",
    "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62":
        "ERC1155 TransferSingle",
    "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb":
        "ERC1155 TransferBatch",
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
        "ERC20 Transfer",
    "0x2e6bb91f8cbcda0c93623c54d0403a43514fabc40084ec96b6d5379a74786298":
        "CTF PositionSplit",
    "0x6f13ca62553fcc2bcd2372180a43949c1e4cebba603901ede2f4e14f36b282ca":
        "CTF PositionsMerge",
    "0xacffcc86834d0f1a64b0d5a675798deed6ff0bcfc2231edd3480e7288dba7ff4":
        "FeeCharged",
}
KNOWN_ADDR = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e": "V1 main exchange",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a": "V1 negrisk exchange",
    "0xe111180000d2663c0091e4f400237545b87b996b": "V2 main exchange",
    "0x4d97dcd97ec945f40cf65f87097ace5ea0476045": "ConditionalTokens (CTF)",
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
}


def api_json(url: str, timeout_s: float = 15.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def rpc_call(url: str, method: str, params: list, timeout_s: float = 20.0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def newest_txs(roster: list[str], hours: float, max_txs: int) -> list[dict]:
    """Newest data-api TRADEs with tx hashes across the roster."""
    rows = []
    for a in roster:
        url = DATA_API + "?" + urllib.parse.urlencode(
            {"user": a, "limit": 10, "type": "TRADE"})
        try:
            acts = api_json(url)
        except Exception as e:
            print(f"    data-api error {a[:12]}…: {e!r}", file=sys.stderr)
            continue
        for t in (acts if isinstance(acts, list) else []):
            if str(t.get("type", "")).upper() != "TRADE":
                continue
            ts = t.get("timestamp")
            if ts is None or time.time() - float(ts) > hours * 3600:
                continue
            rows.append({"addr": a, "ts": float(ts),
                         "tx": (t.get("transactionHash") or "").strip(),
                         "side": t.get("side"), "price": t.get("price"),
                         "size": t.get("size")})
        time.sleep(0.3)
    rows.sort(key=lambda r: -r["ts"])
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["tx"] and r["tx"] not in seen:
            seen.add(r["tx"])
            out.append(r)
        if len(out) >= max_txs:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Receipt-trace known Polymarket fills")
    ap.add_argument("--roster", required=True)
    ap.add_argument("--rpc", default="https://polygon.gateway.tenderly.co")
    ap.add_argument("--hours", type=float, default=40.0)
    ap.add_argument("--max-txs", type=int, default=4, dest="max_txs")
    args = ap.parse_args()

    with open(args.roster) as f:
        roster = [str(a).lower() for a in json.load(f).get("clean", [])]

    txs = newest_txs(roster, args.hours, args.max_txs)
    if not txs:
        print("no recent data-api trades WITH tx hashes found — if trades "
              "exist but transactionHash is empty, the API no longer links "
              "fills to chain txs (strong off-chain-settlement signal).")
        return 1

    print(f"tracing {len(txs)} known fills on {args.rpc}\n")
    topic_census: dict[tuple[str, str], int] = {}
    missing = 0
    for t in txs:
        age_h = (time.time() - t["ts"]) / 3600
        print(f"  fill: {t['addr'][:12]}… {t['side']} size={t['size']} "
              f"@{t['price']} {age_h:.1f}h ago")
        print(f"    tx {t['tx']}")
        try:
            res = rpc_call(args.rpc, "eth_getTransactionReceipt", [t["tx"]])
        except Exception as e:
            print(f"    receipt fetch ERROR {e!r}")
            continue
        rec = res.get("result")
        if not rec:
            missing += 1
            print("    NO RECEIPT ON CHAIN (null) — tx hash not found by "
                  "this RPC")
            continue
        status = rec.get("status")
        print(f"    status={status} block={int(rec.get('blockNumber', '0x0'), 16)} "
              f"to={rec.get('to')} logs={len(rec.get('logs', []))}")
        for lg in rec.get("logs", []):
            addr = str(lg.get("address", "")).lower()
            topics = lg.get("topics") or []
            t0 = str(topics[0]).lower() if topics else "(none)"
            label_a = KNOWN_ADDR.get(addr, "?")
            label_t = KNOWN_TOPICS.get(t0, "UNKNOWN EVENT")
            topic_census[(addr, t0)] = topic_census.get((addr, t0), 0) + 1
            print(f"      {addr}  [{label_a}]")
            print(f"        topic0={t0}  [{label_t}] "
                  f"topics={len(topics)} data_bytes={max(0, (len(lg.get('data', '0x')) - 2) // 2)}")
        print()

    print("=" * 78)
    print("  CENSUS — (contract, topic0) pairs seen across the traced fills:")
    for (addr, t0), n in sorted(topic_census.items(), key=lambda x: -x[1]):
        print(f"    {n}x  {addr}  {KNOWN_ADDR.get(addr, '?')}")
        print(f"         {t0}  {KNOWN_TOPICS.get(t0, 'UNKNOWN EVENT')}")
    if missing == len(txs):
        print("  ALL receipts missing: data-api tx hashes do not exist on "
              "chain via this RPC — settlement is off-chain (or another "
              "chain). On-chain fill detection is DEAD for these trades;")
        print("  the watcher must pivot to data-api polling.")
    elif topic_census:
        print("  READ: the watcher should subscribe to the (contract, topic0)")
        print("  pairs above that carry the fill semantics. Paste this output")
        print("  back for the exact watcher patch.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
