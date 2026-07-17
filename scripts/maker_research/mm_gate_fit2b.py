import json, glob, collections, bisect, re
from datetime import datetime
def parse_iso(s):
    try:
        t = str(s).strip().replace("Z", "+00:00")
        if re.search(r"[+-]\d{2}$", t): t += ":00"
        return datetime.fromisoformat(t).timestamp()
    except Exception: return None
ends = {k: parse_iso(v) for k, v in json.load(open("/tmp/fill_market_ends.json")).items()}
ends = {k: v for k, v in ends.items() if v}
v1s = collections.defaultdict(list); fills = []
for fp in sorted(glob.glob("/opt/pa2-maker-sim/samples-*.jsonl")):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is None: continue
        v1s[r["id"]].append((r["t"], r["mid"]))
        if r.get("fills") and r.get("pos"): fills.append((r["id"], r["t"], r["mid"], r["pos"]))
for v in v1s.values(): v.sort()
def mid_at(mkt, t):
    s = v1s[mkt]; i = bisect.bisect_left(s, (t, -1))
    for j in (i, i-1, i+1):
        if 0 <= j < len(s) and abs(s[j][0]-t) <= 600: return s[j][1]
    return None
buck = collections.defaultdict(list)
used = 0
for mkt, t, mid, pos in fills:
    e = ends.get(str(mkt))
    if not e: continue
    h = (e - t) / 3600.0
    if h < 0: continue
    used += 1
    b = 0.5 if h < 1 else (2 if h < 3 else (6 if h < 9 else (16 if h < 24 else (48 if h < 72 else 99))))
    a = mid_at(mkt, t + 1800)
    if a is None: continue
    buck[b].append((a - mid) * (1 if pos > 0 else -1))
print("fills with known end + future end: %d" % used)
print("%-12s %7s %10s %12s %12s" % ("hrs-to-end", "fills", "mean", "adverse>1pt", "adverse>2pt"))
for b in sorted(buck):
    g = buck[b]; n = len(g)
    print("%-12s %7d %+10.4f %11.1f%% %11.1f%%" % ("<%sh" % b, n, sum(g)/n,
          100*sum(1 for x in g if x < -0.01)/n, 100*sum(1 for x in g if x < -0.02)/n))
