<!-- SNAPSHOT (docs copy). Generated 2026-09-08T20:22:28Z by a HAND RUN of
scripts/mb_tailable_list.py as polymarket against SCRATCH boards
(/tmp/mbt_run/out, produced 20:21Z by mb_backtest.py daily-replay on the
P3+P5 code of branch claude/mb-tailable-0908) - NOT by the cron (stage 11
deploys with the merge). The live copy regenerates daily at
/opt/pa2-shared/mb_copyable_data/tailable/MB_TAILABLE_LIST.md. The
"@ ?" code_rev below is the hand-run's /tmp copy (no .git); the cron run
prints the clone's HEAD. EVERY $ IS HYPOTHETICAL. -->

# MB TAILABLE LIST - verified tailable traders (generated; do not hand-edit)

**Generated:** 2026-09-08T20:22:28Z by `scripts/mb_tailable_list.py` @ ? | **EVERY $ IS HYPOTHETICAL** (no real order exists; real money only via docs/MB_GO_CHECKLIST.md).

**Rulings applied:** backtest ADMITS to the list, forward watching = drop-off tripwire only (2026-09-08 #1); money headline = $algo/wk at OUR sizer stake per wager, as-if-approved (2026-09-08 #2; board field `ho_wk_net_algo_lcb`, P3), $ref100/wk = $100/wager comparison; VERIFIED = eligibility PASS AND conc<=20 (position-level) AND cov>=50% AND holdout ROI LCB>0 AND dive ADMIT. Tiers are labels: nothing here removes a wallet from anything (report + ask).

## Provenance (label vintage travels with every $)

| input | vintage (mtime UTC) | size / count |
|---|---|---|
| leaderboard_firehose | 2026-09-08T20:21:49Z | 19236 |
| leaderboard_roster | 2026-09-08T19:46:51Z | 95 |
| chain_audit | 2026-09-08T03:02:12Z | 127 |
| peak_conc | 2026-09-07T15:53:27Z | 46037 |
| forward_status | ABSENT | 0 |
| shadow_sink | 2026-09-08T20:22:22Z | 96 |
| dossiers | 2026-09-08T19:26:55Z | 153 |
| eligibility_cache | 2026-09-08T20:22:24Z | 13 |
| null_replication | 2026-09-08T19:24:56Z | 3 |

- Label vintage: **704350 keys** in gamma_resolutions.json @ 2026-09-08T17:31:47Z - quote every holdout $ with this.
- Sizer foursome (mb_sizer.env via env): {'MB_SIZER_BANKROLL': '500', 'MB_SIZER_KELLY_MULT': '0.25', 'MB_SIZER_CONCURRENCY': '1', 'MB_SIZER_MIN_VIABLE': '1'}; $algo columns come from the board (P3) - present.
- Null replication: docs/MB_STATE.md 2026-09-08 audit block; null = break-even, 400 reps, 12,889-wallet universe, deployed roi_lcb; family-wise bar = 5,271 holdout-tested wallets -> e >= 105,420
- Forward status: ABSENT (grader has not written it yet)
- Candidate universe: 12 wallets = post-conversion roster groups U pipeline dossiers U board QUALIFIES with $ref100/wk LCB>0 (firehose 19236 rows, roster 95 rows)

## Summary: VERIFIED 5 | PENDING 7 | FLAGGED 0 | DROPPED 0 | EXCLUDED 0

## VERIFIED (5)

| # | wallet | $algo/wk LCB | $ref100/wk LCB | roi_lcb | n_ho | conc pos/replay | cov% | elig | dive | selection | roster | forward (grader) | tripwire | last fill |
|---|---|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|
| 1 | `0x5feea3460c` | +222 | +13,351 | +1.109 | 145 | 18/18 | 97 | PASS | ADMIT | Y p=0.00 | cohort5 | not registered | - | 2026-09-08T20:00 |
| 2 | `0xe48217d0b7` | +8 | +485 | +0.530 | 11 | 17/16 | 98 | PASS | ADMIT | no p=0.19 | cohort5 | not registered | - | - |
| 3 | `0x75a27d0cc9` | +1 | +72 | +0.096 | 9 | 19/19 | 74 | PASS | ADMIT | untested | no | not registered | - | - |
| 4 | `0x2c50852938` | +0 | +688 | +0.947 | 8 | 11/9 | 81 | PASS | ADMIT | no p=0.47 | cohort5 | not registered | - | 2026-09-08T08:36 |
| 5 | `0xe9f5c75ee1` | +0 | +87 | +0.055 | 19 | 18/18 | 91 | PASS | ADMIT | untested | no | not registered | - | - |

- `0x5feea3460cc6d46d800a2540876324c8b9d5f719` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=145 wagers=188 over 6.82d, $ref100/wk real +35,697. FILL-MODELED x0.81 (raw $ref100/wk LCB +16,492, raw $algo/wk LCB +280). Eligibility PASS (>=25 trades, first 276d ago; read 2026-09-08T19:25:11Z). Dive ADMIT deep_dive_promo0907 2026-09-07T16:49:59Z span=274d mkts=547 P=1.000 - complete sweep, 0 mismatch, 100% of 1257 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=547, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0xe48217d0b70128d77a00615275d26916a95f025d` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=11 wagers=13 over 6.82d, $ref100/wk real +2,041. FILL-MODELED x0.81 (raw $ref100/wk LCB +598, raw $algo/wk LCB +10). Eligibility PASS (>=25 trades, first 78d ago; read 2026-09-08T19:25:12Z). Dive ADMIT deep_dive_promo0907 2026-09-07T17:08:33Z span=76d mkts=886 P=1.000 - complete sweep, 0 mismatch, 100% of 2164 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=886, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0x75a27d0cc95b3586297c1e6b5417269332cd6992` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=9 wagers=49 over 6.82d, $ref100/wk real +3,389. FILL-MODELED x0.82 (raw $ref100/wk LCB +88, raw $algo/wk LCB +1). Eligibility PASS (>=25 trades, first 103d ago; read 2026-09-08T19:25:11Z). Dive ADMIT deep_dive_promo0907 2026-09-08T18:54:35Z span=103d mkts=73 P=0.998 - complete sweep, 0 mismatch, 100% of 1438 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=73, P=0.9975), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0x2c50852938f7cb4ed10c7a8e49877adc076470e3` - VERIFIED: all criteria green. Board firehose verdict QUALIFIES, holdout n=8 wagers=11 over 6.82d, $ref100/wk real +6,066. FILL-MODELED x0.89 (raw $ref100/wk LCB +777, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 107d ago; read 2026-09-08T19:25:09Z). Dive ADMIT deep_dive_promo0907 2026-09-07T15:13:53Z span=104d mkts=62 P=1.000 - complete sweep, 0 mismatch, 100% of 316 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=62, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.
- `0xe9f5c75ee1ae1aae57bf8582e359913c795a3e7a` - VERIFIED: all criteria green. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=19 wagers=35 over 6.82d, $ref100/wk real +1,316. FILL-MODELED x0.82 (raw $ref100/wk LCB +106, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 266d ago; read 2026-09-08T19:25:12Z). Dive ADMIT deep_dive_promo0907 2026-09-08T19:17:15Z span=265d mkts=1038 P=1.000 - complete sweep, 0 mismatch, 100% of 3675 API-BUYs chain-backed (>= 80%), chain skill clears (mkts=1038, P=1.0), no forensic flag — PROPOSED to operator for a cohort (own start date, separate readout). Forward: not registered in the grader.

## PENDING (7)

| # | wallet | $algo/wk LCB | $ref100/wk LCB | roi_lcb | n_ho | conc pos/replay | cov% | elig | dive | selection | roster | forward (grader) | tripwire | last fill |
|---|---|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|
| 1 | `0xd6730ad2d5` | +2,625 | +431 | +0.372 | 14 | 10/2 | 100 | FAIL | none | untested | no | not registered | - | - |
| 2 | `0x5cb1327663` | +221 | +878 | +2.420 | 4 | 3/3 | 100 | FAIL | none | untested | no | not registered | - | - |
| 3 | `0x4ab40f2a49` | +147 | +5,847 | +4.952 | 13 | 8/8 | 97 | PASS | INSUFF | untested | no | not registered | - | - |
| 4 | `0x4f453abb65` | +15 | +137 | +0.178 | 9 | 6/6 | 84 | PASS | INSUFF | untested | no | not registered | - | - |
| 5 | `0x1c010e69db` | +0 | +50 | +0.007 | 88 | 10/8 | 97 | PASS | INSUFF | untested | no | not registered | - | - |
| 6 | `0x5d2e0736fb` | -0 | -4 | -0.005 | 8 | 11/11 | 78 | PASS | INSUFF | untested | no | not registered | - | - |
| 7 | `0xf7d03961dd` | -0 | -330 | -0.163 | 24 | 19/20 | 94 | PASS | INSUFF | untested | no | not registered | - | - |

- `0xd6730ad2d585b4f144d307b83c4cd58537475fb7` - PENDING: eligibility FAIL; dive none. Board firehose verdict QUALIFIES, holdout n=14 wagers=242 over 6.82d, $ref100/wk real +1,336. FILL-MODELED x0.81 (raw $ref100/wk LCB +534, raw $algo/wk LCB +3,307). Eligibility FAIL (first trade 1.7d ago < 30d; read 2026-09-08T20:22:24Z). Dive: none (no dossier). Forward: not registered in the grader.
- `0x5cb132766326b5e661cfe72ff49c531c016accc6` - PENDING: eligibility FAIL; dive none. Board firehose verdict QUALIFIES, holdout n=4 wagers=34 over 6.82d, $ref100/wk real +12,585. FILL-MODELED x0.88 (raw $ref100/wk LCB +993, raw $algo/wk LCB +256). Eligibility FAIL (first trade 3.0d ago < 30d; read 2026-09-08T20:22:23Z). Dive: none (no dossier). Forward: not registered in the grader.
- `0x4ab40f2a49cb4ceefc3c90786e9f40ac9221c34b` - PENDING: dive INSUFFICIENT-EVIDENCE. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=13 wagers=17 over 6.82d, $ref100/wk real +22,577. FILL-MODELED x0.89 (raw $ref100/wk LCB +6,604, raw $algo/wk LCB +166). Eligibility PASS (>=25 trades, first 50d ago; read 2026-09-08T19:25:09Z). Dive INSUFFICIENT-EVIDENCE deep_dive_promo0907 2026-09-08T18:24:48Z span=49d mkts=80 P=1.000 - skill underpowered/short-span, not disproven: mkts=80, span=48d, P(edge>0)=1.0 — deepen (more resolved markets). Forward: not registered in the grader.
- `0x4f453abb65afd586feec88d774aa04c27a74ae40` - PENDING: dive INSUFFICIENT-EVIDENCE. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=9 wagers=32 over 6.82d, $ref100/wk real +1,375. FILL-MODELED x0.83 (raw $ref100/wk LCB +164, raw $algo/wk LCB +19). Eligibility PASS (>=25 trades, first 149d ago; read 2026-09-08T19:42:16Z). Dive INSUFFICIENT-EVIDENCE deep_dive_promo0907 2026-09-08T19:26:55Z span=148d mkts=61 P=0.427 - skill underpowered/short-span, not disproven: mkts=61, span=148d, P(edge>0)=0.4275 — deepen (more resolved markets). Forward: not registered in the grader.
- `0x1c010e69dbab9af4fe9998129f9f8f9e775cb690` - PENDING: dive INSUFFICIENT-EVIDENCE. Board firehose verdict QUALIFIES, holdout n=88 wagers=163 over 6.82d, $ref100/wk real +8,884. FILL-MODELED x0.81 (raw $ref100/wk LCB +61, raw $algo/wk LCB +0). Eligibility PASS (>=25 trades, first 60d ago; read 2026-09-08T19:25:08Z). Dive INSUFFICIENT-EVIDENCE deep_dive_promo0907 2026-09-07T16:59:03Z span=58d mkts=454 P=1.000 - skill underpowered/short-span, not disproven: mkts=454, span=57d, P(edge>0)=1.0 — deepen (more resolved markets). Forward: not registered in the grader.
- `0x5d2e0736fb39feace0ddf66f9cff7fa3ad1d2581` - PENDING: holdout ROI LCB not > 0; dive INSUFFICIENT-EVIDENCE. Board firehose verdict QUALIFIES, holdout n=8 wagers=18 over 6.82d, $ref100/wk real +6,489. FILL-MODELED x0.88 (raw $ref100/wk LCB -4, raw $algo/wk LCB -0). Eligibility PASS (>=25 trades, first 271d ago; read 2026-09-08T19:25:10Z). Dive INSUFFICIENT-EVIDENCE deep_dive_promo0907 2026-09-07T17:16:54Z span=269d mkts=157 P=0.520 - skill underpowered/short-span, not disproven: mkts=157, span=219d, P(edge>0)=0.52 — deepen (more resolved markets). Forward: not registered in the grader.
- `0xf7d03961dd362a6e5565787f6b36a5d5c37c4c1d` - PENDING: holdout ROI LCB not > 0; dive INSUFFICIENT-EVIDENCE. Board firehose verdict NOT DEMONSTRATED (futility 1wk), holdout n=24 wagers=24 over 6.82d, $ref100/wk real +724. FILL-MODELED x0.82 (raw $ref100/wk LCB -401, raw $algo/wk LCB -0). Eligibility PASS (>=25 trades, first 144d ago; read 2026-09-08T19:25:12Z). Dive INSUFFICIENT-EVIDENCE deep_dive_promo0907 2026-09-08T18:37:08Z span=144d mkts=299 P=0.193 - skill underpowered/short-span, not disproven: mkts=299, span=144d, P(edge>0)=0.1925 — deepen (more resolved markets). Forward: not registered in the grader.

## FLAGGED (0)

_none_

## DROPPED (0)

_none_

## EXCLUDED (0)

_none_

## Legend

- **$algo/wk LCB** - LCB net winnings per week at OUR sizer's stake per wager (bankroll x kelly_mult / conc at each wager's fill, per-bet cap, min-viable-to-zero), as-if-approved. HYPOTHETICAL. `P3` = the board does not carry it yet.
- **$ref100/wk LCB** - same at a flat $100/wager reference (comparison column only, ruling 2026-09-08 #2).
- **FILL-MODELED** (P5, D3) - firehose rows weight every holdout wager by the measured gate pass probability of its 0.1 price bucket (shadow sink, re-measured daily); raw values in the detail line. Roster rows carry real gate verdicts.
- **conc pos/replay** - position-level peak concurrency (firehose/peak_conc.jsonl, the screen's authority) / the board's replay measurement.
- **selection** - survives the 2026-09-08 null replication (Y/no + p); `untested` = not in that replication, never assumed.
- **forward / tripwire** - the grader's own row (cohort5_forward_status.json): WATCH / DEGRADED / DROPPED / PASSED. DROPPED moves the wallet to the DROPPED tier; roster removal is an operator ruling.

