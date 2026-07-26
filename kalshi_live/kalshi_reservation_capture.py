#!/usr/bin/env python3
"""RESERVATION TIME-SERIES CAPTURE -- NEW FILE, READ-ONLY. openssl-CLI signing. GETs only.
NEVER trades. Polls balance/positions/orders/fills every ~SPACING s for N polls and writes one
raw JSONL record per poll to OUT. Purpose: watch free-cash `balance` move against resting-order
churn and fills, to empirically settle whether resting orders reserve cash and whether offsetting
fills release collateral. Analysis is done offline on the JSONL (keep capture dumb)."""
import json, os, subprocess, sys, time, urllib.error, urllib.request

KID = os.environ.get("KALSHI_API_KEY_ID", "89314df3-b170-4d3d-9a7c-fc49336365f2")
PEM = os.environ.get("KALSHI_RSA_PRIVATE_KEY_PATH", os.path.expanduser("~/.kalshi/prod_key.pem"))
BASE = "https://external-api.kalshi.com"; P = "/trade-api/v2"
OUT = os.environ.get("RESV_OUT", "/tmp/kalshi_resv_ts.jsonl")
N = int(os.environ.get("RESV_N", "45"))
SPACING = float(os.environ.get("RESV_SPACING", "5"))


def sign(m, path):
    ts = str(int(time.time() * 1000)); msg = f"{ts}{m}{path.split('?')[0]}".encode()
    raw = subprocess.run(["openssl", "dgst", "-sha256", "-sign", PEM, "-sigopt",
        "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-2"], input=msg, capture_output=True).stdout
    sig = subprocess.run(["openssl", "base64", "-A"], input=raw, capture_output=True).stdout.decode()
    return {"KALSHI-ACCESS-KEY": KID, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": sig}


def get(path):
    h = {"User-Agent": "kalshi-resvts/1.0", "Content-Type": "application/json", **sign("GET", path)}
    r = urllib.request.Request(BASE + path, headers=h, method="GET")
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:200]}


def poll():
    bal = get(f"{P}/portfolio/balance")
    pos = get(f"{P}/portfolio/positions?limit=1000")
    orders = get(f"{P}/portfolio/orders?status=resting&limit=1000")
    fills = get(f"{P}/portfolio/fills?limit=50")
    mps = [{"t": p["ticker"], "n": float(p.get("position_fp") or 0),
            "exp": p.get("market_exposure_dollars")}
           for p in (pos.get("market_positions") or [])
           if float(p.get("position_fp") or 0) != 0]
    ors = [{"id": o.get("order_id", "")[:8], "t": o.get("ticker"), "s": o.get("side"),
            "a": o.get("action"), "yp": o.get("yes_price_dollars"),
            "np": o.get("no_price_dollars"), "rc": o.get("remaining_count_fp")}
           for o in (orders.get("orders") or [])]
    fl = [{"id": f.get("fill_id", "")[:8], "t": f.get("ticker"), "s": f.get("side"),
           "a": f.get("action"), "c": f.get("count_fp"), "yp": f.get("yes_price_dollars"),
           "np": f.get("no_price_dollars"), "taker": f.get("is_taker"), "ts": f.get("ts")}
          for f in (fills.get("fills") or [])[:20]]
    return {"wall": time.time(), "bal": bal.get("balance"), "pv": bal.get("portfolio_value"),
            "bal_ts": bal.get("updated_ts"), "pos": mps, "orders": ors, "fills": fl}


def main():
    with open(OUT, "w") as fh:
        for i in range(N):
            rec = poll()
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            fh.flush()
            b = rec.get("bal"); pv = rec.get("pv")
            print(f"[{i+1:02d}/{N}] bal={b} pv={pv} n_ord={len(rec['orders'])} "
                  f"n_pos={len(rec['pos'])} last_fill={rec['fills'][0]['id'] if rec['fills'] else '-'}",
                  flush=True)
            if i < N - 1:
                time.sleep(SPACING)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
