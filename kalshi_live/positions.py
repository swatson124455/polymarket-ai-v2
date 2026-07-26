"""Every open position: is it EARNING, and what is the cheapest way out? READ-ONLY."""
import sys, datetime as dt
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q
c = q.KalshiOrderClient()
def iso(s): return dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
now = q.utcnow()

resting = c.get_orders(status="resting").get("orders") or []
print(f"RESTING ORDERS RIGHT NOW: {len(resting)}")
print("  LIP pays for RESTING ORDERS, not for positions. Zero resting = zero reward accruing.\n")

progs, cur = [], ""
for _ in range(8):
    d = q.public_get("/trade-api/v2/incentive_programs?status=active&limit=1000"+(("&cursor="+cur) if cur else ""))
    progs += d.get("incentive_programs") or []; cur = d.get("next_cursor") or ""
    if not cur: break
pmap = {p.get("market_ticker"): p for p in progs if (p.get("incentive_type") or "liquidity")=="liquidity"}

pos = c.get_positions().get("market_positions") or []
rows=[r for r in pos if abs(float(r.get("position_fp") or 0))>0]
print(f"OPEN POSITIONS: {len(rows)}\n")
tot_exit=0.0
for r in sorted(rows, key=lambda x: x["ticker"]):
    t=r["ticker"]; p=float(r["position_fp"])
    try:
        m=q.public_get(f"/trade-api/v2/markets/{t}").get("market") or {}
        ob=q.public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
    except Exception as e:
        print(f"  {t:34s} ERR {e!r}"[:110]); continue
    close=m.get("close_time"); dleft=(iso(close)-now).total_seconds()/86400 if close else None
    yl,_=q._levels(ob.get("yes_dollars") or []); nl,_=q._levels(ob.get("no_dollars") or [])
    by=max((x for x,_ in yl), default=None); bn=max((x for x,_ in nl), default=None)
    # to exit LONG yes you SELL yes = hit the yes bid (best_y). to exit SHORT yes (long no) you
    # sell no = hit the no bid (best_n). Depth at that bid tells you if you can get out at all.
    if p>0: side,bid,depth = "sell YES", by, sum(s for x,s in yl if x==by)
    else:   side,bid,depth = "sell NO",  bn, sum(s for x,s in nl if x==bn)
    spread = (1.0-(by or 0)-(bn or 0))
    prog = pmap.get(t)
    pool = (float(prog.get("period_reward") or 0)/10000.0) if prog else 0.0
    val = abs(p)*(bid or 0)
    tot_exit += val
    print(f"  {t:34s} pos={p:+7.1f}  closes_in={dleft:6.2f}d  exit={side} @{bid}  "
          f"depth={depth:7.0f}  spread={spread*100:4.1f}c  value=${val:7.2f}  "
          f"program={'ACTIVE $'+format(pool,'.0f')+'/d' if prog else 'none'}")
print(f"\n  total mark-to-bid value of all positions: ${tot_exit:.2f}")
