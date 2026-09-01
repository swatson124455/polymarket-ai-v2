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
            it -> is_order_scoring says True -> cancel -> verify gone ->
            raw cancel response run through the ENGINE'S _cancel_shortfall
            (proves the live kill parser accepts the real venue shape).
            (Order rests at a NEVER-CROSS price: fill risk ~0.)
  receipts  (run the NEXT day, after 00:00Z) get_earnings_for_user_for_day
            for yesterday vs the engine's model accrual (paper-twin alarm).

NO TAKER STAGE. A maker never crosses the spread. Settlement is confirmed by
the FIRST REAL MAKER FILL in the tiny-footprint live step (same on-chain
settlement path), watched closely — not by a marketable order that pays the
spread to force a fill. The removed 'fill' stage was the last taker code in
the live path (cut 2026-07-20, operator directive: a maker never takes).

RUN FROM THE DEPLOYED RELEASE DIRECTORY (/opt/pa2-maker-live): the scoring
stage's cancel-shape probe loads _cancel_shortfall from the SIBLING
maker_live_engine.py — run from a repo checkout it would validate whatever
copy sits there, not the engine that actually performs the live kill.

Usage: MAKER_PK=... venv/bin/python maker_preflight.py --stage sanity
       ... --stage scoring [--token <token_id>]
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


def engine_cancel_shortfall():
    """Load the ENGINE'S OWN cancel-response parser from the sibling file —
    never a copy (a hand copy could drift from what the live kill actually
    runs). The scoring stage feeds it the real venue cancel response, so an
    unanticipated-but-benign shape surfaces HERE, not at the first live kill
    (the exact promise made in _cancel_shortfall's docstring)."""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "maker_live_engine.py")
    spec = importlib.util.spec_from_file_location("maker_live_engine_pf", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._cancel_shortfall


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
    # the venue now REQUIRES an explicit asset_type (empty params -> 400
    # "Invalid asset type"; caught live at first sanity 2026-09-01 on the
    # fresh wallet — the exact shape drift this stage exists to surface)
    from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
    bal = c.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    say("balance", bal is not None, str(bal)[:160])
    print("sanity complete — operator: verify the balance above matches the "
          "provisioned collateral before continuing")


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
    cresp = c.cancel_orders([oid])
    time.sleep(2)
    open2 = c.get_open_orders()
    gone = not any((o.get("id") or o.get("orderID")) == oid
                   for o in open2 if isinstance(o, dict))
    say("cancel", gone, "order gone after cancel_orders")
    # cancel-SHAPE probe: run the real venue response through the engine's
    # own kill parser. Checked AFTER the gone-verify so a shape FAIL exits
    # with the order already confirmed off the book. A FAIL here means the
    # engine's first live kill would fail loud on this same response —
    # extend _cancel_shortfall (with this captured shape as the evidence)
    # before any live footprint.
    print("  cancel raw response: %s" % str(cresp)[:300])
    shortfall = engine_cancel_shortfall()(cresp, [oid])
    say("cxshape", not shortfall,
        "engine _cancel_shortfall proves this cancel response"
        if not shortfall else
        "engine kill parser would NOT prove this cancel: %s"
        % "; ".join(shortfall))
    print("scoring stage complete — reward eligibility CONFIRMED for this account")


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
                    choices=["sanity", "scoring", "receipts"])
    ap.add_argument("--token", default=None)
    ap.add_argument("--model-accrual", type=float, default=None)
    args = ap.parse_args()
    {"sanity": stage_sanity, "scoring": stage_scoring,
     "receipts": stage_receipts}[args.stage](args)


if __name__ == "__main__":
    main()
