<!-- SNAPSHOT (docs copy). Generated 2026-09-09T01:23:58Z by the DEPLOYED
clone (master db5f0f77) as polymarket, LIVE boards (01:15Z, merged code),
after the pipeline runner drained 6 dives from the clone (01:19-01:23Z; all
six ADMIT under the integrity-only rules, dossiers in deep_dive_pipeline/).
$algo at the foursome bankroll 500 / kelly 0.25 / conc floor 1 / min_viable 1;
allocator tiers OFF. The cron regenerates the live copy daily at stage 11.
EVERY $ IS HYPOTHETICAL; forward columns fill after the first 11:40Z run. -->

# MB TAILABLE LIST - verified tailable traders (generated; do not hand-edit)

**Generated:** 2026-09-09T01:23:58Z by `scripts/mb_tailable_list.py` @ db5f0f7 | **EVERY $ IS HYPOTHETICAL** (no real order exists; real money only via docs/MB_GO_CHECKLIST.md).

**Rulings applied:** backtest ADMITS to the list, forward watching = drop-off tripwire only (2026-09-08 #1); money headline = $algo/wk at OUR sizer stake per wager, as-if-approved (2026-09-08 #2; board field `ho_wk_net_algo_lcb`, P3), $ref100/wk = $100/wager comparison; VERIFIED = eligibility PASS AND conc<=20 (position-level) AND cov>=50% AND holdout ROI LCB>0 AND dive ADMIT. Tiers are labels: nothing here removes a wallet from anything (report + ask).

## Provenance (label vintage travels with every $)

| input | vintage (mtime UTC) | size / count |
|---|---|---|
| leaderboard_firehose | 2026-09-09T01:15:26Z | 19236 |
| leaderboard_roster | 2026-09-09T00:39:09Z | 98 |
| chain_audit | 2026-09-08T20:44:55Z | 129 |
| peak_conc | 2026-09-07T15:53:27Z | 46037 |
| forward_status | ABSENT | 0 |
| shadow_sink | 2026-09-09T01:23:29Z | 98 |
| dossiers | 2026-09-09T01:23:03Z | 154 |
| eligibility_cache | 2026-09-09T01:23:55Z | 13 |
| null_replication | 2026-09-08T19:24:56Z | 3 |

- Label vintage: **704350 keys** in gamma_resolutions.json @ 2026-09-08T17:31:47Z - quote every holdout $ with this.
- Sizer foursome (mb_sizer.env via env): {'MB_SIZER_BANKROLL': '500', 'MB_SIZER_KELLY_MULT': '0.25', 'MB_SIZER_CONCURRENCY': '1', 'MB_SIZER_MIN_VIABLE': '1'}; $algo columns come from the board (P3) - present.
- Null replication: docs/MB_STATE.md 2026-09-08 audit block; null = break-even, 400 reps, 12,889-wallet universe, deployed roi_lcb; family-wise bar = 5,271 holdout-tested wallets -> e >= 105,420
- Forward status: ABSENT (grader has not written it yet)
- Candidate universe: 13 wallets = post-conversion roster groups U pipeline dossiers U board QUALIFIES with $ref100/wk LCB>0 (firehose 19236 rows, roster 98 rows)

## Summary: VERIFIED 8 | PENDING 5 | FLAGGED 0 | DROPPED 0 | EXCLUDED 0

## VERIFIED (8)

| # | wallet | $algo/wk LCB | $/bet algo lcb / real | $ref100/wk LCB | $/bet ref100 lcb / real | roi_lcb | n_ho | conc pos/replay | cov% | elig | dive | selection | roster | forward (grader) | tripwire | last fill |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|
| 1 | `0x5feea3460c` | +215 | +1.45 / +0.44 | +12,919 | +110.88 / +296.47 | +1.109 | 145 | 18/18 | 97 | PASS | ADMIT | Y p=0.00 | cohort5 | not registered | - | 2026-09-08T21:40 |
| 2 | `0x4ab40f2a49` | +143 | +9.53 / +21.66 | +5,671 | +495.24 / +1912.32 | +4.952 | 13 | 8/8 | 97 | PASS | ADMIT | untested | no | not registered | - | - |
| 3 | `0x4f453abb65` | +15 | +0.56 / +2.93 | +132 | +17.75 / +178.70 | +0.178 | 9 | 6/6 | 84 | PASS | ADMIT | untested | no | not registered | - | - |
| 4 | `0xe48217d0b7` | +8 | +0.74 / +3.30 | +456 | +52.98 / +222.89 | +0.530 | 11 | 17/16 | 98 | PASS | ADMIT | no p=0.19 | cohort5 | not registered | - | - |
| 5 | `0x75a27d0cc9` | +1 | +0.02 / +0.06 | +87 | +10.89 / +406.27 | +0.109 | 10 | 19/19 | 74 | PASS | ADMIT | untested | cohort8 | not registered | - | 2026-09-09T00:40 |
| 6 | `0xe9f5c75ee1` | +1 | +0.02 / +0.13 | +123 | +7.60 / +81.36 | +0.076 | 20 | 18/18 | 91 | PASS | ADMIT | untested | cohort8 | not registered | - | 2026-09-09T00:17 |
| 7 | `0x1c010e69db` | +0 | +0.00 / +0.00 | +48 | +0.67 / +120.90 | +0.007 | 88 | 10/8 | 97 | PASS | ADMIT | untested | no | not registered | - | - |
| 8 | `0x2c50852938` | +0 | +0.00 / +0.00 | +667 | +94.72 / +834.93 | +0.947 | 8 | 11/9 | 81 | PASS | ADMIT | no p=0.47 | cohort5 | not registered | - | 2026-09-08T08:36 |

- `0x5feea3460cc6d46d800a2540876324c8b9d5f719` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=145 wagers=188 over 7.03d, $ref100/wk real +34,543. FILL-MODELED x0.81 (raw $ref100/wk LCB +16,016, raw $algo/wk LCB +272). Eligibility PASS (>=25 trades, first 276d ago; read 2026-09-08T19:25:11Z). Dive ADMIT deep_dive_promo0907 2026-09-07T16:49:59Z span=274d mkts=547 P=1.000 - complete sweep, 0 mismatch, 100% of 1257 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=547, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0x4ab40f2a49cb4ceefc3c90786e9f40ac9221c34b` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=13 wagers=17 over 7.03d, $ref100/wk real +21,898. FILL-MODELED x0.88 (raw $ref100/wk LCB +6,413, raw $algo/wk LCB +161). Eligibility PASS (>=25 trades, first 50d ago; read 2026-09-08T19:25:09Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:22:42Z span=49d mkts=80 P=1.000 - complete sweep, 0 mismatch, 100% of 475 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=80 >= 25, span=48d >= 30d), no forensic flag; old-basis P(edge>0)=1.0 DIAGNOSTIC ONLY - skill is the board's ruled-basis [..]. Forward: not registered in the grader.
- `0x4f453abb65afd586feec88d774aa04c27a74ae40` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=9 wagers=32 over 7.03d, $ref100/wk real +1,328. FILL-MODELED x0.83 (raw $ref100/wk LCB +159, raw $algo/wk LCB +18). Eligibility PASS (>=25 trades, first 149d ago; read 2026-09-08T19:42:16Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:22:45Z span=148d mkts=61 P=0.427 - complete sweep, 0 mismatch, 100% of 289 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=61 >= 25, span=148d >= 30d), no forensic flag; old-basis P(edge>0)=0.4275 DIAGNOSTIC ONLY - skill is the board's [..]. Forward: not registered in the grader.
- `0xe48217d0b70128d77a00615275d26916a95f025d` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=11 wagers=13 over 7.03d, $ref100/wk real +1,919. FILL-MODELED x0.79 (raw $ref100/wk LCB +581, raw $algo/wk LCB +10). Eligibility PASS (>=25 trades, first 78d ago; read 2026-09-08T19:25:12Z). Dive ADMIT deep_dive_promo0907 2026-09-07T17:08:33Z span=76d mkts=886 P=1.000 - complete sweep, 0 mismatch, 100% of 2164 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=886, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0x75a27d0cc95b3586297c1e6b5417269332cd6992` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=10 wagers=50 over 7.03d, $ref100/wk real +3,237. FILL-MODELED x0.80 (raw $ref100/wk LCB +109, raw $algo/wk LCB +1). Eligibility PASS (>=25 trades, first 103d ago; read 2026-09-08T19:25:11Z). Dive ADMIT deep_dive_promo0907 2026-09-08T18:54:35Z span=103d mkts=73 P=0.998 - complete sweep, 0 mismatch, 100% of 1438 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=73, P=0.9975), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0xe9f5c75ee1ae1aae57bf8582e359913c795a3e7a` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=20 wagers=37 over 7.03d, $ref100/wk real +1,313. FILL-MODELED x0.81 (raw $ref100/wk LCB +151, raw $algo/wk LCB +1). Eligibility PASS (>=25 trades, first 266d ago; read 2026-09-08T19:25:12Z). Dive ADMIT deep_dive_promo0907 2026-09-08T19:17:15Z span=265d mkts=1038 P=1.000 - complete sweep, 0 mismatch, 100% of 3675 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=1038, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0x1c010e69dbab9af4fe9998129f9f8f9e775cb690` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=88 wagers=163 over 7.03d, $ref100/wk real +8,598. FILL-MODELED x0.81 (raw $ref100/wk LCB +59, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 60d ago; read 2026-09-08T19:25:08Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:22:53Z span=59d mkts=459 P=1.000 - complete sweep, 0 mismatch, 100% of 2452 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=459 >= 25, span=58d >= 30d), no forensic flag; old-basis P(edge>0)=1.0 DIAGNOSTIC ONLY - skill is the board's ruled-basis [..]. Forward: not registered in the grader.
- `0x2c50852938f7cb4ed10c7a8e49877adc076470e3` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=8 wagers=11 over 7.03d, $ref100/wk real +5,884. FILL-MODELED x0.88 (raw $ref100/wk LCB +755, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 107d ago; read 2026-09-08T19:25:09Z). Dive ADMIT deep_dive_promo0907 2026-09-07T15:13:53Z span=104d mkts=62 P=1.000 - complete sweep, 0 mismatch, 100% of 316 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=62, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.

## PENDING (5)

| # | wallet | $algo/wk LCB | $/bet algo lcb / real | $ref100/wk LCB | $/bet ref100 lcb / real | roi_lcb | n_ho | conc pos/replay | cov% | elig | dive | selection | roster | forward (grader) | tripwire | last fill |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|
| 1 | `0xd6730ad2d5` | +2,526 | +13.32 / +22.69 | +415 | +37.19 / +115.26 | +0.372 | 14 | 10/2 | 100 | FAIL | none | untested | no | not registered | - | - |
| 2 | `0x5cb1327663` | +214 | +7.33 / +76.93 | +851 | +241.98 / +3470.01 | +2.420 | 4 | 3/3 | 100 | FAIL | none | untested | no | not registered | - | - |
| 3 | `0x4ed3513040` | +0 | -0.00 / +0.00 | -348 | -23.52 / +51.67 | -0.235 | 18 | 17/16 | 87 | PASS | ADMIT | untested | no | not registered | - | - |
| 4 | `0x5d2e0736fb` | +0 | -0.00 / +0.00 | -98 | -12.46 / +789.02 | -0.125 | 9 | 11/11 | 78 | PASS | ADMIT | untested | no | not registered | - | - |
| 5 | `0xf7d03961dd` | +0 | -0.00 / +0.00 | -318 | -16.27 / +35.73 | -0.163 | 24 | 19/20 | 94 | PASS | ADMIT | untested | no | not registered | - | - |

- `0xd6730ad2d585b4f144d307b83c4cd58537475fb7` - PENDING: eligibility FAIL; dive none. Board firehose verdict QUALIFIES, holdout n=14 wagers=242 over 7.03d, $ref100/wk real +1,285. FILL-MODELED x0.80 (raw $ref100/wk LCB +519, raw $algo/wk LCB +3,211). Eligibility FAIL (first trade 1.9d ago < 30d; read 2026-09-09T01:16:01Z). Dive: none (no dossier). Forward: not registered in the grader.
- `0x5cb132766326b5e661cfe72ff49c531c016accc6` - PENDING: eligibility FAIL; dive none. Board firehose verdict QUALIFIES, holdout n=4 wagers=34 over 7.03d, $ref100/wk real +12,205. FILL-MODELED x0.88 (raw $ref100/wk LCB +964, raw $algo/wk LCB +248). Eligibility FAIL (first trade 3.2d ago < 30d; read 2026-09-09T01:16:00Z). Dive: none (no dossier). Forward: not registered in the grader.
- `0x4ed35130402a15d06b102f91877a4fe43ed2e416` - PENDING: holdout ROI LCB not > 0. Board firehose verdict QUALIFIES, holdout n=18 wagers=73 over 7.03d, $ref100/wk real +765. FILL-MODELED x0.83 (raw $ref100/wk LCB -422, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 47d ago; read 2026-09-08T19:25:09Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:22:39Z span=47d mkts=78 P=1.000 - complete sweep, 0 mismatch, 100% of 412 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=78 >= 25, span=44d >= 30d), no forensic flag; old-basis P(edge>0)=1.0 DIAGNOSTIC ONLY - skill is the board's ruled-basis [..]. Forward: not registered in the grader.
- `0x5d2e0736fb39feace0ddf66f9cff7fa3ad1d2581` - PENDING: holdout ROI LCB not > 0. Board firehose verdict QUALIFIES, holdout n=9 wagers=19 over 7.03d, $ref100/wk real +6,196. FILL-MODELED x0.88 (raw $ref100/wk LCB -112, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 271d ago; read 2026-09-08T19:25:10Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:22:59Z span=271d mkts=159 P=0.545 - complete sweep, 0 mismatch, 100% of 518 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=159 >= 25, span=221d >= 30d), no forensic flag; old-basis P(edge>0)=0.545 DIAGNOSTIC ONLY - skill is the board's [..]. Forward: not registered in the grader.
- `0xf7d03961dd362a6e5565787f6b36a5d5c37c4c1d` - PENDING: holdout ROI LCB not > 0. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=24 wagers=24 over 7.03d, $ref100/wk real +697. FILL-MODELED x0.82 (raw $ref100/wk LCB -389, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 144d ago; read 2026-09-08T19:25:12Z). Dive ADMIT deep_dive_pipeline 2026-09-09T01:23:03Z span=144d mkts=299 P=0.193 - complete sweep, 0 mismatch, 90% of 946 API-BUYs chain-backed (>= 80%), evidence adequate (mkts=299 >= 25, span=144d >= 30d), no forensic flag; old-basis P(edge>0)=0.1925 DIAGNOSTIC ONLY - skill is the board's [..]. Forward: not registered in the grader.

## FLAGGED (0)

_none_

## DROPPED (0)

_none_

## EXCLUDED (0)

_none_

## Legend

- **$algo/wk LCB** - LCB net winnings per week at OUR sizer's stake per wager (bankroll x kelly_mult / conc at each wager's fill, per-bet cap, min-viable-to-zero), as-if-approved. HYPOTHETICAL. `P3` = the board does not carry it yet.
- **$ref100/wk LCB** - same at a flat $100/wager reference (comparison column only, ruling 2026-09-08 #2).
- **$/bet** - expected profit per bet THAT FILLS (not fill-modeled): lcb = holdout ROI LCB x stake, real = realized profit per filling wager; algo = at our sizer's mean stake per wager for that wallet, ref100 = at $100. The weekly columns multiply in the gate pass probability, so $/bet x wagers/week != $/week on a fill-modeled row by design. HYPOTHETICAL.
- **FILL-MODELED** (P5, D3) - firehose rows weight every holdout wager by the measured gate pass probability of its 0.1 price bucket (shadow sink, re-measured daily); raw values in the detail line. Roster rows carry real gate verdicts.
- **conc pos/replay** - position-level peak concurrency (firehose/peak_conc.jsonl, the screen's authority) / the board's replay measurement.
- **selection** - survives the 2026-09-08 null replication (Y/no + p); `untested` = not in that replication, never assumed.
- **forward / tripwire** - the grader's own row (cohort5_forward_status.json): WATCH / DEGRADED / DROPPED / PASSED. DROPPED moves the wallet to the DROPPED tier; roster removal is an operator ruling.

