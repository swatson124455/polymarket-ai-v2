#!/usr/bin/env python3
"""READ-ONLY raw dump of resting Kalshi orders (all fields) to understand side/action/price.
Does NOT modify anything."""
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
    h = {"User-Agent": "kalshi-raw/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


d = get(f"{P}/portfolio/orders?status=resting")
orders = d.get("orders", []) or []
print(f"resting orders: {len(orders)}\n")
# group yes vs no by ticker
for o in orders:
    keys = {k: o.get(k) for k in ("ticker", "side", "action", "yes_price", "no_price",
                                   "price", "remaining_count", "remaining_count_fp", "type")}
    print(json.dumps(keys, separators=(",", ":")))
print("\n=== side counts ===")
from collections import Counter
print(Counter((o.get("side"), o.get("action")) for o in orders))
