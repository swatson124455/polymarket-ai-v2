import kalshi_attribution_ledger as L, time, json
def mkts(t):
    try:
        r=L.get(f"{L.P}/markets?series_ticker={t}&status=open&limit=1000")
        return r.get("markets",[])
    except Exception as e:
        return [{"err":str(e)[:80]}]
def book_depth(tk):
    try:
        r=L.get(f"{L.P}/markets/{tk}/orderbook")
        ob=r.get("orderbook",{})
        yes=ob.get("yes") or []; no=ob.get("no") or []
        ys=sum(l[1] for l in yes); ns=sum(l[1] for l in no)
        return ys,ns
    except Exception as e:
        return -1,-1
for t in ["KXEOWEEK","KXUSGASCPI","KXH100MAX","KXNETFLIXTOPVIEWSMOVIE"]:
    ms=mkts(t); time.sleep(0.6)
    n=len(ms)
    # sample up to 4 markets for depth + mutex flag
    sample=ms[:4]
    two=0; depths=[]
    for m in sample:
        tk=m.get("ticker")
        if not tk: continue
        ys,ns=book_depth(tk); time.sleep(0.6)
        depths.append((tk[-12:],ys,ns))
        if ys>=1000 and ns>=1000: two+=1
    ex=ms[0] if ms else {}
    print(json.dumps({"series":t,"n_open":n,"can_close_early?":ex.get("can_close_early"),
        "strike_type":ex.get("strike_type"),"mutually_exclusive":ex.get("mutually_exclusive"),
        "yes_sub_title":(ex.get("yes_sub_title") or ex.get("subtitle") or "")[:40],
        "sample_depth_yes_no":depths, "sample_two_sided_at_1000":f"{two}/{len(depths)}"}))
