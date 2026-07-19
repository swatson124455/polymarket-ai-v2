#!/usr/bin/env python3
"""KALSHI VENUE CANON — single source for every Kalshi fact the Maker lane uses.

Doctrine (same as maker_canon.py, per MAKER_MASTER_PLAN §0): NEVER quote a
Kalshi number from memory. Run this file or cite an in-session measurement
with method. Facts below carry their source and verification status.
UNVERIFIED items are marked; do not launder them into verified claims.

Run:  python3 kalshi_canon.py       (prints the canon with sources)
"""

CANON = {
    # ---- API surface (VERIFIED live 2026-07-17, this repo's splinter session) ----
    "api_base": {
        "value": "https://api.elections.kalshi.com/trade-api/v2",
        "source": "live probes 2026-07-17; docs.kalshi.com",
        "status": "VERIFIED",
    },
    "public_endpoints_no_auth": {
        "value": ["/markets", "/markets/{t}", "/markets/{t}/orderbook",
                  "/markets/trades", "/incentive_programs", "/exchange/status",
                  "/events", "/series"],
        "source": "live unauthenticated GETs 2026-07-17 (recorder arm runs on these)",
        "status": "VERIFIED",
    },
    "orderbook_shape": {
        "value": "key 'orderbook_fp': {'yes_dollars': [[price_str, size_str]...asc], "
                 "'no_dollars': [...]} — dollars, fractional contracts, BEST level LAST; "
                 "legacy integer-cents 'orderbook' key ABSENT; a yes ask is a no bid",
        "source": "live probe KXBTC15M/KXWCADVANCE 2026-07-17",
        "status": "VERIFIED",
    },
    "period_reward_units": {
        "value": "CENTI-CENTS (1/10,000 dollar). 200000 == $20.00",
        "source": "docs.kalshi.com get-incentives schema ('Total reward for the period "
                  "in centi-cents'); consistency vs $10-$1,000/day filing cap verified",
        "status": "VERIFIED",
        "trap": "first session read it as cents — 100x overstatement. Convert ALWAYS.",
    },
    "incentive_pagination": {
        "value": "cursor field is 'next_cursor' (NOT 'cursor'); limit max 10,000",
        "source": "docs schema + live pull (2,702 rows single page) 2026-07-17",
        "status": "VERIFIED",
        "trap": "reading d['cursor'] silently returns page 1 only.",
    },
    "tick_size": {
        "value": "market['price_ranges'] = [{start,end,step}] in DOLLARS on the YES "
                 "ladder; sub-cent steps exist (BTC15M books at $0.001). No-side tick "
                 "at no-price p maps to yes region 1-p.",
        "source": "live market objects 2026-07-17 (price_level_structure: linear_cent)",
        "status": "VERIFIED",
    },
    "fractional_contracts": {
        "value": "trade counts and sizes are fractional ('count_fp': '253.16')",
        "source": "live trade tape 2026-07-17",
        "status": "VERIFIED",
    },

    # ---- Liquidity Incentive Program (THE subsidy) ----
    "lip_formula": {
        "value": "1 random snapshot/sec; per side: ref = best bid (< $1.00 required); "
                 "qualifying walk best->down adding ALL size per price until cum >= "
                 "Target Size (never reached -> side void); Score(bid) = DF^ticks x "
                 "size, normalized per side; user snapshot score = yes share + no "
                 "share (all users sum to 2.0); snapshot VOID unless BOTH sides have "
                 "Target-Size qualifying liquidity; payout pro-rata of Time Period "
                 "Reward, $1 min, rounded down",
        "source": "CFTC filing 2026-02-11 (rules02112639183.pdf), effective 2026-02-28, "
                  "read verbatim 2026-07-17",
        "status": "VERIFIED (primary regulatory source)",
    },
    "lip_caps": {
        "value": "Time Period <= 31d; Target Size in (100, 20000) contracts; DF <= 1.00; "
                 "reward $10-$1,000 per calendar day encompassed",
        "source": "same CFTC filing, Appendix A",
        "status": "VERIFIED",
    },
    "lip_eligibility": {
        "value": "all members EXCEPT Kalshi affiliates, Market-Maker-Agreement "
                 "signatories, IB/FCM-routed customers; help center adds: "
                 "international users ineligible; SSN needed above IRS thresholds",
        "source": "CFTC filing + help.kalshi.com/13823851",
        "status": "VERIFIED",
        "note": "signing a formal MM Agreement FORFEITS LIP eligibility — stay informal",
    },
    "lip_sunset": {
        "value": "program runs until the EARLIER of Sep 1, 2026 or amendment/termination",
        "source": "CFTC filing Appendix A",
        "status": "VERIFIED (dated, unlike the retired Polymarket 'Sept-1' rumor)",
        "note": "renewed/amended before (Aug 2025 -> Feb 2026); renewal NOT guaranteed",
    },
    "pool_measured_2026_07_17": {
        "value": "2,702 active programs (1/market), $273,535 scheduled, median window "
                 "7.00d => ~$39K/day EST (total/median-window); ~41% (~$113K) World Cup "
                 "promo series ending ~Jul 19 => ex-WC ~$23K/day EST",
        "source": "live /incentive_programs pull 2026-07-17 21:35Z (session scripts); "
                  "VPS census first tick 22:10Z read $274,535 (0.4% churn drift)",
        "status": "MEASURED (point-in-time; recorder census is the living series)",
    },

    # ---- Other incentive lanes ----
    "combo_program": {
        "value": "June 2026 window: $1M all-events (order-imbalance correction) + "
                 "$200K crypto; distributed by maker-volume share; OPT-IN BY EMAIL",
        "source": "help.kalshi.com/15410257 (as of June 6, 2026)",
        "status": "VERIFIED for June; July status UNKNOWN — recheck monthly",
    },
    "volume_program": {
        "value": "volume incentive type exists in API taxonomy; 0 active rows at "
                 "measurement; third parties cite 'up to $0.005/contract'",
        "source": "API type filter 2026-07-17; actionnetwork.com (secondary)",
        "status": "PARTIAL — rate figure is secondary-source only",
    },
    "apy_on_balances": {
        "value": "3.25% (help center) vs 3.75% (defirate 2026-07-16) — CONFLICTING, "
                 "variable rate — on cash AND open positions, balance >= $250, US only",
        "source": "help.kalshi.com/13823847; news.kalshi.com interest post",
        "status": "VERIFIED-EXISTS; exact current rate UNRESOLVED (pull before quoting)",
    },
    "block_trade_rebate": {
        "value": "100K+ contract blocks, non-Sports, full fee rebate — institutional",
        "source": "CFTC filing 2026-05-01 (rules0501262787.pdf), read verbatim",
        "status": "VERIFIED (irrelevant at our scale)",
    },

    # ---- Fees (THE open item) ----
    "taker_fee": {
        "value": "0.07 x P x (1-P) per contract, rounded up per trade (~1.75c max @ 50c)",
        "source": "SECONDARY ONLY (whirligigbear substack, pm.wiki, marketmath.io); "
                  "official kalshi.com/docs/kalshi-fee-schedule.pdf ('7.7.26 Update') "
                  "is bot-blocked (Vercel checkpoint)",
        "status": "UNVERIFIED-PRIMARY — operator browser download pending",
    },
    "maker_fee": {
        "value": "ZERO on 'Most markets' — the kalshi.com/fee-schedule page's Standard "
                 "fees table lists ONLY taker fees ($0.07-$1.75 per 100 contracts, the "
                 "0.07xPx(1-P) curve) for 'Most markets', no maker fee; a footnoted "
                 "minority of markets DOES charge maker fees (~25% of taker per "
                 "secondary sources). Residual unknown: the exact exception list "
                 "(fetch per-series before quoting a market in the pilot; historically "
                 "index-range series).",
        "source": "OPERATOR-RELAYED from the live kalshi.com/fee-schedule page "
                  "2026-07-18 ('Most markets ... $0.07 - $1.75 taker fees', maker "
                  "column empty); consistent w/ whirligigbear 'some markets have a "
                  "maker fee'. Official PDF remains bot-blocked.",
        "status": "RESOLVED-FAVORABLE for the farm (maker-free on most markets); "
                  "exception list still to enumerate at pilot build.",
    },
    "lip_renewal_assumption": {
        "value": "OPERATOR RULING 2026-07-18: assume the Liquidity Incentive Program "
                 "renews past its Sep 1, 2026 end date — Kalshi has repeatedly re-upped "
                 "it (Aug 2025 program -> Feb 2026 amendment -> current). Plan on "
                 "continuity; the hourly census is the tripwire if it actually lapses.",
        "source": "operator directive in-session 2026-07-18; renewal history per CFTC "
                  "filings",
        "status": "PLANNING ASSUMPTION (operator-set), census-verified continuously",
    },

    # ---- Rate limits / demo ----
    "api_environments": {
        "value": "RECOMMENDED hosts: prod REST https://external-api.kalshi.com/trade-api/v2, "
                 "demo REST https://external-api.demo.kalshi.co/trade-api/v2; WS prod "
                 "wss://external-api-ws.kalshi.com/trade-api/ws/v2, demo "
                 "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2. LEGACY (still "
                 "works): prod api.elections.kalshi.com, demo demo-api.kalshi.co. Paths "
                 "identical across hosts. CREDENTIALS NOT SHARED across environments.",
        "source": "docs.kalshi.com/getting_started/api_environments (2026-07-18); both "
                  "recommended hosts probed live (exchange/status 200)",
        "status": "VERIFIED. Order client now targets recommended hosts; recorder still "
                  "reads legacy api.elections (working, not churned).",
    },
    "rate_limits": {
        "value": "7 tiers (per-second token budgets, read/write): Basic 200/100, "
                 "Advanced 300/300, Expert 600/600, Premier 1000/1000, Paragon "
                 "2000/2000, Prime 4000/4000, Prestige 6000/8000. TOKEN COSTS: most "
                 "requests 10 tok; **order CANCEL = 2 tok**; batch billed PER ITEM "
                 "(25 creates=250, 25 cancels=50). Burst: Advanced+ read / Premier+ "
                 "write hold 2s of budget; Basic/Advanced WRITE hold only 1s (no burst). "
                 "Auto tier progression by 30d volume share (Expert earn 0.075%/keep "
                 "0.05% ... Prestige 1.00%/0.80%). Advanced self-serve needs >=1 of last "
                 "100 orders via API.",
        "source": "docs.kalshi.com/getting_started/rate_limits (2026-07-18)",
        "status": "VERIFIED (docs)",
        "note": "Basic is AMPLE for the pilot: measured quoter churn ~22 creates + "
                "~24 cancels/cycle = ~268 write tokens/cycle (10-min cadence); the "
                "constraint is the PER-SECOND burst, not daily — Basic write = 100 "
                "tok/s with no burst credit, so our 0.16s request spacing (~63 tok/s "
                "peak on all-creates, ~37% margin) is the real guard. Cold-start 110 "
                "creates paces over ~18s. Demo session verifies real 429 behavior.",
    },
    "auth_scheme": {
        "value": "RSA-PSS/SHA-256 over message '{ts_ms}{METHOD}{path}' where path "
                 "EXCLUDES query params; headers KALSHI-ACCESS-KEY (=Key ID), "
                 "KALSHI-ACCESS-TIMESTAMP (ms), KALSHI-ACCESS-SIGNATURE (b64). SAME "
                 "for demo and prod. Keys made at kalshi.com/account/profile -> API "
                 "Keys; private key shown ONCE, never retrievable again.",
        "source": "docs.kalshi.com/getting_started/api_keys (2026-07-18)",
        "status": "VERIFIED — our maker_kalshi_client.py matches exactly (incl. "
                  "query-param stripping at :113)",
    },
    "order_surface_v2": {
        "value": "VERIFIED on demo 2026-07-19 (key cc7845…). CREATE: POST "
                 "/portfolio/events/orders, body {ticker, side:'bid'|'ask', "
                 "count:STR, price:STR, time_in_force, self_trade_prevention_type, "
                 "post_only, client_order_id}. `price` is ALWAYS the YES-scale price: "
                 "an ask@yes-0.90 rests as a NO order @0.10 (yes-ask == no-bid). "
                 "So YES bid@p -> side='bid',price=p ; NO bid@p -> side='ask',"
                 "price=(1-p). CANCEL: DELETE /portfolio/events/orders/{id}. "
                 "READS unchanged (GET /portfolio/orders returns outcome_side + "
                 "{yes,no}_price_dollars). **LEGACY /portfolio/orders write path is "
                 "DEAD — 410 deprecated_v1_order_endpoint** (the SB lane's client + "
                 "our first draft used it; would have failed live).",
        "source": "live demo order lifecycle 2026-07-19 (verify_kalshi_demo.py, "
                  "6 PASS/0 FAIL): auth read $100, two-sided create_quote, read-back "
                  "with correct mapping, cancel, 0 left resting",
        "status": "VERIFIED & PINNED. client.create_quote(outcome,price) is the "
                  "maker entry point. RESIDUAL: post_only not echoed in read-back "
                  "(None) — confirm it actually blocks a crossing order via a "
                  "marketable-order probe before live; STP field accepted. Batch V2 "
                  "path (/portfolio/events/orders/batched) set but NOT yet demo-tested.",
    },
    "maker_fee_observed": {
        "value": "maker_fees_dollars = 0.000000 on resting orders in KXTEMP* and "
                 "KXWNBA* demo markets — direct confirmation of the maker-free "
                 "reading for the temp/weather farm (see maker_fee entry).",
        "source": "demo order read-back 2026-07-19",
        "status": "VERIFIED on sampled markets (not exhaustive — still fetch "
                  "per-series fee flag before quoting an unfamiliar market)",
    },
    "dev_agreement": {
        "value": "API use requires accepting Kalshi's Developer Agreement (click-through "
                 "before first API call). OPERATOR ACTION — accepting terms is not a "
                 "session action.",
        "source": "docs.kalshi.com/welcome (2026-07-18)",
        "status": "OPERATOR TODO (part of account setup)",
    },
    "tier_upgrade_gotcha": {
        "value": "Self-serve Advanced tier (300/300 tokens/s) requires >=1 of the "
                 "user's last 100 Predictions orders was created VIA API — i.e. you "
                 "can't pre-upgrade, you earn Advanced after trading a little. Basic "
                 "(200 read/100 write tok/s) is ample for the pilot (measured churn "
                 "~460 tokens/cycle) so this is not a blocker.",
        "source": "docs.kalshi.com api-reference/account/upgrade-account-api-usage-level",
        "status": "VERIFIED — non-blocking, informational",
    },
    "order_groups": {
        "value": "Native risk tool: Order Groups auto-cancel resting orders when a "
                 "rolling contract limit is hit — a candidate hard backstop for the "
                 "pilot kill-criteria (exchange-side, independent of our code).",
        "source": "docs.kalshi.com/getting_started/order_groups",
        "status": "NICE-TO-HAVE for pilot; evaluate in demo session",
    },
    "demo_env": {
        "value": "external-api.demo.kalshi.co (REST) + external-api-ws.demo.kalshi.co "
                 "(WS); separate credentials; docs verbatim: 'The price and behavior "
                 "of markets in the demo environment may not be reflective of those "
                 "in real markets'",
        "source": "docs.kalshi.com/getting_started/demo_env",
        "status": "VERIFIED",
        "note": "demo = order-plumbing tests ONLY; measurement runs on prod public data",
    },

    # ---- Competition ----
    "designated_mm": {
        "value": "Susquehanna (SIG) = first dedicated institutional MM since Apr 2024; "
                 "thick books show it (WC majors 1c spread, 0.1-1.9M contracts at touch; "
                 "BTC15M sub-cent). Farm tail (tennis/minor-soccer/esports/mentions/"
                 "weather) 2-11c spreads, thin touch.",
        "source": "businesswire 2024-04-03 + kalshi blog; book sample 2026-07-17",
        "status": "VERIFIED",
    },

    # ---- Recorder arm (our measurement instrument) ----
    "recorder_arm": {
        "value": "polymarket-maker-kalshi.timer, /opt/pa2-maker-kalshi, 5-min oneshot, "
                 "120-market footprint, JOIN(100ct)/ACTIVATE policies + hourly census. "
                 "DATA ERAS: launch 2026-07-17T22:10Z; rw-era (rows carry rw+pend) "
                 "2026-07-17T23:52Z. usd_day in samples is a RATE — use rw for "
                 "realizable $. Readout: scripts/maker_kalshi_readout.py.",
        "source": "this branch (claude/maker-kalshi-recorder), deploy logs",
        "status": "VERIFIED (live)",
    },
}


def main():
    for key, f in CANON.items():
        print(f"== {key} [{f['status']}]")
        v = f["value"]
        print(f"   {v if isinstance(v, str) else ', '.join(map(str, v))}")
        print(f"   src: {f['source']}")
        for extra in ("trap", "note"):
            if extra in f:
                print(f"   {extra.upper()}: {f[extra]}")
        print()


if __name__ == "__main__":
    main()
