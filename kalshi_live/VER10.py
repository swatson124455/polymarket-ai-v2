import json,os,sys,datetime as dt,collections
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
recs=[];cur="";pg=0
while pg<40:
    d=L.get(P+"/incentive_programs?limit=10000"+(("&cursor="+cur) if cur else ""))
    b=d.get("incentive_programs") or []
    recs+=[(p["id"],p["market_ticker"],p["period_reward"],p["start_date"],p["end_date"],p.get("incentive_type")) for p in b]
    pg+=1; cur=d.get("next_cursor") or ""
    print("  page %d n=%d total=%d"%(pg,len(b),len(recs)))
    if not cur: break
print("UNFILTERED total=%d pages=%d cursor_left=%s"%(len(recs),pg,bool(cur)))
def T(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
now=dt.datetime.now(dt.timezone.utc)
liq=[r for r in recs if (r[5] or "liquidity")=="liquidity"]
fut=[r for r in liq if T(r[3])>now]; live=[r for r in liq if T(r[3])<=now<T(r[4])]
print("liquidity=%d volume=%d"%(len(liq),len(recs)-len(liq)))
print("FUTURE-start programs: %d   live-now: %d   ended: %d"%(len(fut),len(live),len(liq)-len(fut)-len(live)))
if fut: print("  examples:",fut[:5])
act=json.load(open(os.path.join(OUT,"VER_active.json")))
ai=set(p["id"] for p in act); li=set(r[0] for r in live)
print("live-now == status=active ? %s  (live_not_active=%d, active_not_live=%d)"%(li==ai,len(li-ai),len(ai-li)))
allser=set(r[1].split("-")[0] for r in liq)
print("distinct series across ALL history: %d  (active-only: %d)"%(len(allser),len(set(p['market_ticker'].split('-')[0] for p in act))))
rr=sorted(r[2] for r in liq)
print("period_reward all-history $: min=%.2f max=%.2f ; frac in [10,1000]=%.3f"%(rr[0]/1e4,rr[-1]/1e4,sum(1 for v in rr if 100000<=v<=10000000)/len(rr)))
json.dump([r for r in liq],open(os.path.join(OUT,"VER_all_slim.json"),"w"))
