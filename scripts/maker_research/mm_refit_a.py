"""Refit pass A (no external data needed):
1. Fit-1 vol continuation: POST-parser-fix v3 era only, de-overlapped events.
2. Toxicity baseline re-signed by INFERRED fill side (pos delta), not post-fill pos.
3. v2/v3 gate-exit accrual bias bound (the review's pro-gated inflation)."""
import json, glob, gzip, bisect, collections, calendar

V3_FIX = calendar.timegm((2026, 7, 16, 1, 22, 32))

def rows_of(pat):
    out = []
    for fp in glob.glob(pat):
        f = gzip.open(fp, "rt") if fp.endswith(".gz") else open(fp)
        for ln in f:
            try: out.append(json.loads(ln))
            except Exception: pass
    return out

# ---------- 1. Fit-1 refit ----------
series = collections.defaultdict(list)
for r in rows_of("/opt/pa2-maker-sim-v3/samples-*"):
    if r.get("mid") is not None and r["t"] > V3_FIX:
        series[r["id"]].append((r["t"], r["mid"]))
for v in series.values(): v.sort()
print("=== FIT-1 REFIT (post-fix era only, de-overlapped) ===")
print("%-8s %8s %14s %14s" % ("X(pts)", "events", "next10m med/p75", "next30m med/p75"))
for X in (0.015, 0.02, 0.03):
    conts = {600: [], 1800: []}
    n = 0
    for mkt, s in series.items():
        i = 2
        while i < len(s):
            t0, m0 = s[i]; tp, mp = s[i-2]
            if t0 - tp > 200 or abs(m0 - mp) < X:
                i += 1; continue
            n += 1
            sign = 1 if m0 - mp > 0 else -1
            for hor in conts:
                j = bisect.bisect_left(s, (t0 + hor, -1))
                if j < len(s) and abs(s[j][0] - (t0 + hor)) <= 300:
                    conts[hor].append((s[j][1] - m0) * sign)
            # DE-OVERLAP: skip past the longest outcome horizon
            nxt = bisect.bisect_left(s, (t0 + 1800, -1))
            i = max(i + 1, nxt)
    row = ["%.3f" % X, str(n)]
    for hor in (600, 1800):
        c = sorted(conts[hor])
        row.append("%+.3f/%+.3f" % (c[len(c)//2], c[int(.75*len(c))]) if c else "-")
    print("%-8s %8s %14s %14s" % tuple(row))

# ---------- 2. Toxicity re-sign via inferred fill side ----------
v1rows = collections.defaultdict(list)
for r in rows_of("/opt/pa2-maker-sim/samples-*"):
    if r.get("mid") is not None:
        v1rows[r["id"]].append((r["t"], r["mid"], r.get("pos", 0), r.get("fills", 0)))
for v in v1rows.values(): v.sort()
signed = []
skipped_flat = 0
for mkt, s in v1rows.items():
    for i in range(1, len(s)):
        t, mid, pos, fills = s[i]
        if not fills: continue
        dpos = pos - s[i-1][2]
        if abs(dpos) < 1e-9:
            skipped_flat += 1; continue      # round-trip within tick: no net side
        side = 1 if dpos > 0 else -1
        j = bisect.bisect_left(s, (t + 1800, -1e18, 0, 0))
        best = None
        for k in (j-1, j, j+1):
            if 0 <= k < len(s) and abs(s[k][0] - (t+1800)) <= 600:
                if best is None or abs(s[k][0]-(t+1800)) < abs(best[0]-(t+1800)): best = s[k]
        if best is None: continue
        signed.append((best[1] - mid) * side)
signed.sort()
n = len(signed)
print("\n=== TOXICITY BASELINE RE-SIGNED (inferred fill side from pos delta) ===")
print("fill events with net side: %d (flat round-trips excluded: %d)" % (n, skipped_flat))
if n:
    print("mean_signed_30m=%+.4f  adverse>1pt=%.1f%%  adverse>2pt=%.1f%%  median=%+.4f"
          % (sum(signed)/n, 100*sum(1 for x in signed if x < -0.01)/n,
             100*sum(1 for x in signed if x < -0.02)/n, signed[n//2]))
    print("(v0 method said mean +0.6pt / 34%%>1pt / 27%%>2pt — compare)")

# ---------- 3. v2/v3 gate-exit accrual bias bound ----------
print("\n=== GATE-EXIT ACCRUAL BIAS BOUND (v2/v3, review finding) ===")
pools = {}
for base in ("/opt/pa2-maker-sim-v2", "/opt/pa2-maker-sim-v3"):
    try:
        st = json.load(open(base + "/state.json"))
        for k, v in st.items():
            if isinstance(v, dict) and v.get("pool"): pools[str(k)] = float(v["pool"])
    except Exception: pass
for arm, pat in (("v2", "/opt/pa2-maker-sim-v2/samples-*"), ("v3", "/opt/pa2-maker-sim-v3/samples-*")):
    per = collections.defaultdict(list)
    for r in rows_of(pat):
        per[r["id"]].append((r["t"], bool(r.get("quoting")), r.get("shr", 0)))
    bound = 0.0; trans = 0
    for mkt, s in per.items():
        s.sort()
        for i in range(1, len(s)):
            if s[i][1] and not s[i-1][1]:
                trans += 1
                bound += s[i][2] * pools.get(str(mkt), 50.0) * 120 / 86400.0
    acc_tot = 0.0
    try:
        st = json.load(open(pat.split("/samples")[0] + "/state.json"))
        acc_tot = sum(v.get("acc", 0) for v in st.values() if isinstance(v, dict))
    except Exception: pass
    print("%s: %d gate-exit transitions, bias UPPER BOUND $%.2f vs total rewards $%.2f (%.2f%%)"
          % (arm, trans, bound, acc_tot, 100 * bound / max(acc_tot, 1)))
