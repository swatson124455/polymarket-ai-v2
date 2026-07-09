#!/usr/bin/env python3
import json,os,re,time,urllib.request,urllib.error,urllib.parse
from datetime import datetime,timezone
ENV=os.environ.get("PINNODDS_ENV_PATH","/opt/pa2-shared/.env")
SNAP=os.environ.get("PINNODDS_SNAPSHOT_PATH","/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl")
URL="https://pinnodds.com/kit/v1/markets?sport_id=11&event_type=prematch"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PROP=re.compile(r"\((kills?|games?|maps?|rounds?|towers?|handicap|total)\)\s*$",re.I)
# GAP B: Polymarket match-winner index (Gamma tag_id=64) for bet-time PM price.
GAMMA="https://gamma-api.polymarket.com/markets"
YESNO={"YES","NO","1","0","TRUE","FALSE"}
VS=re.compile(r"\bvs\.?\b",re.I)
PMPROP=re.compile(r"\bgame\s*\d|\bmap\s*\d|handicap|\bkills?\b|\btotal\b|odd\s*/\s*even|\bodd\b|\beven\b|over\s*/\s*under|\bover\b|\bunder\b|\brounds?\b|\btowers?\b|first blood|\bspread\b|correct score|most (picked|banned)",re.I)
def key():
    for l in open(ENV,encoding="utf-8"):
        if l.startswith("PINNACLE_ODDS_API_KEY=") and l.strip()!="PINNACLE_ODDS_API_KEY=":
            return l.split("=",1)[1].strip()
    raise SystemExit("no PINNACLE_ODDS_API_KEY in "+ENV)
def norm(s): return str(s or "").strip().lower()
def mkey(a,b,d):
    a,b=norm(a),norm(b); d=(d or "")[:10]
    if a>b: a,b=b,a
    return f"{a}||{b}||{d}"
def coerce(v):
    try: f=float(v)
    except (TypeError,ValueError): return None
    return f if f>1.0 else None
def jlist(raw):
    if isinstance(raw,list): return raw
    if isinstance(raw,str):
        try: p=json.loads(raw)
        except (TypeError,ValueError): return []
        return p if isinstance(p,list) else []
    return []
def price(v):
    try: f=float(v)
    except (TypeError,ValueError): return None
    return f if 0.0<f<1.0 else None
def fetch(k):
    req=urllib.request.Request(URL,headers={"x-portal-apikey":k,"User-Agent":UA,"Accept":"application/json"})
    for a in range(1,5):
        try:
            with urllib.request.urlopen(req,timeout=25) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code==429 and a<4:
                ra=e.headers.get("Retry-After") if e.headers else None
                try: w=min(int(ra),60) if ra else 2**a
                except ValueError: w=2**a
                print(f"429 attempt {a}, sleep {w}s"); time.sleep(max(1,w)); continue
            print(f"HTTP {e.code} giving up"); return {}
        except (urllib.error.URLError,TimeoutError) as e:
            if a<4: print(f"transport {e}, backoff"); time.sleep(2**a); continue
            return {}
    return {}
def gamma_page(off):
    q=urllib.parse.urlencode({"tag_id":64,"closed":"false","active":"true","archived":"false","limit":100,"offset":off})
    req=urllib.request.Request(GAMMA+"?"+q,headers={"User-Agent":"eb-pm-index/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: d=json.load(r)
        return d if isinstance(d,list) else None
    except Exception: return None
def pm_index():
    # match_key -> (condition_id, yes_token_id, yes_outcome, market_price); drop ambiguous keys.
    idx={}; amb=set(); cid_of={}
    for pg in range(30):
        ms=gamma_page(pg*100)
        if not ms: break
        for m in ms:
            if not isinstance(m,dict): continue
            q=str(m.get("question") or "").strip()
            if not q or not VS.search(q) or PMPROP.search(q): continue
            outs=jlist(m.get("outcomes"))
            if len(outs)!=2: continue
            labs={str(o).strip().upper() for o in outs if str(o).strip()}
            if not labs or labs<=YESNO: continue
            ta,tb=str(outs[0]).strip(),str(outs[1]).strip()
            if not ta or not tb: continue
            toks=jlist(m.get("clobTokenIds"))
            if len(toks)!=2 or not str(toks[0]).strip(): continue
            cid=str(m.get("conditionId") or "").strip()
            gs=str(m.get("gameStartTime") or "").strip()
            if not cid or not gs: continue
            prs=jlist(m.get("outcomePrices"))
            mp=price(prs[0]) if len(prs)==2 else None
            k=mkey(ta,tb,gs)
            if k in amb: continue
            if k not in cid_of:
                cid_of[k]=cid; idx[k]=(cid,str(toks[0]).strip(),ta,mp)
            elif cid_of[k]!=cid:
                amb.add(k); idx.pop(k,None)
        if len(ms)<100: break
    return idx
def main():
    cap=datetime.now(timezone.utc).isoformat(); recs=[]
    try: pmi=pm_index()
    except Exception as e: print(f"pm_index failed {type(e).__name__}, null PM fields"); pmi={}
    for ev in fetch(key()).get("events",[]):
        h,a=str(ev.get("home") or "").strip(),str(ev.get("away") or "").strip()
        if not h or not a or PROP.search(h) or PROP.search(a): continue
        p=ev.get("periods") or {}; n0=p.get("num_0") if isinstance(p,dict) else None
        ml=n0.get("money_line") if isinstance(n0,dict) else None
        if not isinstance(ml,dict): continue
        oa,ob=coerce(ml.get("home")),coerce(ml.get("away"))
        if oa is None or ob is None: continue
        mk=mkey(h,a,ev.get("starts")); pm=pmi.get(mk)
        recs.append({"captured_at":cap,"match_key":mk,"home":h,"away":a,
                     "starts":ev.get("starts"),"league_name":ev.get("league_name"),
                     "odds_a":oa,"odds_b":ob,"event_type":"prematch",
                     "condition_id":pm[0] if pm else None,"yes_token_id":pm[1] if pm else None,
                     "yes_outcome":pm[2] if pm else None,"market_price":pm[3] if pm else None})
    os.makedirs(os.path.dirname(SNAP),exist_ok=True)
    with open(SNAP,"a",encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    total=sum(1 for _ in open(SNAP,encoding="utf-8"))
    matched=sum(1 for r in recs if r.get("condition_id"))
    print(f"{cap} appended={len(recs)} pm_matched={matched} total_lines={total} file={SNAP}")
if __name__=="__main__": main()
