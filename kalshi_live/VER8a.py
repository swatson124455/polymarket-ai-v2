import sys,os
sys.path.insert(0,os.getcwd())
import kalshi_attribution_ledger as L
P=L.P
for v in ["active","closed","upcoming","pending","scheduled","inactive","expired","finished","future","ACTIVE","zzzznotreal"]:
    d=L.get(P+"/incentive_programs?limit=2&status="+v)
    n=len(d.get("incentive_programs") or [])
    print("  status=%-12s n=%d -> %s"%(v,n,"RECOGNISED (filters, empty result)" if n==0 else "returns rows"))
