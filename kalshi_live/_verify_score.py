import kalshi_attribution_ledger as L, time, json
def series(t):
    try:
        r=L.get(f"{L.P}/series/{t}")
        s=r.get("series",r)
        return {"ticker":t,"fee_type":s.get("fee_type"),"category":s.get("category"),"title":(s.get("title") or "")[:45]}
    except Exception as e:
        return {"ticker":t,"err":str(e)[:80]}
for t in ["KXUSGASCPI","KXH200MAX","KXH100MAX","KXA100MAX","KXRTX5090MAX","KXB200MAX","KXEOWEEK","KXAAAGASD","KXBTC50VS100"]:
    print(json.dumps(series(t)))
    time.sleep(0.6)
