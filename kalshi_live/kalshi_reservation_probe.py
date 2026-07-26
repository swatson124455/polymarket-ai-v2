#!/usr/bin/env python3
"""RESERVATION PROBE -- NEW FILE, READ-ONLY. Signs via openssl CLI (venv crypto may be broken).
NEVER places/cancels/modifies anything: only GETs.

Goal: dump the RAW field shapes the venue exposes for balance / positions / resting orders /
fills / settlements, so we can derive EMPIRICALLY what Kalshi reserves. Prints raw JSON of the
first object of each collection (all keys), plus compact rows for reconciliation.

Units (canon M7f): balance/*_exposure/revenue = integer CENTS; *_dollars = dollar strings.
"""
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
    h = {"User-Agent": "kalshi-resv/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error_body": e.read().decode()[:300]}


def main():
    print("=" * 80)
    print("BALANCE (raw, all fields)")
    print("=" * 80)
    sb, bal = get(f"{P}/portfolio/balance")
    print(f"HTTP {sb}")
    print(json.dumps(bal, indent=1))

    print("\n" + "=" * 80)
    print("POSITIONS (raw first obj + compact rows)")
    print("=" * 80)
    sp, pos = get(f"{P}/portfolio/positions?limit=1000")
    mps = pos.get("market_positions") or []
    ep = pos.get("event_positions") or []
    print(f"HTTP {sp}  market_positions={len(mps)}  event_positions={len(ep)}  "
          f"top_keys={sorted(pos.keys())}")
    nonzero = [p for p in mps if float(p.get("position_fp") or p.get("position") or 0) != 0]
    if mps:
        print("--- RAW market_position[0] ---")
        print(json.dumps(mps[0], indent=1))
    if ep:
        print("--- RAW event_position[0] ---")
        print(json.dumps(ep[0], indent=1))
    print(f"--- nonzero market_positions ({len(nonzero)}) ---")
    for p in nonzero:
        print(json.dumps({k: p.get(k) for k in (
            "ticker", "position", "position_fp", "market_exposure", "market_exposure_dollars",
            "realized_pnl", "realized_pnl_dollars", "fees_paid", "fees_paid_dollars",
            "total_traded", "resting_orders_count", "last_updated_ts")}, separators=(",", ":")))

    print("\n" + "=" * 80)
    print("RESTING ORDERS (raw first obj + compact rows)")
    print("=" * 80)
    so, orders = get(f"{P}/portfolio/orders?status=resting&limit=1000")
    ors = orders.get("orders", []) or []
    print(f"HTTP {so}  resting_orders={len(ors)}  top_keys={sorted(orders.keys())}")
    if ors:
        print("--- RAW order[0] ---")
        print(json.dumps(ors[0], indent=1))
    for o in ors:
        print(json.dumps({k: o.get(k) for k in (
            "ticker", "side", "action", "type", "yes_price", "no_price", "price",
            "remaining_count", "remaining_count_fp", "initial_count", "maker_fill_count",
            "taker_fill_count", "status", "created_time")}, separators=(",", ":")))

    print("\n" + "=" * 80)
    print("FILLS (raw first obj + last 30 compact)  -- historical cash-cost of fills")
    print("=" * 80)
    sf, fills = get(f"{P}/portfolio/fills?limit=200")
    fl = fills.get("fills", []) or []
    print(f"HTTP {sf}  fills_returned={len(fl)}  top_keys={sorted(fills.keys())}")
    if fl:
        print("--- RAW fill[0] ---")
        print(json.dumps(fl[0], indent=1))
        for f in fl[:30]:
            print(json.dumps({k: f.get(k) for k in (
                "ticker", "side", "action", "count", "yes_price", "no_price",
                "is_taker", "created_time", "order_id", "trade_id")}, separators=(",", ":")))

    print("\n" + "=" * 80)
    print("SETTLEMENTS (raw first obj + last 15 compact)")
    print("=" * 80)
    ss, sett = get(f"{P}/portfolio/settlements?limit=100")
    st = sett.get("settlements", []) or []
    print(f"HTTP {ss}  settlements_returned={len(st)}  top_keys={sorted(sett.keys())}")
    if st:
        print("--- RAW settlement[0] ---")
        print(json.dumps(st[0], indent=1))
        for s in st[:15]:
            print(json.dumps(s, separators=(",", ":")))

    return 0


if __name__ == "__main__":
    sys.exit(main())
