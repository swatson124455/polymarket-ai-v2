import kalshi_attribution_ledger as L
from collections import defaultdict
from kalshi_market_scorecard import qualifying_share, _f
import datetime as dt
TICK=0.01

def bounded_share(bids, our_price, our_size, target, df):
    """Correct LIP: our_score / (total_qualifying_including_us). Returns (share, book_qualifies)."""
    bids=sorted(((p,s) for p,s in bids if s>0), key=lambda x:-x[0])
    if not bids or bids[0][0]>=1.0: return 0.0, False
    ref=bids[0][0]; cum=total=0.0; lowest=ref
    for price,size in bids:
        n=round((ref-price)/TICK); total+=(df**n)*size; cum+=size; lowest=price
        if cum>=target: break
    if cum<target: return 0.0, False
    if our_price is None or our_price < lowest-1e-9: return 0.0, True
    ourscore=(df**round((ref-our_price)/TICK))*our_size
    # if our order is NOT already in the book, add it to the denominator
    return ourscore/(total+ourscore if ourscore>0 else total), True

orders=L.get(L.P+"/portfolio/orders?status=resting").get("orders") or []
pos={p["ticker"]:p for p in (L.get(L.P+"/portfolio/positions").get("market_positions") or [])}
progs=L.get(L.P+"/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
prog={}
for p in progs:
    t=p.get("market_ticker")
    if not t: continue
    try:
        st=dt.datetime.fromisoformat(p["start_date"].replace("Z","+00:00"))
        en=dt.datetime.fromisoformat(p["end_date"].replace("Z","+00:00"))
        days=max((en-st).total_seconds()/86400,1/24)
    except: days=1.0
    prog[t]={"usd_day":(_f(p.get("period_reward"))/10000)/days,"target":_f(p.get("target_size_fp")) or 1000.0,"df":(_f(p.get("discount_factor_bps"))/10000) or 0.5}

ours=defaultdict(lambda:{"yes":None,"no":None})
for o in orders:
    t=o.get("ticker"); s=o.get("outcome_side")
    if not t: continue
    px=_f(o.get("yes_price_dollars") if s=="yes" else o.get("no_price_dollars"))
    sz=_f(o.get("remaining_count_fp"))
    cur=ours[t][s]
    ours[t][s]=(px,sz+(cur[1] if cur else 0.0)) if px>0 else cur

onesided=[t for t in ours if bool(ours[t]["yes"])!=bool(ours[t]["no"])]
print(f"ONE-SIDED now: {len(onesided)}  (bounded LIP share, missing-side @20ct at touch)")
print(f"{'ticker':<32}{'rest':>4}{'pos':>5}{'restShr':>9}{'missShr':>9}{'pool/d':>8}{'fgMdl/d':>9}{'fgRcpt/d':>9}")
tm=tr=0.0
for t in sorted(onesided):
    pr=prog.get(t)
    ob=L.get(L.P+f"/markets/{t}/orderbook").get("orderbook_fp",{}) or {}
    yb=[(_f(p),_f(s)) for p,s in (ob.get("yes_dollars") or [])]
    nb=[(_f(p),_f(s)) for p,s in (ob.get("no_dollars") or [])]
    tgt=pr["target"] if pr else 1000.0; df=pr["df"] if pr else 0.5
    oy,on=ours[t]["yes"],ours[t]["no"]
    # resting side share via original (order already in book) - keep consistent w/ scorecard
    ys,yq=qualifying_share(yb,oy[0] if oy else None,oy[1] if oy else 0.0,tgt,df)
    ns,nq=qualifying_share(nb,on[0] if on else None,on[1] if on else 0.0,tgt,df)
    book2s=yq and nq
    restshr=ns if on else ys
    if on:
        bb=max((p for p,s in yb if s>0),default=None)
        miss,_=bounded_share(yb,bb,20.0,tgt,df)
    else:
        bb=max((p for p,s in nb if s>0),default=None)
        miss,_=bounded_share(nb,bb,20.0,tgt,df)
    pool=pr["usd_day"] if pr else 0.0
    fg=pool*(miss/2) if book2s else 0.0
    fgr=fg/5.0
    tm+=fg; tr+=fgr
    pf=_f(pos.get(t,{}).get("position_fp"))
    print(f"{t[:32]:<32}{('-Y' if on else 'Y-'):>4}{pf:5.0f}{restshr*100:8.2f}%{miss*100:8.2f}%{pool:8.2f}{fg:9.3f}{fgr:9.3f}")
print(f"{'TOTAL forgone missing-side':<57}{tm:9.3f}{tr:9.3f}")
print("\n[fgMdl = pool*(missShr/2), model. fgRcpt = /5 per M7d receipt haircut]")
