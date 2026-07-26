#!/usr/bin/env python3
"""READ-ONLY final cost/benefit. Naive vs bot-intent reward baseline + fill-rate risk."""
import sys, json, math, datetime as dt
from collections import defaultdict
import kalshi_attribution_ledger as L
from kalshi_market_scorecard import qualifying_share, _f

INV_TOL=1.0; INV_SOFT=15.0; INV_HARD=60.0; MIN_QUOTE_CT=2
JOIN_SIZE=20; MAX_MKT_CAP=15.0; PER_SIDE=MAX_MKT_CAP/2.0
MINP,MAXP=0.04,0.96; WIND_DOWN_MIN=20; MAX_SPREAD_TICKS=8; MIN_DEPTH_SYM=0.25; TICK=0.01
THROTTLE_STEP_TICKS=1; ADV_PER_CT=0.011   # gas fingerprint adverse cost $/ct (worse elsewhere)

def capped_join(best):
    if best<=0: return 0
    return max(1,min(JOIN_SIZE,int(PER_SIDE/best)))

def main():
    now=dt.datetime.now(dt.timezone.utc)
    orders=L.get(L.P+"/portfolio/orders?status=resting").get("orders") or []
    ours=defaultdict(lambda:{"yes":None,"no":None})
    for o in orders:
        t=o.get("ticker"); s=o.get("outcome_side")
        px=_f(o.get("yes_price_dollars") if s=="yes" else o.get("no_price_dollars"))
        sz=_f(o.get("remaining_count_fp")); cur=ours[t][s]
        ours[t][s]=(px,sz+(cur[1] if cur else 0.0)) if px>0 else cur
    positions={p.get("ticker"):p for p in (L.get(L.P+"/portfolio/positions").get("market_positions") or [])}
    # event ladder deltas (all held)
    held_by={t:_f(p.get("position_fp")) for t,p in positions.items() if _f(p.get("position_fp"))!=0}
    evsum=defaultdict(float)
    for t,v in held_by.items(): evsum["-".join(t.split("-")[:2])]+=v
    # fill rate per ticker per side (ct/side/day) from fills
    fills=L.get(L.P+"/portfolio/fills?limit=1000").get("fills") or []
    fr=defaultdict(lambda:defaultdict(float)); times=[]
    for f in fills:
        if f.get("is_taker"): continue
        times.append(f.get("created_time"))
        fr[f.get("market_ticker")][f.get("outcome_side")]+=_f(f.get("count_fp"))
    tt=[x for x in times if x]
    days=max((dt.datetime.fromisoformat(max(tt).replace("Z","+00:00"))-dt.datetime.fromisoformat(min(tt).replace("Z","+00:00"))).total_seconds()/86400,1e-9)
    progs=L.get(L.P+"/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    prog={}
    for p in progs:
        t=p.get("market_ticker")
        if not t: continue
        try:
            st=dt.datetime.fromisoformat(p["start_date"].replace("Z","+00:00"))
            en=dt.datetime.fromisoformat(p["end_date"].replace("Z","+00:00"))
            d2=max((en-st).total_seconds()/86400,1/24)
        except Exception: d2=1.0
        prog[t]={"usd_day":(_f(p.get("period_reward"))/10000)/d2,"target":_f(p.get("target_size_fp")) or 1000.0,
                 "df":(_f(p.get("discount_factor_bps"))/10000) or 0.5,"end":p.get("end_date")}
    rows=[]
    for t in sorted(ours):
        oy,on=ours[t]["yes"],ours[t]["no"]
        if not((oy is None)^(on is None)): continue
        pos=_f((positions.get(t) or {}).get("position_fp")); pr=prog.get(t)
        ob=L.get(L.P+f"/markets/{t}/orderbook").get("orderbook_fp",{}) or {}
        yb=[(_f(a),_f(b)) for a,b in (ob.get("yes_dollars") or [])]
        nb=[(_f(a),_f(b)) for a,b in (ob.get("no_dollars") or [])]
        tgt=pr["target"] if pr else 1000.0; df=pr["df"] if pr else 0.5; usd_day=pr["usd_day"] if pr else 0.0
        best_y=max((a for a,_ in yb),default=None); best_n=max((a for a,_ in nb),default=None)
        ext_y=sum(b for _,b in yb)-(oy[1] if oy else 0.0); ext_n=sum(b for _,b in nb)-(on[1] if on else 0.0)
        void=(ext_y<tgt) or (ext_n<tgt)
        evd=evsum.get("-".join(t.split("-")[:2]),0.0)
        mag=max(abs(pos),abs(evd))
        held_usd=abs(pos)*(best_y if pos>0 else best_n) if (best_y and best_n) else 0.0
        hard=(mag>=INV_HARD) or (held_usd>=MAX_MKT_CAP)
        mins=None
        if pr and pr.get("end"):
            try: mins=(dt.datetime.fromisoformat(pr["end"].replace("Z","+00:00"))-now).total_seconds()/60
            except: pass
        winddown=(mins is not None and mins<WIND_DOWN_MIN)
        flat=abs(pos)<INV_TOL
        miss="yes" if oy is None else "no"; bp=best_y if miss=="yes" else best_n
        ys,yq=qualifying_share(yb,oy[0] if oy else None,oy[1] if oy else 0.0,tgt,df)
        ns,nq=qualifying_share(nb,on[0] if on else None,on[1] if on else 0.0,tgt,df)
        cur_snap=(ys+ns)/2 if (yq and nq) else 0.0
        # regime + intent
        if winddown: regime,intent="winddown",(1 if abs(pos)>=INV_TOL else 0)
        elif flat: regime,intent="flat",2
        elif void: regime,intent="held-void",1
        elif hard: regime,intent="held-hard",1
        elif mag>INV_SOFT: regime,intent="held-throttle",2
        else: regime,intent="held-soft",2
        # NAIVE CF: full join both sides
        def cf_full():
            if not(bp and MINP<bp<=MAXP): return cur_snap
            asz=capped_join(bp)
            if miss=="yes":
                y2,yq2=qualifying_share(yb+[(bp,asz)],bp,asz,tgt,df); return (y2+ns)/2 if(yq2 and nq) else 0.0
            n2,nq2=qualifying_share(nb+[(bp,asz)],bp,asz,tgt,df); return (ys+n2)/2 if(yq and nq2) else 0.0
        # INTENT CF: what doctrine would rest (flat->full join; throttle->stepped+shrunk; hard/void/wd->one-sided=cur)
        def cf_intent():
            if intent!=2: return cur_snap
            if flat: return cf_full()
            # held-throttle: accumulating (missing) side stepped 1 tick behind & shrunk
            if not(bp and MINP<bp<=MAXP): return cur_snap
            over=min(1.0,(mag-INV_SOFT)/max(1.0,INV_HARD-INV_SOFT))
            asz=max(MIN_QUOTE_CT,int(capped_join(bp)*(1-over)))
            ap=round(bp-TICK*THROTTLE_STEP_TICKS,4)
            if not(MINP<ap<=MAXP): return cur_snap
            if miss=="yes":
                y2,yq2=qualifying_share(yb+[(ap,asz)],ap,asz,tgt,df); return (y2+ns)/2 if(yq2 and nq) else 0.0
            n2,nq2=qualifying_share(nb+[(ap,asz)],ap,asz,tgt,df); return (ys+n2)/2 if(yq and nq2) else 0.0
        cfF=cf_full(); cfI=cf_intent()
        dF=usd_day*(cfF-cur_snap)/3.0    # naive, /3 per M7
        dI=usd_day*(cfI-cur_snap)/3.0    # bot-intent
        # risk avoided: missing-side fill rate/day * adv/ct
        mfr=fr[t][miss]/days
        risk_avoided=mfr*ADV_PER_CT
        rows.append({"t":t,"rest":("Y" if oy else "-")+("Y" if on else "-"),"pos":round(pos,1),"evd":round(evd,1),
            "regime":regime,"intent":intent,"usd_day":round(usd_day,2),"miss":miss,
            "cur":round(cur_snap,4),"cf_full":round(cfF,4),"cf_intent":round(cfI,4),
            "dRwd_naive":round(dF,4),"dRwd_intent":round(dI,4),
            "miss_fill_ct_day":round(mfr,2),"risk_avoid_day":round(risk_avoided,4)})
    print(json.dumps({"instant":now.strftime("%Y-%m-%dT%H:%M:%SZ"),"fill_window_days":round(days,2),"rows":rows},indent=1))

if __name__=="__main__": main()
