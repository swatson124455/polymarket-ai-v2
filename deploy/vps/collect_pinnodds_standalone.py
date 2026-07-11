#!/usr/bin/env python3
import json,os,re,time,urllib.request,urllib.error,urllib.parse
from datetime import datetime,timezone
ENV=os.environ.get("PINNODDS_ENV_PATH","/opt/pa2-shared/.env")
SNAP=os.environ.get("PINNODDS_SNAPSHOT_PATH","/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl")
ALIASES=os.environ.get("EB_ALIASES_PATH","/home/ubuntu/eb-odds/aliases.json")
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
# Team matching mirrors esports_v2.model.team_match (kept in sync; parity-checked
# against the canonical module). normalize splits on punctuation like
# orientation.normalize_team; same_team = conservative token-subset sharing a
# non-generic token; match2 = bijective both-teams match. Correct-or-absent.
GENERIC={"esports","esport","e-sports","gaming","team","club","academy","youth","challengers","challenger","junior"}
# Sibling-roster qualifiers (mirrors team_match.SIBLING_QUALIFIERS): a DIFFERENCE
# in these vetoes the match — "T1" != "T1 Academy" (different roster, same org),
# even via aliases. Same-qualifier pairs still match ("T1 Academy"=="T1 Esports Academy").
QUAL={"academy","youth","junior","juniors","rookies","challenger","challengers","female","fe","women","womens","ladies","gc","blue","white","black","gold","stars"}
def tnorm(s):
    s=str(s or "").lower().replace("_"," ")
    s=re.sub(r"[^\w\s]"," ",s)
    return re.sub(r"\s+"," ",s).strip()
# Optional alias map (mirrors esports_v2.data.alias_file, correct-or-absent):
# aliases.json {"groups": [[name, ...], ...]} from deploy/vps/eb_dump_aliases.sh.
# Missing/malformed/empty -> None -> matching runs exactly as before.
def load_aliases():
    try:
        with open(ALIASES,encoding="utf-8") as f: d=json.load(f)
        if not isinstance(d,dict): return None
        m={}
        for g in d.get("groups") or []:
            if not isinstance(g,list): continue
            names=[str(n).strip() for n in g if str(n or "").strip()]
            if len(names)<2: continue
            for n in names:
                k=tnorm(n)
                if k: m.setdefault(k,set()).update(names)
        return m or None
    except Exception: return None
def same_team(x,y,am=None):
    nx,ny=tnorm(x),tnorm(y)
    if nx and nx==ny: return True
    if not nx or not ny: return False
    if (set(nx.split())&QUAL)!=(set(ny.split())&QUAL): return False
    if am:
        ax={nx}|{tnorm(n) for n in am.get(nx,())}; ay={ny}|{tnorm(n) for n in am.get(ny,())}
        ax.discard(""); ay.discard("")
        if ax&ay: return True
    tx,ty=set(nx.split()),set(ny.split())
    if not tx or not ty: return False
    if not ((tx&ty)-GENERIC): return False
    return tx.issubset(ty) or ty.issubset(tx)
def match2(h,a,ra,rb,am=None):
    ha,hb=same_team(h,ra,am),same_team(h,rb,am); aa,ab=same_team(a,ra,am),same_team(a,rb,am)
    o1=ha and ab and not hb and not aa; o2=hb and aa and not ha and not ab
    if o1 and not o2: return True
    if o2 and not o1: return False
    return None
def dwin(d,w):
    from datetime import datetime as _dt,timedelta as _td
    try: base=_dt.strptime(d,"%Y-%m-%d")
    except (ValueError,TypeError): return {d}
    return {(base+_td(days=o)).strftime("%Y-%m-%d") for o in range(-w,w+1)}
def match_ref(h,a,starts,refs,window=1,am=None):
    # refs: list of (condition_id, yes_token_id, yes_outcome, market_price, team_a, team_b, day)
    day=str(starts or "")[:10]
    if len(day)<10: return None
    days=dwin(day,window)
    cands=[r for r in refs if r[6] in days and match2(h,a,r[4],r[5],am) is not None]
    if not cands: return None
    if len({r[0] for r in cands})!=1: return None
    return cands[0]
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
        except ValueError:
            # 200 with a non-JSON body (WAF challenge page): lost tick, not a crash
            print("non-JSON 200 body, giving up"); return {}
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
    # newest-first is REQUIRED: gamma 422-caps offset paging at ~2100 and the tag
    # holds ~3600 active markets — oldest-first order hid 93/130 live match winners.
    q=urllib.parse.urlencode({"tag_id":64,"closed":"false","active":"true","archived":"false","order":"id","ascending":"false","limit":100,"offset":off})
    req=urllib.request.Request(GAMMA+"?"+q,headers={"User-Agent":"eb-pm-index/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: d=json.load(r)
        return d if isinstance(d,list) else None
    except Exception: return None
def pm_index():
    # list of (condition_id, yes_token_id, yes_outcome, market_price, team_a, team_b, day);
    # de-dup by condition_id. Ambiguity resolved at match time (match_ref).
    refs=[]; seen=set()
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
            gs=str(m.get("gameStartTime") or "").strip(); day=gs[:10]
            if not cid or len(day)<10 or cid in seen: continue
            prs=jlist(m.get("outcomePrices"))
            mp=price(prs[0]) if len(prs)==2 else None
            seen.add(cid); refs.append((cid,str(toks[0]).strip(),ta,mp,ta,tb,day))
        if len(ms)<100: break
    return refs
def main():
    cap=datetime.now(timezone.utc).isoformat(); recs=[]
    try: pmi=pm_index()
    except Exception as e: print(f"pm_index failed {type(e).__name__}, null PM fields"); pmi={}
    am=load_aliases()
    for ev in fetch(key()).get("events",[]):
        h,a=str(ev.get("home") or "").strip(),str(ev.get("away") or "").strip()
        if not h or not a or PROP.search(h) or PROP.search(a): continue
        p=ev.get("periods") or {}; n0=p.get("num_0") if isinstance(p,dict) else None
        ml=n0.get("money_line") if isinstance(n0,dict) else None
        if not isinstance(ml,dict): continue
        oa,ob=coerce(ml.get("home")),coerce(ml.get("away"))
        if oa is None or ob is None: continue
        mk=mkey(h,a,ev.get("starts")); pm=match_ref(h,a,ev.get("starts"),pmi,am=am)
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
