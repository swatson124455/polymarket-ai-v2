# MB LIVE-CAPITAL GO CHECKLIST (operator-ruled 2026-09-06: "fix the gaps
# you named before go" -> "build all 5")

A funnel PASS produces a PROPOSAL. No real dollar moves until every line
below is green or explicitly struck by the operator IN WRITING. The first
PASS does not override this checklist; composition sign-off consumes it.

## The five preconditions (status as of 2026-09-06)

1. **Verbatim-first at decision time — RULE LIVE, echo REQUIRED at GO.**
   Daily reporting leads with raw instrument blocks
   (memory feedback_mb_verbatim_first_reporting). At GO specifically: the
   composition conversation MUST quote, verbatim, (a) the PASSED trader's
   funnel row, (b) the sizer recommendation dict with every intermediate,
   (c) the day's [canon] and [chain] lines. Paraphrased stakes are void.

2. **Chain watchdog — BUILT + LIVE 2026-09-06** (`scripts/mb_chain_watch.py`,
   runs last in the 11:40Z cron). Grades all 8 stages OK/CRASHED/MISSING;
   any non-OK makes the [chain] line start with `!!`. First live run
   correctly flagged hypo=MISSING for 09-05 (true positive: the ledger
   joined the cron 09-06). GO requires the day's [chain] line clean.

3. **Market-impact realism — RAIL BUILT 2026-09-06; MEASUREMENT = pilot-day.**
   `mb_sizer.recommend_stake*(depth_frac=...)` shrinks usable book depth to
   an operator-set fraction (no default; strictly downward; delegate
   parity + validation tested). REQUIRED AT GO: operator sets
   `MB_SIZER_DEPTH_FRAC` alongside the foursome, AND the pilot executor
   records per-fill (paper_expected_price, realized_price, realized_size)
   so slippage becomes a measured number within the pilot's first week.
   Scaling beyond pilot size before that number exists is a checklist
   violation.

4. **SELL recording — RECORDER LIVE 2026-09-06** (watcher writes roster
   SELLs to `/opt/pa2-shared/mirror3_shadow_sells.jsonl`, own sink, BUY
   pipeline untouched, 65/65 mirror3 tests green, deployed
   .pre-sellsink-20260906). REMAINING before this line is fully green:
   the with-exits estimand (pre-registered per ZERO_BASED_SIFTER stage 3)
   once the sink holds enough data. GO on entries-only copying is
   permitted with this line marked PARTIAL **only if the operator strikes
   the estimand half explicitly** — hold-to-resolution is then the named
   strategy, not an accident.

5. **Per-event correlated-exposure cap — RAIL BUILT 2026-09-06**
   (`mb_sizer.cap_per_event`, proportional down-scaling within an event,
   ungrouped stakes flagged, operator-set cap, no default; tested +
   mutation-checked). REQUIRED AT GO: the pilot executor supplies each
   candidate's event id (gamma events join) and an operator
   `MB_EVENT_CAP_USD`; ungrouped (event unknown) exposure is surfaced in
   the composition echo, never silently passed. NEVER implement this as a
   neg-risk market block (Bug 14, f66ed43, CLAUDE.md hardcode).

## Accepted-risk table (to be completed AT GO, one line per residual)

| # | residual risk | rail | armed? | operator signature |
|---|---------------|------|--------|--------------------|
| (filled at composition; empty rows are a GO blocker) |

## Standing facts the checklist rides on
- Sizer: $0 for any unproven trader, structural. Caps: depth (x frac),
  $300 canon per-bet, min-viable-to-zero, never clamped up.
- Canon verifier must read ALARMS=0 on GO day or nothing is quotable.
- Bankroll/kelly/concurrency/min-viable: operator foursome in
  /opt/pa2-shared/mb_sizer.env; concurrency divisor = the trader's own
  measured peak (population-study source, firehose/peak_conc.jsonl).
