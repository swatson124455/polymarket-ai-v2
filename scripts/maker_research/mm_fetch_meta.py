import json, time, urllib.request
UA = {"User-Agent": "pa2-maker-research/1.0"}
def get(url):
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:
            time.sleep(1.5)
    return None

cids = [l.strip() for l in open("backtest_cids.txt") if l.strip().startswith("0x")]
fees = {}
# try batched condition_ids
test = get("https://gamma-api.polymarket.com/markets?closed=true&condition_ids=" + ",".join(cids[:20]))
batch_ok = isinstance(test, list) and len(test) > 5
print("batch lookup supported:", batch_ok, "(got %s)" % (len(test) if isinstance(test, list) else None))
B = 20 if batch_ok else 1
t0 = time.time()
for i in range(0, len(cids), B):
    if time.time() - t0 > 900: print("time cap"); break
    time.sleep(0.12)
    r = get("https://gamma-api.polymarket.com/markets?closed=true&condition_ids=" + ",".join(cids[i:i+B]))
    for m in (r or []):
        c = m.get("conditionId")
        if c: fees[c] = bool(m.get("feesEnabled"))
    if (i // B) % 10 == 0: print("  cids %d/%d mapped=%d" % (i, len(cids), len(fees)), flush=True)
json.dump(fees, open("backtest_fees.json", "w"))
on = sum(1 for v in fees.values() if v)
print("feesEnabled: %d/%d mapped, %d enabled, %d fee-free" % (len(fees), len(cids), on, len(fees)-on))

ids = [l.strip() for l in open("fill_market_ids.txt") if l.strip()]
meta = {}
for i, mid in enumerate(ids):
    if time.time() - t0 > 1500: print("time cap ids"); break
    time.sleep(0.1)
    r = get("https://gamma-api.polymarket.com/markets?closed=true&id=" + mid) or get("https://gamma-api.polymarket.com/markets?id=" + mid)
    m = r[0] if isinstance(r, list) and r else None
    if m:
        meta[mid] = {"cid": m.get("conditionId"), "end": m.get("endDate")}
    if (i+1) % 100 == 0: print("  ids %d/%d mapped=%d" % (i+1, len(ids), len(meta)), flush=True)
json.dump(meta, open("fill_market_meta.json", "w"))
print("fill-market meta: %d/%d mapped" % (len(meta), len(ids)))
