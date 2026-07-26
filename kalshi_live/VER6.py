import json,os,collections,datetime as dt,sys
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
def pull(status,maxp=60):
    r=[];cur="";pg=0
    while pg<maxp:
        d=L.get(P+"/incentive_programs?limit=1000"+(("&status="+status) if status else "")+(("&cursor="+cur) if cur else ""))
        r+=d.get("incentive_programs") or []; pg+=1; cur=d.get("next_cursor") or ""
        if not cur: break
    return r
clo=pull("closed")
json.dump(clo,open(os.path.join(OUT,"VER_closed.json"),"w"))
print("closed n=",len(clo))
want=["KXTEMPNYCH-26JUL2206","KXAAAGASD-26JUL25","KXAAAGASD-26JUL23","KXTEMPAUSH-26JUL2021","KXTEMPCHIH-26JUL2211"]
byev=collections.defaultdict(list)
for p in clo+json.load(open(os.path.join(OUT,"VER_active.json"))):
    byev["-".join(p["market_ticker"].split("-")[:2])].append(p)
for w in want:
    v=byev.get(w,[])
    if not v: print(w,"NOT FOUND in active+closed"); continue
    R=sorted(set(p["period_reward"] for p in v)); W=sorted(set((p["start_date"],p["end_date"]) for p in v))
    d0=[(dt.datetime.fromisoformat(b.replace("Z","+00:00"))-dt.datetime.fromisoformat(a.replace("Z","+00:00"))).total_seconds()/86400.0 for a,b in W]
    print("%-24s strikes=%2d reward$=%s window_days=%s  window=%s"%(w,len(v),[r/10000 for r in R],[round(x,2) for x in d0],W[0]))
