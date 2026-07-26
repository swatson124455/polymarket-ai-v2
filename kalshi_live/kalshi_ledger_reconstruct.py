#!/usr/bin/env python3
"""LEDGER RECONSTRUCT -- NEW FILE, READ-ONLY. openssl-CLI signing. GETs only.
Paginates fills + settlements and reconstructs per-market GROSS buy cost (both sides) vs NET
cash flow, to show empirically that the venue's economics are NET within a market (gross cost
is round-tripped away). Also aggregates per-event to show the event has NO cross-market netting.
"""
import json, os, subprocess, time, urllib.request
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
    h = {"User-Agent": "kalshi-ledger/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    with urllib.request.urlopen(r, timeout=25) as resp:
        return json.loads(resp.read() or b"{}")


def D(x):
    try: return float(x)
    except: return 0.0


def paginate(path, key, maxpages=20):
    out, cur = [], ""
    for _ in range(maxpages):
        d = get(path + (f"&cursor={cur}" if cur else ""))
        rows = d.get(key) or []
        out += rows
        cur = d.get("cursor") or ""
        if not cur or not rows:
            break
        time.sleep(0.3)
    return out


fills = paginate(f"{P}/portfolio/fills?limit=200", "fills")
print(f"fills pulled: {len(fills)}")

# Per market: signed net contracts and NET cash flow (buys negative, sells positive).
# On Kalshi a fill has outcome_side (yes/no) + action (buy/sell) + count + price.
# cash out on buy = price*count ; cash in on sell = price*count. Net position in YES-equiv:
#   buy yes:+count ; sell yes:-count ; buy no:-count ; sell no:+count.
mkt_net_yes = defaultdict(float)      # signed yes-equivalent contracts
mkt_cash = defaultdict(float)         # realized cash flow from trading (excl settlement)
mkt_gross_buy = defaultdict(float)    # gross cash spent on buys (both sides)
mkt_fillcount = defaultdict(int)
for f in fills:
    t = f.get("ticker"); side = f.get("side"); action = f.get("action")
    c = D(f.get("count_fp"))
    yp = D(f.get("yes_price_dollars")); npr = D(f.get("no_price_dollars"))
    price = yp if side == "yes" else npr
    mkt_fillcount[t] += 1
    signed = c if side == "yes" else -c
    if action == "buy":
        mkt_net_yes[t] += signed
        mkt_cash[t] -= price * c
        mkt_gross_buy[t] += price * c
    else:  # sell
        mkt_net_yes[t] -= signed
        mkt_cash[t] += price * c

print("\n=== per-market: gross buy cost vs net cash flow vs net position ===")
print(f"{'ticker':36s} {'fills':>5} {'grossBuy$':>10} {'netCash$':>9} {'netYesEq':>9}")
tot_gross = tot_cash = 0.0
two_sided = []
for t in sorted(mkt_gross_buy, key=lambda k: -mkt_gross_buy[k]):
    gb = mkt_gross_buy[t]; nc = mkt_cash[t]; nq = mkt_net_yes[t]
    tot_gross += gb; tot_cash += nc
    if gb > 3:  # only show markets with meaningful gross
        print(f"{t:36s} {mkt_fillcount[t]:5d} {gb:10.2f} {nc:9.2f} {nq:9.2f}")

print(f"\nTOTAL gross buy cash spent (both sides, lifetime pulled) = ${tot_gross:.2f}")
print(f"TOTAL net trading cash flow (buys+sells, pre-settlement)  = ${tot_cash:.2f}")
print(f"  => gross buys overstate cash-at-risk by ${tot_gross + tot_cash:.2f} that round-tripped"
      f" (net cash out only ${-tot_cash:.2f})")

# settlements: revenue vs gross cost, to show settlement pays on NET
setts = paginate(f"{P}/portfolio/settlements?limit=200", "settlements")
print(f"\n=== settlements pulled: {len(setts)} ===")
print(f"{'ticker':32s} {'res':>4} {'yesCt':>8} {'noCt':>8} {'yesCost$':>9} {'noCost$':>9} "
      f"{'grossCost$':>10} {'revenue$':>9} {'netYesEq':>8}")
srev = sgross = 0.0
for s in setts[:25]:
    yc = D(s.get("yes_count_fp")); nc = D(s.get("no_count_fp"))
    ycost = D(s.get("yes_total_cost_dollars")); ncost = D(s.get("no_total_cost_dollars"))
    rev = (s.get("revenue") or 0) / 100.0
    gross = ycost + ncost
    neteq = yc - nc
    srev += rev; sgross += gross
    print(f"{s.get('ticker'):32s} {str(s.get('market_result')):>4} {yc:8.2f} {nc:8.2f} "
          f"{ycost:9.2f} {ncost:9.2f} {gross:10.2f} {rev:9.2f} {neteq:8.2f}")
print(f"\n(top 25) sum gross settle cost ${sgross:.2f} vs sum revenue ${srev:.2f} "
      f"-- revenue tracks NET position*payout, not gross")
