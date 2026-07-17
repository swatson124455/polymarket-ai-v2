"""Complement-side thinness probe: for sampled rewarded markets, compare
in-band liquidity (within 3c of mid) on the YES book vs the NO book.
GET-only, throttled, local IP."""
import json, time, urllib.request, random
UA = {"User-Agent": "pa2-maker-research/1.0"}
def get(u):
    time.sleep(0.12)
    for _ in range(2):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=15))
        except Exception: time.sleep(1)
    return None
uni = json.load(open("universe_v3.json"))["markets"]
random.seed(7)
sample = random.sample(uni, 25)
rows = []
for m in sample:
    out = {}
    for side in ("yes","no"):
        b = get("https://clob.polymarket.com/book?token_id=" + m[side])
        if not b: continue
        bids, asks = b.get("bids") or [], b.get("asks") or []
        try:
            bb = float(bids[-1]["price"]) if bids else None
            ba = float(asks[-1]["price"]) if asks else None
        except Exception: continue
        if bb is None or ba is None: continue
        mid = (bb+ba)/2
        dep = 0.0
        for lv in bids+asks:
            try:
                p, s = float(lv["price"]), float(lv["size"])
                if abs(p-mid) <= 0.03: dep += p*s
            except Exception: pass
        out[side] = dep
    if len(out)==2:
        rows.append((m.get("q","?")[:44], m.get("sector","?"), out["yes"], out["no"]))
print("%-44s %-10s %10s %10s %7s" % ("market","sector","YES$inband","NO$inband","NO/YES"))
ratios=[]
for q,sec,y,n in rows:
    r = n/max(y,1e-9)
    ratios.append(r)
    print("%-44s %-10s %10.0f %10.0f %7.2f" % (q,sec,y,n,r))
ratios.sort()
print("\nmarkets=%d  median NO/YES in-band ratio=%.2f  thin-side(<0.5x)=%d  balanced=%d"
      % (len(ratios), ratios[len(ratios)//2],
         sum(1 for r in ratios if r<0.5 or r>2), sum(1 for r in ratios if 0.5<=r<=2)))
