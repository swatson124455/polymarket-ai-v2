import json,os,collections,datetime as dt,sys
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
act=json.load(open(os.path.join(OUT,"VER_active.json")))
def T(s): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
now=dt.datetime.now(dt.timezone.utc)
# --- R1-reading internal consistency vs documented $10-1000/day floor
below=0; above=0; vals=[]
for p in act:
    d=max((T(p["end_date"])-T(p["start_date"])).total_seconds()/86400.0,1/1440)
    r=float(p["period_reward"])/10000.0/d
    vals.append(r)
    if r<10: below+=1
    if r>1000: above+=1
vals.sort()
print("R1 reading (reward/window_days): n=%d  below $10/day=%d (%.0f%%)  above $1000/day=%d  min=%.2f p50=%.2f max=%.1f"
      %(len(vals),below,100*below/len(vals),above,vals[0],vals[len(vals)//2],vals[-1]))
raw=sorted(float(p["period_reward"])/10000.0 for p in act)
print("RAW period_reward $: min=%.2f p50=%.2f max=%.2f  in[10,1000]=%d/%d (%.1f%%)"
      %(raw[0],raw[len(raw)//2],raw[-1],sum(1 for v in raw if 10<=v<=1000),len(raw),
        100*sum(1 for v in raw if 10<=v<=1000)/len(raw)))
print()
# --- verify real event membership for the 7 varying-reward groups
groups=["KXA100MS-26JUL","KXCOINBASE-26JULVOL","KXNEXTMANAGERMLB-BOS26"]
by={}
for p in act: by[p["market_ticker"]]=p
for g in groups:
    tk=[t for t in by if t.startswith(g+"-") or t==g]
    print("== group",g)
    for t in sorted(tk):
        try:
            m=L.get(P+"/markets/"+t)["market"]
            print("   %-34s event=%-26s reward=%-8s %s->%s"%(t,m.get("event_ticker"),by[t]["period_reward"],by[t]["start_date"][:16],by[t]["end_date"][:16]))
        except Exception as e:
            print("   %-34s ERR %s"%(t,e))
