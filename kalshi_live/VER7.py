import json,os,sys,datetime as dt
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
# probe filter support + bad-param behaviour
for q in ["/incentive_programs?limit=2&market_ticker=KXAAAGASD-26JUL26-4.100",
          "/incentive_programs?limit=2&event_ticker=KXAAAGASD-26JUL26",
          "/incentive_programs?limit=2&series_ticker=KXAAAGASD",
          "/incentive_programs?limit=2&status=ACTIVE",
          "/incentive_programs?limit=2&status=upcoming",
          "/incentive_programs?limit=2&status=open",
          "/incentive_programs?limit=2&status=active&bogusparam=1"]:
    try:
        d=L.get(P+q); ips=d.get("incentive_programs") or []
        print("%-70s n=%d  first=%s"%(q,len(ips),(ips[0].get("market_ticker") if ips else None)))
    except Exception as e:
        body=b""
        try: body=e.read()[:200]
        except Exception: pass
        print("%-70s ERR %s %s"%(q,e,body))
