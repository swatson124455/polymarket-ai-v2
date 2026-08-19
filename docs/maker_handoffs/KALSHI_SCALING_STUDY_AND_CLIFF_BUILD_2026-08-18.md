# SIZE-vs-ACCRUAL SCALING STUDY + CONCENTRATED-CLIFF BUILD PLAN (2026-08-18)

## The measured scaling curve (ESTABLISHED; script `workflow_scripts/size_scaling_study.py`)

Join: quotes-*.jsonl (our per-cycle resting ct/price/ref, 2026-08-05..08-12, 248,308
rows) x estimates tape (hourly cumulative accrual per program, 08-06+). Denominator:
272 ticker-hours with TWO-SIDED resting presence that joined to an accrual delta.

| our two-sided size at reference | ticker-hours | accrual $/day/market |
|---|---:|---:|
| 1-9 ct   | 164 | $0.20-0.29 |
| 10-29 ct | 43  | $0.17-0.58 |
| 30-59 ct | 65  | **$1.56 (full presence)**; best hours $4-8/day pace |

Scaling ~linear across 1-60ct. NO DATA >60ct (never rested bigger two-sided) —
any projection past 60ct is INFERRED-linear and so labeled.

## The analytic unlock (from this curve + the $1-cliff canon)

The historical failure shape is now fully explained by our own numbers: breadth
(~30-40 markets at effective 1-30ct each) put nearly every market's accrual UNDER
the per-market $1 cliff -> paid ~$0 (era-3: $9.80 over ~9 days) while fills bled.
The same capital concentrated at 40-60ct/market clears the cliff at the MEASURED
rate ($1.56/day). 8-10 such markets = $12-16/day gross on ~$300-600 deployed
(measured rates, no share model anywhere in the chain). Net depends on holding
fill costs in D3's survivable band (-2..-3.5c/ct classes); the toxic classes
(announcement, near-strike, data-release at touch) are excluded by selection.

## BUILD: CONCENTRATED-CLIFF MODE (on the existing quoter chassis, per T7 no-rewrite)

Config/code deltas (each reviewed + tested before relight; relight itself =
operator-named GO after shadow):
1. FOOTPRINT: cap ~8 markets (<=12 cancel-physics bound holds).
2. SIZE: per-market two-sided resting 40-60ct (within per-market $ caps).
3. SELECTION: series allowlist = D3-survivable slow-mechanical classes ONLY;
   measured-toxic classes hard-excluded; close<=8d LOCKED rule; program window
   >= 49h (cliff needs runway); prefer 3-7d program windows.
4. NEW CLIFF GATE: enter/hold a market only while projected accrual (measured
   curve x remaining window, later replaced by live est-feed pace) >= $1.50/program.
   Sub-cliff pacing at daily check -> cancel + reallocate (est-feed 3-state gate:
   stale -> FREEZE-AND-HOLD, never fire-sale).
5. reward_pnl LEAKAGE FIX: accrued-at-conclusion from tape history (closes the
   proven-vacuous latest-snapshot blindness).
6. SCOREBOARD RE-REGISTRATION for the new window (it still scores the dead 08-12
   window until edited) + new-window pre-registration (T0, PASS = counted credits >
   |position-aware drag|, per-EVENT same-set scoring, verdict at end+72h).
Unchanged: $10/day halt, post-only, re-quote-on-fill, STOP discipline, all kill
switches, KALSHI_LIVE_ARMED three-lock, deploy md5-vs-blob, suite exit codes.

Calendar: build+review 08-18/19 -> shadow -> relight ask (operator GO) -> run to
~08-25 -> payment reads 08-26/27 -> mandate decision 08-27.
