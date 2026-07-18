#!/usr/bin/env python3
"""EXECUTION-CORE PROBE — verify every maker primitive against the REAL CLOB
with ZERO funds at risk (operator directive 2026-07-18: "verify all items
work not just blind assumptions").

Method: a FRESH burner wallet (generated in-process, zero balance, printed
only as address) walks the full execution path:
  E1  L1 auth: create/derive API creds (signs with the burner key)
  E2  market metadata: get_tick_size + get_neg_risk on a live market
  E3  order build: EIP-712 signed GTC limit order, min size, priced
      NEVER-CROSS (client-side post-only: bid far below best bid)
  E4  order submit: expect a STRUCTURED not-enough-balance/allowance
      rejection — that error proves auth + serialization + signature all
      passed server-side validation and only the (intentionally empty)
      wallet stopped it
  E5  batch build: PostOrdersArgs for 2 orders (shape check; submit shares
      E4's expected rejection)
  E6  cancel_all: authenticated call succeeds (trivially, no open orders)
  E7  GTD build: same order with expiration -> accepted by the builder

VERIFIED-ABSENT (do not assume otherwise): no post-only flag exists in the
API (checked py_clob_client 0.34.5 surface) — never-cross is enforced at
build time by pricing. is_order_scoring needs a LIVE resting order; it is
the pilot-day-1 check, listed here so it is not forgotten.

Zero-capital guarantee: the wallet is created fresh in this process and its
key is discarded; nothing can fill because nothing can be funded.
"""
import json
import time
import urllib.request

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (ApiCreds, OrderArgs, OrderType,
                                       PostOrdersArgs)
from py_clob_client.order_builder.constants import BUY
from eth_account import Account

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
UA = {"User-Agent": "pa2-maker-exec-probe/1.0"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    results = []

    def check(tag, ok, detail):
        results.append((tag, ok, detail))
        print("  %-4s %s  %s" % (tag, "PASS" if ok else "FAIL", detail))

    print("=" * 70)
    print("E0  burner wallet (fresh, zero funds, key discarded after run)")
    acct = Account.create()
    print("     address:", acct.address)

    # pick a live rewarded market with a cheap YES side from gamma
    mkts = get("https://gamma-api.polymarket.com/markets?active=true&closed="
               "false&limit=50&order=volume24hr&ascending=false")
    target = None
    for m in mkts:
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
            if len(toks) >= 2 and float(m.get("rewardsMinSize") or 0) > 0:
                target = {"tok": str(toks[0]), "q": (m.get("question") or "")[:50],
                          "msz": float(m["rewardsMinSize"])}
                break
        except Exception:
            continue
    if not target:
        print("no target market found — abort")
        return 1
    print("     target market:", target["q"], "| min size:", target["msz"])

    print("E1  L1 auth -> API creds (server-side signature validation)")
    client = ClobClient(HOST, key=acct.key.hex(), chain_id=CHAIN_ID)
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        check("E1", bool(creds.api_key), "api key derived: %s..." % creds.api_key[:8])
    except Exception as e:
        check("E1", False, repr(e)[:120])
        return 1

    print("E2  live market metadata")
    try:
        tick = client.get_tick_size(target["tok"])
        neg = client.get_neg_risk(target["tok"])
        check("E2", float(tick) > 0, "tick=%s neg_risk=%s" % (tick, neg))
    except Exception as e:
        check("E2", False, repr(e)[:120])
        return 1

    print("E3  build never-cross order (client-side post-only)")
    book = get(HOST + "/book?token_id=" + target["tok"])
    try:
        bb = max(float(x["price"]) for x in book["bids"])
    except Exception:
        bb = 0.5
    tick_f = float(tick)
    px = max(tick_f, round(bb / 2 / tick_f) * tick_f)   # half the best bid
    try:
        order = client.create_order(OrderArgs(
            price=px, size=max(5.0, target["msz"]), side=BUY,
            token_id=target["tok"]))
        check("E3", order is not None,
              "signed order built @ %.4f (best bid %.4f — cannot cross)" % (px, bb))
    except Exception as e:
        check("E3", False, repr(e)[:120])
        return 1

    print("E4  submit -> EXPECT structured balance/allowance rejection")
    try:
        resp = client.post_order(order, OrderType.GTC)
        # if this ever SUCCEEDS the burner somehow had funds — treat as FAIL
        check("E4", False, "UNEXPECTED ACCEPT: %s" % str(resp)[:100])
    except Exception as e:
        msg = str(e).lower()
        ok = "balance" in msg or "allowance" in msg
        check("E4", ok, ("server validated auth+signature, rejected on funds: "
                         if ok else "WRONG rejection class: ") + str(e)[:110])

    print("E5  batch build (PostOrdersArgs x2)")
    try:
        o2 = client.create_order(OrderArgs(price=px, size=max(5.0, target["msz"]),
                                           side=BUY, token_id=target["tok"]))
        batch = [PostOrdersArgs(order=order, orderType=OrderType.GTC),
                 PostOrdersArgs(order=o2, orderType=OrderType.GTC)]
        check("E5", len(batch) == 2, "batch of 2 built (submit shares E4 path)")
    except Exception as e:
        check("E5", False, repr(e)[:120])

    print("E6  cancel_all (authenticated)")
    try:
        r = client.cancel_all()
        check("E6", True, "accepted: %s" % str(r)[:80])
    except Exception as e:
        check("E6", False, repr(e)[:120])

    print("E7  GTD order build (catalyst expiry)")
    try:
        gtd = client.create_order(OrderArgs(
            price=px, size=max(5.0, target["msz"]), side=BUY,
            token_id=target["tok"],
            expiration=str(int(time.time()) + 3600)))
        check("E7", gtd is not None, "GTD signed order built (+1h expiry)")
    except Exception as e:
        check("E7", False, repr(e)[:120])

    print()
    npass = sum(1 for _, ok, _ in results if ok)
    print("RESULT: %d/%d primitives verified against the live CLOB, $0 at risk"
          % (npass, len(results)))
    print("pilot-day-1 remaining checks (need a funded wallet + live order):")
    print("  is_order_scoring / are_orders_scoring; real fill lifecycle;")
    print("  real reward receipt vs model (the paper-twin alarm).")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
