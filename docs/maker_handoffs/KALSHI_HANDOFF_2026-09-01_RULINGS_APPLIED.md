# KALSHI HANDOFF 2026-09-01 (~13:5xZ) — RULINGS APPLIED TO ENV, BOT STILL OFF

## STATE (all this session's own reads, timestamps inline)
- **Bot OFF**: polymarket-maker-kalshi-ws inactive + disabled (verified 13:45:28Z, after env apply).
- Account: **$314.5736 free, 0 positions, 0 resting** (API 12:57:20Z).
- **Credited: $205.06 lifetime (63 rows), newest 2026-08-16T06:55Z → $0.00 in 16 days** (credit_history 13:18:13Z).
- Pending accruals (est-feed 13:11Z, pay ONLY if ≥$1.00/market by period end): DIESELW-26SEP07 T5.60 $0.1760 / T5.58 $0.1134 / T5.62 $0.0679; smaller gas-wkly/topmodel rows. Diesel+topmodel periods end 09-06T04:00Z; gas wkly 09-07T04:00Z; gas dailies nightly 04:00Z.

## WHAT THIS SESSION DID
1. Built + presented the money plan (`KALSHI_MONEY_PLAN_2026-09-01.md`), operator Q&A'd it down to 5 decisions (`KALSHI_RELIGHT_SHEET_2026-09-01.md`).
2. **Operator rulings (2026-09-01):** (1) turn-on: **"not now"**, then **"apply all changes do not go live"** — done; (2) picker fix bot-wide: YES (UPTIME_RANK=0, MIN_RUNWAY_H=0, +6 state gas series); (3) stay-resting values: YES (MID_BAND_OUT=0.30,0.70, WIDEBOOK_MIN_SPREAD_TICKS=5); (4) capital = **$314.57 only, no new money**; (5) daily credited-$ reporting, 7×$0 → stop-or-change sheet.
3. **APPLIED 13:45:28Z** to /opt/pa2-maker-kalshi-live/live.env (backup `live.env.bak-RELIGHT-20260901_134528`; diff verified = exactly these 5 lines; service untouched):
   UPTIME_RANK 1→0 · MIN_RUNWAY_H 49→0 · MID_BAND_OUT 0.10,0.90→0.30,0.70 · WIDEBOOK_MIN_SPREAD_TICKS +=5 · SERIES_ALLOW += KXAAAGASDCA,FL,IL,NJ,NY,TX.

## KEY FACTS FOR THE NEXT SESSION (measured this session; sources in the two sheets)
- Money map (reads 13:02–13:40Z): allowlist family = 166 active programs, **$17,520/day pools** (119 gas dailies ×$100 incl. 6 state series ×17 each, 21 gas-wkly ×$100, 21 diesel ×$120, 5 topmodel ×$200); 80 of 104 pulled books held Target both sides. Dailies settle NIGHTLY 04:00Z → credited-or-not receipts within ~24-48h of any go-live.
- Why the old footprint paid pennies: presence measured 40%/40%/16% on the accruing diesels, ≤4% elsewhere, 0% dailies (gates: band-edge 0.90 flap, widebook 20-tick spread, MIN_RUNWAY 49h) at ≤40ct ramp sizes. All three causes addressed by the applied env values.
- **Census is BLIND for the paying class** (d4 tape keeps only 3 ticks off touch; read 0.0% on markets the venue paid) — why UPTIME_RANK=0. Census full-book fix = open item, needs operator approval (read-only recorder change).
- Walk/share model over-predicts ~43x at matched size (T5.60 40ct: model $18.30/d vs measured $0.178/d) — ceiling only, NEVER an expectation. Only measured-anchored linear-in-size projections allowed.
- Honest expectation at $314/current caps (TOTAL 290 / MARKET 100 / halt 10): ~3-6 markets, single-digit $ credited per week (INFERRED from 1 day of anchors). Worst tail ≈ committed ~$290.
- `incentive_programs` paginates fine (limit=10000 + next_cursor; 5,822 active at 13:40:18Z) — old F6 "unpageable" is stale.

## NEXT SESSION'S JOB
1. Verify state on arrival (balance/positions/orders + service inactive+disabled + live.env md5 vs backup diff).
2. **WAIT for the operator's explicit GO. At GO and only then:** `systemctl enable --now polymarket-maker-kalshi-ws`; verify first cycle (fails=0, quoted list, committed ≤$290); report footprint + committed $ same day.
3. Daily 07:30Z once live: credit_history read → report ONE number (credited dollars, absolute). est-feed delta as color. **7 consecutive days $0 credited while resting → bring a stop-or-change sheet.**
4. Watch item (no action): est-feed accruals should freeze while bot is off; if a row climbs with 0 resting orders, the feed model is wrong — report.
5. Open items list (operator-gated, none started): census full-book fix · TOPMODEL sibling books never pulled (CLAU7/F/M/T) · tight near-money dailies (1-4c spread, mid 0.3-0.7) remain excluded by design under approved values · $3k scale path RULED NO for now (operator, this date).

## BINDING (unchanged from the wind-down handoff — read its failures section)
Nothing goes live without the operator's explicit YES on specific values; a general directive is not approval. Absolute credited dollars only — never relative spin. All hook rules bind; 07-27 data quarantined.
