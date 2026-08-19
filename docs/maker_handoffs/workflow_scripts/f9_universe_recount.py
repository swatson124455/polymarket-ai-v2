#!/usr/bin/env python3
"""F9 RECOUNT — concentrated-cliff qualifying universe under the EXACT plan filters.
READ-ONLY. Filters: (1) survivable series allowlist (D3 measured), (2) market
close<=8d, (3) program runway min(close, program_end) - now >= 49h.
Scoring: pool/day = period_reward/10000 (R1 canon); projected accrual band =
runway_days x [0.50, 0.63] $/market-day (v2 measured best-real-day rate at
30-59ct, INFERRED forward); cliff gate pass = low-end projection >= $1.50.
"""
import datetime, json, sys
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import kalshi_attribution_ledger as kal

SURVIVABLE = {"KXAAAGASD", "KXAAAGASW", "KXTOPMODEL", "KXCLAYTONDNI",
              "KXDIESELW", "KXUSDJPY", "KXCLARITYVOTE"}
RATE_LO, RATE_HI = 0.50, 0.63
CLIFF_GATE = 1.50
NOW = datetime.datetime.now(datetime.timezone.utc)

def iso(s):
    dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)

progs, cursor = [], ""
for _ in range(50):
    q = kal.P + "/incentive_programs?status=active&limit=10000" + (f"&cursor={cursor}" if cursor else "")
    d = kal.get(q)
    progs.extend(d.get("incentive_programs") or [])
    cursor = d.get("next_cursor") or d.get("cursor") or ""
    if not cursor: break
else:
    print("ALARM: pagination bound hit", file=sys.stderr)
progs = [p for p in progs if (p.get("incentive_type") or "liquidity") == "liquidity" and p.get("market_ticker")]
print(f"read_ts: {NOW.isoformat()}  active_liquidity_programs: {len(progs)}")

surv = [p for p in progs if p["market_ticker"].split("-")[0] in SURVIVABLE]
by_series = {}
for p in surv: by_series[p["market_ticker"].split("-")[0]] = by_series.get(p["market_ticker"].split("-")[0], 0) + 1
print("survivable-class programs (pre close/window filters):", len(surv), by_series)

tickers = sorted({p["market_ticker"] for p in surv})
meta = {}
for i in range(0, len(tickers), 90):
    dd = kal.get(kal.P + "/markets?limit=1000&tickers=" + ",".join(tickers[i:i+90]))
    for m in dd.get("markets") or []: meta[m["ticker"]] = m

drops = {"no_meta": 0, "not_open": 0, "no_close": 0, "close_gt_8d": 0, "runway_lt_49h": 0}
rows = []
for p in surv:
    t = p["market_ticker"]; m = meta.get(t)
    if not m: drops["no_meta"] += 1; continue
    if (m.get("status") or "").lower() not in ("active", "open"): drops["not_open"] += 1; continue
    ct = m.get("close_time")
    if not ct: drops["no_close"] += 1; continue
    close_s = (iso(ct) - NOW).total_seconds()
    if close_s > 8*86400: drops["close_gt_8d"] += 1; continue
    end_s = (iso(p["end_date"]) - NOW).total_seconds() if p.get("end_date") else close_s
    runway_s = min(close_s, end_s)
    if runway_s < 49*3600: drops["runway_lt_49h"] += 1; continue
    runway_d = runway_s / 86400.0
    pool_day = (p.get("period_reward") or 0) / 10000.0
    proj_lo = min(runway_d * RATE_LO, pool_day * runway_d)
    proj_hi = min(runway_d * RATE_HI, pool_day * runway_d)
    yb = float(m.get("yes_bid_dollars") or 0) or (m.get("yes_bid") or 0)/100.0
    ya = float(m.get("yes_ask_dollars") or 0) or (m.get("yes_ask") or 0)/100.0
    mid = (yb+ya)/2.0 if (yb and ya) else (yb or ya or 0.5)
    rows.append({"ticker": t, "pool_day": pool_day, "runway_d": round(runway_d,2),
                 "days_to_close": round(close_s/86400.0,2),
                 "program_end": p.get("end_date"), "target": float(p.get("target_size_fp") or 0),
                 "df": (p.get("discount_factor_bps") or 5000)/10000.0,
                 "proj_lo": round(proj_lo,2), "proj_hi": round(proj_hi,2),
                 "cliff_pass": proj_lo >= CLIFF_GATE, "mid": round(mid,3),
                 "pref_window": 3 <= runway_d <= 7,
                 "cap50ct_usd": round(50*mid + 50*(1-mid),2),
                 "title": (m.get("title") or "")[:60]})

rows.sort(key=lambda r: -r["proj_lo"])
n_pass = sum(1 for r in rows if r["cliff_pass"])
print("drops:", drops)
print(f"QUALIFYING (all filters): {len(rows)}   cliff-gate proj_lo>=$1.50: {n_pass}")
for r in rows:
    print(" %-34s pool/d %6.2f  runway %5.2fd  d2c %5.2f  proj $%.2f-%.2f  %s%s  tgt %g  %s"
          % (r["ticker"], r["pool_day"], r["runway_d"], r["days_to_close"],
             r["proj_lo"], r["proj_hi"], "PASS" if r["cliff_pass"] else "sub ",
             "*" if r["pref_window"] else " ", r["target"], r["title"][:38]))
json.dump({"read_ts": NOW.isoformat(), "n_active_liquidity": len(progs),
           "n_survivable_class": len(surv), "by_series": by_series, "drops": drops,
           "qualifying": rows}, open("/tmp/F9_RECOUNT.json","w"), indent=1)
print("wrote /tmp/F9_RECOUNT.json")
