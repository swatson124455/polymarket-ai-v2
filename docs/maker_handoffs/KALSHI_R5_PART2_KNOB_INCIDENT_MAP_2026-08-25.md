# R5 PART 2 — KNOB -> INCIDENT MAP + DELETION SHEET (2026-08-25)

Companion to `KALSHI_R4_R5_STATUS_2026-08-25.md` (part 1 inventory: 145 quoter-pattern
knobs + 16 declared elsewhere; 81 set in live.env). §1 maps every knob FAMILY to the
incident/study that created it (citations = docs/commits in this repo + memory canon).
§2 is the DELETION SHEET — proposals only, every row is an operator decision (Rule Nine).
Appendix A is the mechanical per-knob origin extract (source comments, line-referenced).

## §1 Family -> originating incident (all knobs belong to exactly one family)
| family (knobs) | born from | citation |
|---|---|---|
| Flatten/naked safety: PRECLOSE_FLATTEN*, TAKER_FLATTEN, STRAND_CROSS*, SETTLE_* | riding naked inventory into settlement; the 55-min naked episode | build doc 07-24; `KALSHI_AUDIT_2026-08-21` fill event |
| Inventory doctrine: INV_TOLERANCE/SOFT/HARD, HOLDING-exit-only, REDUCE_ONLY_KEEP_BOTH | the 42-contract re-post compounding, KXNDQHUD 07-27 | Q1 decision 07-28 (code :3344 comment) |
| Re-entry: REENTRY_COOLDOWN_S, DROP_GRACE | strand-cross -> immediate re-join churn | self-audit F5 07-29 (code :940 comment) |
| Governors: loss-ladder v2, two-strikes, MKT_UNWIND_ALLOW_PER_CT, DD_CARRY, DAY_HALT | defect-4 unwind-spread trips; drips-are-fine ruling; money-ladder halt | operator rulings 08-01/08-02; roadmap 08-13 |
| Caps: MAX_MARKET/TOTAL/ACTIVATE_CAPITAL, SERIES_*, FOOTPRINT_TOP | F15 caps/halt section of the cliff build | `KALSHI_SCALING_STUDY_AND_CLIFF_BUILD_2026-08-18` |
| Reward-model gates: QUALIFIABLE, CAPTURE*, PRESENCE*, NETEV*, STANDDOWN*, MIN_CREDIT, W12* | each a distinct audit finding (07-22 CFTC read; 07-26 audit S3; 08-02 double-blind; 08-04 receipts; W10) | R3 canon + this session's D1/D4 chain |
| Runway/cliff: MIN_RUNWAY_H, RUNWAY_ACCRUED_EXEMPT (D2), OBS_HOLD/D3 ramp/rungs | R1 probe lifecycle + cliff build + 08-21 structural-contradiction audit | ladder memory; option-A 08-21 |
| D3 hold-state: REPAIR_*, EVENT_DELTA_DOLLARS/SOFT/HARD/FALLBACK | the 08-25 $0.80-mutes-earners incident | `KALSHI_D3_DESIGN_REPAIR_AND_DOLLAR_RISK_2026-08-25` |
| Selection: PIVOT_* (incl. FAR_FIRST, COVERAGE), SCORE_RANK, EXPLORE* | 0-quotes/70-cycles shadow measurement; slot-burn on gate-outs | 08-19 counter-studies + 3A ratification |
| Anchor: ANCHOR_EMPTY_SIDE/PRICE | 65%-of-checks one-sided blocker; v3 grid-fit | 08-24 anchor v1/v2/v3 review doc |
| Ops/infra: WRITE/READ budgets, THROTTLE_SMART, WS_*, MKT_TELEMETRY, blackout guard | rate ceilings, WS cold books, blind-fill protection | Stage B/C docs; ops-hardening 07-30 |
| AMEND_DECREASE | queue-preserving size decreases; endpoint UNVERIFIED live | code :367 comment; prior demotion is Rule-Ten quarantined -> status simply: OFF, unverified |

## §2 DELETION SHEET — operator decisions only (nothing removed until ruled)
| # | knob(s) | status today | delete? pro | delete? con | my input (not a ruling) |
|---|---|---|---|---|---|
| 1 | REDUCE_ONLY_KEEP_BOTH | dead code path since Q1 07-28; kept so =0 could restore old pairing | -1 knob, -1 dead branch | D3-A re-pair now provides the modern version of "earn while holding"; the old restore path is the last-resort rollback if D3-A misbehaves | keep until D3-A has a clean week, then delete |
| 2 | STANDDOWN + MIN_USD_DAY + VOID_MULT | built, never armed; CAPTURE (armed) is documented as the strictly-more-complete signal | -3 knobs | zero runtime cost while off; density-only signal could serve if capture's model is ever distrusted | operator call; no operational difference either way |
| 3 | NETEV_GATE family (5 knobs + table path) | off-for-mode (1A ratified); its table is defect-era (canon: never build on it) | -5 knobs + kills a known bad-table trap | the GATE code is sound — only the TABLE was bad; clean-era receipts could recalibrate it post-08-30 | keep through the 08-30 receipts; revisit with clean data |
| 4 | PRESENCE_GATE (+ presence table/calibrate) | OFF per 08-19 cliff ruling (void-scoped + refuted projection) | -2 knobs + removes a refuted model | MIN_CREDIT on the activate path lives behind it; deleting orphans that check | if deleting: first rehome the activate-path MIN_CREDIT check |
| 5 | AMEND_DECREASE | OFF; venue endpoint never exercised live | -1 knob | one cheap supervised live test would settle keep-vs-delete on facts | verify once live (supervised), then rule |
| 6 | Shadow/probe leftovers: ALLOW_PROBE_EXCEPTION, JOIN_ALWAYS drill, MACRO_PROBE* | drill/probe tooling, off in live | fewer arming-mistake surfaces | they are the test/probe toolkit; deleting removes diagnostic capability | keep; they are instruments not strategy |

Everything not listed in §2 is load-bearing per §1 and NOT proposed for deletion.

## Appendix A — mechanical per-knob origin extract (source comments, quoter @ HEAD)
### KALSHI_FOOTPRINT_TOP (:185)
(no comment block)
INLINE: markets quoted per cycle

### KALSHI_PER_SERIES_CAP (:186)
(no comment block)


### KALSHI_PIVOT_SELECT (:194)
> select_footprint over-selects a density-weighted, near-money-ordered candidate pool (larger than FOOTPRINT_TOP) and the quote loop PIVOTS past markets that gate out (return []) — pulling the NEXT eligible market into the slot — until FOOTPRINT_TOP markets are actually QUOTED or the pool is exhausted. The GATES are untouched: a gated (non-earning) market is still skipped, never quoted; pivot means quoting a DIFFERENT earner, not relaxing a gate.
INLINE: 0 = legacy select+quote (provable no-op)

### KALSHI_PIVOT_POOL_MULT (:195)
(no comment block)
INLINE: candidate pool = MULT * FOOTPRINT_TOP

### KALSHI_PIVOT_COVERAGE (:196)
(no comment block)
INLINE: min slots/series before density fill

### KALSHI_PIVOT_READ_RESERVE (:197)
(no comment block)
INLINE: reads held back (strand/ladder/settle)

### KALSHI_PIVOT_FAR_FIRST (:205)
actly backwards for a mode whose mid-band gate excludes near-money — every near-money candidate burns a pool slot + a book read and then gates out, so the per-series slots never reach the investable extreme strikes (measured: shadow 03:15-14:20Z quoted 0/70 cycles; first quotes only after traversal widening). 1 = extreme-strikes-first within each series (unparseable strikes still sort LAST); 0 (default) = today's near-money-first, byte-identical.


### KALSHI_JOIN_SIZE (:206)
(no comment block)
INLINE: contracts/side on non-void markets

### KALSHI_MIN_QUOTE_CT (:210)
REWARDS ARE PAID FOR QUOTES ON THE BOOK, NOT INVENTORY HELD. So BOTH sides must stay live every cycle — the throttle SHRINKS the accumulating side but never pulls it to zero (that would kill the reward on that side). This is the floor it shrinks toward.
INLINE: never quote a live side below this

### KALSHI_STANDDOWN (:243)
: flagship temp ~$1,920/day per strike, live gas ~$150/day, a dead day leaves only sub-$10/day dregs. The default $20/day sits with a wide margin BELOW live gas (so a normal reward-present day keeps quoting gas at full size — requirement: never forfeit the +EV gas lane) and ABOVE the dead-day dregs (so a genuinely dark regime sizes down). Tune against the ledger; because the flag ships OFF the default only takes effect once the operator flips it.
INLINE: 0 = today exact behavior, byte-for-byte

### KALSHI_STANDDOWN_MIN_USD_DAY (:244)
(no comment block)
INLINE: reward-density floor ($/day)

### KALSHI_STANDDOWN_VOID_MULT (:245)
(no comment block)
INLINE: R3 discount for one-sided books

### KALSHI_MAX_ACTIVATE_CAPITAL (:246)
(no comment block)
INLINE: $/void market

### KALSHI_QUALIFIABLE_GATE (:249)
1 = the CFTC-snapshot "unqualifiable -> never open" skip (today's behavior); 0 = bypass it (R1 refuted its premise: sub-target books DO accrue; see the call-site comment).


### KALSHI_ANCHOR_EMPTY_SIDE (:253)
ANCHOR (concentrated-cliff, operator-named 2026-08-24): create the missing side of a one-sided EXTREME book ourselves so the pair exists (see the gate in desired_quotes). Default 0 = provable no-op. ANCHOR_PRICE is the created side's own-scale price.


### KALSHI_ANCHOR_PRICE (:254)
(no comment block)


### KALSHI_MAX_MARKET_CAPITAL (:255)
(no comment block)
INLINE: $ cap per market (both sides)

### KALSHI_MAX_TOTAL_CAPITAL (:256)
(no comment block)
INLINE: $ cap on the whole resting book

### KALSHI_CAPTURE_GATE (:300)
rate against actual period-close credits via the capture_min_pc_usd_day telemetry. Ships OFF -> the default only bites once the operator flips it. vs STAND-DOWN (KALSHI_STANDDOWN, built, NOT deployed): that uses pool DENSITY only; THIS gate uses our actual SHARE x pool and is the more complete signal — the PRIMARY market-quality gate. Stand-down can stay OFF; the two compose harmlessly if both are on (stand-down shrinks size, this skips/reduces).
INLINE: 0 = today's exact behavior, byte-for-byte

### KALSHI_CAPTURE_MIN_USD_DAY (:301)
(no comment block)
INLINE: model $/day floor (see above)

### KALSHI_CAPTURE_DF (:302)
(no comment block)
INLINE: discount_factor_bps=5000 => 0.50 (live)

### KALSHI_MKT_TELEMETRY (:323)
trading cycle.  IT ALSO RUNS WHILE PARKED. The book is fetched and desired_quotes is evaluated for every footprint market BEFORE the capital cap gates the create, so at KALSHI_MAX_TOTAL_CAPITAL=1 every column is still measured, with our resting size honestly recorded as 0. That makes the competition denominator observable on markets we have NEVER quoted — including KXTEMP* the moment its hourly programs return — without resting a single contract.


### KALSHI_AMEND_DECREASE (:367)
e. Kalshi preserves queue position for a size DECREASE and nothing else, so routing just that case through amend is free time-on-book with no behavioural trade-off. Increases and reprices forfeit queue either way and deliberately keep the existing path. ⚠ The amend endpoint is UNVERIFIED against the live venue (exercising it would mutate real resting orders on a parked account), which is why this ships OFF and its first live cycle needs watching.


### KALSHI_DROP_GRACE (:382)
ll teardown and lose queue position for a market we still want.  NARROW BY DESIGN — grace applies ONLY when the ticker is absent from this cycle's FOOTPRINT (we never looked at it). It does NOT apply when the market WAS looked at and something rejected it: a gate, the capital cap, the breaker, wind-down. Those are decisions, and retaining through a decision would defeat the cap that made it. Grace is for "we didn't check", never for "we said no".
INLINE: cycles a rotated-out ticker keeps its book

### KALSHI_SCORE_RANK (:404)
cycle ranks on them. EXPLORE reserves slots for never-seen markets so the venue keeps being swept; without it the bot converges on whatever it read first and never discovers anything better. Scores DECAY toward the pool prior so a stale winner cannot pin it.  SWING PENALTY: a market whose reference price moves between cycles fills us adversely — that is how a maker hands the rewards back. ref_move discounts the score and costs nothing to collect.


### KALSHI_SCORE_EXPLORE (:405)
(no comment block)
INLINE: slots/cycle reserved for unscored markets

### KALSHI_SCORE_SWING_PENALTY (:406)
(no comment block)


### KALSHI_SCORE_UNKNOWN_BONUS (:407)
(no comment block)


### KALSHI_SCORE_PATH (:408)
(no comment block)


### KALSHI_INCUMBENCY_BONUS (:413)
INCUMBENCY BONUS (operator slate item A, 2026-07-29, weighted heavily by operator): a market we rested in LAST cycle keeps its seat unless a challenger beats it by this margin — the queue position built by sitting is an asset, destroyed on exit. Value is PROVISIONAL (HYPOTHESIS) until the Aug 1-2 receipts price a seat; sunk losses buy no loyalty (loss governor's job).
INLINE: 0 = OFF (provable no-op)

### KALSHI_EXPLORE_PROBE_CT (:418)
EXPLORE PROBE SIZING (operator slate item E: "$2 bopping around"): exploration slots get probe-sized accumulating orders instead of full _capped_join size, so sampling a market costs a few dollars of collateral, not an earner's full allocation. 0 = OFF (full size).


### KALSHI_CAPRANK_TELEMETRY (:447)
ered 2026-07-29 (task #1), TELEMETRY-FIRST: the current rank is blind to dollars committed per market and to per-market realized fill cost. This block only LOGS the would-be capital-aware ordering alongside the actual one (caprank-YYYYMMDD.jsonl, one row per cycle) so the operator can review the divergence on real cycles. Selection is UNTOUCHED — flipping the live rank to cap_score is a separate, operator-named change. See kalshi_capital_rank.py.


### KALSHI_CAPRANK_CALIB (:450)
receipt-vs-model calibration multiplier on the capture term. STAYS 1.0 until the first real reward credit lands (Thu 2026-07-31 ballot window) — receipts > models.


### KALSHI_CAPRANK_RISK_LAMBDA (:454)
RISK-AVERSION knobs (operator ask 2026-07-29) — shadow-only, defaults 1.0 = prior behavior: lambda multiplies the measured fill-cost penalty; the haircuts discount evidence quality (prospective = book measured offline but our join hypothetical; unknown = pure pool guess).


### KALSHI_CAPRANK_PROSPECTIVE_HAIRCUT (:455)
(no comment block)


### KALSHI_CAPRANK_UNKNOWN_HAIRCUT (:456)
(no comment block)


### KALSHI_FILL_COST_PATH (:457)
(no comment block)


### KALSHI_PROSPECTIVE_PATH (:459)
(no comment block)


### KALSHI_D2_FEEDBACK (:491)
settlements); the rank multiplies a "paid" series' base by D2_BONUS and a filled-never-paid-and-due series' base by D2_NEVERPAID_MULT. Sweep evidence (w3_policy_sweep over 6 recorded days, 2026-08-04): the never-paid penalty is the working lever — never-paid median rank 16-25 -> 31-36 while payer ranks improve; the bonus alone moves almost nothing. Enabling is a separate operator-named deploy after the P2 clean days (ruling 2026-08-04, option a).


### KALSHI_D2_BONUS (:492)
(no comment block)


### KALSHI_D2_NEVERPAID_MULT (:493)
(no comment block)


### KALSHI_CREDIT_FEEDBACK_PATH (:494)
(no comment block)


### KALSHI_W12_PRICE_SHAPE (:525)
 alone would NOT have capped the NETFLIX loss (the market was ~109 min old at the fill, top rung); the history clamp is the piece that would have (rung 1 = 10 ct -> ~1/3 the damage). A MISSING feedback table therefore clamps DOWN, not open — for a risk limiter the conservative direction is smaller size, the exact opposite of the estimator-fail-open doctrine, and deliberate. Unwind quotes are NEVER ramped (de-risk is never gated — house doctrine).
INLINE: 0 = OFF (provable no-op)

### KALSHI_W12_SHAPE_EXP (:526)
(no comment block)
INLINE: P2-receipt calibration knob

### KALSHI_ALLOW_PROBE_EXCEPTION (:535)
(no comment block)
INLINE: 0 = allowlist absolute

### KALSHI_PROBE_MAX_SLOTS (:536)
(no comment block)
INLINE: concurrent probe markets, "as small

### KALSHI_D3_RAMP (:541)
as you can to get what is needed" (operator 2026-08-05): 5 probes x EXPLORE_PROBE_CT=5ct bounds discovery exposure to ~$12 notional worst-case while each probe stays big enough for its accrual to clear the venue's $1 credit floor (a smaller probe on a 100-ct-target book earns a share too small to ever pay, which reads as a false "never pays").


### KALSHI_OBS_HOLD (:557)
rget hazard. Stale/missing feed -> held (fails CLOSED toward smaller size, the D3 doctrine) — but ONLY inside the fresh window, so a dead recorder can never deflate an established book. ⛔ DO NOT ARM until the floor-separation validation passes on a PAID basis (pre-registered in KALSHI_HANDOFF_2026-08-10_POST_INCIDENT.md §10): the accrued->paid sensor over-predicted 2x once (KXAPRPOTUS 0.481) and 0/16 held-class tickers ever reached $1.20 accrued.


### KALSHI_OBS_HOLD_MIN_USD (:558)
(no comment block)


### KALSHI_OBS_HOLD_FRESH_S (:559)
(no comment block)


### KALSHI_OBS_HOLD_MAX_RUNG (:560)
(no comment block)


### KALSHI_D3_RUNG_S (:561)
(no comment block)


### KALSHI_D3_NEWSERIES_MAX_RUNG (:562)
(no comment block)
INLINE: -1 disables the clamp

### KALSHI_D3_KEEP_S (:628)
(no comment block)
INLINE: F14: ramp memory survives absences up to this

### KALSHI_MACRO_PROBE_USD (:731)
(no comment block)
INLINE: reserved-$ cap per market (both sides)

### KALSHI_MACRO_PROBE_TOP (:732)
(no comment block)
INLINE: top ladder level, dollars

### KALSHI_EST_FEED (:781)
(no comment block)


### KALSHI_EST_FEED_MAX_AGE_S (:782)
(no comment block)
INLINE: ignore snapshots older

### KALSHI_EST_FEED_MIN_FRAC (:783)
(no comment block)
INLINE: review #3: the estimate is

### KALSHI_MKT_DAY_LOSS_EXITONLY_USD (:943)
ating quotes are stripped and the diff cancels any resting ones. Receipt-based (churn/mark immune, same doctrine as the cost ratchet). Trip latches for the day even if the row vanishes on full flat. REENTRY_COOLDOWN_S  after a strand taker-cross on a ticker (we PAID to leave), the ticker is exit-only for this many seconds — a book that just ran us over must not be rejoined one cycle later. Persisted in quoter_state so a restart cannot amnesty it.
INLINE: 0 = OFF

### KALSHI_MKT_OUT_LOSS_USD (:952)
 a strike; any day its realized day-loss reaches MKT_OUT_LOSS_USD (live $5) it is OUT -- permanent, prune-exempt, persisted in quoter_state['mkt_out']; only an operator clearing the entry (or market close) ends it. Markets banned under the earlier one-strike rule were grandfathered into mkt_out at the first cycle this shipped. Count-based strike bans are now OFF by default (STRIKES_OUT=0); the knob remains for the operator's 2026-08-03 re-review.


### KALSHI_MKT_UNWIND_ALLOW_PER_CT (:967)
08-02 loss sample, INFERRED from the session doc, not a canonical script) plus ~60% margin. It is capped at the DERIVED rung gap (MKT_OUT_LOSS_USD - MKT_DAY_LOSS_EXITONLY_USD = $2.00 live) so the allowance can never span more than one rung, and at INV_HARD_CT=50 the cap binds exactly. Set to 0 for a pure live-delta rung 2 (which would repeal the M3 directive for markets tripping between -$4 and -$5, where one unwind's spread can carry them over).


### KALSHI_TAKER_GOV_CROSSES (:975)
= -TAKER_GOV_LOSS_USD -> exit-only for the day + strike. Encodes the measured era fingerprint (repeatedly PAYING to leave = the toxicity receipt: -$176.01 of -$182.06 era realized on taker legs; this compound trip would have saved $98-122 of that era, COMPUTED). Counter is incremented at the single point every paid exit passes (_taker_cross_capped), mirrored in quoter_state['mkt_taker_xn'], reset at the UTC day roll. TAKER_GOV_CROSSES=0 disables.


### KALSHI_TAKER_GOV_LOSS_USD (:976)
(no comment block)


### KALSHI_TWO_STRIKES (:1037)
rket trip dates; entries BELOW the OUT threshold are pruned at TWO_STRIKES_MEMORY_D so the file stays bounded). Rides the same governor: inert unless MKT_DAY_LOSS_EXITONLY_USD > 0. Born 2026-07-31 00:00-02:32Z: the midnight reset re-admitted all five of yesterday's tripped markets and they burned another ~$10.6 in 2.5h (fills API); tightened to 1 the same day after MLABELSHARE (-25.76 venue realized) showed a single day can cost 5x the threshold.


### KALSHI_TWO_STRIKES_MEMORY_D (:1038)
(no comment block)


### KALSHI_STRIKES_OUT (:1039)
(no comment block)
INLINE: 0 = count-based bans OFF (E ladder rules)

### KALSHI_INCUMBENT_ONLY (:1050)
 quotes — accumulating quotes are stripped at the same choke point the loss governor uses, so selection, scoring, sizing, and exits are all byte-identical ("keep all else equal"). Held inventory always unwinds; new markets simply never open. Flipping OFF clears the captured set, so the next enable re-captures fresh. HOT-RELOADABLE (in _refresh_safety_knobs' watch list) — the directive's original blocker was that no selection knob could hot-apply.


### KALSHI_SELECT_BUDGET (:1065)
n it does, budget_backstop_fired alarms. MARGIN covers the commit model's measured ~30% under-read (study §3) — tune from the alarm counter. Default 0 = byte-identical (test-pinned). Both knobs hot-reloadable. DEPENDENCY: est refs come from the SCORE_RANK cache — with SCORE_RANK=0 every ref is unknown and est pins at the 0.50/0.50 maximum (conservative: footprint ~limit/max_est markets). Run this with SCORE_RANK=1 (live config) for honest sizing.


### KALSHI_SELECT_BUDGET_MARGIN (:1066)
(no comment block)


### KALSHI_REENTRY_COOLDOWN_S (:1246)
(no comment block)
INLINE: 0 = OFF

### KALSHI_STOPFLAT_REPEAT_S (:1253)
 re-ran the FULL flatten — cancel, re-offset, sleep, and a fresh tries=4 taker burst with a fresh slippage anchor — indefinitely, on any residual that would not fill (the C5 review named this "a metronomic taker fire-sale" and fixed only the order-id collision). The first invocation still flattens IMMEDIATELY; repeats are spaced at least this far apart (the maker offsets it rested stay working the whole time). 0 = legacy every-heartbeat behavior.


### KALSHI_HALT_CONFIRM_N (:1260)
ger arrived in the 90s after a 60-ct fill on a thin book — a mark blip supplied the push over the arm, and the halt then crystallized it by selling into the same thin book. The breach must now HOLD for N consecutive cycles (~15-30s at daemon cadence) before the STOP is written: a one-tick paper mark cannot shut the book, a real crash still halts in <30s. The streak resets the moment equity recovers inside the arm. 1 = legacy fire-on-first-breach.


### KALSHI_PRESENCE_GATE (:1265)
stamp path is computed AT USE (not import): freezing it at import is the exact F17 import-once class this same audit flagged — and it broke the test harness, which redirects DATA_DIR per test. Sidecar file because the STOP branch runs pre-state.
INLINE: 0 = today's exact behavior

### KALSHI_MIN_CREDIT_USD (:1267)
(no comment block)
INLINE: venue floor + 20% modelling margin

### KALSHI_PRESENCE_DEFAULT (:1268)
(no comment block)
INLINE: no table -> assume perfect execution

### KALSHI_NETEV_GATE (:1415)
ll-fingerprint > 0, else unproven-skip. Labelled model-not-receipt. Void/activate books are scoped OUT (as in the capture gate). This SUPERSEDES the pool-only KALSHI_STANDDOWN (density only) and the reward-only KALSHI_CAPTURE_GATE (model capture only): both answer a strictly weaker question. They compose harmlessly if co-enabled (each only skips/shrinks further), but net-EV is the complete signal. Ships OFF -> the default only bites once flipped.
INLINE: 0 = today's exact behavior, byte-for-byte

### KALSHI_NETEV_MIN_MARGIN_PCT (:1416)
(no comment block)
INLINE: family net% floor (0 => skip net-negative)

### KALSHI_NETEV_MODEL_HAIRCUT (:1417)
(no comment block)
INLINE: §M7 model over-prediction haircut (unproven)

### KALSHI_NETEV_FINGERPRINT_USD_DAY (:1418)
(no comment block)
INLINE: conservative fill cost, unproven series

### KALSHI_NETEV_TABLE (:1419)
(no comment block)


### KALSHI_FUNDING_GATE (:1518)
an free cash funds, so no overdraw either way; if `balance` turned out NET the worst case is a re-freeze (revert the flag), never a blowup. VENUE ASSUMPTION (state it, do not silently rely on it): Kalshi reserves cash at FILL, not at placement (GROSS) — measured n~4 place/cancel with balance delta 0 (KALSHI_RUNNING_TAB.md 07-20, kalshi_attribution_ledger.py:436). Rootfix design: docs/maker_handoffs/KALSHI_CAPITAL_ACCOUNTING_ROOTFIX_2026-07-23.md.
INLINE: 0 = legacy gross+held gate; 1 = free-cash funding gate

### KALSHI_MAX_PRICE_DOLLARS (:1519)
(no comment block)
INLINE: never OPEN a bid above this

### KALSHI_MIN_PRICE_DOLLARS (:1520)
(no comment block)
INLINE: never OPEN a bid at/below this

### KALSHI_EXIT_MAX_PRICE_DOLLARS (:1524)
EXIT bounds are the VENUE's, not the strategy's (see _ok_exit_price). A reducing order must not be refused for being expensive — that is MAX_UNWIND_LOSS's job — only for being unacceptable to Kalshi (valid range 0.01-0.99 inclusive).


### KALSHI_EXIT_MIN_PRICE_DOLLARS (:1525)
(no comment block)


### KALSHI_WIND_DOWN_MIN (:1555)
(no comment block)
INLINE: pull quotes N min before end

### KALSHI_WIND_DOWN_FRAC (:1561)
 audit 2026-08-06, operator-ruled): the ABSOLUTE 45-min wind-down forfeited ~78% of a sub-hour program window (58-min hourly temp: enterable ~13 min, quoted-down 45) — C13 fixed exactly this over-coverage for the ramp (RAMP_LIFE_FRAC) but not here. The wind-down is now proportional for short windows: min(WIND_DOWN_MIN, WIND_DOWN_FRAC x window), floored at WIND_DOWN_MIN_FLOOR so we always stop entering before the very end. Long programs unchanged.


### KALSHI_WIND_DOWN_MIN_FLOOR (:1562)
(no comment block)


### KALSHI_WRITE_BUDGET (:1583)
(no comment block)
INLINE: order-ops ceiling/cycle

### KALSHI_JOIN_ALWAYS (:1584)
(no comment block)
INLINE: drill switch (default off)

### KALSHI_THROTTLE_STEP_TICKS (:1619)
count_factor_bps=5000 => 0.50. So stepping the accumulating side 1 tick inside HALVES that side's credit — and can zero it outright, because the qualifying walk stops once the book reaches Target Size: a quote one tick back can fall out of the scored set entirely. Set 0 to keep the accumulating side AT reference (full credit) and throttle by SIZE alone. Default 1 = existing behaviour; this is a knob to A/B against the ledger, NOT a silent change.


### KALSHI_THROTTLE_SMART (:1625)
ART-STEP (default OFF): skip the price step when the top level alone already meets Target Size, because the sandbox A/B measured the step ZEROING our credit in 12% of such snapshots. DEFAULT OFF ON PURPOSE: it puts the accumulating side back AT reference, which is exactly the placement the live A/B measured as ~tripling naked-inventory build. The reward gain is measured; the risk cost of THIS narrower version is NOT. Enable only to run that test.


### KALSHI_INV_SOFT_CT (:1637)
D (operator Q1 decision, 2026-07-28): KALSHI_REDUCE_ONLY_KEEP_BOTH kept the accumulating side alive (floor-sized) on HELD markets under the breaker. That is a direct contradiction of the holding => exit-only risk rule, and the live 07-27 tape showed the shape it enables (KXDXYDUD flipped -20 -> +17 THROUGH flat under reduce-only). The old pairing is in git history (`0a86b2b`, removed with the flags in this commit); revert is the revert mechanism.


### KALSHI_INV_HARD_CT (:1638)
(no comment block)


### KALSHI_INV_TOLERANCE (:1653)
--- taker de-risk BACKSTOP (the ONLY place the bot pays a taker fee) --- Passive maker-unwind (above) is PRIMARY. This last-resort crosses the spread ONLY to GUARANTEE flat when passive can't: near settlement (carry no delta into resolution) or a hard inventory breach (passive not keeping up in a one-way drift). Tunable to OFF.
INLINE: < this many ct == "flat"

### KALSHI_SETTLE_UNWIND_MIN (:1654)
(no comment block)
INLINE: taker-flatten if settlement within N min

### KALSHI_TAKER_FLATTEN (:1670)
(no comment block)
INLINE: last-resort enabled (set 0 = never)

### KALSHI_TAKER_MAX_MKTS (:1671)
(no comment block)
INLINE: cap taker-flattens per cycle (rate/cost guard)

### KALSHI_RAMP_MIN (:1676)
--- SETTLEMENT RAMP (audit HIGH-2): the settlement taker fires into the WORST liquidity, so the design goal is to BE SMALL at settlement, making that taker a rare backstop. Within RAMP_MIN of market end the ACCUMULATING quote sizes scale down linearly toward MIN_QUOTE_CT (reducing/unwind quotes are NOT ramped — de-risking gets easier, adding gets harder).
INLINE: start shrinking N min before end

### KALSHI_RAMP_LIFE_FRAC (:1682)
N over-covers SHORT markets: a ~58-min hourly temp market is younger than 180 min for its whole life, so it would rest at the ramp floor (2-4 ct) from birth — near-zero reward on the flagship temp lane (review C13). Cap the effective ramp per-market at a FRACTION of THAT market's own program lifetime (computed in select_footprint) so the ramp only bites in the final stretch of short markets while long gas markets still get the full 180-min taper.


### KALSHI_LATE_LIFE_FRAC (:1689)
 one-way informed market — the outcome is nearly known, resting bids get adversely lifted (hourly temp at 10:30pm: the day's max temp already happened). NEVER *enter* (footprint) a market past LATE_LIFE_FRAC of its OWN life; a held position on such a market unwinds via the strand path (reduce-only). For long-lived markets the fraction over-blocks, so the cutoff is capped at MAX_ENTRY_CUTOFF_MIN absolute (e.g. gas daily: no entry in the final 2h).


### KALSHI_MAX_ENTRY_CUTOFF_MIN (:1690)
(no comment block)


### KALSHI_MAX_DAYS_TO_CLOSE (:1705)
s the daily pool by 30 days. So this is a hard structural cap, independent of any calibration file being present. Deliberately conservative for now — the directive is to gather results and data on short markets first and ramp the horizon up later. Held inventory in an excluded market is NOT stranded: it falls through to the STRAND UNWIND path, which rests the reducing side at reference so the position still flattens passively. 0 disables the cap.


### KALSHI_MIN_RUNWAY_H (:1712)
9; the 08-13 roadmap's LOCKED "window >= 49h" entry rule, learned live in R1: a program placeable at read time can be un-placeable by GO time). Hours of PROGRAM runway (m["end"] - now) a market must have left for a FRESH entry; 0 = gate off (today's exact behavior). Enforced in desired_quotes, entry-only and resting-aware — NOT a footprint drop (a footprint drop would evict resting markets at end-49h and forfeit the accrual tail; section review).


### KALSHI_RUNWAY_ACCRUED_EXEMPT_USD (:1718)
D2 (operator-approved 2026-08-25): a program the venue's own estimate feed shows ALREADY ACCRUING for us above this floor has PROVEN it earns inside its remaining window — refusing RE-ENTRY there forfeits the accrual tail (measured cost: ~1.6d of DIESELW-26AUG24, 08-21). Ended-program rows never reach the table (_est_feed_cached skips them) and the feed lags its recompute batches (sweep F2), so the trigger is conservative. 0 = exemption off.


### KALSHI_REPAIR_CHEAP_FILL (:1726)
<= REPAIR_BASIS_MAX_D, ALSO rest the consumed (accumulating) side beside the exit — the official LIP terms pay per-side shares in a qualifying book, so exit-only forfeits ~the accumulating side's share for the whole holding period. Reached ONLY after every entry gate has passed (poor/unqualifiable/banned markets return reduce-only from their own gates first); max added downside per round = basis x count <= 0.02 x join. Default 0 = byte-identical.


### KALSHI_REPAIR_BASIS_MAX_D (:1727)
(no comment block)
INLINE: $/ct; 07-27 class (0.30-0.40) stays exit-only

### KALSHI_EVENT_DELTA_DOLLARS (:1734)
 so 40ct of $0.02-basis inventory ($0.80 bounded loss) muted sibling earners exactly like 40ct at $0.35 (live 2026-08-25 16:1xZ: T5.82 gate_event_directional off T5.42's $0.80 position). With the flag on, ev = sum(inv_ct x basis $/ct) and the thresholds below apply; defaults = the contract thresholds x the 07-27 incident's ~$0.35 basis, so the mid-band class that motivated the throttle keeps today's protection exactly. Default 0 = byte-identical.


### KALSHI_EVENT_SOFT_USD (:1735)
(no comment block)
INLINE: = INV_SOFT_CT 15 x $0.35

### KALSHI_EVENT_HARD_USD (:1736)
(no comment block)
INLINE: = INV_HARD_CT 50 x $0.35

### KALSHI_EVENT_FALLBACK_BASIS_D (:1737)
(no comment block)
INLINE: unknown basis -> legacy parity

### KALSHI_PAIR_BOTH_SIDES (:1767)
commit; behaviour flags deliberately NOT recreated). HOLD BOTH SIDES (2026-07-26). The block's own design comment is "shrink the accumulating side, grow the reducing side, both stay live" — but the reducing side was sized min(|inv|, room) off the NOMINAL join, so at inventory below INV_SOFT_CT (where the throttle never fires) it rested ADD=100 vs RED=8 and a double fill left us +100. Measured across regimes. 0 restores that legacy sizing exactly.


### KALSHI_BREAKER_HELD_GROWTH_USD (:1773)
--- VELOCITY CIRCUIT BREAKER (2026-07-22 live loss): held-$ grew $0->$28 in 3 cycles of 'cycle ok' — adverse accumulation is invisible to plumbing telemetry. If held cost grows more than BREAKER_HELD_GROWTH_USD within BREAKER_WINDOW_S, the WHOLE book goes REDUCE-ONLY (only 'unwind' quotes survive; accumulating quotes are cancelled by the diff) until the growth condition clears. Generic backstop for every toxicity mode not yet imagined.


### KALSHI_BREAKER_WINDOW_S (:1774)
(no comment block)


### KALSHI_HELD_MAX_USD (:1781)
never lose more than the reward"): total unpaired held cost is the ONLY uncapped loss channel left (pairs capped at MAX_UNWIND_LOSS, taker off). Above HELD_MAX_USD the whole book goes REDUCE-ONLY until it drains — sized so the worst-case settlement loss on any day is about one day's measured rewards (~$20 receipt rate). LEVEL trigger, complementing the velocity trigger above; can overshoot by at most one cycle's small quote sizes before it bites.


### KALSHI_DAILY_LOSS_HALT_USD (:1794)
LY_DOWN_HALT_USD, the 07-22 "treadmill" guard) — was REMOVED BY OPERATOR ORDER 2026-08-02 after the 08-02 halt post-mortem: 34.51% of its $68.68 reading was a torn-read accounting artifact, and it never netted recoveries. KNOWN ACCEPTED GAP: a realize-loss/recover-via-credits cycle no longer accumulates toward a halt; only the drawdown arm stands. The equity snapshot itself is torn-read-proof — see the consistency re-read in the meter (run_once).


### KALSHI_DD_CARRY (:1803)
velope while already down (measured live 2026-08-09: dd $3.00 at 23:58:18Z -> $0.00 at 00:00:09Z, $7.55 of slide erased). Shipping this OFF would leave that hole open every single midnight, which is the opposite of a safe default. Set KALSHI_DD_CARRY=0 to restore the old forgive-at-baseline behaviour. The carry DECAYS as equity recovers — a debt, not a penalty — so it can only make the halt fire EARLIER on a bleed that is still open, never later.


### KALSHI_STOP_ESCALATE_S (:1816)
--- STOP ESCALATION (audit HIGH-1): pure-maker STOP can leave you hanging (offsets may never fill); pure-taker STOP is a fire-sale. STOP = maker-first with BOUNDED escalation: rest the offsets, wait, re-check, and taker-cross ONLY what is still material after the wait.
INLINE: seconds passive offsets get to fill

### KALSHI_STOP_TAKER_MIN_CT (:1817)
(no comment block)
INLINE: escalate only if |pos| still >= this

### KALSHI_FLATTEN_MAX_SLIP (:1824)
p hit whatever the touch was after each pass, 4 tries deep — live 2026-07-27 the STOP escalation walked KXDXYDUD 0.52 -> 0.50 -> 0.46 -> 0.25 in ~2s, selling 23 ct at 0.25 that settled at 1.00 the next day. A pass whose touch has moved more than this many DOLLARS against us from the FIRST pass's touch is refused; the residual keeps/regains its maker exit and later passes (next cycle / strand clock) retry from the fresh book. 0 disables the bound.


### KALSHI_CLOSE_CACHE_POS_TTL_S (:1837)
B-3 (identity review, operator "go" 2026-08-06): POSITIVE entries used to live forever, but the venue can amend close_time (early determination / extension) — re-verify every 6h. With state persistence (B-2) the TTL also bounds how stale a restored clock can be.


### KALSHI_MAX_VOL24H_CT (:1867)
HIGH-ACTIVITY FIRST GATE (operator-named 2026-08-02, coarse v1 — review later): skip markets whose venue-wide 24h traded volume exceeds MAX_VOL24H_CT contracts. Crowded/hot markets are where adverse fills live and where our share of the reward pool is smallest; the ideology targets low-to-moderate activity. Volume piggybacks on the close-time read (zero extra reads on a miss; TTL'd so activity spikes are seen within VOL24_TTL_S).
INLINE: 0 = gate OFF (provable no-op)

### KALSHI_VOL24_TTL_S (:1868)
(no comment block)
INLINE: re-read activity every 6h

### KALSHI_PRECLOSE_FLATTEN (:1922)
l design left the resting maker exit alone ("the taker is additive") on a no-self-trade argument — but self-trade was never the risk. Live 2026-07-27 19:40:03Z: a taker crossed KXNDQHUD flat, and the un-cancelled 41ct@0.73 exit filled 7 SECONDS LATER (+40.55 ct) — the moment the position died, the "exit" became a naked ENTRY. Never-strand is now provided by the RE-REST leg (a failed/partial cross re-rests the maker exit), not by never cancelling.
INLINE: 0 = OFF, provable no-op until flipped

### KALSHI_PRECLOSE_FLATTEN_MIN (:1923)
(no comment block)
INLINE: act within N min of MARKET CLOSE

### KALSHI_STRAND_CROSS_S (:1941)
 run. Default 15s — OPERATOR-CONFIRMED 2026-07-28 ("ok proceed with 15s and we can adjust"), revised down from the 30s proposal on the operator's read that a spike that matters persists: the one live chain (07-27 KXNDQHUD) ran 0.60 -> 0.66 in 32s, 0.70 at 47s, and never came back, so a shorter wait exits cheaper in exactly the trends that hurt. Effective exit latency is this clock + up to one cycle (~5-8s live). 0 disables the mechanism entirely.


### KALSHI_EXIT_LADDER_STEPS (:1952)
se the resting maker exit is IMPROVED one tick per strand period (EXIT_LADDER_STEPS periods max) — giving up a tick beats paying half the spread + fee on mean-reverting books, and the era receipts put -$176.01 of -$182.06 realized on taker legs. Bounded time is preserved: worst case adds EXIT_LADDER_STEPS * STRAND_CROSS_S seconds before the taker backstop fires. EXIT_LADDER_STEPS=0 restores the legacy cross-at-first-strand behavior byte-for-byte.


### KALSHI_EXIT_CHEAP_CROSS_USD (:1953)
(no comment block)


### KALSHI_SWEEP_VETO_TICKS (:1962)
t us (>= SWEEP_VETO_TICKS)      -> SPIKE: defer this pass entirely (a whale sweep's worst prints revert; measured ~$6.7 lost buying 35-ct slices of 700-1200 ct sweeps). Deferral is bounded: it cannot repeat consecutively. two CONSECUTIVE fast moves                          -> TREND: cross IMMEDIATELY, skip the maker ladder (patience in a real trend is how -$5-in-44s happens). SWEEP_VETO_TICKS=0 disables both arms (byte-identical strand behavior).


### KALSHI_MAX_SPREAD_TICKS (:1965)
--- selection: prefer BALANCED books (maker-unwind fills) over one-sided drift traps ---
INLINE: skip wide/illiquid books

### KALSHI_MIN_DEPTH_SYM (:1966)
(no comment block)
INLINE: min(depth)/max(depth) both sides

### KALSHI_REQ_SPACING_S (:1975)
et cycle spends ~22s sleeping here vs ~5-10s on actual network, i.e. our own throttle is ~2x the entire round trip and is applied 40-200x/cycle. Token math (Basic tier, ~100 tok/s): reads bill far less than the create=10 tok writes, and 0.55s is only ~1.8 reads/s, so there is large headroom — but the exact read token cost is NOT documented anywhere we have verified, so LOWERING THIS IS AN OPERATOR DECISION, not a default. Measure 429s if changed.


### KALSHI_READ_BUDGET (:1976)
(no comment block)


### KALSHI_FILLCOST_REFRESH_S (:2812)
(no comment block)
INLINE: 0 = off

### KALSHI_FARCLOSE_PAYING_EXCEPTION (:3503)
GRAM window, and a program's expiry evicts its rows from the harvest so strand-unwind flattens holds. With the flag on, a row is kept past the market clock ONLY when its series has venue credit RECEIPTS (credits_n>0 -- the same proof the W7 ramp trusts) AND its program window ends inside MAX_DAYS_TO_CLOSE. UNPROVEN series keep the hard rule (receipts are the criterion, allowlist membership is not -- a once-paid probe series qualifies, review F3).


### KALSHI_ALLOC_INCUMBENT_FIRST (:3858)
extinguish then slowly build new markets with new rules" — BUILT, NOT ENABLED). When ON, cap_desired funds markets we are ALREADY standing in before any new entrant, so a rank/allocation-rule change can phase in without ripping up queue positions: incumbents keep their dollars until their reward windows close; freed capital then enters under whatever key orders the non-incumbent group. Ships OFF (default 0) => cap_desired ordering byte-identical.


### KALSHI_SERIES_MAX_USD (:3867)
y and is live-set to 100). Max accumulating dollars per series (ticker family); a sibling that would push its family past the cap is SKIPPED (capital flows on to the next family — unlike the total cap's tail-cut). Reducing (unwind) orders are NEVER blocked, but their dollars DO count toward the family total, so a heavy family stops accepting new siblings first — the conservative direction. 0 = OFF (default) => cap_desired behavior byte-identical.


### KALSHI_SERIES_PCT (:3874)
-07-31 "25% capital per family at a time"): the per-family budget is SERIES_PCT of live capital (the portfolio-tracking equity the total cap uses), with env SERIES_MAX_USD kept as a static ceiling when set. VERIFIED in the entry formula: cap_desired skips a sibling BEFORE any create when the family budget is full, and families are seeded with HELD dollars, so fills never reopen headroom. SERIES_PCT=0 falls back to the static SERIES_MAX_USD alone.


### KALSHI_PAIR_UNWIND (:3887)
mbined proceeds beat the $1 settlement floor: (1-pn_A)+(1-py_B) >= 1 + MIN_EDGE. Each leg rests at its own inside (1 - opposite best bid = that leg's touch), so a lone fill leaves the partner NAKED at a touch-priced sale — the normal naked machinery (skew, strand clock) takes over, bounded. Orders carry reason='unwind': every polarity-aware gate (capital-cap keep, probe clamp, exit-only strips, breaker shape) already treats them as risk-reducing.


### KALSHI_PAIR_UNWIND_MIN_EDGE (:3888)
(no comment block)


### KALSHI_ALLOC_KEY (:3917)
wer for NEW dollars, and ALLOC_RISK_LAMBDA > 1 punishes proven burners harder than their average loss — variance- aversion priced into allocation, not just observed. Fail-OPEN: any fault in scoring falls back to the pool dict (legacy ordering) and counts _SILENT["alloc_key_fail"]. Ships OFF (default 0) => every consumer receives the pool dict, byte-identical behavior. Enabling is a separate operator naming AFTER receipts set KALSHI_CAPRANK_CALIB.


### KALSHI_ALLOC_RISK_LAMBDA (:3918)
(no comment block)


### KALSHI_ALLOC_PROSPECTIVE_HAIRCUT (:3919)
(no comment block)


### KALSHI_ALLOC_UNKNOWN_HAIRCUT (:3920)
(no comment block)


### KALSHI_ALLOC_PCAP_MAX_AGE_S (:3923)
Sweeper pcap older than this is NOT fed into the key (freshness plan: rank correlation is decision-grade to the 6-12h band, measured 2026-07-30; receipts may re-fit this).


### KALSHI_BLACKOUT_CANCEL_AFTER (:4073)
(no comment block)
INLINE: consecutive blind cycles

### KALSHI_BLACKOUT_RETRY_BASE_S (:4080)
(no comment block)


### KALSHI_BLACKOUT_RETRY_MAX_S (:4081)
(no comment block)

