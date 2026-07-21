#!/usr/bin/env python3
"""READ-ONLY: signed positions + per-EVENT net delta (the true directional exposure).
Mirrors the bot's event_deltas() grouping (ticker.split('-')[:2]). No modifications."""
import json, os, subprocess, sys, time, urllib.request
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
    r = urllib.request.Request(BASE + path, headers={"User-Agent": "kd/1.0",
        "Content-Type": "application/json", **sign("GET", path)}, method="GET")
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


bal = get(f"{P}/portfolio/balance").get("balance_dollars")
pos = get(f"{P}/portfolio/positions").get("market_positions") or []
ev = defaultdict(float); gross = 0.0; ev_exp = defaultdict(float)
print(f"balance: ${bal}\n--- positions (signed) ---")
for p in pos:
    n = float(p.get("position_fp") or 0)
    if n == 0:
        continue
    t = p.get("ticker"); k = "-".join(t.split("-")[:2])
    exp = float(p.get("market_exposure_dollars") or 0)
    ev[k] += n; ev_exp[k] += exp; gross += exp
    print(f"  {t:32s} pos={n:+7.2f}  exp=${exp:.2f}")
print("\n--- per-EVENT net delta (the throttle signal) ---")
for k in sorted(ev):
    flag = "  <-- DIRECTIONAL >SOFT(30)" if abs(ev[k]) > 30 else ("  <-- >TOL" if abs(ev[k]) >= 3 else "  ~flat")
    print(f"  {k:28s} net={ev[k]:+8.2f} ct  exp=${ev_exp[k]:.2f}{flag}")
print(f"\ngross exposure ${gross:.2f} | net |sum| across events {sum(abs(v) for v in ev.values()):.1f} ct")
