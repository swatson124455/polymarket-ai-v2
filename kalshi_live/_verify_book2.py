import kalshi_attribution_ledger as L, time, json
def mkts(t):
    r=L.get(f"{L.P}/markets?series_ticker={t}&status=open&limit=1000")
    return r.get("markets",[])
def depth(tk):
    r=L.get(f"{L.P}/markets/{tk}/orderbook")
    ob=r.get("orderbook",{})
    yes=ob.get("yes") or []; no=ob.get("no") or []
    return sum(l[1] for l in yes), sum(l[1] for l in no)
for t in ["KXVOTEPRIMARY","KXH100MAX","KXAAAGASD"]:
    ms=mkts(t); time.sleep(0.6)
    rows=[]; two1000=0; twoAny=0
    for m in ms:
        tk=m.get("ticker")
        ys,ns=depth(tk); time.sleep(0.6)
        rows.append((tk.split("-")[-1],ys,ns))
        if ys>=1000 and ns>=1000: two1000+=1
        if ys>0 and ns>0: twoAny+=1
    print(json.dumps({"series":t,"n":len(ms),"two_at_1000":f"{two1000}/{len(ms)}",
        "two_any_depth":f"{twoAny}/{len(ms)}","depths":rows}))
