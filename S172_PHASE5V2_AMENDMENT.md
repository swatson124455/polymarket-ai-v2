# S172 CONSOLIDATED PLAN — AMENDMENT 1: Phase 5v2

**Status:** APPROVED 2026-04-13
**Amends:** S172 Consolidated Plan v6.0
**Scope:** Replaces Phase 5 ONLY. All other phases unchanged.
**Date:** 2026-04-13

---

## What This Amendment Does

1. **Replaces Phase 5** (EsportsBot elevation) with Phase 5v2 (EsportsBot rebuild)
2. **Changes nothing else.** Phases 2, 3, 4, 6, 7, 8, 10, 12, 13 are unmodified.
3. WB continues running with Day 1 fixes (D7/D8/D10), accumulating post-fix data
4. MB continues running with Day 1 fixes (D7/D8/D10), accumulating post-fix data
5. Phase 2 infra proceeds in parallel as planned
6. Phase RC diagnostics (approved v2.2) apply to WB and MB only — EB gets a fresh start

---

## Why Phase 5 Is Replaced

Phase 5 (S172 v6.0) was designed to elevate EB's existing Glicko-2 model. The 1I edge gate fired — EB has P(edge>0) = 0.002 [source: edge_verification.py], meets 4 of 4 kill criteria (no profitable subset, never profitable in any week, model uninformative across all calibration buckets, WR 36.2% with symmetric losses). The existing model cannot be elevated because there is nothing to elevate. Phase 5v2 rebuilds the prediction engine from scratch while keeping all working infrastructure.

---

## Updated Master Plan Timeline

| Phase | Timeline | Status | Change |
|-------|----------|--------|--------|
| Day 1 | Complete | Done | — |
| Phase 1 | Complete | Done (12/12 items) | — |
| Phase RC | Week 1-2 | Diagnostics complete. Findings applied to WB/MB recommendations. | — |
| Phase 2 | Weeks 2-4 | ACTIVE — parallel track | No change |
| Phase 3 | Week 4-5 | Queued | No change |
| Phase 4 | Week 5-6 | Queued | No change |
| **Phase 5v2** | **Weeks 3-12** | **NEW — replaces Phase 5** | **EB rebuild (this amendment)** |
| Phase 6 | Weeks 4-10 | Queued — independent of EB | No change |
| Phase 7 | Month 2-3 | Queued — independent of EB | No change |
| Phase 8 | Month 2-5 | Slight shift — 8B gate accounts for EB v2 | 8B evaluates EB v2 data, not v1 |
| Phase 10 | Month 3-4 | Queued | No change |
| Phase 12 | Month 4-8 | Queued | No change |
| Phase 13 | Ongoing | Active | No change |

### Parallel Execution

```
Week:  1   2   3   4   5   6   7   8   9  10  11  12
       |---|
       RC (WB/MB diagnostics — complete)
           |-------------------|
           Phase 2 (infra, all bots)
                   |--------------------------------------|
                   Phase 5v2 (EB rebuild)
                       |---|
                       Ph3 (VPS)
                           |---|
                           Ph4 (hygiene)
                       |-------------------------|
                       Phase 6 (WB elevation — gated on RC findings)
                               |-----------------------------|
                               Phase 7 (MB — gated on prediction count)
```

Phase 5v2 runs independently. No dependencies on Phase 2, 6, or 7. Phase 6/7 gates are re-evaluated based on RC findings and post-fix WB/MB data — those decisions are separate from EB.

---

## Phase 5v2: EsportsBot Rebuild

### Architecture: Rating Trinity + XGBoost + Conformal Filter

Three independent rating systems produce win probabilities per match. Their agreement/divergence is the primary confidence signal. An XGBoost meta-model combines ratings with game-specific features. Venn-ABERS calibrates outputs. MAPIE conformal prediction filters to high-confidence bets only. Quarter-Kelly sizes positions.

| Layer | Component | Purpose |
|-------|-----------|---------|
| 1. Ratings | Elo + Glicko-2 + OpenSkill | Three independent probability estimates |
| 2. Consensus | Trinity spread/mean/agreement | Confidence from agreement, abstain on divergence |
| 3. Meta-model | XGBoost | Combines ratings + game features -> raw probability |
| 4. Calibration | Venn-ABERS | Calibrated probability with validity guarantees |
| 5. Filter | MAPIE conformal (LAC, alpha=0.10) | Only bet singletons — skip uncertain matches |
| 6. Sizing | Quarter-Kelly, $100 cap, 5% bankroll max | Conservative sizing learned from EB v1 failures |

**Rating system roles:**

| System | Level | Strength | Library |
|--------|-------|----------|---------|
| Elo | Team | Stable baseline, fast convergence | Custom (~100 lines) |
| Glicko-2 | Team | Uncertainty via RD, volatility tracks form | `glicko2` PyPI |
| OpenSkill | Player -> Team | Player-level decomposition, handles roster changes | `openskill` PyPI |

**Trinity consensus signal:**
- `trinity_spread` = max(P_elo, P_glicko, P_openskill) - min(...)
- Spread < 0.05 -> high agreement -> trust prediction
- Spread > 0.15 -> divergence -> abstain
- Spread is a feature in XGBoost — model learns when to trust its ratings

**Game scope:** CS2 + LoL only (74% of EB v1 volume, best data availability, highest prediction ceilings). Other games expand after edge is demonstrated.

---

### Sub-Phase 5v2-A: Data + Ratings Foundation (Weeks 3-4)

| Commit | Item | Details |
|--------|------|---------|
| A1 | Kill EB v1 | `BOT_ENABLED=false`, `systemctl disable polymarket-esports`. Code stays in repo. |
| A2 | Schema migration 072 | 6 new tables: `esports_matches`, `esports_players`, `esports_ratings`, `esports_features`, `esports_predictions`, `esports_odds`. No changes to existing tables. |
| A3 | Oracle's Elixir loader | LoL 2024-2026 historical match data. Normalize to `esports_matches`. |
| A4 | HLTV/GRID loader | CS2 2024-2026 match results + player stats. GRID Open Access (primary) + `hltv-async-api` (supplementary, scraping — may be blocked). |
| A5 | Elo engine | `esports_v2/ratings/elo.py`. Team-level, K=32 configurable. |
| A6 | Glicko-2 engine | `esports_v2/ratings/glicko2.py`. Team-level, tracks RD + volatility. |
| A7 | OpenSkill engine | `esports_v2/ratings/openskill_engine.py`. Player-level Plackett-Luce. |
| A8 | Trinity runner | Process historical matches chronologically. Update all 3 systems per match. Snapshot to `esports_ratings`. Compute `esports_features`. |

**Gate 5v2-A:**
- All 3 systems produce plausible win probabilities on historical data
- Known dominant teams have highest ratings (spot-check)
- Trinity spread distribution centered near 0.05-0.10
- Unit tests for each rating engine pass

**Tests required:**
- Per-engine: known input -> known output (deterministic)
- Integration: 100 matches processed, rating monotonicity for consistent winners
- Invariant: unbiased predictions (total predicted wins ~ total actual wins)

---

### Sub-Phase 5v2-B: Backtester + Meta-Model (Weeks 5-6)

| Commit | Item | Details |
|--------|------|---------|
| B1 | Walk-forward engine | Train on patches N-3..N-1, predict patch N, roll forward. Strict temporal ordering. |
| B2 | XGBoost meta-model | Features: P_elo, P_glicko, P_openskill, trinity_spread, trinity_mean + game-specific features. Binary target. |
| B3 | Venn-ABERS calibration | `pip install venn-abers`. Per-game calibrators (separate for CS2 and LoL). |
| B4 | MAPIE conformal filter | `pip install mapie`. MapieClassifier with LAC scoring. Singleton filter at alpha=0.10. |
| B5 | CLV tracking | Capture Pinnacle odds via OddsPapi (or equivalent odds API). CLV = model_prob - pinnacle_closing_prob. |
| B6 | Metrics suite | Accuracy, Brier, log loss, ECE, CLV, yield, max drawdown, z-score, reliability diagram. |
| B7 | Full backtest run | CS2 + LoL, 2024-2026, walk-forward. Output to `esports_predictions` with mode='backtest'. |

**Gate 5v2-B (HARD — do not proceed without):**

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Accuracy (singletons) | > 58% | Above breakeven after fees |
| Brier score | < 0.23 | Respectable calibration |
| CLV vs Pinnacle | > +1.5% mean | Edge over sharp market |
| Singleton rate | > 30% | Enough bettable matches |
| z-score (500+ preds) | > 1.5 | Approaching significance |
| Both CS2 and LoL individually profitable | Required | No cross-game hiding |

**If gate fails:** Iterate features/hyperparams. If still failing after 2 iterations -> approach doesn't have edge -> stop, reassess. Do not proceed to shadow.

**Lookahead bias prevention:**
- Walk-forward only — no future data in training
- Shuffle-label test: re-run with randomized outcomes -> accuracy must drop to ~50%
- Roster changes applied only after effective date, not announcement date

---

### Sub-Phase 5v2-C: Shadow Mode (Weeks 7-9)

| Commit | Item | Details |
|--------|------|---------|
| C1 | Live data pipeline | Real-time match ingestion from GRID/HLTV. Rating updates after each result. |
| C2 | Market discovery | Map esports matches to Polymarket market_ids. Extend existing scanner. |
| C3 | Shadow prediction engine | For each market: trinity -> meta-model -> calibrate -> conformal filter -> Kelly sizing. Log to `esports_predictions` with mode='shadow'. No trades placed. |
| C4 | Live CLV tracking | Polymarket price at prediction time + resolution. Pinnacle odds where available. |

**Duration:** Minimum 2 weeks or 50 resolved predictions, whichever is later.

**Gate 5v2-C (HARD):**

| Metric | Threshold |
|--------|-----------|
| Shadow accuracy (singletons) | > 55% |
| Shadow Brier | < 0.25 |
| CLV vs Polymarket | > +2% mean |
| Backtest-to-shadow accuracy drop | < 5% absolute |

Shadow mode follows the 1L protocol (approved in Phase 1): candidate alongside live (no live EB currently, so shadow runs solo), prediction_log with model_version flag, min 50 resolved, promote if metrics pass.

**If gate fails:** Root-cause the gap. Fix and re-run shadow. Do not proceed to paper.

---

### Sub-Phase 5v2-D: Paper Trading (Weeks 10-12+)

| Commit | Item | Details |
|--------|------|---------|
| D1 | Wire to base_engine | `bots/esports_bot_v2.py` extending `BaseBot`. Uses existing order_gateway, risk_manager, trade_events. |
| D2 | Sizing integration | Quarter-Kelly, $100 cap. D7 hard stop at -50%. |
| D3 | prediction_log writes | All predictions logged with model_version='v2-trinity'. |
| D4 | Enable | `BOT_ENABLED=true`, `SIMULATION_MODE=true`, `systemctl enable polymarket-esports-v2`. |

**Duration:** Minimum 4 weeks or 100 resolved predictions.

**Gate 5v2-D (determines live readiness):**

| Metric | Threshold |
|--------|-----------|
| P(edge > 0) via edge_verification.py | >= 0.70 |
| Paper accuracy (singletons) | > 55% |
| Win/loss ratio | > 0.80 |
| Max drawdown | < 25% of paper bankroll |

**If gate passes:** EB v2 approved for live at 25% target size. Scale over 4 weeks.
**If gate fails:** Run rc_diagnostic.py on v2 paper trades. Iterate or kill.

---

## What Stays, What Goes

### Keep (existing infrastructure)

- systemd service unit (rename to `polymarket-esports-v2`)
- BaseBot integration pattern
- order_gateway.py (market validation)
- risk_manager.py (D7 hard stop -50%, min_edge_hold=0.03)
- trade_events schema (v2 trades log to same table, same columns)
- prediction_log schema (v2 uses model_version='v2-trinity')
- Polymarket market scanner
- Position management
- Health checks and monitoring
- All diagnostic scripts (edge_verification.py, rc_diagnostic.py, bot_pnl.py)

### Replace (EB v1 prediction engine)

- Glicko-2 standalone -> Trinity (Elo + Glicko-2 + OpenSkill)
- model_prob generation -> XGBoost meta-model
- Confidence scoring -> Venn-ABERS calibration + MAPIE conformal
- Current feature set -> Trinity features + game-specific features
- 8-game support -> CS2 + LoL (expand later)
- TabPFN stub -> Removed (dead code)

---

## Old Phase 5 Item Mapping

| Old Item | Disposition |
|----------|-------------|
| 5A: TabPFN removal | Done — dead code cleanup in 5v2-A1 |
| 5B: Training data cleanup | Replaced by 5v2-A3/A4 (fresh data pipeline) |
| 5C+5O: Rating system eval | Replaced — trinity uses all three |
| 5H: Map-specific ratings | Included in 5v2-A feature set |
| 5I: Economy modeling | Deferred — add post-live if edge found |
| 5N: Conformal prediction | Included in 5v2-B4 |
| 5J: Player form EWMA | Included as OpenSkill feature |
| 5K: Meta-shift detection | Included as patch transition guard |
| 5L: Roster changepoint | Handled by OpenSkill divergence signal |
| 5M: LoL draft analysis | Phase 2 of game features (after CS2+LoL baseline works) |
| 5G: WebSocket upgrade | Deferred — not needed until live at scale |
| 5-PREREQ: Data pipelines | Replaced by 5v2-A3/A4 |
| EB prediction gate (300) | Replaced by 5v2-D gate (100 resolved + P(edge>0) >= 0.70) |

---

## Impact on Other Phases

| Phase | Impact |
|-------|--------|
| Phase 2 (infra) | None. Proceeds in parallel. |
| Phase 3 (VPS) | None. |
| Phase 4 (hygiene) | 4B (archive orphaned scripts): include EB v1 scripts in archive list. |
| Phase 6 (WB) | None. WB elevation is gated on RC findings and post-fix data, independent of EB. |
| Phase 7 (MB) | None. MB elevation is gated on prediction_log accumulation, independent of EB. |
| Phase 8 (cross-bot) | 8B prediction gate: evaluates EB v2 data (model_version='v2-trinity'), not v1. 8K dead code removal: includes EB v1 prediction engine (after v2 is proven). 8A position registry: accounts for EB v2's flow. |
| Phase 10 | 10F backtester: largely subsumed by 5v2-B. Reuse 5v2-B engine for WB/MB backtesting. |
| Phase 12 (WB EMOS) | None. |
| Phase 13 | None. |

---

## Schema (Migration 072)

```sql
-- All new tables. No modifications to existing tables.

CREATE TABLE esports_matches (
    match_id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    event_name TEXT,
    event_tier TEXT,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    winner TEXT,
    score_a INTEGER,
    score_b INTEGER,
    best_of INTEGER,
    map TEXT,
    patch TEXT,
    match_date TIMESTAMPTZ NOT NULL,
    is_lan BOOLEAN DEFAULT FALSE,
    source TEXT NOT NULL,
    raw_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE esports_players (
    player_id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    ign TEXT NOT NULL,
    team TEXT,
    role TEXT,
    active BOOLEAN DEFAULT TRUE,
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE esports_ratings (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    game TEXT NOT NULL,
    system TEXT NOT NULL,
    rating DOUBLE PRECISION NOT NULL,
    deviation DOUBLE PRECISION,
    volatility DOUBLE PRECISION,
    matches_played INTEGER DEFAULT 0,
    snapshot_time TIMESTAMPTZ NOT NULL,
    match_id TEXT REFERENCES esports_matches(match_id),
    UNIQUE(entity_id, game, system, match_id)
);
-- NOTE: UNIQUE allows multiple NULLs for match_id. If initial ratings need
-- dedup, add: CREATE UNIQUE INDEX idx_esports_ratings_initial
--   ON esports_ratings(entity_id, game, system) WHERE match_id IS NULL;

CREATE TABLE esports_features (
    match_id TEXT PRIMARY KEY REFERENCES esports_matches(match_id),
    p_elo DOUBLE PRECISION,
    p_glicko DOUBLE PRECISION,
    p_openskill DOUBLE PRECISION,
    trinity_spread DOUBLE PRECISION,
    trinity_mean DOUBLE PRECISION,
    features JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE esports_predictions (
    id BIGSERIAL PRIMARY KEY,
    match_id TEXT REFERENCES esports_matches(match_id),
    game TEXT NOT NULL,
    predicted_winner TEXT,
    p_model DOUBLE PRECISION NOT NULL,
    p_raw DOUBLE PRECISION,
    conformal_set TEXT[],
    is_singleton BOOLEAN,
    market_price DOUBLE PRECISION,
    pinnacle_odds DOUBLE PRECISION,
    edge DOUBLE PRECISION,
    kelly_fraction DOUBLE PRECISION,
    actual_winner TEXT,
    correct BOOLEAN,
    mode TEXT NOT NULL,
    model_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE esports_odds (
    id BIGSERIAL PRIMARY KEY,
    match_id TEXT REFERENCES esports_matches(match_id),
    source TEXT DEFAULT 'pinnacle',
    team_a_odds DOUBLE PRECISION,
    team_b_odds DOUBLE PRECISION,
    captured_at TIMESTAMPTZ NOT NULL,
    is_closing BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_esports_matches_game_date ON esports_matches(game, match_date DESC);
CREATE INDEX idx_esports_ratings_entity ON esports_ratings(entity_id, game, system);
CREATE INDEX idx_esports_predictions_mode ON esports_predictions(mode, game);
```

---

## Code Organization

```
esports_v2/                          # Parallel to existing esports/
  ratings/
    elo.py
    glicko2.py
    openskill_engine.py
    trinity.py
  features/
    cs2_features.py
    lol_features.py
    feature_registry.py
  model/
    meta_model.py
    calibrator.py
    conformal.py
  data/
    hltv_loader.py
    oracle_loader.py
    grid_loader.py                   # Primary CS2 data source
    odds_loader.py
    normalizer.py
  backtest/
    walk_forward.py
    metrics.py
    runner.py
  scripts/
    load_historical.py
    run_backtest.py
    run_shadow.py

bots/esports_bot_v2.py              # Bot class extends BaseBot (same pattern as v1)

tests/esports_v2/
  unit/
    test_elo.py
    test_glicko2.py
    test_openskill.py
    test_trinity.py
    test_meta_model.py
    test_conformal.py
  integration/
    test_rating_pipeline.py
    test_backtest_engine.py
  fixtures/
    sample_matches.json
```

---

## Dependencies (pip install — new only)

```
openskill>=6.0.0       # NEW: Player-level Plackett-Luce ratings
venn-abers>=0.4.0      # NEW: Calibration with validity guarantees
hltv-async-api>=0.8.0  # NEW: CS2 data (supplementary — GRID is primary)
shap>=0.43.0           # NEW: Feature importance analysis
# Already in requirements.txt: xgboost>=2.0.0, mapie>=0.9.0
# Already in requirements.txt: numpy, pandas, sqlalchemy, structlog, scikit-learn
```

---

## Risk Controls

| Control | Value | Source |
|---------|-------|--------|
| Max position | $100 | EB v1: $200+ was 93% of losses |
| Max bankroll/bet | 5% | Kelly best practice |
| Kelly fraction | 0.25 | Conservative start |
| Hard stop-loss | -50% | Existing D7 |
| Min edge to enter | 5% | Research consensus |
| Conformal filter | alpha=0.10, singleton only | MAPIE LAC |
| Trinity guard | spread < 0.15 to bet | Divergence = uncertainty |
| Max daily bets | 10 | Prevent overtrading |
| Stale rating guard | Skip if last match > 45 days | Outdated ratings |
| Patch guard | 50% sizing for 2 weeks post-patch | Meta shift |

---

## Implementation Notes (from schema/code audit)

1. **Code location:** `esports_v2/` at repo root (parallel to existing `esports/`). Bot file at `bots/esports_bot_v2.py` (consistent with existing bot pattern).
2. **xgboost already installed** — no version change needed.
3. **mapie already installed** — no version change needed.
4. **GRID Open Access is primary CS2 data source** — `hltv-async-api` is supplementary (scraping library, may be blocked by HLTV).
5. **`esports_ratings` UNIQUE constraint:** Add partial unique index for NULL match_id to prevent initial rating duplicates.
6. **Migration 072 is next** — 071 (strategy_lifecycle) is latest.

---

## Updated Decisions (appended to S172 v6.0 Decisions section)

- **EB v1:** KILLED via 8B procedure. Code retained in repo. Historical trade_events preserved.
- **EB v2:** Rating trinity (Elo + Glicko-2 + OpenSkill) + XGBoost + Venn-ABERS + MAPIE conformal.
- **EB v2 game scope:** CS2 + LoL only. Expand after edge demonstrated.
- **EB v2 methodology:** Backtest first -> shadow -> paper -> conditional live. No phase skipping.
- **EB v2 backtest gate:** Accuracy >58% singletons, Brier <0.23, CLV >+1.5% vs Pinnacle, both games profitable.
- **EB v2 paper gate:** P(edge>0) >= 0.70 (same as S172 1I), 100+ resolved predictions.
- **EB v2 position cap:** $100 (down from uncapped).
- **WB:** Continues running. Post-fix data accumulating. RC findings inform Phase 6 scope.
- **MB:** Continues running. Post-fix data accumulating. RC findings inform Phase 7 scope.

---

## Updated Success Criteria (amends S172 v6.0)

Original criterion: "All 3 bots have P(edge > 0) >= 0.7"

Amended: "WB and MB have P(edge > 0) >= 0.7 on post-Day-1 trades (measured after 4+ weeks of post-fix accumulation). EB v2 has P(edge > 0) >= 0.7 on paper trades (measured at 5v2-D gate). If EB v2 fails its gate, it is killed — the system operates with 2 bots."

All other success criteria unchanged.

---

## Verification (appended to S172 v6.0)

- 5v2-A: All 3 rating engines have unit tests. Integration test on 100 historical matches.
- 5v2-B: Walk-forward backtest with shuffle-label control. Reliability diagram shows calibration.
- 5v2-C: Shadow predictions logged for minimum 2 weeks / 50 resolved.
- 5v2-D: P(edge>0) via edge_verification.py on v2 paper trades. rc_diagnostic.py on v2 trade_events (model_version='v2-trinity').
- pytest pass count: must remain >= 1892 (current baseline) + new v2 tests.
