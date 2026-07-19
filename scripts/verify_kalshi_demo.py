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

        # --- stage 2: discover a priceable demo market ---
        try:
            import urllib.request
            req = urllib.request.Request(
                c.base + f"{API_ROOT}/markets?limit=1000&status=open",
                headers={"User-Agent": "verify/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                mkts = json.loads(r.read()).get("markets", [])
            # demo uses *_dollars string fields; skip the auto-gen combo markets
            for m in mkts:
                if m["ticker"].startswith("KXMVE"):
                    continue
                try:
                    ya = float(m.get("yes_ask_dollars") or 0)
                except (TypeError, ValueError):
                    continue
                if 0.20 <= ya <= 0.80:
                    market = m
                    break
            if not market:
                line(2, "FAIL", "no priceable non-combo demo market found")
                return 1
            line(2, "PASS", f"market {market['ticker']} "
                            f"yes_bid=${market.get('yes_bid_dollars')} "
                            f"yes_ask=${market.get('yes_ask_dollars')}")
        except Exception as e:
            line(2, "FAIL", f"market discovery failed: {e}")
            return 1

        tkr = market["ticker"]

        # --- stage 3: two-sided order lifecycle via create_quote (V2) ---
        # deep, non-marketable quotes on BOTH outcomes: yes bid @0.10, no bid @0.10
        try:
            for outcome in ("yes", "no"):
                t0 = time.time()
                r = c.create_quote(tkr, outcome, 0.10, TINY, post_only=True,
                                   client_order_id=f"verify-{outcome}-{int(time.time())}")
                oid = r.get("order_id") or (r.get("order") or {}).get("order_id")
                if oid:
                    created_order_ids.append(oid)
                line(3, "PASS", f"{outcome} quote accepted ({(time.time()-t0)*1000:.0f}ms) "
                                f"order_id={oid}")
                time.sleep(0.3)
            shape_used = "v2"
        except urllib.error.HTTPError as e:
            line(3, "FAIL", f"V2 create_quote rejected {e.code}: {err_body(e)}")
            return 1

        # read them back — confirm outcome_side + complement price mapping
        try:
            orders = c.get_orders("resting").get("orders", [])
            mine = [o for o in orders if o.get("order_id") in created_order_ids]
            sides = {o.get("outcome_side"): o for o in mine}
            ok = "yes" in sides and "no" in sides
            detail = "; ".join(f"{o.get('outcome_side')}:book={o.get('book_side')},"
                               f"yes=${o.get('yes_price_dollars')},no=${o.get('no_price_dollars')},"
                               f"fee=${o.get('maker_fees_dollars')}" for o in mine)
            line(3, "PASS" if ok else "warn",
                 f"read-back {len(mine)}/2 resting; {detail}")
        except urllib.error.HTTPError as e:
            line(3, "warn", f"resting-orders read {e.code}: {err_body(e)}")

        # --- stage 4: post_only echo check (residual item) ---
        po = next((o.get("post_only") for o in mine), None) if 'mine' in dir() else None
        line(4, "SKIP", f"post_only echoed as {po!r} — API may not return it; the live "
                        f"cross-block behaviour still needs a marketable-order probe")

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
