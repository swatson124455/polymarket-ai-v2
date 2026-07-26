import json,statistics,collections
from collections import defaultdict,deque
def f(x):
    try: return float(x or 0)
    except: return 0.0
def d_(x): return +1.0 if x.get("action")=="buy" else -1.0
def px(x): return f(x.get("yes_price_dollars"))
def fam(t):
    if t.startswith("KXAAAGAS"): return "gas"
    if t.startswith("KXTEMP"): return "temp"
    return "other"
def mid_from_candle(c):
    b,a=c.get("yes_bid") or {}, c.get("yes_ask") or {}
    bc=f(b.get("close_dollars")); ac=f(a.get("close_dollars"))
    if not (0<bc<1 and 0<ac<=1) or ac<bc: return None
    return (bc+ac)/2.0
fills=json.load(open('AUD_fills.json'))
candles={k:{int(a):b for a,b in v.items()} for k,v in json.load(open('AUD_candles.json')).items()}
def mid_at(t,ts,tol=45):
    by=candles.get(t) or {}
    for back in range(0,tol*60,60):
        c=by.get((ts//60)*60-back)
        if c:
            m=mid_from_candle(c)
            if m is not None: return m
    return None
BUCK=[(60,"<1m"),(300,"1-5m"),(1800,"5-30m"),(7200,"30m-2h"),(21600,"2-6h"),(86400,"6-24h"),(10**9,">24h")]
def bucket(dt):
    for lim,l in BUCK:
        if dt<lim: return l
    return ">24h"
by_t=defaultdict(list)
for x in fills: by_t[x["ticker"]].append(x)
rows=[]
for t,xs in by_t.items():
    xs.sort(key=lambda z:int(z.get("ts") or 0))
    lots=deque()
    for x in xs:
        d=d_(x);q=f(x.get("count_fp"));p=px(x);ts=int(x.get("ts") or 0)
        if not (0<p<1) or q<=0: continue
        while q>1e-9 and lots and lots[0][3]!=d:
            lts,lpx,lq,ld=lots[0];m=min(q,lq)
            rows.append(dict(t=t,fam=fam(t),dt=ts-lts,r=(p-lpx)*m*ld,q=m,tk=bool(x.get("is_taker")),
                             ets=lts,xts=ts,epx=lpx,xpx=p,ed=ld,
                             efee=0.0,xfee=f(x.get("fee_cost"))*m/max(f(x.get("count_fp")),1e-9)))
            lq-=m;q-=m
            if lq<=1e-9: lots.popleft()
            else: lots[0]=(lts,lpx,lq,ld)
        if q>1e-9: lots.append((ts,p,q,d))
print("total matched trips %d ct %.0f"%(len(rows),sum(r['q'] for r in rows)))
print("\n1) <1m MAKER-ONLY BUCKET — CONCENTRATION")
sub=[r for r in rows if r['dt']<60 and not r['tk']]
print("   trips=%d ct=%.0f realized=%+.4f$"%(len(sub),sum(r['q'] for r in sub),sum(r['r'] for r in sub)))
print("   distinct tickers=%d  distinct exit minutes=%d"%(len(set(r['t'] for r in sub)),len(set(r['xts']//60 for r in sub))))
for r in sorted(sub,key=lambda z:-abs(z['r'])):
    print("     %-32s dt=%3ds q=%5.0f ent=%.2f exit=%.2f d=%+.0f r=%+.4f"%(r['t'][:32],r['dt'],r['q'],r['epx'],r['xpx'],r['ed'],r['r']))
byt=defaultdict(float)
for r in sub: byt[r['t']]+=r['r']
tot=sum(byt.values())
print("   per-ticker: "+", ".join("%s=%+.2f"%(k.split('-')[-1],v) for k,v in sorted(byt.items(),key=lambda z:-abs(z[1]))))
for k,v in sorted(byt.items(),key=lambda z:-abs(z[1]))[:1]:
    print("   LEAVE-ONE-OUT (drop %s): %+.4f$ on %.0f ct -> %+.2fc/ct"%(k,tot-v,
      sum(r['q'] for r in sub if r['t']!=k), 100*(tot-v)/max(sum(r['q'] for r in sub if r['t']!=k),1e-9)))
vals=[]
for r in sub: vals += [r['r']/r['q']*100]*int(round(r['q']))
print("   per-contract c: mean %+.2f  sd %.2f  se %.2f  -> mean/se = %.2f"%(
  statistics.mean(vals),statistics.pstdev(vals),statistics.pstdev(vals)/len(vals)**.5,
  statistics.mean(vals)/(statistics.pstdev(vals)/len(vals)**.5)))
print("\n2) FEES IGNORED, BY BUCKET (holding_time asserts 'fees are $0')")
fb=defaultdict(float); rb=defaultdict(float)
for r in rows: fb[bucket(r['dt'])]+=r['xfee']; rb[bucket(r['dt'])]+=r['r']
for _,l in BUCK:
    if l in rb: print("   %-8s realized=%+8.2f  exit fees ignored=%6.2f  net=%+8.2f"%(l,rb[l],fb[l],rb[l]-fb[l]))
print("   TOTAL   realized=%+8.2f  fees=%6.2f  net=%+8.2f"%(sum(rb.values()),sum(fb.values()),sum(rb.values())-sum(fb.values())))
print("   (all account fees: %.4f, all on taker fills)"%sum(f(x.get('fee_cost')) for x in fills))
print("\n3) IDENTITY CHECK realized == entry_edge + exit_edge + mid_drift  (maker-only round trips)")
E=X=D=R=Q=0.0; n=0
for r in rows:
    if r['tk']: continue
    me=mid_at(r['t'],r['ets']); mx=mid_at(r['t'],r['xts'])
    if me is None or mx is None: continue
    n+=1;Q+=r['q']
    E+=(me-r['epx'])*r['q']*r['ed']; X+=(mx-r['xpx'])*r['q']*(-r['ed'])
    D+=(mx-me)*r['q']*r['ed']; R+=r['r']
print("   n=%d ct=%.0f  entry_edge=%+.2f$ exit_edge=%+.2f$ drift=%+.2f$  sum=%+.2f$ vs realized=%+.2f$ (resid %.4f)"%(
  n,Q,E,X,D,E+X+D,R,E+X+D-R))
print("   per-ct: entry %+.2fc + exit %+.2fc + drift %+.2fc = %+.2fc"%(100*E/Q,100*X/Q,100*D/Q,100*(E+X+D)/Q))
print("\n4) markout ENTRY vs EXIT contract counts must satisfy ENTRY>=EXIT for real round trips")
