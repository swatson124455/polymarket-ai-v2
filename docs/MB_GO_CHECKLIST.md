# MB LIVE-CAPITAL GO CHECKLIST (operator-ruled 2026-09-06: "fix the gaps
# you named before go" -> "build all 5")

**RE-KEYED 2026-09-09 (operator "yes 3"): VERIFIED on the tailable list
(docs/MB_TAILABLE_LIST.md - backtest holdout LCB > 0 + dive ADMIT +
eligibility + conc<=20 + cov>=50%) produces the PROPOSAL. The forward
grader's QUALIFIES / NOT DEMONSTRATED verdicts are DROP-OFF signals only,
never the trigger.** No real dollar moves until every line below is green
or explicitly struck by the operator IN WRITING. A VERIFIED row does not
override this checklist; composition sign-off consumes it.

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
   .pre-sellsink-20260906). **LAYOUT DEFECT FIXED 2026-09-06T03:14Z**
   (`8e9c989`, deployed .pre-sellfix-20260906): the V2 fill event's amount
   words are maker-perspective, so the first night's records carried
   whale_price = tokens/usdc (inverted) and whale_size_usd = token count.
   Chain-verified on tx 0x0f422cdb (Exchange V2) + 0x31f7d3b9 (NegRisk
   V2); sell_record now inverts; all 79 pre-fix records migrated exactly
   (marker `layout_fix_20260906`, backup .pre-sellfix-migration-20260906).
   BUY pipeline unaffected (owner BUY events genuinely have usdc at w[2]).
   REMAINING before this line is fully green:
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

6. **Allocator tiers OFF for the hypothetical phase — MUST BE RE-RULED BEFORE
   LIVE (operator 2026-09-08 ~20:4xZ: "lets remove tiers bet full, this NEEDS
   TO BE CHANGED BEFORE LIVE THOUGH").** `MB_ALLOC_TIER_FRACS` was blanked in
   /opt/pa2-shared/mb_sizer.env (backup `mb_sizer.env.pre-tiersoff-20260908`),
   so every displayed stake uses the FULL bankroll per trader, no
   proven/confirming envelopes, no reserve. GO REQUIRES an explicit operator
   ruling on tier fractions (restore proven:0.50,confirming:0.10 from the
   2026-09-06 ruling, or a new split) written back into mb_sizer.env before
   the first real order. A blank value at GO is a checklist violation.

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
