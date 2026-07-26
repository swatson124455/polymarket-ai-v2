import json,os,collections,datetime as dt
OUT=r"C:\Users\samwa\AppData\Local\Temp\claude\C--lockes-picks-polymarket-ai-v2\8289ccb6-6121-4cea-8b80-ab6ee71a2ab1\scratchpad"
act=json.load(open(os.path.join(OUT,"VER_active.json")))
ev=collections.defaultdict(list)
for p in act:
    t=p["market_ticker"]; ev["-".join(t.split("-")[:2])].append(p)
print("events with programs:",len(ev))
big=sorted(ev.items(),key=lambda kv:-len(kv[1]))[:6]
for k,v in big:
    prs=set(p["period_reward"] for p in v); st=set(p["start_date"] for p in v); en=set(p["end_date"] for p in v)
    tg=set(p["target_size_fp"] for p in v)
    print("%-28s strikes=%3d  period_reward=%s  distinct_start=%d distinct_end=%d end=%s target=%s"%(k,len(v),sorted(prs),len(st),len(en),sorted(en)[:2],sorted(tg)))
print()
for k in ["KXAAAGASD-26JUL25","KXAAAGASD-26JUL23","KXAAAGASD-26JUL26","KXAAAGASW-26JUL27"]:
    v=ev.get(k)
    if not v: print(k,"-- not in active set"); continue
    print("==",k,"strikes=",len(v))
    for p in sorted(v,key=lambda x:x["market_ticker"]):
        print("   %-34s reward=%-9s start=%s end=%s tgt=%s"%(p["market_ticker"],p["period_reward"],p["start_date"],p["end_date"],p["target_size_fp"]))
