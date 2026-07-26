import json,sys,os,datetime as dt
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
def pull(status,maxp=300):
    progs=[];cur="";pages=0
    while pages<maxp:
        q=P+"/incentive_programs?limit=1000"+(("&status="+status) if status else "")+(("&cursor="+cur) if cur else "")
        d=L.get(q); b=d.get("incentive_programs") or []
        progs+=b; pages+=1; cur=d.get("next_cursor") or ""
        if not cur: break
    return progs,pages,cur
act,pg,cur=pull("active")
print("ACTIVE n=%d pages=%d cursor_left=%s"%(len(act),pg,bool(cur)))
json.dump(act,open(os.path.join(OUT,"VER_active.json"),"w"))
print("KEYS:",sorted(act[0].keys()))
print("SAMPLE:",json.dumps(act[0],indent=1))
