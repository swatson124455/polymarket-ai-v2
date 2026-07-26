#!/usr/bin/env python3
"""ADVERSARIAL REFUTATION PROBE — lens dilution-illusory-r3 (READ-ONLY, public API, GET only).
Independent fresh fetch. Does NOT import maker_kalshi_quoter.py. Replicates the deployed gate
+ round-robin exactly, then fetches CURRENT books to answer ONE question:

  Does GAS (and AMSAVO) have MORE two-sided-reachable strikes than the deployed footprint
  already rests?  If gas two-sided count <= gas slots the footprint gives it, the "dilution
  is illusory" verdict HOLDS (concentrating captures nothing). If gas has materially more
  two-sided strikes than it gets, the dilution is REAL and the verdict is refuted.

Also: verify the premise arithmetic (usd_day == period_reward/10000/days == reward $/day,
NOT volume) on a live gas program, and quantify how much of the *picked* gas footprint is
actually one-sided (pays nobody under R3) — the tiebreak-selection gap.
"""
import json, os, sys, time, urllib.request
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE = 0.35
_last = [0.0]

FOOTPRINT_TOP = 40
PER_SERIES_CAP = 100
SERIES_ALLOW = {"KXTEMPDCH","KXTEMPAUSH","KXTEMPLAXH","KXTEMPNYCH","KXTEMPCHIH",
                "KXAAAGASD","KXAAAGASW","KXB200MON","KXAMSAVO","KXH100MON",
                "KXMUSKNW","KXCHIPBURRITO","KXTRUMPENDORSEMENTS","KXGENERICBALLOTVOTEHUB"}
GAS = {"KXAAAGASD","KXAAAGASW"}
LATE_FRAC, LATE_MIN, LATE_MAX = 0.6, 45, 120

def get(path):
    dt = time.time() - _last[0]
    if dt < SPACE:
        time.sleep(SPACE - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent":"refute-dilution/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    _last[0] = time.time()
    return out

def piso(s): return datetime.fromisoformat(s.replace("Z","+00:00"))

def fetch_programs():
    progs, cur = [], ""
    for _ in range(20):
        d = get("/incentive_programs?status=active&limit=10000" + (f"&cursor={cur}" if cur else ""))
        got = d.get("incentive_programs") or []
        progs += got
        cur = d.get("next_cursor") or ""
        if not cur or not got: break
    return progs

def levels(raw):
    out=[]
    for row in raw or []:
        try: p,s=float(row[0]),float(row[1])
        except (TypeError,ValueError,IndexError): continue
        if s>0: out.append((p,s))
    return out

def qualifying_walk(lv, target):
    """canon R3 walk: best-price-down, cumulative >= target, ref price must be < 1.0"""
    if not lv or target<=0: return False
    lv=sorted(lv,key=lambda x:-x[0])
    if lv[0][0] >= 1.0: return False
    tot=0.0
    for _,s in lv:
        tot+=s
        if tot>=target: return True
    return False

def two_sided(ticker, target):
    try:
        ob=get(f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    except Exception as e:
        return None
    yl=levels(ob.get("yes_dollars")); nl=levels(ob.get("no_dollars"))
    return qualifying_walk(yl,target) and qualifying_walk(nl,target)

def eligible(progs, now):
    rows=[]
    for p in progs:
        if (p.get("incentive_type") or "liquidity")!="liquidity": continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None: continue
        t=p.get("market_ticker")
        if not t: continue
        s=t.split("-")[0]
        if s not in SERIES_ALLOW: continue
        try: end,start=piso(p["end_date"]),piso(p["start_date"])
        except Exception: continue
        life=max((end-start).total_seconds()/60.0,1.0)
        cut=min(LATE_MAX,max(LATE_MIN,LATE_FRAC*life))
        if end < now+timedelta(minutes=cut): continue
        days=max((end-start).total_seconds()/86400.0,1/24.0)
        rows.append({"ticker":t,"series":s,
                     "usd_day":((p.get("period_reward") or 0)/10000.0)/days,
                     "target":float(p["target_size_fp"]),
                     "period_reward":p.get("period_reward"),"days":days})
    return rows

def round_robin(rows):
    by=defaultdict(list)
    for r in sorted(rows,key=lambda r:(-r["usd_day"],r["ticker"])):
        by[r["series"]].append(r)
    order=sorted(by,key=lambda s:(-by[s][0]["usd_day"],s))
    picked,per=[],Counter()
    while len(picked)<FOOTPRINT_TOP:
        added=False
        for s in order:
            if len(picked)>=FOOTPRINT_TOP or per[s]>=PER_SERIES_CAP: continue
            if per[s]<len(by[s]):
                picked.append(by[s][per[s]]); per[s]+=1; added=True
        if not added: break
    return picked

def main():
    now=datetime.now(timezone.utc)
    print(f"FRESH probe NOW={now:%Y-%m-%dT%H:%M:%SZ}")
    progs=fetch_programs()
    rows=eligible(progs,now)
    print(f"active programs={len(progs)}  eligible(post gate+allowlist)={len(rows)}")
    elig=Counter(r["series"] for r in rows)
    print("eligible by series:",dict(sorted(elig.items(),key=lambda kv:-kv[1])))

    # ---- PREMISE ARITHMETIC CHECK on a live gas program ----
    gd=[r for r in rows if r["series"]=="KXAAAGASD"]
    if gd:
        r=gd[0]
        print(f"\n[PREMISE] {r['ticker']}: period_reward={r['period_reward']} (fp x10000) "
              f"=> pool ${r['period_reward']/10000:.2f}; window days={r['days']:.4f}; "
              f"usd_day=${r['usd_day']:.3f}  (== pool/days == reward $/day per contract, NOT volume)")

    picked=round_robin(rows)
    pick_by=Counter(p["series"] for p in picked)
    print(f"\ndeployed round-robin picks {len(picked)} slots; gas slots="
          f"{pick_by['KXAAAGASD']+pick_by['KXAAAGASW']}  (D={pick_by['KXAAAGASD']} W={pick_by['KXAAAGASW']})")

    # ---- FRESH R3 on ALL gas eligible + AMSAVO eligible ----
    gas_rows=sorted([r for r in rows if r["series"] in GAS],key=lambda r:(-r["usd_day"],r["ticker"]))
    ams_rows=sorted([r for r in rows if r["series"]=="KXAMSAVO"],key=lambda r:(-r["usd_day"],r["ticker"]))
    picked_gas_t={p["ticker"] for p in picked if p["series"] in GAS}

    print(f"\n=== FRESH R3 GAS census ({len(gas_rows)} eligible) ===")
    gas_two=0; gas_two_D=0; gas_two_W=0; picked_gas_two=0; picked_gas_one=0
    for r in gas_rows:
        ts=two_sided(r["ticker"],r["target"])
        inpick = r["ticker"] in picked_gas_t
        if ts:
            gas_two+=1
            if r["series"]=="KXAAAGASD": gas_two_D+=1
            else: gas_two_W+=1
        if inpick:
            if ts: picked_gas_two+=1
            elif ts is False: picked_gas_one+=1
        print(f"  {r['ticker']:<30} ${r['usd_day']:6.1f}/d  {'2S' if ts else ('1S' if ts is False else 'ERR')}"
              f"  {'<PICKED>' if inpick else ''}")
    print(f"\n  GAS two-sided total={gas_two} (D={gas_two_D} W={gas_two_W})  "
          f"gas slots picked={len(picked_gas_t)}  of which two-sided={picked_gas_two} one-sided={picked_gas_one}")

    print(f"\n=== FRESH R3 AMSAVO census ({len(ams_rows)} eligible) ===")
    ams_two=0; picked_ams=sum(1 for p in picked if p["series"]=="KXAMSAVO")
    for r in ams_rows:
        ts=two_sided(r["ticker"],r["target"])
        if ts: ams_two+=1
        print(f"  {r['ticker']:<30} ${r['usd_day']:6.1f}/d  {'2S' if ts else ('1S' if ts is False else 'ERR')}")
    print(f"\n  AMSAVO two-sided total={ams_two}  amsavo slots picked now={picked_ams}  "
          f"(all AMSAVO usd_day ~equal, tiebreak by ticker)")

    # ---- The settling numbers ----
    gas_slots=len(picked_gas_t)
    print("\n" + "="*70)
    print("SETTLING MEASUREMENT (lens dilution-illusory-r3):")
    print(f"  gas two-sided strikes venue-wide (fresh) : {gas_two}")
    print(f"  gas slots the deployed footprint rests    : {gas_slots}")
    print(f"  => concentrating gas can rest at most {gas_two} two-sided; footprint already gets {gas_slots}.")
    print(f"     picked gas that is ONE-sided (pays $0 R3): {picked_gas_one}")
    print(f"  AMSAVO two-sided (all reachable, capital permitting): {ams_two}; picked now={picked_ams}")
    out={"now":now.isoformat(),"n_programs":len(progs),"n_eligible":len(rows),
         "eligible_by_series":dict(elig),
         "gas_two_sided_fresh":gas_two,"gas_two_D":gas_two_D,"gas_two_W":gas_two_W,
         "gas_slots_picked":gas_slots,"picked_gas_two":picked_gas_two,"picked_gas_one":picked_gas_one,
         "amsavo_two_sided_fresh":ams_two,"amsavo_slots_picked":picked_ams,
         "pick_by_series":dict(pick_by)}
    json.dump(out,open(os.path.join(HERE,"refute_dilution_illusory.json"),"w"),indent=2)
    print("\nwrote refute_dilution_illusory.json")
    return 0

if __name__=="__main__":
    sys.exit(main() or 0)
