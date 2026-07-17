"""Offline gate-parameter fits from recorded arm data (read-only).
Fit 1 (vol gate): after a mid move of >=X pts within 2 min, how much further
does the price move (same direction) over the next 10/20/30 min, vs baseline?
Fit 2 (wind-down ramp): adverse-fill rate vs hours-to-market-end.
"""
import json, glob, collections, bisect

# --- load v3 minute-mids (richest series) + v1 fills + market end times ---
series = collections.defaultdict(list)
for fp in sorted(glob.glob("/opt/pa2-maker-sim-v3/samples-*.jsonl")):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is not None: series[r["id"]].append((r["t"], r["mid"]))
for v in series.values(): v.sort()

ends = {}
for base in ("/opt/pa2-maker-sim-v3", "/opt/pa2-maker-sim", "/opt/pa2-maker-sim-v2", "/opt/pa2-maker-sim-v4"):
    try:
        u = json.load(open(base + "/universe.json"))
        mkts = u["markets"] if isinstance(u, dict) else u
        for m in mkts:
            if m.get("end"): ends[str(m.get("id"))] = float(m["end"])
    except Exception: pass
print("series markets: %d, end-times known: %d" % (len(series), len(ends)))

# --- Fit 1: continuation after a 2-min move ---
print("\nFIT 1 — continuation after |move over 2min| >= X (same-direction further move, median/p75):")
print("%-8s %8s %14s %14s %14s" % ("X(pts)", "events", "next10m", "next20m", "next30m"))
for X in (0.01, 0.015, 0.02, 0.03, 0.04):
    conts = {600: [], 1200: [], 1800: []}
    n = 0
    for mkt, s in series.items():
        for i in range(2, len(s)):
            t0, m0 = s[i]
            tp, mp = s[i-2]
            if t0 - tp > 200: continue
            d = m0 - mp
            if abs(d) < X: continue
            n += 1
            sign = 1 if d > 0 else -1
            for hor in conts:
                j = bisect.bisect_left(s, (t0 + hor, -1))
                if j < len(s) and abs(s[j][0] - (t0 + hor)) <= 300:
                    conts[hor].append((s[j][1] - m0) * sign)
    row = ["%.3f" % X, str(n)]
    for hor in (600, 1200, 1800):
        c = sorted(conts[hor])
        row.append("%+.4f/%+.4f" % (c[len(c)//2], c[int(.75*len(c))]) if c else "-")
    print("%-8s %8s %14s %14s %14s" % tuple(row))

# --- Fit 2: v1 fill adverse rate vs hours-to-end ---
v1s = collections.defaultdict(list)
fills = []
for fp in sorted(glob.glob("/opt/pa2-maker-sim/samples-*.jsonl")):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is None: continue
        v1s[r["id"]].append((r["t"], r["mid"]))
        if r.get("fills") and r.get("pos"):
            fills.append((r["id"], r["t"], r["mid"], r["pos"]))
for v in v1s.values(): v.sort()
def mid_at(mkt, t):
    s = v1s[mkt]; i = bisect.bisect_left(s, (t, -1))
    for j in (i, i-1, i+1):
        if 0 <= j < len(s) and abs(s[j][0]-t) <= 600: return s[j][1]
    return None
print("\nFIT 2 — v1 fill outcomes by hours-to-market-end:")
buck = collections.defaultdict(list)
unk = 0
for mkt, t, mid, pos in fills:
    e = ends.get(str(mkt))
    if not e: unk += 1; continue
    h = (e - t) / 3600.0
    b = 0.5 if h < 1 else (2 if h < 3 else (6 if h < 9 else (16 if h < 24 else (48 if h < 72 else 99))))
    a = mid_at(mkt, t + 1800)
    if a is None: continue
    buck[b].append((a - mid) * (1 if pos > 0 else -1))
print("%-12s %7s %10s %12s %12s" % ("hrs-to-end", "fills", "mean", "adverse>1pt", "adverse>2pt"))
for b in sorted(buck):
    g = buck[b]; n = len(g)
    print("%-12s %7d %+10.4f %11.1f%% %11.1f%%" % ("<%s h" % b, n, sum(g)/n,
          100*sum(1 for x in g if x < -0.01)/n, 100*sum(1 for x in g if x < -0.02)/n))
print("(fills w/o known end-time: %d — rolled-out markets; treated as missing not zero)" % unk)
