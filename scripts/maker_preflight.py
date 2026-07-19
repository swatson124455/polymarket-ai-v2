#!/usr/bin/env python3
"""MAKER FUNDED PREFLIGHT — step 3 of the live-engine build spec
(AGENT_HANDOFF_2026-07-18_MAKER_SESSION3_CLOSE.md §1.3).

Needs the OPERATOR-PROVISIONED deposit-flow wallet (pUSD + POL + V2
approvals) in env — the same vars the engine uses:
  MAKER_PK, optionally MAKER_FUNDER / MAKER_SIG_TYPE.
Runs on the VPS ONLY (residential IPs are geo-403 for order submission).
Everything is tiny and bounded: max spend is a few dollars, every stage
prints PASS/FAIL and stops on the first failure.

Stages (run in order, each idempotent):
  sanity    V2 SDK + auth + balance/allowance + server reachability
  scoring   min-size post-only order far from touch -> get_open_orders sees
            it -> is_order_scoring says True -> cancel -> verify gone.
            (Order rests at a NEVER-CROSS price: fill risk ~0.)
  fill      ONE tiny marketable FAK buy (~$1-2) -> get_trades shows the
            maker/taker fill -> settlement tx hash recorded. This is the
            #338 settlement-revert day-1 monitored metric: a revert here
            is a hard stop.
  receipts  (run the NEXT day, after 00:00Z) get_earnings_for_user_for_day
            for yesterday vs the engine's model accrual (paper-twin alarm).

Usage: MAKER_PK=... venv/bin/python maker_preflight.py --stage sanity
       ... --stage scoring [--token <token_id>]
       ... --stage fill    [--token <token_id>] [--max-usd 2]
       ... --stage receipts [--model-accrual <engine acc_day $>]
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
UA = {"User-Agent": "pa2-maker-preflight/1.0"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def say(tag, ok, detail):
    print("  %-8s %s  %s" % (tag, "PASS" if ok else "FAIL", detail), flush=True)
    if not ok:
        print("PREFLIGHT STOPPED at %s" % tag, flush=True)
        sys.exit(1)


def client_or_die():
    if not os.environ.get("MAKER_PK"):
        print("MAKER_PK not set — the funded preflight needs the operator wallet")
        sys.exit(2)
    # V2 ONLY — py_clob_client (no _v2) is the archived rejected SDK
    import httpx
    import py_clob_client_v2.http_helpers.helpers as _h
    from py_clob_client_v2.client import ClobClient
    _h._http_client = httpx.Client(http2=True, timeout=10.0)
    sig = os.environ.get("MAKER_SIG_TYPE")
    c = ClobClient(CLOB_HOST, CHAIN_ID, key=os.environ["MAKER_PK"],
                   signature_type=int(sig) if sig else None,
                   funder=os.environ.get("MAKER_FUNDER") or None)
    c.set_api_creds(c.create_or_derive_api_key())
    return c


def pick_token(args):
    if args.token:
        return args.token, "(operator-chosen)"
    mkts = get("https://gamma-api.polymarket.com/markets?active=true&closed=false"
               "&limit=50&order=volume24hr&ascending=false")
    for m in mkts:
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
            msz = float(m.get("rewardsMinSize") or 0)
            if len(toks) >= 2 and 0 < msz <= 100 and not m.get("negRisk"):
                return str(toks[0]), (m.get("question") or "")[:60]
        except Exception:
            continue
    print("no suitable market found")
    sys.exit(1)


def stage_sanity(args):
    print("STAGE sanity")
    c = client_or_die()
    say("auth", bool(c.creds and c.creds.api_key),
        "L2 creds derived for %s" % c.get_address())
    ok = c.get_ok()
    say("server", bool(ok), "clob reachable: %s" % str(ok)[:60])
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams
        bal = c.get_balance_allowance(BalanceAllowanceParams())
    except Exception:
        bal = c.get_balance_allowance()
    say("balance", bal is not None, str(bal)[:160])
    print("sanity complete — operator: verify the balance above matches the "
          "provisioned pUSD amount before continuing")


def stage_scoring(args):
    print("STAGE scoring (min order -> open -> is_order_scoring -> cancel)")
    from py_clob_client_v2.clob_types import (OrderArgs, OrderType,
                                              OrderScoringParams,
                                              PartialCreateOrderOptions)
    from py_clob_client_v2.order_builder.constants import BUY
    c = client_or_die()
    tok, qname = pick_token(args)
    print("  market:", qname)
    tick = float(c.get_tick_size(tok))
    neg = bool(c.get_neg_risk(tok))
    book = get(CLOB_HOST + "/book?token_id=" + tok)
    bb = max((float(x["price"]) for x in book.get("bids") or []), default=0.5)
    # rest INSIDE the reward band but far enough to be near-zero fill risk:
    # a few ticks under best bid (never-cross by construction)
    px = max(tick, round((bb - 3 * tick) / tick) * tick)
    mkt_meta = get("https://gamma-api.polymarket.com/markets?clob_token_ids=" + tok)
    msz = 5.0
    try:
        msz = float((mkt_meta or [{}])[0].get("rewardsMinSize") or 5.0)
    except Exception:
        pass
    signed = c.create_order(
        OrderArgs(token_id=tok, price=px, size=msz, side=BUY),
        PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg))
    resp = c.post_order(signed, OrderType.GTC, post_only=True)
    oid = (resp or {}).get("orderID") if isinstance(resp, dict) else None
    say("place", bool(oid), "post-only GTC %s @ %.4f (bb %.4f): %s"
        % (msz, px, bb, str(resp)[:120]))
    time.sleep(2)
    open_ = c.get_open_orders()
    seen = any((o.get("id") or o.get("orderID")) == oid
               for o in open_ if isinstance(o, dict))
    say("open", seen, "order visible in get_open_orders (%d open)" % len(open_))
    sc = c.is_order_scoring(OrderScoringParams(orderId=oid))
    scoring = bool(getattr(sc, "scoring", None) or
                   (isinstance(sc, dict) and sc.get("scoring")))
    say("scoring", scoring, "is_order_scoring: %s" % str(sc)[:80])
    c.cancel_orders([oid])
    time.sleep(2)
    open2 = c.get_open_orders()
    gone = not any((o.get("id") or o.get("orderID")) == oid
                   for o in open2 if isinstance(o, dict))
    say("cancel", gone, "order gone after cancel_orders")
    print("scoring stage complete — reward eligibility CONFIRMED for this account")


def stage_fill(args):
    print("STAGE fill (ONE tiny marketable FAK — the #338 settlement metric)")
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, TradeParams, \
        PartialCreateOrderOptions
    from py_clob_client_v2.order_builder.constants import BUY
    c = client_or_die()
    tok, qname = pick_token(args)
    print("  market:", qname)
    tick = float(c.get_tick_size(tok))
    neg = bool(c.get_neg_risk(tok))
    book = get(CLOB_HOST + "/book?token_id=" + tok)
    ba = min((float(x["price"]) for x in book.get("asks") or []), default=None)
    say("book", ba is not None, "best ask %.4f" % (ba or -1))
    size = max(5.0, round(args.max_usd / ba, 2))
    if size * ba > max(args.max_usd, 3.0) + 1.0:
        say("size", False, "computed spend $%.2f exceeds --max-usd" % (size * ba))
    signed = c.create_order(
        OrderArgs(token_id=tok, price=ba, size=size, side=BUY),
        PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg))
    resp = c.post_order(signed, OrderType.FAK)
    print("  submit resp:", str(resp)[:200])
    time.sleep(5)
    trades = c.get_trades(TradeParams(maker_address=c.get_address())) or []
    mine = [t for t in trades if isinstance(t, dict)
            and str(t.get("asset_id") or "") == tok][:3]
    say("fill", bool(mine), "trades seen: %s" % str(mine)[:200])
    tx = None
    for t in mine:
        tx = t.get("transaction_hash") or t.get("tx_hash") or tx
        status = t.get("status")
        print("  trade status=%s tx=%s" % (status, tx))
    print("fill stage complete — RECORD THE TX HASH above; verify it did not "
          "revert (Polygonscan). A revert = #338 class = hard stop + operator flag.")


def stage_receipts(args):
    print("STAGE receipts (run AFTER the first 00:00Z following quoting)")
    c = client_or_die()
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    rec = c.get_earnings_for_user_for_day(yday)
    total = 0.0
    for r in rec or []:
        try:
            total += float(r.get("earnings") or r.get("amount") or 0)
        except Exception:
            pass
    print("  %s receipts: %d rows, total-parsed $%.4f" % (yday, len(rec or []), total))
    print("  raw:", str(rec)[:400])
    if args.model_accrual is not None:
        m = args.model_accrual
        div = abs(total - m) / m if m else float("inf")
        print("  model $%.4f vs receipt $%.4f — divergence %.0f%%"
              % (m, total, 100 * div))
        print("  paper-twin alarm threshold: 50%% -> %s"
              % ("ALARM (investigate share model before scaling)"
                 if div > 0.5 and abs(total - m) > 5 else "within tolerance"))
    print("receipts stage complete — receipts are the ONLY real income "
          "number; model accrual is an estimate.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["sanity", "scoring", "fill", "receipts"])
    ap.add_argument("--token", default=None)
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--model-accrual", type=float, default=None)
    args = ap.parse_args()
    {"sanity": stage_sanity, "scoring": stage_scoring,
     "fill": stage_fill, "receipts": stage_receipts}[args.stage](args)


if __name__ == "__main__":
    main()
