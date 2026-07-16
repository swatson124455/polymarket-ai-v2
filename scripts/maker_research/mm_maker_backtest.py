"""Maker policy BACKTEST — systematic, 30 days, all sectors. Conservative floor.

Replays maker policies against REAL history:
  fills     = real tape prints strictly through the hypothetical quote (deduped)
  outcomes  = real on-chain resolutions (full-lifecycle markets only)
  rebates   = EXACT per fill: rebate% x taker_fee(shares x rate x p(1-p)),
              conservative rates (sports 15%, crypto 20%, others 25%, geo 0%)
  spreads   = measured priors from the clean best-of-book history (sector x
              price-bucket medians), not assumptions
  pools     = EXCLUDED (competitor depth not in history) -> NET here is a FLOOR;
              measured pools ($75.8K/day posted) are upside on top.
Policies: {naive, gated} x {fast re-center per print, stale 5-min re-center}.
Gates (historical proxies): in_play ~ within 6h of resolved_at (esports/sports);
extreme_wx = weather mid outside [0.10,0.90]; last_hours = within 2h of
end_date_iso; vol_pull = |mid move| > 2pt within 2 min -> off 10 min.
"""
import json, os, subprocess, time
from collections import defaultdict

def q(sql, timeout=340):
    out = subprocess.run(["psql", os.environ["DATABASE_URL"], "-Atc",
                          "SET statement_timeout='300s'; " + sql],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        print("SQL ERR:", out.stderr[:300]); return []
    return [r.split("|") for r in out.stdout.strip().splitlines() if r and r != "SET"]

FEE = {"sports": (0.05, 0.15), "esports": (0.05, 0.15), "crypto": (0.07, 0.20),
       "finance": (0.04, 0.25), "politics": (0.04, 0.25), "weather": (0.05, 0.25),
       "geopolitical": (0.0, 0.0), "unknown": (0.05, 0.25)}
MSZ = {"weather": 30, "esports": 250, "sports": 150, "finance": 200,
       "politics": 50, "crypto": 100, "geopolitical": 50, "unknown": 100}

# 1. measured spread priors from CLEAN book history (sector x price bucket)
print("building spread priors from clean orderbook history...", flush=True)
prior_rows = q(
    "SELECT COALESCE(lower(NULLIF(m.category,'')),'unknown'), width_bucket(o.mid_price,0,1,10), "
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY o.spread) "
    "FROM orderbook_snapshots o JOIN markets m ON m.id=o.market_id "
    "WHERE o.spread <= 0.2 AND o.mid_price BETWEEN 0.02 AND 0.98 GROUP BY 1,2")
PRIOR = {}
for cat, b, s in prior_rows:
    PRIOR[(cat, int(b))] = max(float(s), 0.002)
def spread_of(cat, mid):
    b = min(max(int(mid * 10) + 1, 1), 10)
    return PRIOR.get((cat, b)) or PRIOR.get(("unknown", b)) or 0.01
print("priors:", len(PRIOR), flush=True)

# 2. universe: full-lifecycle resolved markets w/ tape in the last 30d
mkts = q(
    "SELECT m.id, COALESCE(lower(NULLIF(m.category,'')),'unknown'), m.yes_token_id, "
    " upper(m.resolution), extract(epoch FROM m.resolved_at), extract(epoch FROM m.end_date_iso), n.np "
    "FROM markets m JOIN ("
    "  SELECT market_id, count(*) np FROM trades WHERE timestamp > now()-interval '30 days' "
    "  GROUP BY 1 HAVING count(*) >= 50) n ON n.market_id=m.id "
    "WHERE m.created_at > now()-interval '30 days' AND m.resolved AND m.resolution IS NOT NULL "
    " AND m.yes_token_id IS NOT NULL")
print("backtest universe: %d full-lifecycle resolved markets w/ >=50 prints" % len(mkts), flush=True)

POLICIES = [("naive_fast", False, 1), ("naive_stale", False, 300),
            ("gated_fast", True, 1), ("gated_stale", True, 300)]
agg = defaultdict(lambda: defaultdict(float))   # (policy, cat) -> metrics

def run_market(mid_id, cat, yes_tok, res, resolved_at, end_iso, prints):
    """prints: list of (ts, token, price, size) ascending, deduped, YES token only."""
    rate, reb = FEE.get(cat, (0.05, 0.25))
    msz = MSZ.get(cat, 100)
    sgn_payout = 1.0 if res == "YES" else 0.0
    for pname, gated, recenter_s in POLICIES:
        pos = cash = rebates = fills = 0.0
        q_mid = None; q_t = -1e18; pull_until = 0.0; last_mid = None; last_mid_t = -1e18
        cap = 3 * msz
        for ts, p in prints:
            # re-center
            if q_mid is None or ts - q_t >= recenter_s:
                if last_mid is not None and ts - last_mid_t <= 180 and abs(p - last_mid) > 0.02:
                    pull_until = ts + 600
                q_mid, q_t = p, ts
            last_mid, last_mid_t = p, ts
            if gated:
                if cat in ("esports", "sports") and resolved_at and ts >= resolved_at - 6 * 3600:
                    continue
                if cat == "weather" and not (0.10 <= q_mid <= 0.90):
                    continue
                if end_iso and ts >= end_iso - 2 * 3600:
                    continue
                if ts < pull_until:
                    continue
            s = spread_of(cat, q_mid)
            bid, ask = q_mid - s / 2, q_mid + s / 2
            if p < bid and pos + msz <= cap:
                pos += msz; cash -= msz * bid; fills += 1
                rebates += reb * (msz * rate * bid * (1 - bid))
            elif p > ask and pos - msz >= -cap:
                pos -= msz; cash += msz * ask; fills += 1
                rebates += reb * (msz * rate * ask * (1 - ask))
        cash += pos * sgn_payout               # settle at REAL resolution
        a = agg[(pname, cat)]
        a["pnl"] += cash; a["reb"] += rebates; a["fills"] += fills
        a["mkts"] += 1; a["notional"] += fills * msz * 0.5

BATCH = 400
for i in range(0, len(mkts), BATCH):
    chunk = mkts[i:i + BATCH]
    ids = ",".join("'%s'" % m[0] for m in chunk)
    yes_by = {m[0]: m[2] for m in chunk}
    rows = q("SELECT market_id, token_id, price, extract(epoch FROM timestamp) "
             "FROM (SELECT DISTINCT market_id, token_id, price, size, timestamp FROM trades "
             " WHERE market_id IN (%s) AND timestamp > now()-interval '30 days') d "
             "ORDER BY market_id, timestamp" % ids, timeout=340)
    tape = defaultdict(list)
    for mk, tok, p, ts in rows:
        if tok == yes_by.get(mk):              # YES-token prints only (consistent basis)
            tape[mk].append((float(ts), float(p)))
    for m in chunk:
        pr = tape.get(m[0])
        if pr and len(pr) >= 25:
            run_market(m[0], m[1], m[2], m[3],
                       float(m[4]) if m[4] else None,
                       float(m[5]) if m[5] else None, pr)
    print("  processed %d/%d markets" % (min(i + BATCH, len(mkts)), len(mkts)), flush=True)

print("\n=== BACKTEST: 30d, conservative FLOOR (pools excluded) ===")
print("%-12s %-14s %6s %8s %12s %10s %12s" % ("policy", "sector", "mkts", "fills", "tradePnL$", "rebates$", "NET_floor$"))
for pname, _, _ in POLICIES:
    tot = [0.0, 0.0]
    for cat in sorted({k[1] for k in agg if k[0] == pname},
                      key=lambda c: -(agg[(pname, c)]["pnl"] + agg[(pname, c)]["reb"])):
        a = agg[(pname, cat)]
        net = a["pnl"] + a["reb"]
        tot[0] += a["pnl"]; tot[1] += a["reb"]
        print("%-12s %-14s %6d %8d %12.0f %10.0f %12.0f" % (pname, cat, a["mkts"], a["fills"], a["pnl"], a["reb"], net))
    print("%-12s %-14s %6s %8s %12.0f %10.0f %12.0f" % (pname, "TOTAL", "", "", tot[0], tot[1], tot[0] + tot[1]))
    print()
