import kalshi_attribution_ledger as L
# try to find actual LIP reward credits / settlements
for path in ["/portfolio/settlements?limit=200", "/portfolio/balance"]:
    try:
        r=L.get(L.P+path)
        keys=list(r.keys())
        print(f"{path} -> keys={keys}")
        if "settlements" in r:
            s=r["settlements"] or []
            print(f"  n_settlements={len(s)}")
            if s: print("  sample:", {k:s[0].get(k) for k in list(s[0].keys())[:12]})
        if "balance" in r or "balance_dollars" in r:
            print("  balance fields:", {k:v for k,v in r.items() if 'bal' in k.lower() or 'cash' in k.lower()})
    except Exception as e:
        print(f"{path} -> ERR {e}")
