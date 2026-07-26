"""CAN the capture model be validated against money actually received? Test the DATA first.

Thesis: reward = (our score / all scores) x pool, our score ~ size x time resting.
To check it we need, per rewarded event: (a) the receipt, (b) our resting size x time.
"""
import sys, collections, datetime as dt, statistics as st
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q
def iso(s): return dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
PRUNE = iso("2026-07-23T20:05:00Z")

# (a) receipts, aggregated to the event key the UI uses
rows=[]
for ln in open("/tmp/rewards_ui.tsv"):
    p=ln.rstrip("\n").split("\t")
    if len(p)>=3 and p[2].strip(): rows.append((p[0], p[1], float(p[2])))
rew=collections.defaultdict(float)
for t,d,v in rows: rew[t]+=v
print(f"receipts: {len(rows)} rows -> {len(rew)} distinct rewarded events, ${sum(rew.values()):.2f}")

# (b) our resting size x time per event, from order history
c=q.KalshiOrderClient()
orders=[]
for stt in ("canceled","executed","resting"):
    try: orders += (c.get_orders(status=stt).get("orders") or [])
    except Exception as e: print("ERR",stt,repr(e)[:60])
ev_cs=collections.defaultdict(float); ev_n=collections.Counter(); ev_first={}
for o in orders:
    try:
        t=o["ticker"]; a=iso(o["created_time"]); b=iso(o["last_update_time"])
        life=(b-a).total_seconds()
        if life<0: continue
        key="-".join(t.split("-")[:2])
        ev_cs[key]+= float(o.get("initial_count_fp") or 0)*life
        ev_n[key]+=1
        ev_first[key]=min(ev_first.get(key,a),a)
    except Exception: pass

print(f"order history: {len(orders)} orders -> {len(ev_cs)} events with resting time\n")
print(f"{'rewarded event':30s} {'$reward':>8s} {'ct-seconds':>12s} {'orders':>7s}  history complete?")
usable=[]
for k in sorted(rew, key=lambda x:-rew[x]):
    cs=ev_cs.get(k,0.0); n=ev_n.get(k,0); first=ev_first.get(k)
    complete = (first is not None and first>=PRUNE)
    if complete and cs>0: usable.append((rew[k],cs))
    print(f"{k:30s} {rew[k]:8.2f} {cs:12,.0f} {n:7d}  "
          + ("YES" if complete else ("PRUNED (pre-07-23T20:05)" if n else "NO ORDERS IN HISTORY")))
print(f"\nUSABLE PAIRS (receipt + complete order history): n={len(usable)}")
if len(usable)>=3:
    xs=[c for _,c in usable]; ys=[r for r,_ in usable]
    def rank(v):
        s=sorted(range(len(v)), key=lambda i:v[i]); o=[0]*len(v)
        for p,i in enumerate(s): o[i]=p
        return o
    rx,ry=rank(xs),rank(ys); n=len(xs)
    d=sum((rx[i]-ry[i])**2 for i in range(n))
    rho=1-6*d/(n*(n*n-1)) if n>1 else float("nan")
    print(f"  Spearman rho(contract-seconds, $reward) = {rho:+.2f}  over n={n}")
    for r,cs in sorted(usable): print(f"    ${r:6.2f}  <- {cs:12,.0f} ct-s")
else:
    print("  -> CANNOT compute a correlation. Thesis is NOT testable on this data.")
