# KALSHI MONEY PLAN — ONE-PAGE DEPLOYMENT SHEET (2026-09-01, session reads 12:56–13:18Z)

**Nothing here is executed. Every line is an operator approval item. Bot is OFF.**

## 0. State verified by this session (own reads)
- Balance **$314.5736**, 0 positions, 0 resting orders (API 12:57:20Z). Service inactive+disabled (12:56:45Z).
- **Credited lifetime $205.06 (63 rows); newest credit 2026-08-16T06:55:28Z; $0.00 credited in the 16 days since** (credit_history read 13:18:13Z).
- Live pending accruals (est-feed 13:11Z; pay at period end ONLY the markets that cross $1.00):
  DIESELW-26SEP07 T5.60 **$0.1760** / T5.58 **$0.1134** / T5.62 **$0.0679**; AAAGASW-26SEP07 4.120 $0.0184 / 4.100 $0.0006 / 4.080 $0.0002; TOPMODEL CLAU5 $0.0059. Periods end 09-06T04:00Z (diesel/topmodel) / 09-07T04:00Z (gas wkly).

## 1. Measured anchors (ESTABLISHED — est-feed timeline + quotes-tape presence, window 08-31T13:20→09-01T13:11Z)
| market | pool | accrued→$/day | our size | presence (frac of cycles resting) | book 13:02–13:10Z (qualifies both sides ≥1000ct walk) |
|---|---|---|---|---|---|
| DIESELW T5.60 | $120/d | $0.176 → **$0.178/d** | ≤40ct (D3 ramp) | **0.397** | 0.89/0.91, 2c spr, cums 2,735/2,151 ✓ |
| DIESELW T5.58 | $120/d | $0.113 → $0.114/d | ≤40ct | 0.403 | 0.90/0.96, 6c, 2,735/2,533 ✓ |
| DIESELW T5.62 | $120/d | $0.068 → $0.068/d | ≤40ct | 0.164 | 0.53/0.68, 15c, 2,781/2,031 ✓ |
| TOPMODEL CLAU5 | $200/d | $0.006 → $0.008/d | ≤40ct | 0.031 | 0.28/0.31, 3c, 6,907/24,877 ✓ |
| AAAGASW 4.120 | $100/d | $0.018 → $0.020/d | ≤40ct | 0.039 | 0.18/0.22, 4c, 2,207/29,402 ✓ |

Why presence was broken (measured mechanics): T5.60 mid sits exactly on the MID_BAND edge (0.90) and breathes across it; T5.62/CLAU5/gas-wkly are in-band and widebook needs spread ≥20 ticks while these books run 2–15c. Dailies presence 0% (MIN_RUNWAY_H=49 > their 16h life).
**Uptime caveat (RULE SIX):** the census records depth only within 3 ticks of touch → it read 0.0% for T5.58/60/62 while the venue was paying them. Census unusable for this class; instantaneous qualification verified by full-book walk at 13:02–13:10Z. True qualifying-uptime for these books: UNKNOWN (high enough to accrue daily).
**Model honesty:** the instant-book share model predicts $18.30/d for T5.60 at 40ct park2 vs $0.178/d measured (≈43x over). It is a CEILING, not an expectation. All projections below are measured-anchored, linear-in-size (the one linearity the filing guarantees), labeled INFERRED.

## 2. OPTION 1 — deploy current cash ($314.57). The receipt machine.
**Footprint (default 1a, pool-ranked):** TOPMODEL CLAU5 + DIESELW T5.60 + T5.58, 100ct/side ≈ **$285 committed** (93+98+94 at 13:02Z prices). **Variant 1b (pin to measured accruers):** SERIES_ALLOW=KXDIESELW only → T5.60+T5.58+T5.62 ≈ **$273**.
**Settings needing YES (exact values; live.env already holds the first row — it was never numbers-approved):**
- KEEP: MAX_TOTAL_CAPITAL=290 · MAX_MARKET_CAPITAL=100 · JOIN_SIZE=100 · WIDEBOOK_MAX_CT=100 · INV_SOFT/HARD=30/100 · D3_RUNGS=5,10,25,40,100 · DAILY_LOSS_HALT_USD=10 · REENTRY_COOLDOWN_S=3600 · MIN_RUNWAY_H=49
- CHANGE 1 (required): **KALSHI_UPTIME_RANK=0** — the blind census scores the proven accruers 0.0; leaving =1 ranks them LAST.
- CHANGE 2 (presence fix, pick one): **(A)** WIDEBOOK_MIN_SPREAD_TICKS 20→**5** (admits T5.62/CLAU5/gas-wkly parked 2-ticks-inside) · **(B)** MID_BAND_OUT 0.10,0.90→**0.30,0.70** (T5.60/T5.58 stop flapping; gas-wkly 4.100/4.120 become at-touch eligible) · **(C)=A+B (recommended)** · **(D)** none — run as-measured. A and B modify 08-19-ratified settings — flagged per Rule Nine, rules-canon (R3) + D3 fill-class data (gas/diesel/topmodel = survivable, > −3.5c/ct) are the grounds.
- ACTION: restart + enable polymarket-maker-kalshi-ws.
**Expected credited dollars 09-06T04Z (ABSOLUTE, INFERRED):** as-measured presence: **~$3.6** (T5.60 $2.2 + T5.58 $1.4; T5.62 projects $0.85 → misses the $1 floor → $0). With presence fix working: **~$13** (all three clear). Ceiling irrelevant. First credit possible ONLY 09-06 — nothing lands before that by rule.
**Worst case:** correlated tail (all deep sides fill 100ct and settle $0): **−$230**. Single-market bad case −$51..−$90. Realized churn bounded by halt $10/d; settlement of held inventory is NOT halt-bounded. Fill-cost budget F14 stands (−2..−3.5c/ct diesel historical). Overnight empirical: 0 adverse fills at 33–40ct at-touch (balance flat 08-31 19:34→09-01 12:40 except +$0.04 settlement; 1-night sample).

## 3. OPTION 2 — the $3k scale path (money mandate 08-13; fund only on receipts or operator conviction)
Same mechanics, sizes up (share is linear in size; measured share ~0.15%/market at 40ct → ~4.6% at 500ct, far from saturation):
diesels T5.58/60/62 **500ct** (~$1,325) + CLAU5 **500ct** (~$465) + AAAGASW 4.100/4.120 **250ct** (~$240) ≈ **$2,030 committed** + buffer; knobs: TOTAL=2400 · MARKET=500 · JOIN=500 · WIDEBOOK_MAX=500 · INV_HARD=500 · RUNGS=...,500 · halt: keep 10 or raise to 25 (ask).
**Expected gross accrual (INFERRED, presence-fixed, linear): ~$8–17/day** → weekly credits ~$55–120 if pace holds (every market clears its $1 floor at these sizes). Worst-case correlated tail ≈ **−$1,850**.
**Phase-2 breadth well (separate approval, after first receipts):** 71 of 90 pulled allowlist-family daily-gas books QUALIFY right now (national + CA/FL/IL/NJ/NY/TX state dailies, $100/d each, 2–9k ct deep, 1–10c spreads). Needs: MIN_RUNWAY_H exemption for ~16h periods + SERIES_ALLOW additions (state series are exact-match-excluded today) + per-market $1/period floor math at daily cadence. This is the structural room toward $50–100/d — priced at roughly $50–150 committed per market.

## 4. Decision gate (pre-registered, forward data only)
09-06T04–08Z credit_history read: **≥$3 credited** → anchor held, Option 2 sizing is arithmetic → fund/scale. **$1–3** → partial: re-anchor at measured rate, resize sheet. **<$1** → machine still mispointed: stop, post-mortem, no scale. Est-feed deltas read daily until then (07:30Z), report absolute dollars only.

## 5. Flagged, not proposed
USDJPY rungs (census 21–87% by the 3-tick measure): operator-evicted 08-24, books 0.99-touch = pay $0 by max-price rule, closed 14:00Z today. Stays out absent operator word. · AAAGASW-4.000 (census 100%/19h, 0.92/0.93 extreme shell, $100/d) and DIESELW-T5.80 — optional add-on rows at 40–100ct if wanted. · Census full-book fix (recorder change, read-only) — approval item; restores UPTIME_RANK later. · Watch: est-feed should FREEZE now the bot is off — if T5.60 still climbs after ~15:00Z with 0 resting, the feed model is wrong (report only).
