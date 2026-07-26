import json,os,sys,datetime as dt,collections
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
# map the status enum: 0 results => value RECOGNISED; 2 results => value IGNORED (unfiltered)
for v in ["active","closed","upcoming","pending","scheduled","inactive","expired","finished",
          "paid_out","future","ACTIVE","Active","zzzznotreal"]:
    d=L.get(P+"/incentive_programs?limit=2&status="+v)
    n=len(d.get("incentive_programs") or [])
    print("  status=%-14s n=%d  -> %s"%(v,n,"RECOGNISED(empty)" if n==0 else "returns rows"))
# full unfiltered pull, slim
recs=[];cur="";pg=0
while pg<200:
    d=L.get(P+"/incentive_programs?limit=1000"+(("&cursor="+cur) if cur else ""))
    b=d.get("incentive_programs") or []
    recs+= [(p["id"],p["market_ticker"],p["period_reward"],p["start_date"],p["end_date"],p.get("incentive_type")) for p in b]
    pg+=1; cur=d.get("next_cursor") or ""
    if not cur: break
print("UNFILTERED n=%d pages=%d cursor_left=%s"%(len(recs),pg,bool(cur)))
json.dump(recs,open(os.path.join(OUT,"VER_all_slim.json"),"w"))
now=dt.datetime.now(dt.timezone.utc)
def T(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
fut=[r for r in recs if T(r[3])>now]
live=[r for r in recs if T(r[3])<=now<T(r[4])]
past=[r for r in recs if T(r[4])<=now]
print("future-start=%d  live-now=%d  ended=%d"%(len(fut),len(live),len(past)))
act=json.load(open(os.path.join(OUT,"VER_active.json")))
aid=set(p["id"] for p in act); allid=set(r[0] for r in recs)
print("active subset of unfiltered? %s   active_not_in_unfiltered=%d"%(aid<=allid,len(aid-allid)))
liveid=set(r[0] for r in live)
print("live-now n=%d ; live-now NOT in status=active: %d ; active NOT live-now: %d"%(len(liveid),len(liveid-aid),len(aid-liveid)))
for r in list(liveid-aid)[:5]:
    print("   missed:",[x for x in recs if x[0]==r][0])
print("first 3 unfiltered:",recs[:3])
print("last 3 unfiltered:",recs[-3:])
