import json,statistics
from collections import defaultdict
def f(x):
    try: return float(x or 0)
    except: return 0.0
def direction(x):
    a,s=x.get("action"),x.get("side")
    if s=="yes": return +1 if a=="buy" else -1
    return -1 if a=="sell" else +1
def fam(t):
    if t.startswith("KXAAAGAS"): return "gas"
    if t.startswith("KXTEMP"): return "temp"
    return "other"
def mid_from_candle(c):
    b,a=c.get("yes_bid") or {}, c.get("yes_ask") or {}
    bc=f(b.get("close_dollars",b.get("close"))); ac=f(a.get("close_dollars",a.get("close")))
    if bc>1.0 or ac>1.0: bc,ac=bc/100.0,ac/100.0
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
            if m is not None: return m,(ts//60)*60-back
    return None,None
mk=sorted([x for x in fills if not x.get("is_taker")],key=lambda z:int(z.get("ts") or 0))
pos=defaultdict(float); tagged=[]
for x in mk:
    t=x["ticker"];ct=f(x.get("count_fp"));d=direction(x);b=pos[t];s=d*ct
    red=(b>0 and s<0) or (b<0 and s>0); pos[t]=b+s
    tagged.append((x,"EXIT" if red else "ENTRY",d,ct))
# 1) stale-baseline bias: edge vs baseline lag
lagb=defaultdict(lambda:[0.0,0.0])
alt=defaultdict(lambda:[0.0,0.0])   # variant: baseline = candle ending at floor(ts/60) (lag<=60s, may contain our fill)
for x,kind,d,ct in tagged:
    t,ts=x["ticker"],int(x["ts"]);p=f(x.get("yes_price_dollars"))
    if not(0<p<1) or ct<=0: continue
    m0,e0=mid_at(t,ts-60)
    if m0 is None: continue
    lag=ts-e0
    k = "<=60s" if lag<=60 else ("61-120s" if lag<=120 else ("121-300s" if lag<=300 else ">300s"))
    lagb[k][0]+=(m0-p)*d*ct; lagb[k][1]+=ct
    m1,e1=mid_at(t,ts,tol=45)
    if m1 is not None:
        alt[(kind,fam(t))][0]+=(m1-p)*d*ct; alt[(kind,fam(t))][1]+=ct
print("A) 'at_fill' edge SPLIT BY HOW STALE THE BASELINE CANDLE IS")
for k in ["<=60s","61-120s","121-300s",">300s"]:
    if k in lagb:
        u,c=lagb[k]; print("   lag %-9s ct=%6.0f  edge=%+6.2fc/ct  tot=%+7.2f$"%(k,c,u/c*100,u))
print("\nB) SAME EDGE, BASELINE = MOST RECENT CANDLE AT/BEFORE THE FILL MINUTE (lag<=60s)")
tot=0
for k in sorted(alt):
    u,c=alt[k]; tot+=u; print("   %-14s ct=%6.0f  %+6.2fc/ct  %+7.2f$"%("%s/%s"%k,c,u/c*100,u))
print("   TOTAL %+ .2f$   (script's ts-60 baseline total: see below)"%tot)
# 2) per-bucket n at each horizon (the term-structure sample problem)
HOR=[(60,"1m"),(300,"5m"),(1800,"30m"),(7200,"2h"),(86400,"24h")]
agg=defaultdict(lambda:defaultdict(lambda:[0.0,0.0])); base=defaultdict(lambda:[0.0,0.0])
for x,kind,d,ct in tagged:
    t,ts=x["ticker"],int(x["ts"]);p=f(x.get("yes_price_dollars"))
    if not(0<p<1) or ct<=0: continue
    m0,_=mid_at(t,ts-60)
    if m0 is None: continue
    k=(kind,fam(t)); base[k][0]+=(m0-p)*d*ct; base[k][1]+=ct
    for hs,lbl in HOR:
        m,_=mid_at(t,ts+hs,tol=90)
        if m is not None: agg[k][lbl][0]+=(m-p)*d*ct; agg[k][lbl][1]+=ct
print("\nC) CONTRACTS BEHIND EACH CELL (the term structure is NOT a fixed sample)")
print("   %-14s %8s"%("bucket","at_fill")+"".join(" %8s"%l for _,l in HOR))
gt=0
for k in sorted(base):
    gt+=base[k][0]
    print("   %-14s %8.0f"%("%s/%s"%k,base[k][1])+"".join(" %8.0f"%agg[k][l][1] for _,l in HOR))
print("   at_fill GRAND TOTAL edge = %+.2f$ on %.0f ct"%(gt,sum(v[1] for v in base.values())))
print("\nD) CELLS WITH ZERO OBSERVATIONS THAT STILL PRINT A DOLLAR FIGURE:")
for k in sorted(base):
    for _,l in HOR:
        if agg[k][l][1]==0: print("   %-14s %-4s  n_ct=0  but the $tot row prints %.2f$"%("%s/%s"%k,l,agg[k][l][0]))
