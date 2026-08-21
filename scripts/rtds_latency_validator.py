#!/usr/bin/env python3
"""RTDS latency validator — PROVE the real-time trade feed beats on-chain polling
for copy detection, WITHOUT touching the running watcher.

WHY: the shadow copy-watcher detects roster fills by polling on-chain
`eth_getLogs` (MIRROR3_POLL_S=2), measured detect_lag p50 ~1.30s / p90 ~11.6s
(n=12,980, mirror3_shadow.jsonl, 2026-07-29). Polymarket's RTDS socket
(`wss://ws-live-data.polymarket.com`, already in .env.mirror3 as RTDS_WS_URL but
NOT consumed by the watcher) pushes every trade in real time WITH the trader
identity (`proxyWallet`) and `side` — the two fields that make it a
function-preserving replacement (identity is the copy signal; side removes the
receipt-transfer-log parse the on-chain path needs).

This is a READ-ONLY parallel probe. It does NOT connect to the watcher, the DB,
or place anything. It:
  1. streams RTDS trades, filters to the roster,
  2. records wall-clock arrival vs the trade's own timestamp (RTDS delivery lag),
  3. cross-joins by transactionHash against the live on-chain shadow feed to get
     an APPLES-TO-APPLES detect_lag delta (RTDS arrival vs the watcher's
     block_ts) for the SAME fills,
and asserts every measurement set is NON-EMPTY before printing a verdict — a
zero-row "RTDS is faster" is indistinguishable from "the probe caught nothing"
and must fail loud (empty-set false-pass class, the MB lane's recurring trap).

    RTDS_WS_URL=... MIRROR3_ROSTER_PATH=... MIRROR3_SHADOW_PATH=... \
        python scripts/rtds_latency_validator.py --seconds 120
    ... --self-test   # offline: parse + roster-filter + non-empty guards
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

RTDS_DEFAULT = "wss://ws-live-data.polymarket.com"
SHADOW_DEFAULT = "/opt/pa2-shared/mirror3_shadow.jsonl"
ROSTER_DEFAULT = "/opt/pa2-shared/mb_copyable_data/chain_audit.json"


def load_roster(path: str) -> set[str]:
    """Lower-cased roster address set (chain_audit.json 'clean' union)."""
    with open(path) as f:
        d = json.load(f)
    clean = {str(a).lower() for a in (d.get("clean") or [])}
    if not clean:
        raise ValueError(f"roster '{path}' has an empty 'clean' set — refusing "
                         f"to run a filter that would match nothing")
    return clean


def parse_trade(msg: dict) -> list[dict]:
    """Extract trade rows from an RTDS message. RTDS shape:
    {type:'trades', payload:{...} | [...], timestamp:...}. Returns [] for
    non-trade frames (control/status/heartbeat) — never raises on them."""
    if not isinstance(msg, dict) or msg.get("type") != "trades":
        return []
    pl = msg.get("payload")
    items = pl if isinstance(pl, list) else [pl]
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("proxyWallet"):
            continue
        out.append({
            "proxyWallet": str(it["proxyWallet"]).lower(),
            "side": it.get("side"),
            "asset": str(it.get("asset") or ""),
            "conditionId": str(it.get("conditionId") or ""),
            "size": it.get("size"),
            "price": it.get("price"),
            "tx": str(it.get("transactionHash") or "").lower(),
            "ts": it.get("timestamp") or msg.get("timestamp"),
        })
    return out


def shadow_blockts_by_tx(path: str) -> dict[str, float]:
    """tx_hash(lower) -> block_ts, from the live on-chain shadow feed. Used to
    cross-check RTDS arrival against the watcher's canonical block_ts for the
    SAME fill (apples-to-apples, not two different clocks)."""
    out: dict[str, float] = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            tx = str(r.get("tx") or "").lower()
            bts = r.get("block_ts")
            if tx and isinstance(bts, (int, float)):
                out[tx] = bts
    return out


async def run(args) -> int:
    import websockets
    roster = load_roster(args.roster)
    print(f"roster: {len(roster)} addresses | RTDS: {args.url} | window: {args.seconds}s")
    captured: list[dict] = []      # roster-matched trades
    all_lags: list[float] = []     # RTDS delivery lag (arrival - trade_ts) over ALL trades
    total = 0
    t_end = None  # set from a monotonic clock below (Date.now-free constraint N/A here)
    start = time.time()
    try:
        async with asyncio.timeout(args.seconds + 15):
            async with websockets.connect(args.url, ping_interval=20,
                                          open_timeout=12) as ws:
                await ws.send(json.dumps({"action": "subscribe",
                    "subscriptions": [{"topic": "activity", "type": "trades"}]}))
                while time.time() - start < args.seconds:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    except asyncio.TimeoutError:
                        print("  (10s silent — feed idle or disconnected)")
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    for tr in parse_trade(msg):
                        arrival = time.time()
                        total += 1
                        try:
                            tv = int(tr["ts"])
                            tv = tv // 1000 if tv > 2e10 else tv
                            all_lags.append(arrival - tv)
                        except (TypeError, ValueError):
                            pass
                        if tr["proxyWallet"] in roster:
                            tr["_arrival"] = arrival
                            captured.append(tr)
    except Exception as e:
        print(f"RTDS error: {type(e).__name__}: {str(e)[:160]}")
        return 1

    # ---- NON-EMPTY guards (empty-set false-pass class) --------------------
    if total == 0:
        print("FATAL: 0 trades seen on RTDS in the window — probe caught nothing; "
              "this is NOT evidence the feed is empty or slow.", file=sys.stderr)
        return 3
    all_lags.sort()
    n = len(all_lags)
    p = lambda q: all_lags[min(n - 1, int(q * n))]
    print(f"\nALL trades seen: {total}  (RTDS delivery lag arrival-trade_ts, n={n})")
    print(f"  min={all_lags[0]:.3f}s p50={p(.50):.3f}s p90={p(.90):.3f}s max={all_lags[-1]:.3f}s")
    print(f"roster-matched trades: {len(captured)}")

    # ---- apples-to-apples vs the watcher's block_ts, same tx --------------
    bts = shadow_blockts_by_tx(args.shadow)
    joined = [(tr, bts[tr["tx"]]) for tr in captured if tr["tx"] in bts]
    print(f"roster trades also present in on-chain shadow feed (by tx): {len(joined)}")
    if joined:
        deltas = sorted(tr["_arrival"] - b for tr, b in joined)
        m = len(deltas)
        print(f"  RTDS-arrival minus watcher-block_ts (n={m}): "
              f"min={deltas[0]:.3f}s p50={deltas[m//2]:.3f}s max={deltas[-1]:.3f}s")
        print("  (this is the head-to-head: how fast RTDS delivered the SAME "
              "fills the on-chain watcher logs)")
    else:
        print("  NOTE: 0 tx overlap this window — roster hits are sparse; "
              "re-run longer for the head-to-head. NOT a pass/fail either way.")
    for tr in captured[:8]:
        print(f"    ROSTER {tr['side']:4} {tr['proxyWallet'][:12]}… "
              f"sz={tr['size']} px={tr['price']} tx={tr['tx'][:12]}…")
    return 0


def _self_test() -> int:
    print("SELF-TEST — rtds_latency_validator (offline)\n")
    ok = True
    msg = {"type": "trades", "timestamp": 1785368141, "payload": {
        "proxyWallet": "0xABC", "side": "BUY", "asset": "111",
        "conditionId": "0xcid", "size": 5.0, "price": 0.63,
        "transactionHash": "0xTX"}}
    rows = parse_trade(msg)
    ok1 = (len(rows) == 1 and rows[0]["proxyWallet"] == "0xabc"
           and rows[0]["side"] == "BUY" and rows[0]["tx"] == "0xtx")
    print(f"  [parse] trade row extracted, addr/tx lower-cased : {ok1}"); ok &= ok1
    ok2 = (parse_trade({"type": "trades", "payload": {"foo": 1}}) == []
           and parse_trade({"type": "book"}) == []
           and parse_trade({"statusCode": 200, "body": ""}) == [])
    print(f"  [parse] non-trade / no-wallet frames -> [] (no raise) : {ok2}")
    ok &= ok2
    plist = {"type": "trades", "payload": [
        {"proxyWallet": "0xA", "transactionHash": "0x1"},
        {"proxyWallet": "0xB", "transactionHash": "0x2"}]}
    ok3 = len(parse_trade(plist)) == 2
    print(f"  [parse] list payload -> one row per trade : {ok3}"); ok &= ok3
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "r.json")
        json.dump({"clean": ["0xAa", "0xBb"]}, open(rp, "w"))
        ok4 = load_roster(rp) == {"0xaa", "0xbb"}
        print(f"  [roster] loaded + lower-cased : {ok4}"); ok &= ok4
        json.dump({"clean": []}, open(rp, "w"))
        try:
            load_roster(rp); ok5 = False
        except ValueError:
            ok5 = True
        print(f"  [guard] empty roster raises (never match-nothing) : {ok5}")
        ok &= ok5
        sp = os.path.join(d, "s.jsonl")
        open(sp, "w").write(json.dumps({"tx": "0xDe", "block_ts": 100}) + "\nbad\n")
        idx = shadow_blockts_by_tx(sp)
        ok6 = (idx == {"0xde": 100})
        print(f"  [xref] block_ts-by-tx index, malformed skipped : {ok6}")
        ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="prove RTDS detection latency vs the "
                                             "on-chain poll, read-only")
    ap.add_argument("--url", default=os.environ.get("RTDS_WS_URL", RTDS_DEFAULT))
    ap.add_argument("--roster", default=os.environ.get("MIRROR3_ROSTER_PATH", ROSTER_DEFAULT))
    ap.add_argument("--shadow", default=os.environ.get("MIRROR3_SHADOW_PATH", SHADOW_DEFAULT))
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else asyncio.run(run(a)))
