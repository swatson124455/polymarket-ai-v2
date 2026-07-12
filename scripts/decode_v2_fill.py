#!/usr/bin/env python3
"""Reverse the V2 exchange fill event's field layout from KNOWN trades.

CONTEXT (2026-07-12): receipt tracing proved recent Polymarket fills settle
on the V2 main exchange (0xE111…996B) emitting topic0
0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee
(3 indexed params + 7 data words — V1 OrderFilled had 3+5; signature-name
guessing missed). The watcher doesn't need the event NAME: it needs the
POSITIONS of maker/taker/assetIds/amounts. This script derives them
empirically and self-validates against ground truth.

METHOD: for the roster's newest data-api trades (known size in shares and
price), fetch the tx receipts, take every 0xd543… log, and:
  * print topics 1-3 raw, flagging which look like addresses and which
    equal the trading proxy;
  * print the 7 data words as uints;
  * search word pairs (u, t): u/t within 2% of the API price with t
    aggregating (across the tx's fill events) to within 2% of the API
    size×1e6 — that pair IS (usdc-ish amount, token amount);
  * flag words that equal the CTF token id seen in the same tx's
    ERC1155 TransferSingle/Batch logs (asset-id positions).
Prints an inferred layout summary. READ-ONLY, stdlib only.

INVOCATION (VPS):
    python3 scripts/decode_v2_fill.py \
        --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json
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
FILL_TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
T1155_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
T1155_BATCH = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
SCALE = 1e6


def api_json(url, timeout_s=15.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def rpc(url, method, params, timeout_s=20.0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def words(data_hex: str) -> list[int]:
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return [int(h[i:i + 64], 16) for i in range(0, len(h) - 63, 64)]


def looks_addr(topic: str) -> bool:
    h = topic[2:] if topic.startswith("0x") else topic
    return len(h) == 64 and h[:24] == "0" * 24 and h[24:] != "0" * 40


def topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def newest_trades(roster, hours, max_n):
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
            if (str(t.get("type", "")).upper() == "TRADE"
                    and t.get("timestamp") is not None
                    and time.time() - float(t["timestamp"]) < hours * 3600
                    and (t.get("transactionHash") or "").strip()):
                rows.append({"addr": a, "tx": t["transactionHash"].strip(),
                             "ts": float(t["timestamp"]),
                             "side": t.get("side"),
                             "price": float(t.get("price", 0) or 0),
                             "size": float(t.get("size", 0) or 0)})
        time.sleep(0.3)
    rows.sort(key=lambda r: -r["ts"])
    seen, out = set(), []
    for r in rows:
        if r["tx"] not in seen:
            seen.add(r["tx"])
            out.append(r)
        if len(out) >= max_n:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode the V2 fill event layout")
    ap.add_argument("--roster", required=True)
    ap.add_argument("--rpc", default="https://polygon.gateway.tenderly.co")
    ap.add_argument("--hours", type=float, default=40.0)
    ap.add_argument("--max-txs", type=int, default=3, dest="max_txs")
    args = ap.parse_args()

    with open(args.roster) as f:
        roster = [str(a).lower() for a in json.load(f).get("clean", [])]
    trades = newest_trades(roster, args.hours, args.max_txs)
    if not trades:
        print("no recent trades with tx hashes")
        return 1

    # (usdc_word_idx, token_word_idx) votes across all validated txs
    pair_votes: dict[tuple[int, int], int] = {}
    addr_topic_votes: dict[int, int] = {}   # which topic slot holds the proxy
    tokenid_word_votes: dict[int, int] = {}

    for t in trades:
        print(f"\nfill: {t['addr'][:12]}… {t['side']} size={t['size']} "
              f"@{t['price']}  tx {t['tx'][:20]}…")
        try:
            rec = rpc(args.rpc, "eth_getTransactionReceipt", [t["tx"]]).get("result")
        except Exception as e:
            print(f"  receipt error {e!r}")
            continue
        if not rec:
            print("  no receipt")
            continue
        fills, ctf_ids = [], set()
        for lg in rec.get("logs", []):
            tops = [str(x).lower() for x in (lg.get("topics") or [])]
            if not tops:
                continue
            if tops[0] == FILL_TOPIC:
                fills.append({"addr": str(lg.get("address", "")).lower(),
                              "topics": tops, "w": words(lg.get("data", "0x"))})
            elif tops[0] in (T1155_SINGLE, T1155_BATCH):
                for w in words(lg.get("data", "0x")):
                    if w > 10 ** 30:      # CTF token ids are ~77-digit uints
                        ctf_ids.add(w)
        print(f"  fill events: {len(fills)}  ctf token ids in tx: {len(ctf_ids)}")
        exp_tok = t["size"] * SCALE
        exp_usdc = t["size"] * t["price"] * SCALE
        n_words = max((len(f["w"]) for f in fills), default=0)
        # per (i,j): sum words across fill events, test against ground truth
        for i in range(n_words):
            for j in range(n_words):
                if i == j:
                    continue
                su = sum(f["w"][i] for f in fills if len(f["w"]) > max(i, j))
                st = sum(f["w"][j] for f in fills if len(f["w"]) > max(i, j))
                if st <= 0 or su <= 0:
                    continue
                if (abs(st - exp_tok) < 0.02 * exp_tok
                        and abs(su - exp_usdc) < 0.02 * exp_usdc):
                    pair_votes[(i, j)] = pair_votes.get((i, j), 0) + 1
                    print(f"  MATCH: sum(word[{i}])={su / SCALE:.2f} usdc-ish, "
                          f"sum(word[{j}])={st / SCALE:.2f} tokens "
                          f"(price {su / st:.4f} vs api {t['price']})")
        for f in fills[:3]:
            print(f"    topics1-3: " + "  ".join(
                (topic_addr(x) + ("*ROSTER*" if topic_addr(x) == t["addr"] else "")
                 if looks_addr(x) else x[:18] + "…") for x in f["topics"][1:]))
            print(f"    words: " + "  ".join(
                (f"[{k}]={w}" + ("(CTFID)" if w in ctf_ids else
                                 f"({w / SCALE:.2f})" if 0 < w < 10 ** 14 else ""))
                for k, w in enumerate(f["w"])))
        for f in fills:
            for slot, x in enumerate(f["topics"][1:], start=1):
                if looks_addr(x) and topic_addr(x) == t["addr"]:
                    addr_topic_votes[slot] = addr_topic_votes.get(slot, 0) + 1
            for k, w in enumerate(f["w"]):
                if w in ctf_ids:
                    tokenid_word_votes[k] = tokenid_word_votes.get(k, 0) + 1

    print("\n" + "=" * 78)
    print("  INFERRED LAYOUT (votes across validated fills):")
    print(f"    proxy address in topic slot: {addr_topic_votes or 'NOT SEEN'}")
    print(f"    CTF token id in data word:  {tokenid_word_votes or 'NOT SEEN'}")
    print(f"    (usdc_word, token_word) ground-truth matches: "
          f"{pair_votes or 'NONE'}")
    print("  Paste this back — the watcher patch encodes exactly these "
          "positions.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
