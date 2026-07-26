#!/usr/bin/env python3
"""RESERVATION RECONCILE -- NEW FILE, READ-ONLY. openssl-CLI signing. GETs only.

One detailed snapshot that reconciles the venue's own accounting:
  balance (free cash) | portfolio_value (position mark) | per-market exposure |
  per-EVENT exposure vs cumulative total_cost | resting-order buy-reserve vs sell.

Then reconstructs the bot's `committed` figure the way maker_kalshi_quoter.py:1193-1198 does
(sum resting price*count + gross held_cost) and contrasts it with what the venue actually locks.
Units: balance/portfolio_value/*_exposure=cents; *_dollars=dollar strings.
"""
import json, os, subprocess, time, urllib.error, urllib.request
from collections import defaultdict

KID = os.environ.get("KALSHI_API_KEY_ID", "89314df3-b170-4d3d-9a7c-fc49336365f2")
PEM = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH", os.path.expanduser("~/.kalshi/prod_key.pem"))
BASE = "https://external-api.kalshi.com"; P = "/trade-api/v2"


def sign(m, path):
    ts = str(int(time.time() * 1000)); msg = f"{ts}{m}{path.split('?')[0]}".encode()
    raw = subprocess.run(["openssl", "dgst", "-sha256", "-sign", PEM, "-sigopt",
        "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-2"], input=msg, capture_output=True).stdout
    sig = subprocess.run(["openssl", "base64", "-A"], input=raw, capture_output=True).stdout.decode()
    return {"KALSHI-ACCESS-KEY": KID, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sig}


def get(path):
    h = {"User-Agent": "kalshi-recon/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    with urllib.request.urlopen(r, timeout=25) as resp:
        return json.loads(resp.read() or b"{}")


def D(x):  # dollar-string -> float
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


bal = get(f"{P}/portfolio/balance")
pos = get(f"{P}/portfolio/positions?limit=1000")
orders = get(f"{P}/portfolio/orders?status=resting&limit=1000")

free_cash = bal.get("balance", 0) / 100.0
pv = bal.get("portfolio_value", 0) / 100.0
print(f"FREE CASH balance = ${free_cash:.2f}   portfolio_value(mark) = ${pv:.2f}   "
      f"sum = ${free_cash+pv:.2f}")

mps = [p for p in (pos.get("market_positions") or [])
       if float(p.get("position_fp") or 0) != 0]
eps = pos.get("event_positions") or []

# signed position + exposure by market, grouped by event
by_event_mkt_exp = defaultdict(float)
by_event_positions = defaultdict(list)
sum_mkt_exposure = 0.0
posmap = {}
for p in mps:
    t = p["ticker"]; n = float(p.get("position_fp") or 0)
    me = D(p.get("market_exposure_dollars"))
    ev = "-".join(t.split("-")[:2])
    by_event_mkt_exp[ev] += me
    by_event_positions[ev].append((t, n, me))
    sum_mkt_exposure += me
    posmap[t] = n

print(f"\nsum of per-market exposure (max-loss basis) = ${sum_mkt_exposure:.2f}")

print("\n=== EVENT-LEVEL (venue's own event_positions) ===")
print(f"{'event':26s} {'event_exp$':>11} {'total_cost$':>12} {'shares_fp':>10} "
      f"{'sum_mkt_exp$':>12}  ratio total/exp")
for e in eps:
    et = e.get("event_ticker")
    ee = D(e.get("event_exposure_dollars"))
    tc = D(e.get("total_cost_dollars"))
    sh = e.get("total_cost_shares_fp")
    smx = by_event_mkt_exp.get(et, 0.0)
    ratio = (tc / ee) if ee else float("nan")
    print(f"{et:26s} {ee:11.2f} {tc:12.2f} {str(sh):>10} {smx:12.2f}  {ratio:6.2f}x")

print("\n=== per-event open positions (signed) ===")
for ev in sorted(by_event_positions):
    print(f"  {ev}  (sum mkt_exp ${by_event_mkt_exp[ev]:.2f})")
    for t, n, me in sorted(by_event_positions[ev]):
        print(f"      {t:34s} pos={n:+8.2f}  mkt_exp=${me:6.2f}")

# ---- resting orders: split buy-reserve vs sell, using correct *_dollars price fields ----
ors = orders.get("orders", []) or []
buy_reserve = 0.0   # sum price*count for BUY orders (what the bot's committed counts)
sell_ct = 0.0
buy_rows, sell_rows = [], []
for o in ors:
    side = o.get("side"); action = o.get("action")
    rc = float(o.get("remaining_count_fp") or 0)
    yp = D(o.get("yes_price_dollars")); npr = D(o.get("no_price_dollars"))
    price = yp if side == "yes" else npr
    if action == "buy":
        buy_reserve += price * rc
        buy_rows.append((o["ticker"], side, price, rc))
    else:
        sell_ct += rc
        sell_rows.append((o["ticker"], side, price, rc))

print(f"\n=== RESTING ORDERS ({len(ors)}) ===")
print(f"  BUY orders: {len(buy_rows)}   buy-reserve (sum price*count) = ${buy_reserve:.2f}")
print(f"  SELL orders: {len(sell_rows)}  total sell count = {sell_ct:.0f}")
print("  --- BUY orders (reserve cash if venue reserves resting) ---")
for t, s, pr, rc in sorted(buy_rows):
    print(f"      {t:34s} {s:3s} buy @ ${pr:.2f} x{rc:.0f}  = ${pr*rc:.2f}")
print("  --- SELL orders (reduce/close; may or may not reserve) ---")
for t, s, pr, rc in sorted(sell_rows):
    held = posmap.get(t, 0)
    reduces = (s == "yes" and held > 0)
    print(f"      {t:34s} {s:3s} sell @ ${pr:.2f} x{rc:.0f}  held={held:+.0f} "
          f"{'REDUCES-LONG' if reduces else 'opens/short?'}")

# ---- the bot's committed figure (quoter :1193-1198) vs venue reality ----
bot_committed = buy_reserve + sum_mkt_exposure
print("\n=== GUARD RECONCILIATION ===")
print(f"  bot 'committed' ~= buy_reserve + gross held_cost = "
      f"${buy_reserve:.2f} + ${sum_mkt_exposure:.2f} = ${bot_committed:.2f}")
print(f"  venue free cash (balance)               = ${free_cash:.2f}")
print(f"  venue position mark (portfolio_value)   = ${pv:.2f}")
print(f"  free_cash + sum_mkt_exposure(cost)      = ${free_cash+sum_mkt_exposure:.2f}")
print(f"  free_cash + portfolio_value(mark)       = ${free_cash+pv:.2f}")
