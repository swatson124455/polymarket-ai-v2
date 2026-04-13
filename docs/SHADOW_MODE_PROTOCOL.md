# Shadow Mode Protocol — S172 Phase 1L

**Status:** ACTIVE
**Applies to:** All model changes in Phases 5-7 (EsportsBot, WeatherBot, MirrorBot elevation)
**Date:** 2026-04-13

---

## Purpose

Shadow mode allows a candidate model to run alongside the live model, making predictions that are logged but never traded. This validates the candidate on live data before it risks capital.

---

## Architecture

```
Market scan → Live model → TRADE (if edge passes gates)
           ↘ Candidate model → LOG ONLY (prediction_log with model_version flag)
```

Both models receive identical inputs (same market data, same features). Only the live model's output reaches the order gateway.

---

## Implementation Requirements

### 1. Dual Prediction Path

Each bot's scan loop calls the candidate model in addition to the live model. The candidate prediction is written to `prediction_log` with:

| Field | Live model | Candidate model |
|-------|-----------|----------------|
| `model_version` | current version int | candidate version int (always > live) |
| `model_name` | e.g. `glicko2_xgb_v3` | e.g. `glicko2_openskill_v4` |
| `confidence` | live confidence | candidate confidence |
| `predicted_probability` | live prob | candidate prob |

The candidate row is otherwise identical (same market_id, bot_name, event_time).

### 2. No Trading from Candidate

The candidate model MUST NOT:
- Trigger any order placement
- Affect position sizing
- Modify exit decisions
- Update any state used by the live model

Enforcement: candidate predictions are written to `prediction_log` only. They never enter the edge calculation or order gateway.

### 3. Resource Budget

The candidate model runs within the bot's existing MemoryMax. If the candidate increases RSS by more than 200MB, it must be profiled (tracemalloc) before deployment.

Candidate prediction latency must not increase scan cycle time by more than 50%. If it does, run the candidate asynchronously (fire-and-forget task with done callback — NOT awaited in the main scan loop).

---

## Promotion Criteria

A candidate model is promoted to live if ALL of the following are met:

### Minimum Data

- At least **50 resolved predictions** from the candidate, OR
- At least **2 weeks** of shadow data (whichever comes first for the minimum; both ideally)

### Statistical Tests

| Metric | Promotion threshold | Rejection threshold |
|--------|-------------------|-------------------|
| Brier Score | Candidate < Live by >= 5% relative | Candidate >= Live (worse by any margin) |
| ROI (simulated) | Positive on candidate predictions | Negative on candidate predictions |
| Calibration (reliability diagram) | No worse than live | Significantly worse slope or intercept |

### Simulated ROI Calculation

For each resolved candidate prediction:
1. Would the candidate have entered? (Apply same edge/confidence gates as live)
2. What was the resolution? (From trade_events RESOLUTION or market resolution)
3. P&L = (resolution_value - candidate_entry_price) * hypothetical_size - fees

Use the same sizing rules as live (BotBankrollManager with current config).

### Promotion Decision

- **Promote** if: Brier < live by >= 5% relative AND simulated ROI > 0
- **Reject** if: Brier worse by any margin OR simulated ROI < 0
- **Extend** if: < 50 resolved predictions and < 2 weeks elapsed — continue shadow

---

## Promotion Process

1. Run `scripts/shadow_evaluation.py <bot> <live_version> <candidate_version>` (to be built when first candidate is ready)
2. Review output: Brier comparison, ROI, calibration plot
3. If PROMOTE:
   - Update bot config to set candidate as live model
   - Archive old model version
   - Deploy via `deploy.sh`
   - Monitor 24h for regression
4. If REJECT:
   - Document why in handoff
   - Remove candidate from shadow
   - Investigate: is the approach wrong, or does it need more data/tuning?

---

## Rollback

If a promoted model regresses within 48h post-promotion:
1. Revert model_version config to previous
2. `sudo systemctl restart polymarket-<bot>`
3. The old model resumes immediately (no retraining needed — models are persisted)

---

## Constraints

- Only ONE candidate per bot at a time (avoid confounding)
- Shadow mode adds ~2-5ms per prediction (DB write). Negligible on 30-120s scan intervals.
- Candidate model must use the same feature set available in production (no features from external APIs that aren't wired yet)
- Shadow data older than 90 days should be pruned (same retention as prediction_log)

---

## When to Use Shadow Mode

| Scenario | Shadow required? |
|----------|-----------------|
| New base model (e.g. OpenSkill replacing Glicko-2) | YES |
| New calibration method | YES |
| Hyperparameter tune (same model architecture) | YES if tune changes > 3 params |
| Feature addition to existing model | YES |
| Bug fix in prediction pipeline | NO (fix and deploy) |
| Config change (thresholds, gates) | NO (Tier 1-2 per CLAUDE.md) |
| New data source integration | YES (affects predictions) |
