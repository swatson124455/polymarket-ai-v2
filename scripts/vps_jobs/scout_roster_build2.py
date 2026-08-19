import json
from collections import defaultdict
stats=defaultdict(lambda:[0,0.0,set()])
with open("/opt/pa2-shared/mb_copyable_data/rtds_scout/scout_20260730.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except ValueError: continue
        s=stats[r["w"]]; s[0]+=1; s[1]+=r["p"]*r["z"]; s[2].add(r["c"])
roster={a.lower() for a in json.load(open("/opt/pa2-shared/mb_copyable_data/chain_audit.json"))["clean"]}
sel=[w for w,(n,usd,mk) in stats.items() if w not in roster and usd>=250000 and len(mk)>=100 and n>=500]
assert sel, "0 candidates - ABORT"
open("/tmp/scout_dive_roster.txt","w").write("\n".join(sorted(sel))+"\n")
print("selected",len(sel))
