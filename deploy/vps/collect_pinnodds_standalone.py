#!/usr/bin/env python3
import json,os,re,time,urllib.request,urllib.error
from datetime import datetime,timezone
ENV=os.environ.get("PINNODDS_ENV_PATH","/opt/pa2-shared/.env")
SNAP=os.environ.get("PINNODDS_SNAPSHOT_PATH","/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl")
URL="https://pinnodds.com/kit/v1/markets?sport_id=11&event_type=prematch"
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PROP=re.compile(r"\((kills?|games?|maps?|rounds?|towers?|handicap|total)\)\s*$",re.I)
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
def main():
    cap=datetime.now(timezone.utc).isoformat(); recs=[]
    for ev in fetch(key()).get("events",[]):
        h,a=str(ev.get("home") or "").strip(),str(ev.get("away") or "").strip()
        if not h or not a or PROP.search(h) or PROP.search(a): continue
        p=ev.get("periods") or {}; n0=p.get("num_0") if isinstance(p,dict) else None
        ml=n0.get("money_line") if isinstance(n0,dict) else None
        if not isinstance(ml,dict): continue
        oa,ob=coerce(ml.get("home")),coerce(ml.get("away"))
        if oa is None or ob is None: continue
        recs.append({"captured_at":cap,"match_key":mkey(h,a,ev.get("starts")),"home":h,"away":a,
                     "starts":ev.get("starts"),"league_name":ev.get("league_name"),
                     "odds_a":oa,"odds_b":ob,"event_type":"prematch"})
    os.makedirs(os.path.dirname(SNAP),exist_ok=True)
    with open(SNAP,"a",encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    total=sum(1 for _ in open(SNAP,encoding="utf-8"))
    print(f"{cap} appended={len(recs)} total_lines={total} file={SNAP}")
if __name__=="__main__": main()
