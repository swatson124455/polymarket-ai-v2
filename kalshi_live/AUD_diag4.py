import json,random,statistics
from collections import defaultdict,deque
exec(open('AUD_diag3.py').read().split("print(\"total matched")[0])
BUCK=[(60,"<1m"),(300,"1-5m"),(1800,"5-30m"),(7200,"30m-2h"),(21600,"2-6h"),(86400,"6-24h"),(10**9,">24h")]
def bucket(dt):
    for lim,l in BUCK:
        if dt<lim: return l
    return ">24h"
print("=== DECOMPOSITION BY HOLDING-TIME BUCKET (maker-only exits) ===")
print("%-8s %6s %7s %9s %9s %9s %9s"%("hold","trips","ct","entry_c","exit_c","drift_c","real_c"))
agg=defaultdict(lambda:[0,0.,0.,0.,0.,0.])
for r in rows:
    if r['tk']: continue
    me=mid_at(r['t'],r['ets']); mx=mid_at(r['t'],r['xts'])
    if me is None or mx is None: continue
    a=agg[bucket(r['dt'])]
    a[0]+=1;a[1]+=r['q']
    a[2]+=(me-r['epx'])*r['q']*r['ed']; a[3]+=(mx-r['xpx'])*r['q']*(-r['ed'])
    a[4]+=(mx-me)*r['q']*r['ed']; a[5]+=r['r']
for _,l in BUCK:
    if l in agg:
        n,q,e,x,d,rr=agg[l]
        print("%-8s %6d %7.0f %+8.2fc %+8.2fc %+8.2fc %+8.2fc"%(l,n,q,100*e/q,100*x/q,100*d/q,100*rr/q))
print("\n=== TRIP-LEVEL BOOTSTRAP, <1m MAKER-ONLY (n=10 trips) ===")
sub=[r for r in rows if r['dt']<60 and not r['tk']]
random.seed(7)
tots=[];pc=[]
for _ in range(20000):
    s=[random.choice(sub) for _ in sub]
    tots.append(sum(z['r'] for z in s)); pc.append(100*sum(z['r'] for z in s)/sum(z['q'] for z in s))
tots.sort();pc.sort()
print("  realized$   point %+.2f   95%% CI [%+.2f, %+.2f]"%(sum(r['r'] for r in sub),tots[500],tots[19499]))
print("  per_ct c    point %+.2f   95%% CI [%+.2f, %+.2f]   P(<=0)=%.3f"%(
  100*sum(r['r'] for r in sub)/sum(r['q'] for r in sub),pc[500],pc[19499],sum(1 for v in pc if v<=0)/len(pc)))
# ticker-block bootstrap (6 clusters)
byt=defaultdict(list)
for r in sub: byt[r['t']].append(r)
ks=list(byt)
tots2=[]
for _ in range(20000):
    s=[]
    for _ in ks: s+=byt[random.choice(ks)]
    tots2.append(sum(z['r'] for z in s))
tots2.sort()
print("  ticker-block bootstrap (6 clusters): 95%% CI on realized$ [%+.2f, %+.2f]  P(<=0)=%.3f"%(
  tots2[500],tots2[19499],sum(1 for v in tots2 if v<=0)/len(tots2)))
print("\n  <1m maker bucket share of maker round-trip volume: %.0f/%.0f ct = %.1f%%"%(
  sum(r['q'] for r in sub),sum(r['q'] for r in rows if not r['tk']),
  100*sum(r['q'] for r in sub)/sum(r['q'] for r in rows if not r['tk'])))
print("  trips with dt<=2s (simultaneous both-sides fill, not a 'fast flatten'): %d of %d"%(
  sum(1 for r in sub if r['dt']<=2),len(sub)))
