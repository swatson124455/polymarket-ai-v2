import json,os,sys,collections
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
act=json.load(open(os.path.join(OUT,"VER_active.json")))
ser=sorted(set(p["market_ticker"].split("-")[0] for p in act))
bad=[]
for s in ser:
    try:
        d=L.get(P+"/series/"+s); ok=(d.get("series") or {}).get("ticker")
        if ok!=s: bad.append((s,"mismatch",ok))
    except Exception as e:
        bad.append((s,"HTTP",str(e)))
print("candidate series checked: %d   NOT a real series ticker: %d"%(len(ser),len(bad)))
for b in bad: print("   ",b)
# wrong-merge probe: any multi-dash-prefix families where the 2nd segment is not a date?
fam=collections.defaultdict(set)
for p in act:
    t=p["market_ticker"].split("-")
    if len(t)>=3: fam[t[0]].add(t[1])
odd={k:sorted(v) for k,v in fam.items() if any(not (x[:2].isdigit()) for x in v)}
print("\nseries whose 2nd ticker segment is NOT a date (merge-risk candidates):")
for k,v in odd.items(): print("   %-24s 2nd-segments=%s"%(k,v[:8]))
# confirm the true series for the riskiest ones by market->event->series
for mt in ["KXBNBMAXY-BNB-26DEC31-65000","KXNEXTMANAGERMLB-BOS26-CTRA","KXTRUMPTIME-26AUG01-H1","KXROLEINPRODUCTIONDOOMSDAY-HUG"]:
    m=L.get(P+"/markets/"+mt)["market"]; ev=m["event_ticker"]
    e=L.get(P+"/events/"+ev)["event"]
    print("%-34s event=%-28s series=%-24s split0=%s  MATCH=%s"%(mt,ev,e.get("series_ticker"),mt.split("-")[0],e.get("series_ticker")==mt.split("-")[0]))
