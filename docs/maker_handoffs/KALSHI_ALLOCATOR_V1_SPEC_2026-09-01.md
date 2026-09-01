# ALLOCATOR V1 BUILD SPEC — post-review, per operator "do all recs" (2026-09-01 ~17:3xZ)

Scope: JOIN-mode allocator v1 + quoter file-consumption + hardening. SUPPLY (D2) parked
pending the B4 reconciliation study (running). Dailies enter as REAL-SIZE entries at GO
(no sub-cliff probes — Rule Eight). This spec implements review findings B1-B8/C1-C8/D
(KALSHI_ACDG_TRIPLE_BLIND_REVIEW_2026-09-01.md); each requirement cites its finding.

## 1. Projection (answers B1/B6/C1)
- `proj = accrued_est × (1 − DILUTION_BUFFER) + rate_hat × time_left_eff`
- `rate_hat`: trailing rate per (program_id, period) over ≥24h of tape, presence-conditioned
  (joined against quotes tape: rate counted only over resting hours), staleness-decayed;
  a decrease event in-window flags the row DILUTING and caps rate_hat at its post-drop slope.
- `time_left_eff = min(period_end, market_close) − now` (C5/R2-F9), minus scheduled
  wind-down/cutoff windows.
- Eligibility for real size: `proj ≥ 1.50` computed at EFFECTIVE size — offline replica of
  `_capped_join` + D3 rung state + widebook caps (A3/B6-fix; R2-F17). Never at plan max_ct.
- Cost term (B5): rank key = `(proj_credited − fillcost_rate × time_left_eff)` per
  committed-$; fillcost_rate from the fills tape per market (F14 basis); budget-fail armed.
- DILUTION_BUFFER initial value: derived at build from the measured decrease distribution
  (25 events on tape) — number comes back at signoff, not invented.

## 2. Measurement identity (answers B3/C6)
- Keyed on (program_id, period); accrued NEVER summed across programs of a ticker; period
  boundaries reset accrued (C6 sum-bug class excluded by construction).
- Series-level rate inheritance LICENSED (operator D8 ruling) for cold-start: a new
  program_id inherits its series' per-ct measured rate, labeled INHERITED until its own
  feed rows exist. Concluded-program accrual read from tape HISTORY, never live feed.

## 3. Allocation (answers B7/C2 + family cap)
- Greedy by rank key under MAX_TOTAL_CAPITAL at effective committed-$; concentration-first.
- Family cap now $200 flat (live.env KALSHI_SERIES_MAX_USD=200 + KALSHI_SERIES_PCT=0,
  applied 17:20:33Z, backup live.env.bak-FAMCAP-20260901_172033).
- Eviction hysteresis (C2): a market with accrued ≥ HOLD_THRESHOLD keeps its slot to period
  end unless operator-evicted or budget-fail fires; incumbent bonus in ranking; value at
  signoff.
- Real-size daily entries (D8): 1-2 per night, census-informed, at cliff-clearing size;
  positions-for-profit under the cold-start prior; measurement is byproduct.

## 4. File contract + quoter change (answers C7/B7/R1-F12/R3-F11/F17)
- JSON {version, generated_utc, rows:[{ticker, program_id, mode, max_ct, priority}]}.
- Writer: tmp + os.replace (atomic), single-writer flock.
- Quoter (KALSHI_FOOTPRINT_FILE, flag-gated): file drives selection; FOOTPRINT_TOP ignored
  and PIVOT loop bypassed under file mode (pinned under both PIVOT flags); SERIES_ALLOW
  stays as a belt (file ∩ allowlist); FP_DROPS reasons emitted for file tickers.
- Fail-CLOSED: missing/corrupt/stale (> STALE_H, value at signoff) file → quote NOTHING
  new (reduce-only continues), loud plan-row alarm every cycle. Never fail-open to the
  scrapped proxy selection.
- Priority field consumed by cap_desired/bound_creates for file tickers (replaces usd_day
  order — B7); implementation must not disturb non-file mode (flag-off byte-identical pin).

## 5. Reconciliation + coverage (answers C3/C4/D3-bucket)
- Each run reads back plan-row gate counters + placed orders; intent-vs-placed mismatch =
  alarm row. Coverage report buckets every family pool dollar: EARNING (proj ≥ floor AND
  resting confirmed) / PROBING / EXCLUDED(named gate/reason) / UNKNOWN. Sub-cliff accruers
  bucket EXCLUDED(cliff), never EARNING (R2-F12).
- est-feed anomaly guard: accrual delta while quotes tape shows 0 resting → ANOMALY row
  (the +$0.0001 class), rate_hat excluded.

## 6. Census G (parallel build; answers C10/D4-tripwires)
- Full-book capture; walk pinned byte-equivalent to the FIXED `_qualifying_score`
  (max-price rule) — never the `_qualifying_breakdown` pattern (0.99 defect, :2992).
- Watchlist = allowlist family (not footprint-follow); capacity beyond 40 tickers +
  sweep-budget plan comes back at signoff; partial coverage reported as UNKNOWN.
- No DF `or 0.5` coercion; filing parameter-range assertions (Target ∈ (100,20000),
  DF ≤ 1, pool $10-1k/day) as units tripwires; programs read cursor-paged.

## 7. Verification (B8-honest)
- Per-section adversarial review incl. EV lens before merge (standing 08-13 rule).
- Backtest scope = cliff arithmetic + eviction/hysteresis logic ONLY; it licenses nothing
  else (label on the report). Dry-run cycles on a real footprint file before arming.
- Forward week = first real test, pre-registered: footprint + committed + nightly
  credited-$; 7×$0 rule per D9 ruling (ASK PENDING: re-base clock to first period
  conclusion post-GO).

## 8. Status at spec time (17:3xZ)
- DONE: D4 env applied (17:20:33Z, service off, verified); accrual monitor live
  (kalshi_accrual_monitor.py on box + artifact dashboard); B4 study running; this spec.
- PENDING OPERATOR: GO (D3); D9 clock ruling; signoff values (DILUTION_BUFFER,
  HOLD_THRESHOLD, STALE_H, census capacity); v1 code review at PR time.
- NOT BUILT YET: allocator code, quoter flag change, census recorder change — next work
  items in this sequence unless redirected.
