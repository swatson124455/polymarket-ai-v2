"""WHAT DOES AN HOUR ON THE BOOK COST? The number that should set the presence target.

MUST use the unpruned slice: before 2026-07-23T20:05Z Kalshi retains only orders that FILLED, so
including that era would inflate the fill rate toward 100% by construction.
"""
import sys, collections, datetime as dt, statistics as st
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import maker_kalshi_quoter as q
def iso(s): return dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
CUT = iso("2026-07-23T20:05:00Z")

orders=[]
for stt in ("canceled","executed","resting"):
    try: orders += (c.get_orders(status=stt).get("orders") or []) if (c:=q.KalshiOrderClient()) else []
    except Exception as e: print("ERR",stt,repr(e)[:60])

rec=[]
for o in orders:
    try:
        a,b=iso(o["created_time"]),iso(o["last_update_time"])
        if a < CUT: continue                       # pruned era -> survivorship bias
        life=(b-a).total_seconds()
        if life<0: continue
        rec.append({"t":o["ticker"],"life":life,
                    "ct":float(o.get("initial_count_fp") or 0),
                    "fill":float(o.get("fill_count_fp") or 0),
                    "fees":float(o.get("maker_fees_dollars") or 0)+float(o.get("taker_fees_dollars") or 0),
                    "cost":float(o.get("maker_fill_cost_dollars") or 0)+float(o.get("taker_fill_cost_dollars") or 0)})
    except Exception: pass

n=len(rec)
ct_h=sum(r["ct"]*r["life"] for r in rec)/3600.0        # contract-hours of resting
filled=sum(r["fill"] for r in rec)
placed=sum(r["ct"] for r in rec)
fees=sum(r["fees"] for r in rec)
cost=sum(r["cost"] for r in rec)
print(f"UNPRUNED SLICE: {n} orders, {placed:,.0f} contracts placed, {ct_h:,.1f} contract-hours resting\n")
print(f"  contracts FILLED           {filled:10,.0f}  = {100*filled/max(placed,1):5.1f}% of contracts placed")
print(f"  fill rate per contract-hour{filled/max(ct_h,1):10.4f}  contracts filled per contract-hour rested")
print(f"  fees paid                  ${fees:9.4f}  = ${fees/max(ct_h,1):.6f} per contract-hour")
print(f"  gross fill cost (notional) ${cost:9.2f}")
o_filled=[r for r in rec if r["fill"]>0]
print(f"\n  orders touched at all: {len(o_filled)}/{n} = {100*len(o_filled)/max(n,1):.1f}%")
if o_filled:
    print(f"  median life of a FILLED order   {st.median([r['life'] for r in o_filled]):8.0f}s")
    print(f"  median life of an UNFILLED order{st.median([r['life'] for r in rec if r['fill']==0]):8.0f}s")
# per hour of presence, how much inventory do we take on?
print(f"\n  => resting 20 contracts for 1 hour takes on ~{20*filled/max(ct_h,1):.2f} contracts of inventory")
print(f"  => and costs ~${20*fees/max(ct_h,1):.4f} in fees")
