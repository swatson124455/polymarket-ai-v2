import json
from collections import defaultdict
stats=defaultdict(lambda:[0,0.0,set()])
with open("/opt/pa2-shared/mb_copyable_data/rtds_scout/scout_20260730.jsonl") as f:
    for line in f:
        try: r=json.loads(line)
        except ValueError: continue
        s=stats[r["w"]]; s[0]+=1; s[1]+=r["p"]*r["z"]; s[2].add(r["c"])
roster={a.lower() for a in json.load(open("/opt/pa2-shared/mb_copyable_data/chain_audit.json"))["clean"]}
# SELECTION BAND (operator go 2026-08-24, "fix the filter first"):
# Sweep #1 selected 9/9 machines (4,399-26,180 fills/day) - and on review the
# OLD floor (>=500 trades in a 6h capture = 2,000/day) sat ABOVE the dive's
# UNCOPYABLE ceiling (1,000/day), so the old filter could ONLY select
# accounts the dive must reject. The ceiling and floor must bracket a
# human-copyable band:
#   trades: 10 <= n < 250 per 6h  (40/day activity floor .. 1,000/day ceiling)
#   markets >= 5 per 6h           (breadth, human-scale)
#   notional >= $25k per 6h       (real-money whale, human-scale)
CAPTURE_HOURS = 6.0
CEIL = 1000 * CAPTURE_HOURS / 24.0   # dive's UNCOPYABLE bar, pro-rated
FLOOR = 10
MIN_MARKETS = 5
MIN_NOTIONAL = 25_000
sel=[w for w,(n,usd,mk) in stats.items()
     if w not in roster and usd>=MIN_NOTIONAL and len(mk)>=MIN_MARKETS
     and FLOOR<=n<CEIL]
dropped_machine=[w for w,(n,usd,mk) in stats.items()
                 if w not in roster and usd>=MIN_NOTIONAL and len(mk)>=MIN_MARKETS
                 and n>=CEIL]
print("dropped as machine-flow at selection:", len(dropped_machine))
assert sel, "0 candidates - ABORT"
open("/tmp/scout_dive_roster.txt","w").write("\n".join(sorted(sel))+"\n")
print("selected",len(sel))
