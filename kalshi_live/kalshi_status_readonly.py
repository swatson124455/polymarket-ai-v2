#!/usr/bin/env python3
"""READ-ONLY Kalshi account status: balance, positions, resting orders. Signs via the
openssl CLI (works even if the venv cryptography is broken). Does NOT cancel or place
anything — safe to run while maker cover bids are clearing inventory."""
import json, os, subprocess, sys, time, urllib.error, urllib.request

KID = os.environ.get("KALSHI_API_KEY_ID", "89314df3-b170-4d3d-9a7c-fc49336365f2")
PEM = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH", os.path.expanduser("~/.kalshi/prod_key.pem"))
BASE = "https://external-api.kalshi.com"
P = "/trade-api/v2"


def sign(method, path):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{path.split('?')[0]}".encode()
    raw = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", PEM,
         "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-2"],
        input=msg, capture_output=True).stdout
    sig = subprocess.run(["openssl", "base64", "-A"], input=raw, capture_output=True).stdout.decode()
    return {"KALSHI-ACCESS-KEY": KID, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sig}


def get(path):
    h = {"User-Agent": "kalshi-status/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode()[:200]}


def main():
    sb, bal = get(f"{P}/portfolio/balance")
    sp, pos = get(f"{P}/portfolio/positions")
    so, orders = get(f"{P}/portfolio/orders?status=resting")
    print(f"balance: HTTP {sb} -> {bal.get('balance_dollars', bal)}")
    mps = [p for p in (pos.get("market_positions") or [])
           if float(p.get("position_fp") or 0) != 0]
    print(f"nonzero positions: {len(mps)}")
    net_cost = 0.0
    for p in mps:
        me = float(p.get("market_exposure_dollars") or 0)
        net_cost += me
        print(f"   {p.get('ticker')}  pos={p.get('position_fp')}  exposure=${me:.2f}")
    print(f"total position exposure: ${net_cost:.2f}")
    ors = orders.get("orders", []) or []
    print(f"resting orders: {len(ors)}")
    for o in ors:
        print(f"   {o.get('ticker')} {o.get('side')} @{o.get('yes_price') or o.get('price')} x{o.get('remaining_count')}")


if __name__ == "__main__":
    sys.exit(main())
