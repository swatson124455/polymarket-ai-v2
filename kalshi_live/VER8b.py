import json,os,sys,datetime as dt
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
recs=[];cur="";pg=0
while pg<400:
    d=L.get(P+"/incentive_programs?limit=1000"+(("&cursor="+cur) if cur else ""))
    b=d.get("incentive_programs") or []
    recs+=[(p["id"],p["market_ticker"],p["period_reward"],p["start_date"],p["end_date"],p.get("incentive_type")) for p in b]
    pg+=1; cur=d.get("next_cursor") or ""
    if not cur: break
print("UNFILTERED n=%d pages=%d cursor_left=%s"%(len(recs),pg,bool(cur)))
json.dump(recs,open(os.path.join(OUT,"VER_all_slim.json"),"w"))
