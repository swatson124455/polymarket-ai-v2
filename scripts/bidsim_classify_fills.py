"""Retro-classify bidsim epoch-2 fills by ON-CHAIN aggressor side (read-only).
For each recorded fill_tx: raw eth_getTransactionReceipt -> the OrderFilled
whose counterparty field IS the exchange = the TAKER's order -> that taker's
side for our token via the receipt-validated transfer-log rule."""
import json, sys, time, urllib.request

RPC = "https://polygon.gateway.tenderly.co"
sys.path.insert(0, "/opt/mirror3")
from mirror_v3.copy_watcher import (FILL_TOPIC_V2, side_from_receipt_logs,
                                    _topic_addr, _hex)

def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:   # timeout: mandatory
        return json.loads(r.read())["result"]

rows = [json.loads(l) for l in open("/opt/pa2-shared/mirror3_bidsim.jsonl")
        if l.strip()]
assert rows, "EMPTY SINK - FAIL LOUD"
fills = [r for r in rows if r["type"] == "fill"]
assert fills, "NO FILLS - FAIL LOUD"

out = {"taker_sell": 0, "taker_buy": 0, "no_tx": 0, "no_taker_evt": 0,
       "side_unknown": 0, "rpc_error": 0}
detail = []
for f in fills:
    tx = f.get("fill_tx")
    if not tx:
        out["no_tx"] += 1; continue
    try:
        rcpt = rpc("eth_getTransactionReceipt", [tx])
    except Exception as e:
        out["rpc_error"] += 1; detail.append((tx[:14], "RPC_ERR", f["wait_s"])); continue
    if not rcpt:
        out["rpc_error"] += 1; continue
    logs = rcpt.get("logs") or []
    taker = None
    for lg in logs:
        tp = lg.get("topics") or []
        if len(tp) > 3 and _hex(tp[0]).lower() == FILL_TOPIC_V2 \
           and _topic_addr(tp[3]) == str(lg.get("address", "")).lower():
            taker = _topic_addr(tp[2]); break
    if taker is None:
        out["no_taker_evt"] += 1; detail.append((tx[:14], "NO_TAKER_EVT", f["wait_s"])); continue
    side = side_from_receipt_logs(logs, taker, f["token_id"])
    if side == "SELL":
        out["taker_sell"] += 1; lab = "TAKER_SELL"
    elif side == "BUY":
        out["taker_buy"] += 1; lab = "TAKER_BUY"
    else:
        out["side_unknown"] += 1; lab = "SIDE_UNKNOWN"
    detail.append((tx[:14], lab, f["wait_s"]))
    time.sleep(0.15)

n = len(fills)
print("fills classified: %d" % n)
for k, v in out.items():
    print("  %-13s %3d  (%.1f%% of %d)" % (k, v, 100.0*v/n, n))
resolved = sum(1 for r in rows if r["type"] in ("fill", "expire"))
ts = out["taker_sell"]
print()
print("CHAIN-TRUTH fill rate (taker-SELL aggression only): %d/%d = %.3f  vs 74%% bar" % (ts, resolved, ts/resolved))
print("charter-rule fill rate (all prints):                %d/%d = %.3f" % (n, resolved, n/resolved))
print()
print("by wait bucket (label, count<=5s, count>60s):")
import collections
b = collections.defaultdict(lambda: [0, 0, 0])
for _, lab, w in detail:
    b[lab][0] += 1
    if w <= 5: b[lab][1] += 1
    if w > 60: b[lab][2] += 1
for lab, (tot, fast, slow) in sorted(b.items()):
    print("  %-13s n=%2d  <=5s:%2d  >60s:%2d" % (lab, tot, fast, slow))
