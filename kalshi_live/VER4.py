import json,os,collections
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
act=json.load(open(os.path.join(OUT,"VER_active.json")))
ev=collections.defaultdict(list)
for p in act: ev["-".join(p["market_ticker"].split("-")[:2])].append(p)
varyR=varyW=0; ex=[]
for k,v in ev.items():
    if len(v)<2: continue
    R=set(p["period_reward"] for p in v); W=set((p["start_date"],p["end_date"]) for p in v)
    if len(R)>1: varyR+=1; ex.append(("REWARD",k,sorted(R),len(v)))
    if len(W)>1: varyW+=1
print("multi-strike events:",sum(1 for v in ev.values() if len(v)>1))
print("events w/ >1 distinct period_reward:",varyR)
print("events w/ >1 distinct window:",varyW)
for e in ex[:10]: print("  ",e)
print()
v=ev["KXAAAGASM-26JUL31"]
for p in sorted(v,key=lambda x:(x["start_date"],x["market_ticker"])):
    print("  %-30s r=%s %s -> %s"%(p["market_ticker"],p["period_reward"],p["start_date"],p["end_date"]))
print()
# reward value distribution
rd=collections.Counter(p["period_reward"] for p in act)
print("period_reward values (active):",sorted(rd.items(),key=lambda x:-x[1])[:12])
# window lengths
import datetime as dt
def P(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
wl=collections.Counter()
for p in act:
    d=(P(p["end_date"])-P(p["start_date"])).total_seconds()/86400.0
    wl[round(d,1)]+=1
print("window-days distribution:",sorted(wl.items())[:20])
# duplicate market_ticker within active? (would mean same market has multiple programs)
tc=collections.Counter(p["market_ticker"] for p in act)
print("markets w/ >1 active program:",sum(1 for c in tc.values() if c>1), "max",max(tc.values()))
print("distinct program ids:",len(set(p["id"] for p in act)),"distinct market_ids:",len(set(p["market_id"] for p in act)))
