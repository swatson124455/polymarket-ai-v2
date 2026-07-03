# MirrorBot v3 Trader-Scoring Engine

**Status: SHADOW-ONLY. No order authority. Not wired to execution.**

A clean-room rebuild of how MB decides which traders to tail. It scores
candidates on *what MB can actually capture* — first mirrorable entry per
`condition_id`, at MB latency, under MB's replayed exits, net of costs —
with statistics that were adversarially verified (session 2026-07-02) to
avoid specific money-losing failure modes.

It does **not** replace the live MirrorBot. Live MB keeps running; it is the
source of the `mirror_rejected_signals` data this engine validates against.

## Why a rebuild (what the old scoring got wrong)

Each estimator here exists because a naive version was shown, by simulation,
to lose money:

| Failure mode | Naive behavior | Fix in this engine |
|---|---|---|
| Correlated trades | iid SE passed 31% of zero-edge traders | cluster-robust SE by event |
| Edge in thin markets | `mean(e)·mean(w)` sign-wrong | per-trade weighted mean + correct weighted cluster SE |
| Favorite tail-sellers | spotless record → SE≈0 → auto-pass, max-sized | Jeffreys binomial LB + `MIN_ADVERSE_EVENTS≥2` + variance floor |
| Improver penalized | `min/max` ratio blind to direction | event-blocked holdout gate; `d` is diagnostic only |
| Mining M traders | naive top-K up to 63% false | BH-FDR at q=0.10 on holdout p-values |
| Winner's curse | post-selection edge inflated ~2.16× | empirical-Bayes shrinkage before sizing |
| Over-betting | per-trade var → ~1.6× full Kelly | event-level variance + hard per-event cap |
| Small-C p-values | CR1 t anti-conservative | wild cluster bootstrap (Webb weights) |

## Estimand (verified against MB's guards)

MB's one-bet-per-market guards **all key on `condition_id`** (verified: no
event-level linkage exists). So the scoreable unit is a trader's **first BUY
per `condition_id`**, not their volume-averaged re-entries. `SELL` rows are
the trader's own **exits** (used for hold time / trader-sell replay), never
NO-side bets — a bug in existing analytics this engine avoids.

## Pipeline

```
trades + resolutions ──► select_first_entries (per condition_id)
                    └──► pair_exits (FIFO BUY→SELL, same token)
Stage 1 (Q):  score_trader → cluster edge, Jeffreys LB, event-blocked
              train/test split, wild-bootstrap holdout p
              select_and_size → BH-FDR admission → EB shrink → event Kelly
Validation:   validate_ranking vs mirror_rejected_signals (KILL CRITERION)
Stage 2 (T):  score_tailability → p@t+Δ (staleness-bounded), MB exit replay,
              net edge L_net, coverage ρ  [runs only after validation PASS]
```

**Two decoupled gates (audit-critical):**
- **Admission** spends alpha once: structural backstops (`≥2` adverse
  events, non-degenerate SE) filter the BH pool, then BH on the test-half
  p-value. Jeffreys is **not** an admission gate (a second hard LB empties
  the watchlist).
- **Sizeability** additionally requires Jeffreys edge LB `> 0`. A trader can
  be admitted (watch) but sized at zero — this is the favorite-seller
  containment.

## Running (shadow)

```bash
# Stage-1 scores + shadow Kelly weights (report JSON only):
python scripts/mirror_scoring_run.py --stage q

# With an explicit out-of-sample holdout cutoff:
python scripts/mirror_scoring_run.py --stage q --cutoff 2026-05-15T00:00:00

# Counterfactual kill-criterion vs rejected signals:
python scripts/mirror_scoring_run.py --stage validate --cutoff 2026-05-15T00:00:00
```

Every report is labeled `UNVERIFIED` until validation passes.

## The kill criterion

`validate_ranking` compares admitted vs non-admitted traders' realized edge
on `mirror_rejected_signals` **after** the cutoff — signals the engine never
trained on. If admitted traders don't out-perform with one-sided bootstrap
`p < ALPHA`, the ranking carries no out-of-sample information: **do not wire
it to anything.** A PASS clears the label on the *ranking* only; sizing
weights stay shadow until a fill-modeled validation exists.

## Known limitations (honest)

- **Universe** = leaderboard-elite traders persisted to `trades` (top ~300,
  survivorship-filtered by `orphan_cleanup`). Not the full platform.
- **Δ=60s scoring** works only where `market_prices` has WS-tick density;
  low-coverage traders (`ρ < RHO_MIN`) are watch-only, never sized.
- **Fill weights** `w_i=1` for now: `orderbook_snapshots` covers ~top-200
  markets and pre-print depth overstates fillable size on informed flow.
  Reports carry the `unweighted_fills` caveat until shadow_fills-calibrated.
- **Operator must verify** actual `trades`/`market_prices` depth on the VPS;
  code cannot confirm row counts.

## Config

All knobs live in `bots/mirror_scoring/config.py` (`ScoringConfig`) with
Config-Tuning-Protocol tiers annotated. Changing any value is a behavioral
change: state expected impact + rollback in the commit.
