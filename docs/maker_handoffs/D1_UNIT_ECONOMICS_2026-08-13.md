# D1 — FULL-HISTORY UNIT ECONOMICS (Kalshi maker lane)

Run: 2026-08-13. READ-ONLY study (GET API + file reads only; nothing placed, cancelled, restarted, or modified on the box).
Frozen table: `D1_UNIT_ECONOMICS_2026-08-13.json` — **md5 `db64af18bbe177ad57c70c4cd4c912fb`**.

**Read timestamps (all UTC):** fills 2026-08-13T01:01:12Z→01:01:19Z (1,630 fills, full tape via `get_paginated`); settlements 01:01:21Z (203 rows); credit_history 01:01:21Z (62 rows); estimates tape + program map re-read 2026-08-13 ~01:05Z (latest snapshot per program, newest 2026-08-13T01:00:36Z).

**Method (canon-compliant):** position-aware cash via `kalshi_attribution_ledger.replay_fills` over the FULL tape (never sign-flip); `fee_cost` in dollars; settlement `revenue`/100; credits EVENT-level, never divided to tickers; ticker→event by `rsplit('-',1)[0]`; the one credit row without "for event" is the $15.00 REFERRAL, bucketed separately.

## Sanity anchors — BOTH RECONCILE (ESTABLISHED)

| Anchor | Canon | This run |
|---|---|---|
| Lifetime credits | $204.06 incl $15.00 referral → $189.06 reward | $204.06 total; referral $15.00; event credits $189.06 across 62 rows / 43 paid events (read 01:01:21Z) ✓ |
| Window drag [08-12T01:40:43Z→now], position-aware | ≈ −$21.94 | −$21.9397 (146 fills, $0 settlements) ✓ exact |

## Overall (ESTABLISHED, full account history 2026-07-15→2026-08-13T01:01Z)

| Quantity | $ |
|---|---|
| Fill cash (position-aware, incl fees) | −661.59 |
| of which taker fees | −64.98 |
| Settlement revenue | +80.86 |
| **Realized (fills + settlements)** | **−580.73** |
| Event credits (paid) | +189.06 |
| **Lifetime net (paid + realized)** | **−391.67** |
| Referral (separate bucket, never counted as reward) | +15.00 |
| Open-inventory cost basis inside "realized" (8 unsettled tickers, live 08-12 inventory) | −16.85 |
| Accrued-but-unpaid on credit-less events (INFERRED from estimates tape, NOT added to paid) | +9.34 |

Coverage: 227 tickers, 115 events, 48 series. 28 events net-positive, 87 net-negative. Top-10 losers = 49% of total negative net (−$230.09 of −$470.73).

## Net by era (by event's first fill; ESTABLISHED)

| Era | Events | Paid | Realized | Net |
|---|---|---|---|---|
| pre-07-25 (launch/defect era) | 31 | +94.15 | −213.83 | **−119.68** |
| 07-25→07-31 (scaling) | 25 | +77.60 | −220.16 | **−142.56** |
| 08-01→08-09 (governors era) | 45 | +9.80 | −133.02 | **−123.22** |
| 08-10+ (post-incident fixed build) | 14 | +7.51 | −13.73 | **−6.22** (mostly open-inventory basis; window still running, credits observed to 08-21) |

Caution: era-3/4 credits lag by design (payout at program conclusion) — era boundaries by first fill understate late-era paid.

## Per-series totals (all 48 in JSON; every series ≥|$0.25| shown)

| Series | Net | Paid | Realized | Fees | Events (net-pos) | Fills |
|---|---|---|---|---|---|---|
| KXAAAGASD | −66.21 | 33.04 | −99.25 | 11.89 | 13 (3) | 593 |
| KXAAAGASW | −40.33 | 0.00 | −40.33 | 1.82 | 3 (0) | 91 |
| KXTRUMPTIME | −39.91 | 7.90 | −47.81 | 4.36 | 2 (0) | 78 |
| KXMUSKNW | −33.92 | 0.00 | −33.92 | 8.20 | 1 (0) | 49 |
| KXTRUMPENDORSEMENTS | −27.98 | 9.68 | −37.66 | 4.25 | 3 (0) | 60 |
| KXRAIN | −25.95 | 0.00 | −25.95 | 1.78 | 7 (0) | 76 |
| KXTOPMODEL | −24.25 | 4.61 | −28.86 | 6.55 | 4 (1) | 83 |
| KXTEMPCHIH | −23.26 | 8.21 | −31.47 | 0.12 | 8 (1) | 37 |
| KXDXYDUD | −19.38 | 1.89 | −21.27 | 2.71 | 4 (0) | 70 |
| KXMLABELSHARE | −17.10 | 16.15 | −33.25 | 1.89 | 1 (0) | 30 |
| KXEURUSDAW | −16.26 | 0.00 | −16.26 | 2.64 | 3 (0) | 53 |
| KXCLAYTONDNI | −14.83 | 2.64 | −17.47 | 1.36 | 1 (0) | 15 |
| KXTEMPAUSH | −10.84 | 24.80 | −35.64 | 2.46 | 12 (4) | 86 |
| KXAMSAVO | −9.26 | 0.00 | −9.26 | 0.00 | 1 (0) | 8 |
| KXTEMPLAXH | −9.18 | 1.85 | −11.03 | 0.33 | 4 (1) | 20 |
| KXGENERICBALLOTVOTEHUB | −8.38 | 2.33 | −10.71 | 2.71 | 2 (0) | 39 |
| KXAPRPOTUS | −6.61 | 1.63 | −8.24 | 2.54 | 3 (0) | 40 |
| KXNETFLIXTOPVIEWSTV | −6.29 | 0.00 | −6.29 | 0.52 | 1 (0) | 3 |
| KXYTVIEWSW | −6.19 | 0.00 | −6.19 | 0.44 | 4 (1) | 16 |
| KXCHINAAI | −6.11 | 0.00 | −6.11 | 0.62 | 1 (0) | 9 |
| KXACTBLUETOP | −5.13 | 0.00 | −5.13 | 1.39 | 1 (0) | 9 |
| KXMCMORROWENDORSE | −4.38 | 0.00 | −4.38 | 0.42 | 1 (0) | 10 |
| KXTEMPDCH | −3.44 | 5.86 | −9.30 | 0.60 | 4 (2) | 28 |
| KXMAMDANIEO | −3.01 | 0.00 | −3.01 | 0.71 | 2 (0) | 13 |
| KXNHPRIMARY28 | −1.71 | 0.00 | −1.71 | 0.31 | 1 (0) | 3 |
| KXMLBTRADE | −1.54 | 0.00 | −1.54 | 0.30 | 1 (0) | 8 |
| KXDIESELW | −1.01 | 0.00 | −1.01 | 0.06 | 1 (0) | 7 |
| KXADJOURNRECESS | −0.92 | 1.02 | −1.94 | 0.80 | 1 (0) | 9 |
| KXLIUKCOUPLE | −0.77 | 0.00 | −0.77 | 0.00 | 1 (0) | 6 |
| KXBLANCHEWITHDRAW | −0.71 | 0.00 | −0.71 | 0.41 | 1 (0) | 8 |
| KXA100WS | −0.36 | 0.00 | −0.36 | 0.00 | 1 (0) | 2 |
| KXCLARITYVOTE | −0.30 | 0.00 | −0.30 | 0.00 | 1 (0) | 2 |
| KXGOOGSHARE / KXTRUMPUAP | −0.27 each | 0.00 | −0.27 | ~0.1 | 1 (0) | 2–4 |
| KXNDQHUD | +0.70 | 9.33 | −8.63 | 0.82 | 1 (1) | 15 |
| KXCHIPBURRITO | +1.14 | 1.14 | 0.00 | 0.00 | 1 (1) | 0 |
| KXCLUBFTOTAL | +1.18 | 1.18 | 0.00 | 0.00 | 1 (1) | 0 |
| KXBABELMANDEBWEEKLY | +1.27 | 1.27 | 0.00 | 0.00 | 1 (1) | 0 |
| KXSUEZWEEKLY | +1.51 | 1.51 | 0.00 | 0.00 | 1 (1) | 0 |
| KXPANAMAWEEKLY | +2.41 | 2.41 | 0.00 | 0.00 | 1 (1) | 0 |
| KXCLUBFSPREAD | +3.04 | 4.70 | −1.66 | 0.06 | 1 (1) | 2 |
| KXSENATEADJOURN | +5.05 | 6.44 | −1.39 | 1.09 | 1 (1) | 9 |
| KXCLUBFBTTS | +7.14 | 8.36 | −1.22 | 0.00 | 1 (1) | 4 |
| KXINXHUD | +7.56 | 13.12 | −5.56 | 0.39 | 1 (1) | 4 |
| **KXTEMPNYCH** | **+13.84** | 17.99 | −4.15 | 0.08 | 5 (4) | 11 |

Note: 5 series (+$7.51 total: SUEZ/BABELMANDEB/PANAMA/CHIPBURRITO/CLUBFTOTAL) were paid credits with ZERO fills on our tape — credited events whose fills predate… no: they have no fill rows at all; credits arrived for events where our resting orders scored without a recorded fill. Realized cost $0, pure positive. (`per_event_paid_unmatched` in JSON.)

## Top-10 net-POSITIVE events (ESTABLISHED)

| Event | Net | Paid | Realized | Fills | Taker contracts / total |
|---|---|---|---|---|---|
| KXTEMPDCH-26JUL2123 | +13.84 | 4.35 | +9.49 | 5 | 0/64 |
| KXTEMPNYCH-26JUL2206 | +10.94 | 12.94 | −2.00 | 1 | 0/20 |
| KXINXHUD-26JUL271600 | +7.56 | 13.12 | −5.56 | 4 | 29/58 |
| KXCLUBFBTTS-26JUL26ERKHIL | +7.14 | 8.36 | −1.22 | 4 | 0/38 |
| KXAAAGASD-26JUL25 | +5.24 | 11.99 | −6.75 | 44 | 0/399 |
| KXSENATEADJOURN-27 | +5.05 | 6.44 | −1.39 | 9 | 80/160 |
| KXCLUBFSPREAD-26JUL26ERKHIL | +3.04 | 4.70 | −1.66 | 2 | 8/16 |
| KXAAAGASD-26JUL23 | +2.62 | 10.09 | −7.47 | 96 | 0/920 |
| KXTEMPAUSH-26JUL2208 | +2.42 | 2.69 | −0.27 | 4 | 0/36 |
| KXPANAMAWEEKLY-26AUG02 | +2.41 | 2.41 | 0.00 | 0 | 0/0 |

## Top-10 net-NEGATIVE events (ESTABLISHED)

| Event | Net | Paid | Realized | Fees | Fills | Taker contracts / total |
|---|---|---|---|---|---|---|
| KXMUSKNW-26JUL31 | −33.92 | 0.00 | −33.92 | 8.20 | 49 | 488/1053 |
| KXAAAGASW-26JUL27 | −32.66 | 0.00 | −32.66 | 0.07 | 51 | 20/449 |
| KXTRUMPTIME-26AUG01 | −28.36 | 7.90 | −36.26 | 3.59 | 57 | 437/902 |
| KXAAAGASD-26JUL24 | −26.17 | 8.81 | −34.98 | 0.00 | 146 | 0/1404 |
| KXTRUMPENDORSEMENTS-26AUG01 | −25.18 | 3.60 | −28.78 | 4.21 | 38 | 274/587 |
| KXTOPMODEL-26AUG03 | −22.87 | 2.15 | −25.02 | 5.62 | 59 | 702/1428 |
| KXMLABELSHARE-W3026JUL30 | −17.10 | 16.15 | −33.25 | 1.89 | 30 | 173/346 |
| KXCLAYTONDNI-27JAN01 | −14.83 | 2.64 | −17.47 | 1.36 | 15 | 150/300 |
| KXTEMPDCH-26JUL2021 | −14.78 | 0.00 | −14.78 | 0.13 | 15 | 60/120 |
| KXEURUSDAW-26JUL31 | −14.23 | 0.00 | −14.23 | 2.25 | 40 | 251/525 |

## Accrued-but-unpaid (INFERRED — estimates tape, latest snapshot per program; NEVER added to paid)

$9.34 total across 25 credit-less events; largest: KXTRUMPTIME-26AUG15 $2.02 (snap 2026-08-13T01:00:36Z), KXACTBLUETOP-26AUG07 $1.24, KXAAAGASD-26AUG07 $1.09, KXAAAGASD-26AUG08 $0.91, KXGENERICBALLOTVOTEHUB-26AUG14 $0.59. Full list in JSON `accrued_by_event`. 12 programs in the estimates tape had no program-map entry (`accrued_meta.n_programs_unmapped`).

## Which characteristics separate net-positive from net-negative? (ESTABLISHED patterns)

1. **Taker contamination is the strongest single separator.** Winners: mean taker-contract share 11.6%, mean fees $0.16/event. Losers: 35.4% and $0.69/event. Five of the ten worst events (MUSKNW, TRUMPTIME, TRUMPENDORSEMENTS, TOPMODEL, EURUSDAW) had 40–50% of contracts filled as taker. Pure-maker events dominate the winner list (7/10 with 0 taker contracts).
2. **Whether the credit ever lands.** 24/28 winners were paid a credit; only 14/87 losers were. Credit-less events sum to −$301.81 of the −$391.67 lifetime net vs −$89.86 for paid events. Presence in programs that conclude and pay (and small enough realized drag to be covered) is the business; volume in non-paying / not-yet-paid programs is pure drag (W10 $1-floor zero-payer mechanism).
3. **Small and quiet beats big and busy.** Winners: median 4 fills, mean 99 contracts/event. Losers: median 8 fills, mean 179 contracts/event. The only meaningfully net-positive series is KXTEMPNYCH (+$13.84 on just 11 fills, 0.08 fees); the biggest sink KXAAAGASD (−$66.21) took 593 fills. Even maker-only high-churn ladders lose (KXAAAGASD-26JUL24: 0 taker contracts, 0 fees, still −$26.17 net — adverse selection at settlement, not fees).
4. (Concentration corollary) The loss is head-heavy: top-10 losers = 49% of all negative net; era-4 (post-08-10 fixed build) is −$6.22 with −$16.85 of that being still-open inventory basis inside the live window.

## Caveats

- "Realized" includes −$16.85 open-inventory cost basis on 8 unsettled tickers (live 08-12 quoting inventory, listed in JSON `open_inventory_unsettled`) — day-1 canon drag −$9.9663 was measured FLAT; this table is as-of-now, not flat.
- Credits pay at program conclusion: recent eras' paid column will grow (window credits observed to 08-21). Accrued (INFERRED) is reported separately and never summed into paid.
- Event = ticker prefix by `rsplit('-',1)[0]`; matches the credit `reason` event tickers on 43/43 paid events with fills (5 paid events had zero fills, listed above).
