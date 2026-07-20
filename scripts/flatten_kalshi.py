#!/usr/bin/env python3
"""KILL SWITCH: cancel ALL resting Kalshi orders on the pilot account, then
report the account state (resting/positions/balance). Self-contained: signs via
the openssl CLI, so it works even if the venv's `cryptography` is broken. Reads
key id + PEM from env. SAFE to run anytime — with 0 resting it is a clean no-op.

  KALSHI_API_KEY_ID=<id>  KALSHI_RSA_PRIVATE_KEY_PATH=<pem>  python3 flatten_kalshi.py

Does NOT liquidate positions (a maker rarely holds one; if a fill left inventory
it is reported LOUDLY for a manual decision — auto-selling could realize a loss).
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request

KID = os.environ["KALSHI_API_KEY_ID"]
PEM = os.environ["KALSHI_RSA_PRIVATE_KEY_PATH"]
BASE = "https://external-api.kalshi.com"
P = "/trade-api/v2"


def sign(method, path):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{path.split('?')[0]}".encode()   # sign WITHOUT query
    raw = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", PEM,
         "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-2"],
        input=msg, capture_output=True).stdout
    sig = subprocess.run(["openssl", "base64", "-A"], input=raw,
                         capture_output=True).stdout.decode()
    return {"KALSHI-ACCESS-KEY": KID, "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig}


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "kalshi-flatten/1.0", "Content-Type": "application/json",
         **sign(method, path)}
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode()[:200]}


def resting():
    st, d = req("GET", f"{P}/portfolio/orders?status=resting")
    if st != 200:
        print(f"!! could not read resting orders: HTTP {st} {d}")
        return None
    return d.get("orders", []) or []


def main():
    orders = resting()
    if orders is None:
        return 2
    print(f"resting orders to cancel: {len(orders)}")
    failed = []
    for o in orders:
        oid = o.get("order_id")
        st, _ = req("DELETE", f"{P}/portfolio/events/orders/{oid}")
        ok = st in (200, 201, 204, 404)
        print(f"  cancel {o.get('ticker')} {oid}: HTTP {st} {'ok' if ok else 'FAIL'}")
        if not ok:
            failed.append(oid)
        time.sleep(0.2)
    time.sleep(1.0)
    left = resting()
    _, bal = req("GET", f"{P}/portfolio/balance")
    _, pos = req("GET", f"{P}/portfolio/positions")
    npos = [p for p in (pos.get("market_positions") or []) if p.get("position", 0) != 0]
    print(f"\nAFTER: resting={len(left) if left is not None else '?'} "
          f"balance={bal.get('balance_dollars')} nonzero_positions={len(npos)}")
    if left:
        print(f"!!! {len(left)} orders STILL RESTING — retry or cancel manually in the UI")
        return 1
    if npos:
        print(f"!!! HOLDING {len(npos)} position(s) — NOT auto-sold. Decide manually:")
        for p in npos:
            print(f"     {p.get('ticker')} position={p.get('position')}")
    print("\n=> FLAT (no resting orders)" if not left and not npos
          else "\n=> orders cleared; inventory noted above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
