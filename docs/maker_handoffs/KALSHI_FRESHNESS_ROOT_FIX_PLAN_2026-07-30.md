# FRESHNESS ROOT-FIX PLAN — score-cache staleness (operator-ordered deep dive, 2026-07-30)

Operator directive 2026-07-30: "deep dive root fix long term plan for freshness no half assing."
Scope: why the market-score cache is stale, what staleness actually costs (measured), and the
full root-fix plan. Analysis + measurements only — NO code changed, NO deploy. Implementation
phases below each need operator naming. General hold ("fine as is until numbers come in 8-2/3")
remains in force for everything not named.

## 1. The problem in one paragraph

Ranking wants `capture = share × pool` measured from real orderbooks, but books arrive far
slower than they age out. Cache-at-rest read 14:34:00Z 2026-07-30: 809 scored markets of 3,582
active programs (programs API 14:31:32Z), only 9% of scores ≤30 min old, median age ~3.6 days.
The live rank does not consume these scores yet (task F unwired) — so today this costs nothing,
but wiring F on a stale cache would rank most of the venue on days-old snapshots.

## 2. Measurements (all ESTABLISHED, this session, read-only)

### 2a. How fast does a capture measurement actually decay?
Frozen dataset: all `quotes-*.jsonl` on the box, 75,498 rows / 861 tickers, study run
15:04:17Z 2026-07-30 (script: freshness_decay_study.py, session scratchpad).

VALUE decay — Spearman of (capture at t) vs (capture at t+gap), pooled within-market pairs:

| gap | n pairs | Spearman | med \|relΔ\| | p90 \|relΔ\| |
|---|---|---|---|---|
| 5–15m | 62,057 | 0.972 | 0.00 | 0.22 |
| 15–60m | 69,517 | 0.939 | 0.00 | 0.36 |
| 1–3h | 61,732 | 0.851 | 0.02 | 0.81 |
| 3–6h | 51,914 | 0.747 | 0.09 | 1.00 |
| 6–12h | 48,565 | 0.672 | 0.20 | 1.18 |
| 12–24h | 40,792 | 0.503 | 0.27 | 1.55 |
| 24–40h | 12,056 | 0.385 | 0.10 | 2.45 |

RANK decay — cross-market ranking at t vs t+gap (markets present in both snapshots):
med Spearman 0.997 (5–15m) · 0.990 (15–60m) · 0.924 (1–3h) · 0.846 (3–6h) · 0.735 (6–12h)
· 0.582 (12–24h). Median top-5 overlap: 1.00 under 1h, 0.80 from 1–12h, 0.60 beyond 12h.

Dispersion vs drift: between-market sd of mean capture $15.31 vs median within-market sd
$0.40 (582 markets with ≥5 obs) — markets differ ~38× more than any one market drifts. A
stale score is far better than no score; the question is only how much to discount it.

**Known biases of this dataset (disclosed, not hand-waved):**
- Rows exist only for markets we quoted/explored — selection-biased toward rank winners.
- Measured capture includes OUR resting share while present → stability is overstated by our
  own persistence. The decay curve is therefore a LOWER bound on drift for markets where we
  are absent.
- Spans config eras (stickiness deploy 23:29Z 07-29 changed explore/probe behavior).
- Mitigation: §4 Phase 1 produces an unbiased venue-wide sample; constants get re-fit on that
  before anything goes live (Phase 3 gate).

### 2b. What does the shadow rank consume today?
caprank-20260730.jsonl (2,680 rows, env variant, read 15:04:51Z): of top-40 shadow entries,
62% kind=stale / 26% scored / 12% prospective. Top-10: 73% scored, 24% prospective, 3% stale
— the head is fresh (we quote it, so it re-measures), the tail is stale. Wiring F without a
fix ranks slots ~10–40 mostly on stale data.

### 2c. Why is the cache stale? Throughput, not decay math.
plans-20260730.jsonl (1,339 cycles, read 15:04:51Z): median cycle 63s; REST book reads
p50=7/cycle, p90=11 (budget: READ_BUDGET_PER_CYCLE=200, REQ_SPACING_S=0.55 →
maker_kalshi_quoter.py:975-976 — we use ~4% of it). Explore slots live at 3/cycle
(env, code default 10 — quoter.py:370). Net new scored markets in a full day: +10 (799→809).
Keeping 809 markets ≤3h fresh needs ~270 reads/h; explore=3 at 63s cycles delivers ~171/h,
and that same budget must ALSO cover 2,773 never-scored programs. The sweep can never
complete AND never stay fresh at this rate. That is the root cause.

### 2d. Coded decay constants vs measured reality — a second, independent defect
Code (kalshi_market_scores.py:45,52,145-152): HALF_LIFE_S=3600 (score weight halves every
hour, blending toward pool-prior), STALE_S=1800 (score treated as needing re-sample at 30 min).
Measured (§2a): at 2h a score still ranks with Spearman ~0.92; value Spearman doesn't cross
0.5 until the 12–24h band. The coded half-life discounts ~10× faster than measured decay —
so even the fresh part of the cache is being pushed toward the (wrong-key) pool prior far too
early. INFERRED: with biases in §2a the true half-life is shorter than 12h for markets we
don't sit in, but nowhere near 1h.

## 3. Root-cause statement

Three stacked defects, each needing its own fix — none is a band-aid for another:
1. **Throughput starvation** (§2c): book acquisition ~171 reads/h vs a venue that needs
   thousands; 96% of the REST budget unused; WS infra (Stage C cold books) not used for sweep.
2. **Miscalibrated decay** (§2d): half-life/staleness constants set by intuition before data
   existed; measured persistence is an order of magnitude longer.
3. **Rank not consuming measurements** (task F, known): selection still runs on pool-order +
   unknown-bonus; the whole score system is shadow-only.

## 4. Root-fix plan (phased; each phase = one operator naming; no phase half-done)

**Phase 0 — verify the venue's real rate limits.** Source: Kalshi API docs / trade docs, not
our own constant. Deliverable: documented safe sustained read rate with citation. (Our 0.55s
spacing = ~1.8 req/s was chosen conservatively; the sweeper design must not guess.)

**Phase 1 — dedicated background sweeper (the throughput root fix).** A paced reader,
independent of the 63s quote cycle, that walks ALL active programs continuously and feeds the
same score cache: REST-paced within verified limits, and/or WS-subscription rotation reusing
the existing Stage C plumbing (subscribe batch → snapshot books → score → rotate). Target set
by arithmetic, not vibes: e.g. a sustained 1 read/s sweeps 3,582 programs in ~1h; even
0.25/s keeps the full venue under ~4h — inside the measured ρ≥0.85 band. Sweeper is
read-only wrt trading; zero interaction with order paths. Also fixes the §2a sample bias:
first venue-wide unbiased capture dataset.

**Phase 2 — re-fit decay constants from measured curves.** With Phase 1 data (unbiased,
rival-only share where we are absent): re-fit HALF_LIFE_S to the empirical curve, set the
haircut as a direct lookup of the measured age→Spearman relationship (shrink toward prior
exactly as fast as predictive power is measured to fade), and set the hard age cutoff where
rank correlation stops being decision-grade (current data says the 6–12h band; re-fit before
committing). STALE_S (re-sample eligibility) becomes a scheduling concern for the sweeper,
not a scoring concern.

**Phase 3 — wire sweep → live rank (task F) in ONE change.** Haircut AND age cutoff together,
as the operator already ordered. Gate: shadow-validated first via the existing caprank
multi-variant infra (a "fresh-cache" variant vs live selection), calibrated against the
Aug 2-3 receipts (CAPRANK_CALIB from task K). Rank flip = separate operator naming, as
already standing.

**Phase 4 — permanent guardrail so this never regresses.** Telemetry: per-cycle
median/p90 score-age-at-consult + % of selection resting on stale scores, in the plan row
(signal-only, like fp_retained_pct). A freshness number nobody watches goes stale itself.

## 5. Decision points for the operator

- Phase 0+1 (rate-limit verification + background sweeper) can proceed independently of the
  receipts — they only gather data. Name them and they start.
- Phases 2–3 are receipts-adjacent: constants get finalized after the Aug 2-3
  receipt-vs-model calibration, per the standing plan.
- Nothing here resurrects any shelved item; nothing changes live behavior until Phase 3,
  which arrives shadow-first and flip-gated.
