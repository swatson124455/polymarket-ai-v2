#!/usr/bin/env python3
"""Kalshi DEMO post_only CROSS-BLOCK probe — closes the last pre-live residual.

DEMO ONLY (fake money). Refuses any mode other than demo.

THE QUESTION: `post_only` was accepted by the API but never echoed in read-back,
so we have never PROVEN it actually blocks a marketable order. A maker that
silently crosses pays taker fees and eats adverse selection — exactly what the
farm must never do. This probe answers it from real responses.

EXPERIMENT (control arm is the point — without it a rejection proves nothing,
since a reject could come from a bad price, a closed market, auth, anything):

  ARM A (CONTROL): post_only order placed deeply NON-marketable  -> must REST.
                   Proves the plumbing works on this market right now.
  ARM B (TEST):    post_only order placed deliberately CROSSING  -> must be
                   REJECTED and must NOT fill.

Only "A rests AND B rejected with no fill" is a PASS. If A fails, B's result is
uninterpretable and the run is INCONCLUSIVE (not a pass).

Cross target is EXTERNAL liquidity when the demo book has any (isolates post_only
from self-trade-prevention). If the book has no opposing side, it falls back to
crossing our OWN control order, which tests post_only+STP JOINTLY — reported
honestly as a weaker result, because STP alone could explain a rejection.

Requires (operator-set; never sees raw key material):
    KALSHI_TRADING_MODE=demo
    KALSHI_API_KEY_ID=<demo key id>
    KALSHI_RSA_PRIVATE_KEY_PATH=<path to demo PEM, OUTSIDE the repo>
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient, API_ROOT  # noqa: E402

TINY = 1          # contracts — smallest possible footprint
HTTP_TIMEOUT_S = 15
RESULTS = []


def line(stage, status, msg):
    RESULTS.append((stage, status, msg))
    print(f"[{status:12}] {stage}: {msg}")


def err_body(e):
    try:
        return e.read().decode()[:400]
    except Exception:
        return str(e)


# ---------------- pure helpers (unit-tested offline) ----------------

def best_levels(ob):
    """(best_yes_bid, best_no_bid) in dollars from an `orderbook_fp` payload.

    CANON: levels are ascending and the BEST level is LAST; a yes ask IS a no
    bid, so the best yes ASK price == 1 - best_no_bid. Returns (None, None)
    for an empty/absent side rather than guessing."""
    fp = (ob or {}).get("orderbook_fp") or {}

    def _best(side):
        rows = fp.get(side) or []
        for row in reversed(rows):          # best is LAST; walk back to first sane row
            try:
                p, s = float(row[0]), float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if s > 0 and 0.0 < p < 1.0:
                return p
        return None

    return _best("yes_dollars"), _best("no_dollars")


def crossing_bid_price(best_no_bid):
    """A YES bid price that is guaranteed marketable against the resting yes ask.

    best yes ask == 1 - best_no_bid. Bidding AT the ask crosses. Clamped to
    <=0.99 so we never send a nonsense price."""
    if best_no_bid is None:
        return None
    yes_ask = round(1.0 - best_no_bid, 4)
    if not (0.0 < yes_ask <= 0.99):
        return None
    return yes_ask


def classify(control_rested, test_rejected, test_filled, external):
    """The verdict table. Returns (verdict, message).

    Deliberately conservative: anything that is not an unambiguous block is NOT
    a pass, because the failure mode we are guarding against (silently crossing
    with real money) is expensive and one-directional."""
    if not control_rested:
        return ("INCONCLUSIVE",
                "control (non-marketable) order did NOT rest — the market/plumbing "
                "was not in a testable state, so the crossing arm proves nothing")
    if test_filled:
        return ("FAIL-CRITICAL",
                "crossing post_only order FILLED — post_only does NOT block "
                "marketable orders. DO NOT go live until this is understood")
    if not test_rejected:
        return ("FAIL",
                "crossing post_only order was neither rejected nor filled (it "
                "rested?) — behaviour not understood; treat as unproven")
    scope = ("post_only blocks crossing against EXTERNAL liquidity"
             if external else
             "post_only+STP jointly block a SELF-cross (no external liquidity on "
             "demo — weaker: STP alone could explain this; re-probe at pilot "
             "against a real prod book before scaling)")
    return ("PASS", scope)


# ---------------- probe ----------------

def _fills_count(c):
    try:
        return len((c.get_fills(limit=200) or {}).get("fills", []) or [])
    except Exception:
        return None


def _order_id(resp):
    if not isinstance(resp, dict):
        return None
    return resp.get("order_id") or (resp.get("order") or {}).get("order_id")


def main():
    if os.environ.get("KALSHI_TRADING_MODE") != "demo":
        print("REFUSING: set KALSHI_TRADING_MODE=demo (this probe is demo-only).")
        return 2
    try:
        c = KalshiOrderClient()
    except Exception as e:
        print(f"REFUSING: {e}")
        return 2
    line("env", "PASS", f"mode=demo base={c.base} (creds loaded)")

    created = []
    control_rested = test_rejected = test_filled = False
    external = False
    try:
        fills_before = _fills_count(c)
        line("baseline", "PASS", f"fills before = {fills_before}")

        # --- discover a market WITH an opposing (yes-ask) side to cross ---
        req = urllib.request.Request(
            c.base + f"{API_ROOT}/markets?limit=1000&status=open",
            headers={"User-Agent": "postonly-probe/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            mkts = json.loads(r.read()).get("markets", [])

        target = None
        for m in mkts:
            if m["ticker"].startswith("KXMVE"):      # auto-generated parlay combos
                continue
            try:
                ob_req = urllib.request.Request(
                    c.base + f"{API_ROOT}/markets/{m['ticker']}/orderbook",
                    headers={"User-Agent": "postonly-probe/1.0"})
                with urllib.request.urlopen(ob_req, timeout=HTTP_TIMEOUT_S) as r:
                    ob = json.loads(r.read())
            except Exception:
                continue
            yb, nb = best_levels(ob)
            xp = crossing_bid_price(nb)
            if xp is not None and 0.05 <= xp <= 0.95:
                target, best_yes_bid, cross_px = m, yb, xp
                external = True
                break

        if target is None:
            # fallback: any priceable market, cross our OWN control order
            for m in mkts:
                if m["ticker"].startswith("KXMVE"):
                    continue
                try:
                    ya = float(m.get("yes_ask_dollars") or 0)
                except (TypeError, ValueError):
                    continue
                if 0.20 <= ya <= 0.80:
                    target, best_yes_bid, cross_px = m, None, None
                    break
            if target is None:
                line("discover", "FAIL", "no usable demo market found")
                return 1
            line("discover", "warn",
                 f"{target['ticker']}: NO external opposing liquidity on demo — "
                 f"falling back to a SELF-cross (weaker; tests post_only+STP jointly)")
        else:
            line("discover", "PASS",
                 f"{target['ticker']}: best_yes_bid={best_yes_bid} "
                 f"external yes-ask to cross at ${cross_px:.4f}")

        tkr = target["ticker"]

        # --- ARM A (CONTROL): deeply non-marketable post_only bid MUST rest ---
        ctrl_px = 0.02
        try:
            resp = c.create_order_v2(tkr, "bid", TINY, ctrl_px, post_only=True,
                                     client_order_id=f"po-ctrl-{int(time.time())}")
            oid = _order_id(resp)
            if oid:
                created.append(oid)
            time.sleep(0.4)
            resting = (c.get_orders("resting") or {}).get("orders", []) or []
            control_rested = any(o.get("order_id") == oid for o in resting)
            line("ARM A control", "PASS" if control_rested else "FAIL",
                 f"non-marketable bid @${ctrl_px} -> "
                 f"{'RESTING' if control_rested else 'NOT resting'} (order_id={oid})")
        except (urllib.error.HTTPError, RuntimeError) as e:
            body = err_body(e) if isinstance(e, urllib.error.HTTPError) else str(e)
            line("ARM A control", "FAIL",
                 f"control order did not rest: {body} — plumbing not testable")

        if not external:
            # self-cross target: cross our own resting control bid @ctrl_px
            cross_px = ctrl_px

        # --- ARM B (TEST): deliberately CROSSING post_only order ---
        # An ask at/below the best bid is marketable; a bid at/above the best ask
        # is marketable. External arm crosses the resting yes ASK with a bid;
        # self-cross arm crosses our OWN control BID with an ask.
        if control_rested:
            try:
                if external:
                    resp = c.create_order_v2(tkr, "bid", TINY, cross_px, post_only=True,
                                             client_order_id=f"po-x-{int(time.time())}")
                else:
                    resp = c.create_order_v2(tkr, "ask", TINY, cross_px, post_only=True,
                                             client_order_id=f"po-x-{int(time.time())}")
                oid = _order_id(resp)
                if oid:
                    created.append(oid)
                time.sleep(0.6)
                resting = (c.get_orders("resting") or {}).get("orders", []) or []
                still = any(o.get("order_id") == oid for o in resting)
                fills_after = _fills_count(c)
                test_filled = (fills_before is not None and fills_after is not None
                               and fills_after > fills_before)
                test_rejected = (not still) and (not test_filled)
                line("ARM B test", "PASS" if test_rejected else "FAIL",
                     f"crossing post_only @${cross_px:.4f} -> accepted by API "
                     f"(order_id={oid}); resting={still} fills {fills_before}->{fills_after}")
            except urllib.error.HTTPError as e:
                test_rejected = True
                line("ARM B test", "PASS",
                     f"crossing post_only @${cross_px:.4f} REJECTED at HTTP layer "
                     f"{e.code}: {err_body(e)[:200]}")
            except RuntimeError as e:
                # client raises when a 200 carries status=rejected/canceled
                test_rejected = True
                line("ARM B test", "PASS",
                     f"crossing post_only @${cross_px:.4f} REJECTED by exchange: {e}")
        else:
            line("ARM B test", "SKIP", "control failed — crossing arm not run")

    finally:
        for oid in created:
            try:
                c.cancel_order(oid)
                print(f"       cleanup: cancelled {oid}")
            except Exception as e:
                print(f"       cleanup note: {oid}: {e}")
        try:
            left = [o for o in (c.get_orders("resting") or {}).get("orders", []) or []
                    if o.get("order_id") in created]
            print(f"       cleanup: {len(left)} of ours still resting (want 0)")
        except Exception:
            pass

    verdict, msg = classify(control_rested, test_rejected, test_filled, external)
    print("\n=== VERDICT ===")
    print(f"{verdict}: {msg}")
    if verdict == "PASS":
        print("Residual CLOSED for the demo environment. Runbook Phase 2 item 1 done.")
    else:
        print("Residual STAYS OPEN — do not rely on post_only until resolved.")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
