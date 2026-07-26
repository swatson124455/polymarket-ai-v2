import json,os,collections,datetime as dt
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
act=json.load(open(os.path.join(OUT,"VER_active.json")))
uni=json.load(open(os.path.join(OUT,"universe.json")))
OURS=set("KXTEMPDCH KXTEMPAUSH KXTEMPLAXH KXTEMPNYCH KXTEMPCHIH KXAAAGASD KXAAAGASW KXB200MON KXAMSAVO KXH100MON KXMUSKNW KXCHIPBURRITO KXTRUMPENDORSEMENTS KXGENERICBALLOTVOTEHUB".split())
print("OURS tokens:",len(OURS))
present={r["series"] for r in uni}
print("universe.json rows:",len(uni)," ours flagged:",sum(1 for r in uni if r["ours"]))
print("OURS present in universe.json:",sorted(OURS&present))
print("OURS ABSENT (no ACTIVE program at scan time):",sorted(OURS-present))
def T(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
R1=collections.defaultdict(float); R2=collections.defaultdict(float); N=collections.Counter()
for p in act:
    s=p["market_ticker"].split("-")[0]
    d=max((T(p["end_date"])-T(p["start_date"])).total_seconds()/86400.0,1/1440)
    R1[s]+=(p["period_reward"]/10000.0)/d
    R2[s]+=p["period_reward"]/10000.0
    N[s]+=1
print("\nreproduce script: R1 total $%.0f/day over %d series (universe.json total $%.0f)"%(sum(R1.values()),len(R1),sum(r["usd_day"] for r in uni)))
print("CORRECTED (period_reward = daily, no /window): total $%.0f/day"%sum(R2.values()))
r1=sorted(R1,key=lambda s:-R1[s]); r2=sorted(R2,key=lambda s:-R2[s])
p1={s:i+1 for i,s in enumerate(r1)}; p2={s:i+1 for i,s in enumerate(r2)}
print("\n%-24s %10s %6s %10s %6s %8s"%("series","R1 $/day","rank","TRUE $/day","rank","ratio"))
for s in r2[:12]:
    print("%-24s %10.2f %6d %10.2f %6d %8.1fx"%(s,R1[s],p1[s],R2[s],p2[s],R2[s]/max(R1[s],1e-9)))
print("... top-10 R1 vs top-10 TRUE overlap: %d/10"%len(set(r1[:10])&set(r2[:10])))
print("top-30 overlap: %d/30"%len(set(r1[:30])&set(r2[:30])))
import statistics
mv=[abs(p1[s]-p2[s]) for s in R1]
print("rank shift: median %d, max %d, series moving >20 places: %d/%d"%(statistics.median(mv),max(mv),sum(1 for x in mv if x>20),len(mv)))
print("\nR1 winners that TRUE demotes (R1 top10 -> true rank):",[(s,p1[s],p2[s]) for s in r1[:10]])
