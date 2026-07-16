"""REAL-maker cohort study — measurement only, zero simulation.

Cohort: flagged market-maker wallets active in the 30d tape.
Income:  actual REWARD + MAKER_REBATE payment records (data-api activity,
         every entry a Polygon tx) over the last 30d.
Trading: cash-flow accounting from OUR ingested tape on FULL-LIFECYCLE
         resolved markets (created within 30d AND resolved): realized =
         (sells - buys cash) + final_position x on-chain payout(0/1).
Chain:   spot-verify one REWARD tx receipt on Polygon RPC if configured.
"""
import json, os, subprocess, time, urllib.request
from collections import defaultdict

UA = {"User-Agent": "pa2-maker-real-study/1.0"}
CUT = time.time() - 30 * 86400

def q(sql):
    out = subprocess.run(["psql", os.environ["DATABASE_URL"], "-Atc",
                          "SET statement_timeout='300s'; " + sql],
                         capture_output=True, text=True, timeout=330)
    if out.returncode != 0:
        print("SQL ERR:", out.stderr[:200]); return []
    return [r.split("|") for r in out.stdout.strip().splitlines() if r and r != "SET"]

def api(url):
    for _ in range(2):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=15))
        except Exception:
            time.sleep(1)
    return []

# 1. cohort
cohort = [r[0] for r in q(
    "SELECT DISTINCT u.address FROM users u JOIN trades t ON t.user_address=u.address "
    "WHERE u.is_likely_market_maker AND t.timestamp > now() - interval '30 days'")]
print("cohort wallets:", len(cohort))

# 2. realized trading P&L per wallet x sector (full-lifecycle resolved markets)
addr_list = ",".join("'%s'" % a for a in cohort)
rows = q(
    "WITH mk AS (SELECT id, lower(COALESCE(NULLIF(category,''),'unknown')) cat, "
    " yes_token_id, upper(resolution) res FROM markets "
    " WHERE created_at > now() - interval '30 days' AND resolved AND resolution IS NOT NULL) "
    "SELECT t.user_address, mk.cat, "
    " round(SUM(CASE WHEN t.side='SELL' THEN t.size*t.price ELSE -t.size*t.price END)::numeric,2) cash, "
    " round(SUM((CASE WHEN t.side='BUY' THEN t.size ELSE -t.size END) * "
    "  CASE WHEN (t.token_id=mk.yes_token_id AND mk.res='YES') "
    "        OR (t.token_id<>mk.yes_token_id AND mk.res='NO') THEN 1 ELSE 0 END)::numeric,2) payout, "
    " round(SUM(t.size*t.price)::numeric,0) vol "
    "FROM trades t JOIN mk ON mk.id=t.market_id "
    "WHERE t.user_address IN (%s) AND t.timestamp > now() - interval '30 days' "
    "GROUP BY 1,2" % addr_list)
pnl = defaultdict(lambda: defaultdict(float))
vol = defaultdict(float)
wallet_pnl = defaultdict(float)
for w, cat, cash, payout, v in rows:
    realized = float(cash) + float(payout)
    pnl[cat]["trading"] += realized
    vol[cat] += float(v)
    wallet_pnl[w] += realized
print("pnl rows:", len(rows))

# 3. real income: REWARD + MAKER_REBATE per wallet (30d)
income = defaultdict(float)
wallet_income = defaultdict(float)
sample_tx = None
for i, w in enumerate(cohort):
    for typ in ("REWARD", "MAKER_REBATE"):
        total, off = 0.0, 0
        while off <= 1000:
            acts = api("https://data-api.polymarket.com/activity?user=%s&type=%s&limit=500&offset=%d"
                       % (w, typ, off))
            if not isinstance(acts, list) or not acts:
                break
            oldest = None
            for a in acts:
                try:
                    ts, usd = float(a["timestamp"]), float(a["usdcSize"])
                except Exception:
                    continue
                oldest = ts if oldest is None else min(oldest, ts)
                if ts >= CUT:
                    total += usd
                    if typ == "REWARD" and sample_tx is None:
                        sample_tx = (w, a.get("transactionHash"), usd)
            if len(acts) < 500 or (oldest is not None and oldest < CUT):
                break
            off += 500
        income[typ] += total
        wallet_income[w] += total
    if (i + 1) % 20 == 0:
        print("  income pulled for %d/%d wallets" % (i + 1, len(cohort)), flush=True)

# 4. report
print("\n=== REAL 30d cohort economics (%d flagged makers) ===" % len(cohort))
print("income (payment records): REWARD $%.0f  MAKER_REBATE $%.0f  TOTAL $%.0f"
      % (income["REWARD"], income["MAKER_REBATE"],
         income["REWARD"] + income["MAKER_REBATE"]))
print("\ntrading realized P&L by sector (full-lifecycle resolved markets):")
print("%-14s %12s %14s" % ("sector", "realized$", "maker vol$"))
for cat in sorted(pnl, key=lambda c: -pnl[c]["trading"]):
    print("%-14s %12.0f %14.0f" % (cat, pnl[cat]["trading"], vol[cat]))
print("%-14s %12.0f %14.0f" % ("TOTAL", sum(p["trading"] for p in pnl.values()),
                               sum(vol.values())))
w_both = [w for w in cohort]
net = [(wallet_pnl.get(w, 0) + wallet_income.get(w, 0)) for w in w_both]
pos = sum(1 for x in net if x > 0)
net.sort()
print("\nper-wallet NET (trading realized + income): %d/%d positive, median $%.0f, "
      "p25 $%.0f, p75 $%.0f" % (pos, len(net), net[len(net)//2],
                                net[len(net)//4], net[3*len(net)//4]))
top = sorted(w_both, key=lambda w: -(wallet_pnl.get(w, 0) + wallet_income.get(w, 0)))[:5]
for w in top:
    print("  top: %s trading=%.0f income=%.0f" % (w[:10], wallet_pnl.get(w, 0),
                                                  wallet_income.get(w, 0)))

# 5. chain spot-verify one REWARD tx
if sample_tx:
    w, tx, usd = sample_tx
    rpc = os.environ.get("POLYGON_RPC_URL") or os.environ.get("RPC_URL") or ""
    if rpc and tx:
        try:
            req = urllib.request.Request(rpc, headers={"Content-Type": "application/json"},
                data=json.dumps({"jsonrpc": "2.0", "id": 1,
                                 "method": "eth_getTransactionReceipt",
                                 "params": [tx]}).encode())
            rec = json.load(urllib.request.urlopen(req, timeout=15)).get("result")
            ok = rec and rec.get("status") == "0x1"
            n_transfers = sum(1 for lg in (rec or {}).get("logs", [])
                              if lg.get("topics") and lg["topics"][0].startswith("0xddf252ad"))
            print("\nchain verify REWARD tx %s: receipt=%s transfers_in_tx=%d ($%.2f to %s)"
                  % (tx[:14], "OK" if ok else "FAIL", n_transfers, usd, w[:10]))
        except Exception as e:
            print("\nchain verify skipped:", type(e).__name__)
    else:
        print("\nchain verify: no RPC configured (tx %s, $%.2f)" % ((tx or "?")[:14], usd))
