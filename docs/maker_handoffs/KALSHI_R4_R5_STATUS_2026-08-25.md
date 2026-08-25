# R4 STATUS + R5 PART 1 — INSTRUMENT TRUST RESOLUTIONS + KNOB INVENTORY (2026-08-25)

Operator: "proceed all in proper order". This doc records the R4 item resolutions and the
R5 part-1 mechanical inventory. Rule Nine: every previously-listed R4 sub-item is disposed
here EXPLICITLY (fixed / resolved-not-a-defect / still-open), none silently dropped.

## R4 — instrument trust
1. **Replica 0.99-max-price rule — FIXED (commit `2ae1bf7`)**. `_qualifying_score` (quoter)
   and `kalshi_market_scorecard.qualifying_share` (changed together; equivalence pin kept)
   now disqualify a side whose best bid sits AT 1-TICK (0.99), per the filing's "not less
   than the highest possible price". Behavior change disclosed: 0.99-touch books now model
   $0 -> the capture gate refuses them for FLAT entry (matches what the venue pays);
   held inventory unwinds unchanged. Pin: test_capture_arm_live_config::test_p8.
2. **D4 "occupancy field wrong" — RESOLVED, NOT A DEFECT.** The recorder stores raw depth
   arrays only; the "0/210 two-sided" figure came from report-layer scripts testing
   two-sided-AT-TARGET (>=1000 both sides). Under the official rules that test is CORRECT:
   3.900 was touch-two-sided but its NO side never summed near 1,000 (own telemetry
   n_cum <= ~49 through 08-24/25), so 0/N at-Target was a true reading. The original
   complaint predates the rules read and conflated touch- with Target-two-sidedness.
   Residual guidance (open, small): report scripts should read per-PROGRAM target_size
   (not hardcoded 1000) and apply the 0.99 rule; the shipped scorecard fix covers the
   shared path.
3. **Reference-price discrepancy (filing: best bid vs help article: walk to Target/5) —
   OPEN, awaiting a discriminating episode.** The two models coincide whenever the touch
   level alone holds >= Target/5 and our orders rest AT the touch — true of every book we
   currently quote, so today's accrual data cannot discriminate. Standing detector: when
   a THIN-TOP qualifying book appears in the footprint, compare est-feed batch increments
   against both models' predicted share. No code until the data decides.
4. **incentive_programs endpoint unpageable — CONFIRMED (limit=200 and limit=1000 both
   return exactly `limit` rows, no cursor; reads 15:24Z/16:1xZ).** Any single-request
   program count is a truncated denominator. OPEN: re-verify how F9's 3,542-programs
   count was enumerated before that number is reused.
5. Est-feed gauge semantics (batched ~hourly, lagged ~1-2h, sub-$1 rows dropped unpaid,
   0-rows displayed) — codified in `KALSHI_SWEEP_FINDINGS_2026-08-25.md` F2-F4.

## R5 — part 1: mechanical knob inventory (generated from source, 2026-08-25)
Denominators: **145 knobs** declared via the `_env*` pattern in `maker_kalshi_quoter.py`
(line refs below) + **16 more** set in live.env but declared elsewhere/other patterns
(string-parsed: MID_BAND_OUT, SERIES_ALLOW/DENY, D3_RUNGS, ANCHOR_EMPTY_SIDE,
REDUCE_ONLY_KEEP_BOTH, PIVOT_FAR_FIRST, THROTTLE_SMART, SWEEP_ENABLED, TRADING_MODE,
LIVE_ARMED, WS_HOT, WS_BOOK_COLD, ENV_FILE + client creds API_KEY_ID/RSA_PRIVATE_KEY_PATH).
**81 keys set in live.env**; the rest run on code defaults (the 07-26 audit's silent-default
class — the inventory below makes it visible).

Seed incident map (safety-critical set, to be completed in part 2 with one line per knob):
- `_SAFETY_KNOBS` (PRECLOSE_FLATTEN, TAKER_FLATTEN, THROTTLE_SMART, ...) — the built-but-
  never-armed class (07-26 audit S3); hot-reload guarded in code.
- QUALIFIABLE/CAPTURE/RUNWAY family — today's D1/D2/D4 chain (this doc + R3 canon).
- REPAIR/EVENT_DELTA family — D3 (design doc + pins).
- Governors (loss ladder, two-strikes, DD_CARRY, halt) — drips-are-fine canon + 08-12 traps.

| env key | default | live | quoter line |
|---|---|---|---|
| `KALSHI_ALLOC_INCUMBENT_FIRST` | 0 | default | :3858 |
| `KALSHI_ALLOC_KEY` | 0 | SET | :3917 |
| `KALSHI_ALLOC_PCAP_MAX_AGE_S` | 21600.0 | default | :3923 |
| `KALSHI_ALLOC_PROSPECTIVE_HAIRCUT` | 1.0 | default | :3919 |
| `KALSHI_ALLOC_RISK_LAMBDA` | 1.0 | default | :3918 |
| `KALSHI_ALLOC_UNKNOWN_HAIRCUT` | 1.0 | default | :3920 |
| `KALSHI_ALLOW_PROBE_EXCEPTION` | 0 | SET | :535 |
| `KALSHI_AMEND_DECREASE` | 0 | default | :367 |
| `KALSHI_ANCHOR_PRICE` | 0.01 | SET | :254 |
| `KALSHI_BLACKOUT_CANCEL_AFTER` | 2 | default | :4073 |
| `KALSHI_BLACKOUT_RETRY_BASE_S` | 30.0 | default | :4080 |
| `KALSHI_BLACKOUT_RETRY_MAX_S` | 600.0 | default | :4081 |
| `KALSHI_BREAKER_HELD_GROWTH_USD` | 20.0 | SET | :1773 |
| `KALSHI_BREAKER_WINDOW_S` | 600 | default | :1774 |
| `KALSHI_CAPRANK_CALIB` | 1.0 | default | :450 |
| `KALSHI_CAPRANK_PROSPECTIVE_HAIRCUT` | 1.0 | default | :455 |
| `KALSHI_CAPRANK_RISK_LAMBDA` | 1.0 | default | :454 |
| `KALSHI_CAPRANK_TELEMETRY` | 0 | SET | :447 |
| `KALSHI_CAPRANK_UNKNOWN_HAIRCUT` | 1.0 | default | :456 |
| `KALSHI_CAPTURE_DF` | 0.5 | default | :302 |
| `KALSHI_CAPTURE_GATE` | 0 | SET | :300 |
| `KALSHI_CAPTURE_MIN_USD_DAY` | 5.0 | SET | :301 |
| `KALSHI_CLOSE_CACHE_POS_TTL_S` | 21600.0 | default | :1837 |
| `KALSHI_CREDIT_FEEDBACK_PATH` | (env str) | default | :494 |
| `KALSHI_D2_BONUS` | 1.5 | default | :492 |
| `KALSHI_D2_FEEDBACK` | 0 | SET | :491 |
| `KALSHI_D2_NEVERPAID_MULT` | 0.5 | default | :493 |
| `KALSHI_D3_KEEP_S` | 1800.0 | default | :628 |
| `KALSHI_D3_NEWSERIES_MAX_RUNG` | 1 | SET | :562 |
| `KALSHI_D3_RAMP` | 0 | SET | :541 |
| `KALSHI_D3_RUNG_S` | 600.0 | default | :561 |
| `KALSHI_DAILY_LOSS_HALT_USD` | 20.0 | SET | :1794 |
| `KALSHI_DD_CARRY` | True | SET | :1803 |
| `KALSHI_DROP_GRACE` | 0 | SET | :382 |
| `KALSHI_EST_FEED` | 0 | default | :781 |
| `KALSHI_EST_FEED_MAX_AGE_S` | 1800.0 | default | :782 |
| `KALSHI_EST_FEED_MIN_FRAC` | 0.25 | default | :783 |
| `KALSHI_EVENT_DELTA_DOLLARS` | 0 | SET | :1734 |
| `KALSHI_EVENT_FALLBACK_BASIS_D` | 0.35 | default | :1737 |
| `KALSHI_EVENT_HARD_USD` | 17.50 | default | :1736 |
| `KALSHI_EVENT_SOFT_USD` | 5.25 | default | :1735 |
| `KALSHI_EXIT_CHEAP_CROSS_USD` | 0.25 | default | :1953 |
| `KALSHI_EXIT_LADDER_STEPS` | 2 | default | :1952 |
| `KALSHI_EXIT_MAX_PRICE_DOLLARS` | 0.99 | default | :1524 |
| `KALSHI_EXIT_MIN_PRICE_DOLLARS` | 0.01 | default | :1525 |
| `KALSHI_EXPLORE_PROBE_CT` | 0 | SET | :418 |
| `KALSHI_FARCLOSE_PAYING_EXCEPTION` | 0 | SET | :3503 |
| `KALSHI_FILLCOST_REFRESH_S` | 3600.0 | default | :2812 |
| `KALSHI_FILL_COST_PATH` | (env str) | default | :457 |
| `KALSHI_FLATTEN_MAX_SLIP` | 0.10 | SET | :1824 |
| `KALSHI_FOOTPRINT_TOP` | 60 | SET | :185 |
| `KALSHI_FUNDING_GATE` | 0 | SET | :1518 |
| `KALSHI_HALT_CONFIRM_N` | 3 | default | :1260 |
| `KALSHI_HELD_MAX_USD` | 20.0 | SET | :1781 |
| `KALSHI_INCUMBENCY_BONUS` | 0.0 | SET | :413 |
| `KALSHI_INCUMBENT_ONLY` | 0 | default | :1050 |
| `KALSHI_INV_HARD_CT` | 80.0 | SET | :1638 |
| `KALSHI_INV_SOFT_CT` | 30.0 | SET | :1637 |
| `KALSHI_INV_TOLERANCE` | 3.0 | SET | :1653 |
| `KALSHI_JOIN_SIZE` | 100 | SET | :206 |
| `KALSHI_LATE_LIFE_FRAC` | 0.6 | default | :1689 |
| `KALSHI_MACRO_PROBE_TOP` | 0.03 | default | :732 |
| `KALSHI_MACRO_PROBE_USD` | 60.0 | default | :731 |
| `KALSHI_MAX_ACTIVATE_CAPITAL` | 150.0 | SET | :246 |
| `KALSHI_MAX_DAYS_TO_CLOSE` | 3.0 | SET | :1705 |
| `KALSHI_MAX_ENTRY_CUTOFF_MIN` | 120.0 | default | :1690 |
| `KALSHI_MAX_MARKET_CAPITAL` | 250.0 | SET | :255 |
| `KALSHI_MAX_PRICE_DOLLARS` | 0.97 | SET | :1519 |
| `KALSHI_MAX_SPREAD_TICKS` | 8 | SET | :1965 |
| `KALSHI_MAX_TOTAL_CAPITAL` | 10000.0 | SET | :256 |
| `KALSHI_MAX_VOL24H_CT` | 0.0 | SET | :1867 |
| `KALSHI_MIN_CREDIT_USD` | 1.20 | SET | :1267 |
| `KALSHI_MIN_DEPTH_SYM` | 0.25 | SET | :1966 |
| `KALSHI_MIN_PRICE_DOLLARS` | 0.01 | SET | :1520 |
| `KALSHI_MIN_QUOTE_CT` | 2 | default | :210 |
| `KALSHI_MIN_RUNWAY_H` | 0.0 | SET | :1712 |
| `KALSHI_MKT_DAY_LOSS_EXITONLY_USD` | 0.0 | SET | :943 |
| `KALSHI_MKT_OUT_LOSS_USD` | 5.0 | default | :952 |
| `KALSHI_MKT_TELEMETRY` | 1 | default | :323 |
| `KALSHI_MKT_UNWIND_ALLOW_PER_CT` | 0.04 | default | :967 |
| `KALSHI_NETEV_FINGERPRINT_USD_DAY` | 5.0 | default | :1418 |
| `KALSHI_NETEV_GATE` | 0 | SET | :1415 |
| `KALSHI_NETEV_MIN_MARGIN_PCT` | 0.0 | SET | :1416 |
| `KALSHI_NETEV_MODEL_HAIRCUT` | 3.0 | default | :1417 |
| `KALSHI_NETEV_TABLE` | (env str) | default | :1419 |
| `KALSHI_OBS_HOLD` | 0 | SET | :557 |
| `KALSHI_OBS_HOLD_FRESH_S` | 86400.0 | default | :559 |
| `KALSHI_OBS_HOLD_MAX_RUNG` | 0 | default | :560 |
| `KALSHI_OBS_HOLD_MIN_USD` | 1.20 | default | :558 |
| `KALSHI_PAIR_BOTH_SIDES` | True | default | :1767 |
| `KALSHI_PAIR_UNWIND` | 0 | default | :3887 |
| `KALSHI_PAIR_UNWIND_MIN_EDGE` | 0.02 | default | :3888 |
| `KALSHI_PER_SERIES_CAP` | 10 | SET | :186 |
| `KALSHI_PIVOT_COVERAGE` | 1 | SET | :196 |
| `KALSHI_PIVOT_POOL_MULT` | 2 | SET | :195 |
| `KALSHI_PIVOT_READ_RESERVE` | 30 | default | :197 |
| `KALSHI_PIVOT_SELECT` | 0 | SET | :194 |
| `KALSHI_PRECLOSE_FLATTEN` | 0 | SET | :1922 |
| `KALSHI_PRECLOSE_FLATTEN_MIN` | 15.0 | default | :1923 |
| `KALSHI_PRESENCE_DEFAULT` | 1.0 | default | :1268 |
| `KALSHI_PRESENCE_GATE` | 0 | SET | :1265 |
| `KALSHI_PROBE_MAX_SLOTS` | 5 | default | :536 |
| `KALSHI_PROSPECTIVE_PATH` | (env str) | default | :459 |
| `KALSHI_QUALIFIABLE_GATE` | default_on=True | SET | :249 |
| `KALSHI_RAMP_LIFE_FRAC` | 0.5 | default | :1682 |
| `KALSHI_RAMP_MIN` | 180 | default | :1676 |
| `KALSHI_READ_BUDGET` | 200 | default | :1976 |
| `KALSHI_REENTRY_COOLDOWN_S` | 0.0 | SET | :1246 |
| `KALSHI_REPAIR_BASIS_MAX_D` | 0.02 | default | :1727 |
| `KALSHI_REPAIR_CHEAP_FILL` | 0 | SET | :1726 |
| `KALSHI_REQ_SPACING_S` | 0.55 | SET | :1975 |
| `KALSHI_RUNWAY_ACCRUED_EXEMPT_USD` | 0.50 | SET | :1718 |
| `KALSHI_SCORE_EXPLORE` | 10 | SET | :405 |
| `KALSHI_SCORE_PATH` | (env str) | default | :408 |
| `KALSHI_SCORE_RANK` | 0 | SET | :404 |
| `KALSHI_SCORE_SWING_PENALTY` | 1.0 | default | :406 |
| `KALSHI_SCORE_UNKNOWN_BONUS` | 1.0 | SET | :407 |
| `KALSHI_SELECT_BUDGET` | 0 | SET | :1065 |
| `KALSHI_SELECT_BUDGET_MARGIN` | 0.3 | SET | :1066 |
| `KALSHI_SERIES_MAX_USD` | 0.0 | SET | :3867 |
| `KALSHI_SERIES_PCT` | 0.25 | default | :3874 |
| `KALSHI_SETTLE_UNWIND_MIN` | 30 | SET | :1654 |
| `KALSHI_STANDDOWN` | 0 | SET | :243 |
| `KALSHI_STANDDOWN_MIN_USD_DAY` | 20.0 | default | :244 |
| `KALSHI_STANDDOWN_VOID_MULT` | 0.5 | default | :245 |
| `KALSHI_STOPFLAT_REPEAT_S` | 1800.0 | default | :1253 |
| `KALSHI_STOP_ESCALATE_S` | 90 | default | :1816 |
| `KALSHI_STOP_TAKER_MIN_CT` | 5.0 | default | :1817 |
| `KALSHI_STRAND_CROSS_S` | 15.0 | SET | :1941 |
| `KALSHI_STRIKES_OUT` | 0 | default | :1039 |
| `KALSHI_SWEEP_VETO_TICKS` | 3 | default | :1962 |
| `KALSHI_TAKER_FLATTEN` | True | SET | :1670 |
| `KALSHI_TAKER_GOV_CROSSES` | 3 | default | :975 |
| `KALSHI_TAKER_GOV_LOSS_USD` | 1.0 | default | :976 |
| `KALSHI_TAKER_MAX_MKTS` | 8 | SET | :1671 |
| `KALSHI_THROTTLE_STEP_TICKS` | 1 | SET | :1619 |
| `KALSHI_TWO_STRIKES` | 1 | default | :1037 |
| `KALSHI_TWO_STRIKES_MEMORY_D` | 14 | default | :1038 |
| `KALSHI_VOL24_TTL_S` | 21600.0 | default | :1868 |
| `KALSHI_W12_PRICE_SHAPE` | 0 | default | :525 |
| `KALSHI_W12_SHAPE_EXP` | 1.0 | default | :526 |
| `KALSHI_WIND_DOWN_FRAC` | 0.2 | default | :1561 |
| `KALSHI_WIND_DOWN_MIN` | 45 | SET | :1555 |
| `KALSHI_WIND_DOWN_MIN_FLOOR` | 3.0 | default | :1562 |
| `KALSHI_WRITE_BUDGET` | 400 | SET | :1583 |

Part 2 (next): one line per knob naming the incident/study that created it, then the
deletion sheet -> operator decision. Typed venue-API layer: design after the ledger.
