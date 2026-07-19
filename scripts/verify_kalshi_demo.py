#!/usr/bin/env python3
"""Kalshi DEMO verification harness — run once against the demo environment to
pin the order surface before any live work. DEMO ONLY (fake money).

Requires (operator-set; the harness never sees raw key material):
    KALSHI_TRADING_MODE=demo
    KALSHI_API_KEY_ID=<demo key id>
    KALSHI_RSA_PRIVATE_KEY_PATH=<path to demo PEM, OUTSIDE the repo>

Refuses to run in any mode other than demo. Places TINY (1-contract), deeply
non-marketable, post-only orders on a liquid demo market, reads them back,
then cancels everything it created (cleanup in finally). Resolves the #1 open
item: which order shape (legacy /portfolio/orders vs V2 /portfolio/events/orders)
the endpoint accepts, and the exact field formatting — from REAL responses.

Stages (each prints PASS/FAIL/SKIP; a stage failing does not skip cleanup):
  0 env + credentials + PEM load
  1 authed read (balance)      -> signing works against demo
  2 discover a liquid demo market + its book
  3 order lifecycle: create (legacy, then V2 on failure) -> read back -> cancel
  4 self-trade-prevention probe (only if lifecycle worked)
Nothing is committed; prints a summary + recommendation.
"""
import json
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient, API_ROOT  # noqa: E402

TINY = 1                      # contracts
RESULTS = []


def line(stage, status, msg):
    RESULTS.append((stage, status, msg))
    print(f"[{status:4}] stage {stage}: {msg}")


def err_body(e):
    try:
        return e.read().decode()[:400]
    except Exception:
        return str(e)


def main():
    if os.environ.get("KALSHI_TRADING_MODE") != "demo":
        print("REFUSING: set KALSHI_TRADING_MODE=demo (this harness is demo-only).")
        return 2
    try:
        c = KalshiOrderClient()   # validates creds present for demo
    except Exception as e:
        print(f"REFUSING: {e}")
        return 2
    kid = os.environ.get("KALSHI_API_KEY_ID", "")
    line(0, "PASS", f"mode=demo base={c.base} key_id={kid[:6]}… (creds loaded, PEM parses)")

    created_order_ids = []
    shape_used = None
    market = None
    try:
        # --- stage 1: authed read ---
        try:
            t0 = time.time()
            bal = c.get_balance()
            line(1, "PASS", f"balance read ok ({(time.time()-t0)*1000:.0f}ms): "
                            f"{json.dumps(bal)[:120]}")
        except urllib.error.HTTPError as e:
            line(1, "FAIL", f"authed read {e.code}: {err_body(e)} "
                            f"(signature/key/clock issue — fix before proceeding)")
            return 1

        # --- stage 2: discover a liquid demo market ---
        try:
            import urllib.request
            req = urllib.request.Request(
                c.base + f"{API_ROOT}/markets?limit=200&status=open",
                headers={"User-Agent": "verify/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                mkts = json.loads(r.read()).get("markets", [])
            # pick one with a real two-sided book and mid away from extremes
            for m in mkts:
                yb, ya = m.get("yes_bid"), m.get("yes_ask")
                if yb and ya and 10 <= yb <= 85 and ya > yb:
                    market = m
                    break
            if not market:
                line(2, "FAIL", "no liquid two-sided demo market found in first 200")
                return 1
            line(2, "PASS", f"market {market['ticker']} yes_bid={market['yes_bid']} "
                            f"yes_ask={market['yes_ask']}")
        except Exception as e:
            line(2, "FAIL", f"market discovery failed: {e}")
            return 1

        tkr = market["ticker"]
        # a deep, non-marketable YES bid: well below best bid, post-only -> rests, no fill
        deep_price = max(0.01, round((market["yes_bid"] - 20) / 100.0, 2))

        # --- stage 3: order lifecycle ---
        oid = None
        # 3a legacy shape
        try:
            t0 = time.time()
            r = c.create_order(tkr, "yes", "buy", TINY, deep_price, post_only=True,
                               client_order_id=f"verify-legacy-{int(time.time())}")
            oid = (r.get("order") or {}).get("order_id") or r.get("order_id")
            shape_used = "legacy"
            line(3, "PASS", f"LEGACY create accepted ({(time.time()-t0)*1000:.0f}ms) "
                            f"order_id={oid} resp={json.dumps(r)[:160]}")
        except urllib.error.HTTPError as e:
            line(3, "warn", f"legacy create rejected {e.code}: {err_body(e)} — trying V2")
            # 3b V2 shape (bid == yes)
            try:
                t0 = time.time()
                r = c.create_order_v2(tkr, "bid", TINY, deep_price,
                                      client_order_id=f"verify-v2-{int(time.time())}")
                oid = (r.get("order") or {}).get("order_id") or r.get("order_id")
                shape_used = "v2"
                line(3, "PASS", f"V2 create accepted ({(time.time()-t0)*1000:.0f}ms) "
                                f"order_id={oid} resp={json.dumps(r)[:160]}")
            except urllib.error.HTTPError as e2:
                line(3, "FAIL", f"V2 create ALSO rejected {e2.code}: {err_body(e2)} "
                                f"— neither shape works; inspect field formatting")
                return 1
        if oid:
            created_order_ids.append(oid)

        # read it back
        try:
            orders = c.get_orders("resting").get("orders", [])
            mine = [o for o in orders if o.get("order_id") in created_order_ids]
            line(3, "PASS" if mine else "warn",
                 f"read-back: {len(mine)} of our order(s) resting; "
                 f"sample={json.dumps(mine[0])[:200] if mine else '—'}")
        except urllib.error.HTTPError as e:
            line(3, "warn", f"resting-orders read {e.code}: {err_body(e)}")

        # --- stage 4: STP probe (only if v2 shape + lifecycle worked) ---
        if shape_used == "v2":
            line(4, "SKIP", "STP probe deferred — run only after single-order lifecycle "
                            "confirmed; native self_trade_prevention_type already in V2 body")
        else:
            line(4, "SKIP", "legacy shape has no native STP field — must add on live path")

    finally:
        # --- cleanup: cancel everything we created ---
        for oid in created_order_ids:
            try:
                c.cancel_order(oid)
                print(f"       cleanup: cancelled {oid}")
            except Exception as e:
                print(f"       cleanup WARN: could not cancel {oid}: {e} "
                      f"(cancel manually in the demo UI)")

    # --- summary ---
    print("\n=== SUMMARY ===")
    passes = sum(1 for _, s, _ in RESULTS if s == "PASS")
    fails = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print(f"{passes} PASS / {fails} FAIL")
    if shape_used:
        print(f"ORDER SHAPE THAT WORKS ON DEMO: {shape_used.upper()} "
              f"-> pin maker_kalshi_client.py to this; update the quoter's create path.")
    print("Next: if shape confirmed, add STP + fills-WebSocket check, then calibrate "
          "the quoter with the readout and propose the pilot.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
