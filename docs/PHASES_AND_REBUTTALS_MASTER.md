# Phases and Rebuttals — Master Reference

**Date:** 2026-02-09  
**Purpose:** Single source for all implementation phases and for every deferred/rebutted item: either **why we cannot add** (rebuttal) or **which phase** it belongs to. No item is "lost" to "Phase 3" without a phase number and reason.

---

## Phase Definitions

| Phase | Name | Scope |
|-------|------|--------|
| **1** | Safety & Foundation | ID normalization, risk controls, advisory locks, dead config removal. **Complete.** |
| **2** | Execution & Observability | Arb safety (cost model, leg failure, simultaneous price), prediction_log resolution backfill usage (per-bot metrics, recent performance), training temporal improvements (purge/embargo). |
| **3** | Pipeline & Bot Enhancements | Data pull efficiency (PD1, M3, P2, P4, R2, U1, U2), MirrorBot consensus, MomentumBot multi-window/mean-reversion, finer-grained advisory locks, canary queries, PgBouncer verification, feature/training (shared FeatureComputer, regime, multi-outcome), ArbitrageBot pre-computed pairs, model versioning, HFT pre-validation, backtest walk-forward. |
| **4** | Research & Design | Correlation awareness, multi-outcome markets design, stricter elite binomial test, trade timing/size pattern analysis. Optional and when product demands. |

---

## 1. Game Theory — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| StrategicTimer, OrderBookAnalyzer, CascadeDetector, PersuasionDetector | **Added** | In `base_engine/analysis/game_theory.py`. |
| AdverseSelectionTracker + fill recording | **Added** | OrderGateway calls `record_fill()` after successful fill. Wired in BaseEngine. |
| SmartOrderPlacer, MinimaxPositioner | **Added** | In game_theory.py. CLOB accepts limit price via `place_order(price=...)`. |
| ExecutionEngine retry | **Added** | Phase 3 item; done. `EXECUTION_ENGINE_MAX_RETRIES`. |
| **Stackelberg timing** (event-driven windows) | **Rebuttal** | Requires event-driven scheduler and per-bot triggers. Current design is poll-based. **Why we cannot add now:** Architecture change (event bus, delay queues). Low marginal gain vs StrategicTimer jitter. **Phase 3** if we later add unified event pipeline. |
| **Order book real-time / iceberg** | **Rebuttal** | We have snapshot only (OrderBookTracker, CLOB get_order_book). **Why we cannot add:** Iceberg needs L2 feed and refill tracking; not provided. Depth from snapshot is sufficient for current bots. **Not planned** unless L2 API exists. |
| **Fill recording** | **Added** | Was deferred; now wired. No rebuttal. |
| **CLOB limit orders** | **Added** | Supported. Pass SmartOrderPlacer output as `price` to `place_order`. |

---

## 2. Bayesian Theory — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| **Element 4: Elite reliability** | **Added** | `elite_reliability.EliteReliabilityTracker`, `get_user_resolution_counts`. |
| **Element 2: Model Beta confidence** | **Rebuttal** | **Why we cannot add:** No per-prediction equivalent_samples. Pipeline outputs single probability; tree path / sample count not exposed. Inventing a global N would violate "no invented numbers." **Unblock:** prediction_log + resolution backfill + optional `equivalent_samples` at insert when pipeline exposes it → **Phase 2/3** when metadata exists. |
| **Element 1: Live belief** | **Partial** | Elite-trade updates possible via Element 4 (log_likelihood_ratio). **Why full pipeline not added:** Price/volume evidence LRs not computed from data; no persistent belief store. Optional minimal loop with elite-only evidence is viable; full belief deferred. |
| **Element 3: Market efficiency** | **Rebuttal** | **Why we cannot add:** Needs Element 2 (model precision) and market precision from data. We have volume/liquidity but not "market price variance" or measured market precision. **Unblock:** prediction_log JOIN markets for market accuracy by volume → **Phase 3** after Element 2 path exists. |

---

## 3. Bots & Execution — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| MirrorBot staleness, MomentumBot convergence, EnsembleBot hours filter | **Added** | Implemented; verified in REBUTTAL_VERIFICATION. |
| TradeCoordinator opposite-side blocking, source_bot, global exposure | **Added** | Implemented; migration 009. |
| **MirrorBot consensus** (n_agree >= MIRROR_MIN_CONSENSUS) | **Phase 3** | Requires aggregating elite trades per market across users. MirrorBot currently iterates per-user. Add: scan by market, count elite sides, mirror only when `n_agree >= MIRROR_MIN_CONSENSUS`. |
| **ArbitrageBot cost model** | **Phase 2** | Add `TransactionCostModel.min_edge_for_profitability()` as floor before executing. Prevents fee-negative arbs. |
| **ArbitrageBot leg failure** | **Phase 2** | If leg 2 fails after leg 1 fills: exit leg 1 or retry leg 2. Critical for arb safety. |
| **ArbitrageBot simultaneous price fetch** | **Phase 2** | Fetch both legs in single request or enforce timestamp within 5s. Improves arb validity. |
| **MomentumBot multi-window / mean-reversion** | **Phase 3** | Add 1h/6h/24h/7d windows; fade mode for extreme z-scores. |
| **Kelly in EnsembleBot** | **When ready** | Add when calibration is reliable (e.g. Brier < 0.15). Document trigger. Not a phase—conditional. |
| **Per-bot metrics** (trades_executed, trades_won, total_pnl) | **Phase 2** | Wire when prediction_log resolution backfill is used; backfill is **already wired** in resolution_backfill.py, so dashboard can query prediction_log + positions. |
| **Recent performance factor** | **Phase 2** | Query prediction_log for last N resolved predictions; backfill provides was_correct. |
| **Elite weighted direction** (market-level elite_net_direction) | **Rebuttal** | **Why we cannot add now:** Requires new market-level feature and aggregation. Training has user_win_rate per sample. **Backlog** when feature store supports market-level aggregates. |
| **CryptoPoliticalBot sentiment** | **Rebuttal** | No external sentiment pipeline; uses DB trades as proxy. **Why we cannot add:** Overlap with EnsembleBot; no separate data source. Document as limitation; merge into EnsembleBot only if we add category features there. |
| **BotCoordinator** | **Rebuttal** | TradeCoordinator already handles contradiction. No separate component needed. |

---

## 4. Data Pull & Pipeline — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| **PD1** Two ID resolution implementations diverge | **Phase 3** | Add id_resolver fallback when market_lookup fails in import_poly_data_to_db. |
| **T4** COALESCE(liquidity, 0) false signal | **Phase 3** | Median imputation or liquidity_known binary; feature pipeline change. |
| **U2** Chicken-and-egg elite/trades | **Phase 3** | Near-elite expansion (30+ trades, 45%+); more API load. |
| **M3** Re-fetch all resolved markets every cycle | **Phase 3** | Skip upsert for resolved=true AND resolution IS NOT NULL if API supports or we verify immutability. |
| **P2** Price ingestion biased to high-volume | **Phase 3** | Rotation by last_price_update ASC / volume; schema/query changes. |
| **P4** Empty-response markets re-fetched | **Phase 3** | price_fetch_attempts / last_price_fetch_empty columns; schema migration. |
| **R2** Resolution backfill checks ALL unresolved | **Phase 3** | Prioritize by end_date, last_checked_at; per-run limits. |
| **U1** Top users by volume ≠ elites | **Phase 3** | Additional pass by profit/win-rate if API supports. |
| **5.1** Data validation on insert | **Phase 3** | Market/price validation beyond trades (validate_trade_dict exists). |
| **5.2** Orphan cleanup | **Phase 3** | Periodic DELETE trades WHERE market_id NOT IN (SELECT id FROM markets). |
| **PD3** Unique constraint on trades | **Phase 3** | Check duplicates first; add UNIQUE if needed + migration. |
| **S2** LearningScheduler reads during ingestion writes | **Rebuttal** | Retrain infrequent; ingestion bounded. **Why we don't add:** try_acquire_lock(ingestion, timeout=0) could skip retrain cycles. Revisit if races observed. |
| **Prediction_log resolution backfill** | **Added** | `db.backfill_prediction_log_resolution()` in database.py; resolution_backfill.py calls it (Phase 3 of backfill flow). |

---

## 5. Training & Models — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| **GAP 3** Live performance tracking | **Phase 2** | Job from prediction_log + markets; backfill already wired. |
| **GAP 4** Non-tree models (Ridge, KNN, SVM) | **Phase 3** | More complexity and tuning; 3 tree models + calibration sufficient for now. |
| **GAP 7** Backtest = live feature computation | **Phase 3** | Shared FeatureComputer with explicit as_of timestamp; substantial refactor. |
| **GAP 8** Richer regime detection | **Phase 3** | regime_features exist; full crypto vs political typing later. |
| **GAP 9** Correlation awareness | **Phase 4** | Per doc; design for later. |
| **GAP 10** Multi-outcome markets | **Phase 3** | Current system binary; design when product needs it. |
| **DEEPEN 1** Purge/embargo in walk-forward | **Phase 2** | Adjust train/val boundaries; drop markets resolving in purge/embargo windows. |
| **DEEPEN 3** Elite direction time decomposition | **Phase 3** | elite_direction_1h, 6h, 24h, elite_momentum. |
| **Minimum training sample gate** | **Added** | MODEL_MIN_TRAINING_SAMPLES; refuse train when samples < threshold. |

---

## 6. Infrastructure & Ops — Add or Rebuttal

| Item | Status | Add or Rebuttal |
|------|--------|------------------|
| **F3** Finer-grained advisory locks | **Phase 3** | Markets vs trades vs prices locks; currently one lock for full ingestion. |
| **F4** Min-sample gate | **Added** | See Fix 22; MODEL_MIN_TRAINING_SAMPLES. |
| **Fix 14** Canary queries after pipeline stages | **Phase 3** | Post-stage sanity checks. |
| **Fix 24** PgBouncer advisory lock verification | **Phase 3** | Startup check or use direct DB URL / pg_advisory_xact_lock for pooler. |
| **Fix 2** Kill switch guard in execution_engine.execute() | **Rebuttal** | OrderGateway already enforces; redundant guard optional (defense-in-depth Phase 3). |

---

## 7. Phase 2 Checklist (Concrete Next Steps)

- [x] ArbitrageBot: cost model floor before execute (`min_edge_for_profitability()`) — already in analyze_opportunity.
- [x] ArbitrageBot: leg failure handling (exit leg 1 or retry leg 2) — long/short/cross-market.
- [x] ArbitrageBot: simultaneous price fetch or 5s timestamp window — `price_fetched_at` + ARB_MAX_PRICE_AGE_SECONDS at execute.
- [x] Per-bot metrics: dashboard query positions (get_bot_metrics, get_all_bots_metrics); Overview shows per-bot table.
- [x] Recent performance factor: `get_recent_performance_from_prediction_log(n)`; Learning tab shows recent accuracy.
- [x] GAP 3: `get_model_live_performance(lookback_days)`; Learning tab shows model live accuracy (30d).
- [x] DEEPEN 1: Purge/embargo in walk-forward — TRAINING_PURGE_DAYS, TRAINING_EMBARGO_DAYS; filter in _prepare_training_data.

---

## 8. Phase 3 Checklist (After Phase 2)

- [x] MirrorBot consensus: already in mirror_bot (_collect_and_aggregate_elite_trades, n_agree >= MIRROR_MIN_CONSENSUS).
- [x] MomentumBot: multi-window (1h/6h/24h/7d) + fade mode already (MOMENTUM_WINDOWS_SEC, mean_reversion).
- [x] Data pull PD1: id_resolver fallback in import_poly_data_to_db when market_lookup misses.
- [x] Data pull R2: resolution backfill ORDER BY end_date_iso ASC NULLS LAST.
- [x] Data pull M3: SKIP_RERESOLVED_MARKETS; filter in save_to_db before bulk_insert_markets.
- [x] Data pull P2: PRICE_INGESTION_STALE_FIRST; get_markets_for_price_ingestion orders by last_ts ASC NULLS FIRST.
- [x] 5.1: validate_market_dict / validate_price_row already used (PRE_INSERT_VALIDATION).
- [x] 5.2: Orphan cleanup script + RUN_ORPHAN_CLEANUP_AFTER_INGESTION in scheduler.
- [x] PD3: check_trade_duplicates.py (--fix) + migration 010_trades_unique_constraint.sql.
- [x] Finer advisory locks: ingestion_markets (100006), ingestion_trades (100007), ingestion_prices (100008).
- [x] Canary queries: pipeline_canary.py; run after ingestion and resolution backfill (PIPELINE_CANARY_AFTER_INGESTION).
- [x] PgBouncer check: warn on pooler/6543 at DB init.
- [ ] Data pull: P4, U1, U2 (remaining). GAP 4/7/8/10, DEEPEN 3, backtest walk-forward, HFTBot pre-validation.
- [ ] GAP 4/7/8/10, DEEPEN 3 as in Training table.
- [x] BacktestEngine: BACKTEST_LATENCY_SIMULATION_MS; latency simulation before fill.
- [x] PredictionEngine model versioning: version increment on save, load by version desc.
- [ ] BacktestEngine walk-forward; ArbitrageBot pre-computed pairs; HFTBot pre-validation.

---

## 9. Phase 4 (Research / Design)

- Correlation awareness (portfolio-level).
- Multi-outcome market design.
- Stricter elite binomial test (DATA_TRAINING_REBUTTALS).
- Trade timing/size pattern analysis (Sybil/behavior).

---

## Summary

- **Added:** Game theory elements, fill recording, CLOB limit support, ExecutionEngine retry, Bayesian Element 4, prediction_log + resolution backfill, min-sample gate, bot filters (staleness, convergence, hours), TradeCoordinator/source_bot/exposure.
- **Rebuttal (cannot add):** Stackelberg (architecture), iceberg (no L2), Bayesian Element 2/3 until data exists, elite weighted direction (feature store), CryptoPolitical sentiment (no source), BotCoordinator (redundant), S2 (lock risk), Fix 2 (redundant).
- **Phase 2:** Arb safety (cost, leg, simul), per-bot metrics, recent performance, GAP 3 job, DEEPEN 1.
- **Phase 3:** Data pull efficiency, MirrorBot consensus, Momentum multi-window, locks/canary/PgBouncer, training/feature/backtest items.
- **Phase 4:** Correlation, multi-outcome, stricter elite, pattern analysis.

All phases are now defined; every deferred item has either a rebuttal (why we cannot add) or a phase number and brief description.
