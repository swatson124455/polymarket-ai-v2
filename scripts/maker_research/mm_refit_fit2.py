"""Fit-2 REFIT: adverse-fill rate vs hours-to-ACTUAL-RESOLUTION (not scheduled
end), full 613-market mapping, fill side INFERRED from pos delta. Censoring
reported, h<0 guarded."""
import json, glob, bisect, collections, subprocess, os, re
from datetime import datetime

meta = json.load(open("/tmp/fill_market_meta.json"))
cids = sorted(set(v["cid"] for v in meta.values() if v.get("cid")))
out = subprocess.run(["psql", os.environ["DATABASE_URL"], "-Atc",
    "SELECT id, EXTRACT(EPOCH FROM resolved_at), EXTRACT(EPOCH FROM end_date_iso) FROM markets WHERE id IN (%s)"
    % ",".join("'%s'" % c for c in cids)], capture_output=True, text=True, timeout=120)
res_at = {}
for r in out.stdout.strip().splitlines():
    parts = r.split("|")
    if len(parts) >= 2 and parts[1]:
        try: res_at[parts[0]] = float(parts[1])
        except Exception: pass
def parse_iso(s):
    try:
        t = str(s).strip().replace("Z", "+00:00")
        if re.search(r"[+-]\d{2}$", t): t += ":00"
        return datetime.fromisoformat(t).timestamp()
    except Exception: return None
ends = {}
for gid, v in meta.items():
    r = res_at.get(v.get("cid"))
    if r: ends[gid] = ("resolved", r)
    else:
        e = parse_iso(v.get("end"))
        if e: ends[gid] = ("scheduled", e)
print("end-time source: resolved_at=%d scheduled-only=%d unmapped=%d"
      % (sum(1 for s,_ in ends.values() if s=="resolved"),
         sum(1 for s,_ in ends.values() if s=="scheduled"), len(meta)-len(ends)))

v1 = collections.defaultdict(list)
for fp in glob.glob("/opt/pa2-maker-sim/samples-*.jsonl"):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is not None:
            v1[str(r["id"])].append((r["t"], r["mid"], r.get("pos",0), r.get("fills",0)))
for v in v1.values(): v.sort()

buck = collections.defaultdict(list)
censored = collections.Counter()
neg_h = 0
for mkt, s in v1.items():
    if mkt not in ends: continue
    src, e = ends[mkt]
    for i in range(1, len(s)):
        t, mid, pos, fills = s[i]
        if not fills: continue
        dpos = pos - s[i-1][2]
        if abs(dpos) < 1e-9: continue
        side = 1 if dpos > 0 else -1
        h = (e - t) / 3600.0
        if h < 0: neg_h += 1; continue
        b = 0.5 if h < 1 else (2 if h < 3 else (6 if h < 9 else (16 if h < 24 else (48 if h < 72 else 99))))
        j = bisect.bisect_left(s, (t + 1800, -1e18, 0, 0))
        best = None
        for k in (j-1, j, j+1):
            if 0 <= k < len(s) and abs(s[k][0]-(t+1800)) <= 600:
                if best is None or abs(s[k][0]-(t+1800)) < abs(best[0]-(t+1800)): best = s[k]
        if best is None:
            censored[b] += 1; continue
        buck[b].append((best[1] - mid) * side)
print("\nFIT-2 REFIT — adverse vs hours-to-RESOLUTION (side-inferred, censoring shown):")
print("%-10s %7s %10s %11s %11s %9s" % ("hrs-to-res","fills","mean","adv>1pt","adv>2pt","censored"))
for b in sorted(set(list(buck)+list(censored))):
    g = buck.get(b, []); n = len(g)
    if n:
        print("<%-9s %7d %+10.4f %10.1f%% %10.1f%% %9d" % (b, n, sum(g)/n,
              100*sum(1 for x in g if x<-0.01)/n, 100*sum(1 for x in g if x<-0.02)/n, censored.get(b,0)))
    else:
        print("<%-9s %7d %10s %11s %11s %9d" % (b, 0, "-", "-", "-", censored.get(b,0)))
print("(fills after recorded resolution, excluded: %d)" % neg_h)
