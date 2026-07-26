import kalshi_attribution_ledger as L
from collections import defaultdict
from kalshi_market_scorecard import qualifying_share, _f
TICK=0.01

orders = L.get(L.P+"/portfolio/orders?status=resting").get("orders") or []
pos = {p["ticker"]: p for p in (L.get(L.P+"/portfolio/positions").get("market_positions") or [])}
progs = L.get(L.P+"/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
import datetime as dt
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

# find one-sided markets
onesided=[t for t in ours if bool(ours[t]["yes"]) != bool(ours[t]["no"])]
print(f"ONE-SIDED markets now: {len(onesided)}")
print(f"{'ticker':<34}{'rest':>5}{'pos':>6}{'restShr':>9}{'missShr_cf':>11}{'pool/d':>8}{'fgModel/d':>10}{'fgRecpt/d':>10}")
tot_model=tot_recpt=0.0
for t in sorted(onesided):
    pr=prog.get(t)
    ob=L.get(L.P+f"/markets/{t}/orderbook").get("orderbook_fp",{}) or {}
    yb=[(_f(p),_f(s)) for p,s in (ob.get("yes_dollars") or [])]
    nb=[(_f(p),_f(s)) for p,s in (ob.get("no_dollars") or [])]
    tgt=pr["target"] if pr else 1000.0
    df=pr["df"] if pr else 0.5
    oy,on=ours[t]["yes"],ours[t]["no"]
    ys,yq=qualifying_share(yb,oy[0] if oy else None,oy[1] if oy else 0.0,tgt,df)
    ns,nq=qualifying_share(nb,on[0] if on else None,on[1] if on else 0.0,tgt,df)
    book2s=yq and nq
    restside = "no" if on else "yes"
    restshr = ns if on else ys
    # counterfactual: add 20ct at best-bid touch on the MISSING side
    if on:  # missing YES; put 20 at best yes bid
        bb = max((p for p,s in yb if s>0), default=None)
        miss_shr,_ = qualifying_share(yb, bb, 20.0, tgt, df) if bb else (0.0,False)
    else:   # missing NO
        bb = max((p for p,s in nb if s>0), default=None)
        miss_shr,_ = qualifying_share(nb, bb, 20.0, tgt, df) if bb else (0.0,False)
    pool = pr["usd_day"] if pr else 0.0
    # one-sided our_snap = restshr/2 ; two-sided = (restshr+miss_shr)/2 ; delta = miss_shr/2
    fg_model = pool * (miss_shr/2) if book2s else 0.0
    fg_recpt = fg_model/5.0   # M7d ~5x receipt haircut
    tot_model += fg_model; tot_recpt += fg_recpt
    p=pos.get(t,{})
    pf=_f(p.get("position_fp"))
    print(f"{t[:34]:<34}{('-Y' if on else 'Y-'):>5}{pf:6.0f}{restshr*100:8.2f}%{miss_shr*100:10.2f}%{pool:8.2f}{fg_model:10.3f}{fg_recpt:10.3f}")
print(f"{'TOTAL forgone (missing-side reward)':<64}{tot_model:10.3f}{tot_recpt:10.3f}")
