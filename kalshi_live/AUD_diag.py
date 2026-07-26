import json, os, sys
from collections import defaultdict
import kalshi_drift_fetch as DF
HORIZONS=[(60,"1m"),(300,"5m"),(1800,"30m"),(7200,"2h"),(86400,"24h")]
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
    try:
        b,a=c.get("yes_bid") or {}, c.get("yes_ask") or {}
        bc=f(b.get("close_dollars",b.get("close"))); ac=f(a.get("close_dollars",a.get("close")))
        if bc>1.0 or ac>1.0: bc,ac=bc/100.0,ac/100.0
        if not (0<bc<1 and 0<ac<=1) or ac<bc: return None
        return (bc+ac)/2.0
    except Exception: return None

fills=json.load(open('AUD_fills.json'))
mk=sorted([x for x in fills if not x.get("is_taker")],key=lambda z:int(z.get("ts") or 0))
allf=sorted(fills,key=lambda z:int(z.get("ts") or 0))

# --- ENTRY/EXIT labels, maker-only running pos (as markout does) vs all-fills running pos
def label(seq, universe):
    pos=defaultdict(float); out={}
    for x in seq:
        t=x["ticker"]; d=direction(x); s=d*f(x.get("count_fp")); before=pos[t]
        red=(before>0 and s<0) or (before<0 and s>0)
        pos[t]=before+s
        if x["fill_id"] in universe: out[x["fill_id"]]="EXIT" if red else "ENTRY"
    return out
U={x["fill_id"] for x in mk}
L1=label(mk,U)      # markout's way
L2=label(allf,U)    # correct: takers affect inventory too
dis=[k for k in L1 if L1[k]!=L2[k]]
ctmap={x["fill_id"]:f(x.get("count_fp")) for x in fills}
print("ENTRY/EXIT label disagreement (maker-only running pos vs all-fills): %d/%d fills, %.0f ct (%.1f%% of maker ct)"%(
  len(dis),len(L1),sum(ctmap[k] for k in dis),100*sum(ctmap[k] for k in dis)/sum(ctmap[k] for k in L1)))
print("  markout labels: ENTRY=%d EXIT=%d ; corrected: ENTRY=%d EXIT=%d"%(
  sum(1 for v in L1.values() if v=="ENTRY"),sum(1 for v in L1.values() if v=="EXIT"),
  sum(1 for v in L2.values() if v=="ENTRY"),sum(1 for v in L2.values() if v=="EXIT")))
# crossing-zero fills labelled purely EXIT
pos=defaultdict(float); cross=0; crossct=0
for x in mk:
    t=x["ticker"]; d=direction(x); s=d*f(x.get("count_fp")); b=pos[t]; a=b+s; pos[t]=a
    if b*a<0: cross+=1; crossct+=abs(a)
print("  maker fills that CROSS zero (part entry, part exit, labelled 100%% EXIT): %d fills, %.0f ct opened-in-new-direction but tagged EXIT"%(cross,crossct))

# --- candles
span=defaultdict(lambda:[None,None])
for x in mk:
    t,ts=x["ticker"],int(x["ts"]); s=span[t]
    s[0]=ts if s[0] is None else min(s[0],ts); s[1]=ts if s[1] is None else max(s[1],ts)
CF='AUD_candles.json'
if os.path.exists(CF):
    candles={k:{int(a):b for a,b in v.items()} for k,v in json.load(open(CF)).items()}
else:
    candles={}
    for i,(t,(a,b)) in enumerate(span.items()):
        cs=DF.fetch_candles(t,a-180,b+86400+300)
        by={}
        for c in cs or []:
            ep=int(c.get("end_period_ts") or 0)
            if ep: by[ep]=c
        candles[t]=by
        print("  fetched %d/%d %s -> %d candles"%(i+1,len(span),t,len(by)),file=sys.stderr)
    json.dump(candles,open(CF,'w'))
print("\ntickers with candles: %d ; tickers with ZERO candles: %d"%(len(candles),sum(1 for v in candles.values() if not v)))
nmid=sum(1 for v in candles.values() for c in v.values() if mid_from_candle(c) is not None)
ntot=sum(len(v) for v in candles.values())
print("candle rows: %d, of which usable mid: %d (%.1f%%)"%(ntot,nmid,100*nmid/max(ntot,1)))

def mid_at(t,ts,tol_min=45):
    by=candles.get(t) or {}
    for back in range(0,tol_min*60,60):
        c=by.get((ts//60)*60-back)
        if c:
            m=mid_from_candle(c)
            if m is not None: return m,(ts//60)*60-back
    return None,None

# --- drop accounting + staleness
drop_px=drop_m0=0; dpx_ct=dm0_ct=0; kept=0; kept_ct=0
stale=defaultdict(lambda:[0,0,0])   # label -> [n, n_before_fill, n_same_candle_as_1m]
base_lag=[]
per_h_res={}
for x in mk:
    t,ts=x["ticker"],int(x["ts"]); ct=f(x.get("count_fp")); p=f(x.get("yes_price_dollars"))
    if not (0<p<1) or ct<=0: drop_px+=1; dpx_ct+=ct; continue
    m0,e0=mid_at(t,ts-60)
    if m0 is None: drop_m0+=1; dm0_ct+=ct; continue
    kept+=1; kept_ct+=ct
    base_lag.append(ts-e0)
    res={}
    for hs,lbl in HORIZONS:
        m,e=mid_at(t,ts+hs,tol_min=90)
        if m is None: continue
        res[lbl]=(m,e)
        s=stale[lbl]; s[0]+=1
        if e< ts: s[1]+=1
    for lbl in res:
        if lbl!="1m" and "1m" in res and res[lbl][1]==res["1m"][1]: stale[lbl][2]+=1
print("\nMARKOUT DROP ACCOUNTING (maker fills)")
print("  maker fills=%d ct=%.0f"%(len(mk),sum(f(x.get('count_fp')) for x in mk)))
print("  dropped bad price : %d fills %.0f ct"%(drop_px,dpx_ct))
print("  dropped no m0     : %d fills %.0f ct"%(drop_m0,dm0_ct))
print("  KEPT              : %d fills %.0f ct (%.1f%% of maker ct)"%(kept,kept_ct,100*kept_ct/sum(f(x.get('count_fp')) for x in mk)))
import statistics
print("  baseline candle lag behind fill ts: median %.0fs  p90 %.0fs  max %.0fs"%(
  statistics.median(base_lag),sorted(base_lag)[int(.9*len(base_lag))],max(base_lag)))
print("\nHORIZON RESOLUTION QUALITY (of %d kept fills)"%kept)
print("  %-5s %6s %22s %26s"%("hz","n","candle BEFORE the fill","same candle as the 1m mark"))
for _,lbl in HORIZONS:
    n,bf,sm=stale[lbl]
    print("  %-5s %6d %14d (%4.1f%%) %18d (%4.1f%%)"%(lbl,n,bf,100*bf/max(n,1),sm,100*sm/max(n,1)))
