# ALLOCATION-KEY AUDIT — every scarce-resource decision point, its sorting key, verdict
(operator-ordered full deep dive 2026-07-30: "verify all areas this is an issue and needs to be
fixed"; all line numbers = deployed commit `2a5ba84`; bite-rates from plans-20260730.jsonl,
1,704 cycles, read 18:5xZ)

The defect class: the 07-25 study established that reward POOL is the wrong ranking key
(rival depth varies ~71,330x across series vs pool's 6x; venue_scan.json 2026-07-25). The
selection layer got a measured-capture rank on 07-29; this audit is the completeness sweep
that should have accompanied it — every place that allocates a scarce resource, verified.

## ISSUES — pool-keyed allocation, live, needs the Phase-3 fix

| # | Site | Key today | Bites how often (measured today) |
|---|------|-----------|-------------------------------|
| 1 | **Total-capital cut** `cap_desired` (quoter.py:2088-2107) | raw pool desc, whole markets, cut tail at $350 | **1,700 of 1,704 cycles**, median 5 markets cut, max 10. THE dominant dollar allocator. Cause of the dailies starvation (traced: KXAAAGASD intents 125 rows 00:00-01:59Z, zero creates). |
| 2 | **Write-budget rationing** `bound_creates` (quoter.py:2109-2131) | raw pool desc | **0 of 1,704 cycles** (budget 400 tokens, observed usage 22-58). Latent — becomes live at scale. Same fix, same commit. |
| 3 | **Series rotation order** in live legacy selection (quoter.py:1196) | pool of each series' best-ranked member | Every cycle, but ONLY orders the round-robin rotation; every series still gets slot 1. Affects which series get 2nd+ slots. Mild. |
| 4 | **Unknown-vs-unknown ordering** (rank() unknowns score pool x 0.06) | pool | By design until now — nothing measured existed for unswept markets. The sweeper (live today) now writes `pcap` for the whole venue; Phase 3 replaces this prior with calibrated prospective capture. |

## DORMANT LANDMINE

- **PIVOT_SELECT path** (quoter.py:1212-1253, flag OFF live — env unset, default 0):
  if ever enabled as-is, it re-sorts per-series lists by near-money and fills the remainder
  by RAW POOL (`dens`, :1242) — **discarding the measured rank almost entirely**. Do not
  enable without reworking its ordering to the Phase-3 key. Same class as the order_gateway
  neg-risk landmine pattern: a flag that silently reverts a fix.

## VERIFIED NOT ISSUES (right key or deliberately flat)

- **Selection rank itself** (rank(), live, SCORE_RANK=1): measured capture w/ decay + swing
  penalty; explore slots keyed by AGE (never-seen first, then oldest stale) — correct.
- **Legacy round-robin within series** (:1201-1209): preserves rank order within each series;
  explore picks (front of rows) are each series' slot-1 pick — rank survives in the LIVE path.
- **WS subscription set** (ws_daemon.py:714): the footprint itself, cap 80 (footprint 19-40 —
  cap not binding; tie-break alphabetical, not pool). Revisit only past 80 markets.
- **Sweeper queue** (kalshi_market_sweeper.py): oldest-first by observation age — correct.
- **Presence gate / capture floor / net-EV / standdown**: all keyed on MEASURED quantities —
  correct direction (they skip on measured weakness, not pool).
- **Exits / strand-cross / unwinds**: risk-unconditional, exempt from every cap (polarity-aware)
  — correct; allocation keys must never touch reducing orders (and don't).
- **Per-market $75 cap, probe ct=5, ramp windows**: flat by operator design (all-in-or-all-out
  ruling), time/size-based, not pool-keyed.
- **Read budget consumption**: follows footprint traversal order (rank-derived) — consistent.

## THE FIX (Phase 3 scope, operator-gated, receipts-calibrated — ONE change)

Replace the pool key with the calibrated capture key (measured `capture` where fresh,
sweeper `pcap` x calibration haircut where prospective, age haircut + cutoff per the
freshness plan) at ALL of: cap_desired (#1), bound_creates (#2), series rotation (#3),
unknown ordering (#4) — and rework or permanently guard the PIVOT path (#5). Shadow-first
via the existing caprank variants; flip only on operator naming after the Aug 2-3 receipts
set CAPRANK_CALIB. Rank-key consistency test to pin all sites to ONE shared key function so
this class of divergence cannot recur.

## MIGRATION SAFEGUARDS — BUILT DARK same day (operator-named, both default OFF)

1. **Incumbent-first capital** (`KALSHI_ALLOC_INCUMBENT_FIRST`, commit `685b2c4`): markets we
   already stand in keep their dollars until their windows close; freed capital enters under
   the new rule. Kills the flip's churn/queue-loss/experiment-destruction risks.
2. **Per-family dollar cap** (`KALSHI_SERIES_MAX_USD`, this commit): hard ceiling on
   accumulating dollars per ticker family; a sibling over the cap is SKIPPED and the money
   flows to the next family (no tail-cut). Unwinds never blocked, but count toward the family
   total. Addresses the measured concentration (32% of $346.78 resting in ONE family,
   19:30:47Z read) and the new key's observed sibling-clustering habit — this is the D3 item
   (tabled 07-29) landing as a capital guard instead of a rank term.
   Telemetry: `series_cap_dropped` per plan row when enabled.
   Both compose: incumbents first, then family-capped fill; exits exempt from everything.
   Enabling either, and the cap dollar value, are separate operator namings — receipts first.

## Why this wasn't caught on 07-29
The wrong-key finding was fixed at the site where it was found (selection) without a
codebase sweep for other consumers of `usd_day` ordering — the pattern-completeness defect
(P16) our own audit rules name. This document IS that sweep; the six `usd_day` ordering
sites (grep, this session) are all classified above; no others exist in the quoter or daemon.
