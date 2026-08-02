# OPTION C BUILD PLAN — capital-aware selection (operator-named 2026-08-02)

Operator rulings this plan implements (2026-08-02 session):
- **D1+D6 = Option C**: select-to-budget; `cap_desired` demoted to a backstop that should
  almost never fire. D6 handled inside C via per-family budget = `_series_cap()`.
- **Sizing**: keep `MAX_MARKET_CAPITAL=45` for now (~6–11 funded markets at $44/mkt p50,
  ESTABLISHED, study §3). Reward receipts price the share curve before this knob moves.
- After ALL fixes land: operator-named "reset everything" → clear STOP + resume.
  ⚠ Scope: permanent bans (`mkt_out`) and strike history are NOT cleared unless the
  operator names them explicitly.

Source study: `D1_D6_CAPITAL_COUPLING_STUDY_2026-08-02.md` (same directory). Read §1
(cut mechanics), §3 (commit model reads ~30% low), §5 (family-cap math) before coding.

## Design (from study Option C)

1. **D1 pre-filter completion** (prerequisite, same work package): kill the unchecked-tail
   append (`_kept.extend(rows[_ri:])` in `select_footprint`) — price close_time for
   everything that can reach a slot: bigger read budget + persistent close-time cache
   (positive entries never expire already) + filter-at-fetch where possible.
2. **Select-to-budget**: walk the candidate list in `alloc_prio` order (ALLOC_KEY=1 is
   staged live → cap_score order, fail-open to pool), accumulating per-row
   `est_commit_usd` (already computed by `kalshi_capital_rank`); stop selecting near
   `_total_cap()` × (1 + OVERSHOOT_MARGIN). Margin must cover the commit model's ~30%
   under-read (study §3) — start at 0.3, env-tunable, and measure.
3. **Per-family budget** inside the same walk: family running total capped at
   `_series_cap()` (min($100, 25%·equity) live) → D6 multiplicity, bounded.
4. **Carve-outs (explicit, test-pinned)**:
   - explore probes are probe-sized (5 ct) — budget-count them at probe cost, not join cost;
   - drop-grace retained books are not footprint rows — untouched;
   - unwind/held markets never budget-gated (de-risk is never blocked);
   - the trap found in study §1: drop-grace tickers get `_alloc_priority` 0 and are cut
     first — give retained-with-position tickers incumbent-class priority.
5. **`cap_desired` unchanged** as the safety net; add a counter that ALARMS if it still
   fires regularly post-C (it firing means the budget walk under-counted).
6. **Observability (D10-style, required)**: `drop_budget_full` + `drop_family_budget`
   counters in FP_DROPS; plan gauges for budget used/limit; prove the universe didn't
   silently shrink.

## Protocol (all mandatory)
Flag-gated (`KALSHI_SELECT_BUDGET`, default 0 = byte-identical, test-pinned) · one fix per
commit (D1 filter first, then the budget walk) · full suite + copy-based mutation on every
new guard · two independent blind reviews (diff lens + invariant lens with randomized
property checks, the 2026-08-01 pattern) · staged deploy under STOP · md5 vs git blob (LF —
generate the deploy file from `git show`, the worktree has CRLF contamination).

## After C lands
1. Operator reviews → names the reset: clear STOP, restart, verify first plan rows.
2. Verify on live data: footprint ≈ fundable size, `capped_markets` ≈ 0, churn gauges
   (fp_retained_pct) ≥ pre-C baseline.
3. Then the standing queue: D9 re-review after the explore sweep covers the universe ·
   net-EV calibrator from closed-market receipts (operator D5 ruling) · D2 price-band
   decision on gate_entry_band data · 8-3 ladder re-review outcomes · incumbency-gate
   enable timing (operator question OPEN as of this writing).
