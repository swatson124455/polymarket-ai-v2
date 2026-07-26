#!/usr/bin/env python3
"""READ-ONLY. Rich one-sided classifier: regime (flat/held-under-soft/throttle/hard/winddown/void/strand-ish),
reward counterfactual, intent (2-sided vs 1-sided by doctrine)."""
import sys, json, datetime as dt
from collections import defaultdict
import kalshi_attribution_ledger as L
from kalshi_market_scorecard import qualifying_share, _f

INV_TOL=1.0; INV_SOFT=15.0; INV_HARD=60.0
JOIN_SIZE=20; MAX_MKT_CAP=15.0; PER_SIDE=MAX_MKT_CAP/2.0; MAX_ACTIVATE=15.0
MINP,MAXP=0.04,0.96; WIND_DOWN_MIN=20; MAX_SPREAD_TICKS=8; MIN_DEPTH_SYM=0.25; TICK=0.01

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
    # event aggregate (sum signed pos across same series+date prefix)
    ev=defaultdict(float)
    for t,p in positions.items():
        parts=t.rsplit("-",1)
        ev[parts[0]]+=_f(p.get("position_fp"))
    progs=L.get(L.P+"/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    prog={}
    for p in progs:
        t=p.get("market_ticker")
        if not t: continue
        try:
            st=dt.datetime.fromisoformat(p["start_date"].replace("Z","+00:00"))
            en=dt.datetime.fromisoformat(p["end_date"].replace("Z","+00:00"))
            days=max((en-st).total_seconds()/86400,1/24)
        except Exception: days=1.0
        prog[t]={"usd_day":(_f(p.get("period_reward"))/10000)/days,
                 "target":_f(p.get("target_size_fp")) or 1000.0,
                 "df":(_f(p.get("discount_factor_bps"))/10000) or 0.5,
                 "end":p.get("end_date")}
    rows=[]
    for t in sorted(ours):
        oy,on=ours[t]["yes"],ours[t]["no"]
        if not((oy is None)^(on is None)): continue  # one-sided only
        pos=_f((positions.get(t) or {}).get("position_fp"))
        pr=prog.get(t)
        ob=L.get(L.P+f"/markets/{t}/orderbook").get("orderbook_fp",{}) or {}
        yb=[(_f(a),_f(b)) for a,b in (ob.get("yes_dollars") or [])]
        nb=[(_f(a),_f(b)) for a,b in (ob.get("no_dollars") or [])]
        tgt=pr["target"] if pr else 1000.0; df=pr["df"] if pr else 0.5; usd_day=pr["usd_day"] if pr else 0.0
        best_y=max((a for a,_ in yb),default=None); best_n=max((a for a,_ in nb),default=None)
        ext_y=sum(b for _,b in yb)-(oy[1] if oy else 0.0)
        ext_n=sum(b for _,b in nb)-(on[1] if on else 0.0)
        void = (ext_y<tgt) or (ext_n<tgt)
        mag=max(abs(pos),abs(ev.get(t.rsplit("-",1)[0],0.0)))
        rest_side = "yes" if oy else "no"
        reducing_side = "no" if pos>0 else ("yes" if pos<0 else None)
        rest_is_reducing = (reducing_side==rest_side)
        held_usd = abs(pos)*(best_y if pos>0 else best_n) if (best_y and best_n) else 0.0
        hard = (mag>=INV_HARD) or (held_usd>=MAX_MKT_CAP)
        # minutes to end
        mins=None
        if pr and pr.get("end"):
            try: mins=(dt.datetime.fromisoformat(pr["end"].replace("Z","+00:00"))-now).total_seconds()/60
            except: mins=None
        winddown = (mins is not None and mins<WIND_DOWN_MIN)
        flat = abs(pos)<INV_TOL
        # doctrine INTENT: how many sides desired_quotes would emit
        if winddown:
            intent = 1 if abs(pos)>=INV_TOL else 0
        elif flat:
            # selection gate check
            if best_y and best_n and not void:
                spread_ticks=(1.0-best_n-best_y)/TICK
                sym=min(ext_y,ext_n)/max(ext_y,ext_n,1e-9)
                intent = 0 if (spread_ticks>MAX_SPREAD_TICKS or sym<MIN_DEPTH_SYM) else 2
            else:
                intent = 2 if not void else 2  # void+flat -> activate both (unless standdown/event)
        else: # held
            if void: intent=1               # A2 reducing only
            elif hard: intent=1             # accumulating zeroed
            else: intent=2                  # accumulating join(+throttle) + reducing unwind
        regime = ("winddown" if winddown else "flat" if flat else
                  "held-hard" if hard else "held-throttle" if mag>INV_SOFT else "held-soft")
        # reward counterfactual: add missing side JOIN at best
        miss="yes" if oy is None else "no"
        bp = best_y if miss=="yes" else best_n
        ys,yq=qualifying_share(yb,oy[0] if oy else None,oy[1] if oy else 0.0,tgt,df)
        ns,nq=qualifying_share(nb,on[0] if on else None,on[1] if on else 0.0,tgt,df)
        cur_snap=(ys+ns)/2 if (yq and nq) else 0.0
        cf_snap=cur_snap; add_sz=0
        if bp and MINP<bp<=MAXP:
            add_sz=capped_join(bp)
            if miss=="yes":
                ys2,yq2=qualifying_share(yb+[(bp,add_sz)],bp,add_sz,tgt,df)
                cf_snap=(ys2+ns)/2 if (yq2 and nq) else 0.0
            else:
                ns2,nq2=qualifying_share(nb+[(bp,add_sz)],bp,add_sz,tgt,df)
                cf_snap=(ys+ns2)/2 if (yq and nq2) else 0.0
        d_raw=usd_day*(cf_snap-cur_snap); d_m7=d_raw/3.0
        rows.append({"t":t,"rest":("Y" if oy else "-")+("Y" if on else "-"),"pos":round(pos,2),
            "regime":regime,"intent":intent,"rest_is_reducing":rest_is_reducing,"void":void,
            "mag":round(mag,1),"held_usd":round(held_usd,2),"hard":hard,"mins":round(mins,0) if mins else None,
            "miss":miss,"add_sz":add_sz,"usd_day":round(usd_day,3),
            "cur_snap":round(cur_snap,4),"cf_snap":round(cf_snap,4),"d_reward_m7":round(d_m7,4)})
    print(json.dumps({"instant":now.strftime("%Y-%m-%dT%H:%M:%SZ"),"rows":rows},indent=1))

if __name__=="__main__": main()
