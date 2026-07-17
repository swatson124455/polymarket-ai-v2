import json, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (pa2-maker-research)"}
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20))
def rpc(method, params):
    req = urllib.request.Request("https://polygon-bor-rpc.publicnode.com",
        headers={"Content-Type": "application/json", "User-Agent": UA["User-Agent"]},
        data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode())
    return json.load(urllib.request.urlopen(req, timeout=20)).get("result")
trades = get("https://data-api.polymarket.com/trades?limit=4")
done = 0
for t in trades:
    tx = t.get("transactionHash")
    if not tx: continue
    print("trade:", (t.get("title") or "?")[:44], "| px", t.get("price"), "| size", t.get("size"))
    rec = rpc("eth_getTransactionReceipt", [tx])
    if not rec: print("  no receipt yet"); continue
    print("  to:", (rec.get("to") or "?")[:14], "| logs:", len(rec.get("logs", [])))
    for lg in rec.get("logs", []):
        addr = lg["address"].lower()
        nt = len(lg.get("topics", []))
        if nt == 4 and (addr.startswith("0xe111") or addr.startswith("0xe222")):
            mk = "0x" + lg["topics"][2][-40:]
            tk = "0x" + lg["topics"][3][-40:]
            data = lg["data"][2:]
            words = [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
            amts = [round(w/1e6, 4) if w < 1e14 else "assetid" for w in words]
            print("  ORDERFILLED addr=%s topic0=%s" % (addr[:12], lg["topics"][0][:18]))
            print("    maker=%s taker=%s amounts=%s" % (mk[:14], tk[:14], amts[:6]))
            done += 1
    if done >= 2: break
print("\nfeasibility:", "CONFIRMED — maker addresses + fill amounts decodable from public RPC" if done else "NOT confirmed")
