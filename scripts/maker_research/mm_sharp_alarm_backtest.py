"""Sharp-flow toxicity-alarm backtest v0 (read-only).
Sharps = top-500 wallets by realized P&L on full-lifecycle resolved markets
(30d, our verified cash-flow method; users.is_elite deliberately NOT used —
it is degraded). Question: do v1 recorder fills that follow a sharp wallet's
trade in the same market (within 15 min) show worse 30-min outcomes?"""
import json, glob, subprocess, os, collections, bisect

def q(sql):
    out = subprocess.run(["psql", os.environ["DATABASE_URL"], "-Atc",
                          "SET statement_timeout='300s'; " + sql],
                         capture_output=True, text=True, timeout=330)
    if out.returncode != 0:
        print("SQL ERR:", out.stderr[:300]); return []
    return [r.split("|") for r in out.stdout.strip().splitlines() if r and r != "SET"]

rows = q("""WITH mk AS (SELECT id, yes_token_id, upper(resolution) res FROM markets
 WHERE created_at > now() - interval '30 days' AND resolved AND resolution IS NOT NULL)
SELECT t.user_address,
 round(SUM(CASE WHEN t.side='SELL' THEN t.size*t.price ELSE -t.size*t.price END)::numeric
     + SUM((CASE WHEN t.side='BUY' THEN t.size ELSE -t.size END) *
        CASE WHEN (t.token_id=mk.yes_token_id AND mk.res='YES')
              OR (t.token_id<>mk.yes_token_id AND mk.res='NO') THEN 1 ELSE 0 END)::numeric, 0) pnl
FROM trades t JOIN mk ON mk.id=t.market_id
WHERE t.timestamp > now() - interval '30 days'
GROUP BY 1 HAVING count(*) >= 50 ORDER BY 2 DESC LIMIT 500""")
if not rows:
    raise SystemExit("sharp query failed")
sharps = set(r[0] for r in rows)
print("sharp set: top-%d by realized pnl (>=50 trades); best=$%s cutoff=$%s"
      % (len(sharps), rows[0][1], rows[-1][1]))

addr = ",".join("'%s'" % a for a in sharps)
st = q("SELECT market_id, EXTRACT(EPOCH FROM timestamp) FROM trades "
       "WHERE user_address IN (%s) AND timestamp > now() - interval '4 days'" % addr)
sharp_by_mkt = collections.defaultdict(list)
for m, ts in st: sharp_by_mkt[m].append(float(ts))
for v in sharp_by_mkt.values(): v.sort()
print("sharp trades last 4d: %d across %d markets" % (len(st), len(sharp_by_mkt)))

series = collections.defaultdict(list)
fills = []
for fp in sorted(glob.glob("/opt/pa2-maker-sim/samples-*.jsonl")):
    for ln in open(fp):
        try: r = json.loads(ln)
        except Exception: continue
        if r.get("mid") is None: continue
        series[r["id"]].append((r["t"], r["mid"]))
        if r.get("fills"):
            fills.append((r["id"], r["t"], r["mid"], r.get("pos", 0)))
for v in series.values(): v.sort()
print("fill events: %d" % len(fills))

def mid_at(mkt, t):
    s = series[mkt]
    i = bisect.bisect_left(s, (t, -1))
    best = None
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(s) and abs(s[j][0] - t) <= 600:
            if best is None or abs(s[j][0] - t) < abs(best[0] - t):
                best = s[j]
    return best[1] if best else None

groups = {True: [], False: []}
for mkt, t, mid, pos in fills:
    if not pos: continue
    after = mid_at(mkt, t + 1800)
    if after is None: continue
    sl = sharp_by_mkt.get(mkt, [])
    i = bisect.bisect_left(sl, t)
    present = i > 0 and t - sl[i - 1] <= 3600
    groups[present].append((after - mid) * (1 if pos > 0 else -1))

for present in (True, False):
    g = sorted(groups[present])
    if not g:
        print("sharp_present=%s: n=0" % present); continue
    n = len(g); mean = sum(g) / n
    print("sharp_present=%-5s n=%5d  mean_signed_30m=%+.4f  adverse>1pt=%4.1f%%  >2pt=%4.1f%%  median=%+.4f"
          % (present, n, mean,
             100 * sum(1 for x in g if x < -0.01) / n,
             100 * sum(1 for x in g if x < -0.02) / n, g[n // 2]))
