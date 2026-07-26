"""Is our missing presence CHURN (cancel->recreate gaps of seconds) or ABSENCE (no order at all)?

Measured, not inferred: per market, take the union of resting intervals, then measure the GAPS
between consecutive intervals and bucket them by length. Short gaps = cancel/recreate churn.
Long gaps = the bot simply had no order in that market.
"""
import sys, collections, statistics as st
import datetime as dt
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q
c = q.KalshiOrderClient()
def iso(s): return dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
BOUND = iso("2026-07-23T20:05:00Z")     # unpruned slice only

orders=[]
for stt in ("canceled","executed","resting"):
    try: orders += (c.get_orders(status=stt).get("orders") or [])
    except Exception as e: print("ERR",stt,repr(e)[:80])

per=collections.defaultdict(list)
for o in orders:
    try:
        a,b=iso(o["created_time"]),iso(o["last_update_time"])
        if (b-a).total_seconds()>=0: per[o["ticker"]].append((a,b))
    except Exception: pass

BK=[(0,5,"<5s  churn"),(5,30,"5-30s"),(30,120,"30-120s"),(120,600,"2-10min"),
    (600,3600,"10-60min"),(3600,1e18,">1h  absence")]
tot=collections.Counter(); secs=collections.Counter(); n_mkt=0
churn_s=absence_s=0.0
for t,ivs in per.items():
    if min(a for a,_ in ivs) < BOUND: continue      # pruned-era market, incomplete
    ivs.sort(); m=[list(ivs[0])]
    for a,b in ivs[1:]:
        if a<=m[-1][1]: m[-1][1]=max(m[-1][1],b)
        else: m.append([a,b])
    if len(m)<2: continue
    n_mkt+=1
    for i in range(len(m)-1):
        g=(m[i+1][0]-m[i][1]).total_seconds()
        for lo,hi,nm in BK:
            if lo<=g<hi: tot[nm]+=1; secs[nm]+=g; break
        if g<30: churn_s+=g
        else:    absence_s+=g

print(f"markets in the unpruned slice with >1 resting interval: {n_mkt}")
print(f"{'gap bucket':16s} {'count':>7s} {'total hours':>12s} {'% of gap time':>14s}")
T=sum(secs.values()) or 1
for _,_,nm in BK:
    if tot[nm]: print(f"{nm:16s} {tot[nm]:7d} {secs[nm]/3600:12.2f} {100*secs[nm]/T:13.1f}%")
print(f"\nTOTAL gap time {T/3600:.2f}h")
print(f"  gaps <30s  (cancel/recreate CHURN): {churn_s/3600:8.3f}h = {100*churn_s/T:5.2f}%")
print(f"  gaps >=30s (genuine ABSENCE):       {absence_s/3600:8.3f}h = {100*absence_s/T:5.2f}%")
