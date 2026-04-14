# S172 CONSOLIDATED PLAN v7.0 — INTEGRATED (Phase RC + Phase 5v2)

**Session:** 172 (original) + 173 (RC diagnostics + 5v2 amendment)
**Date:** 2026-04-13
**Status:** APPROVED — integrates v6.0 + Amendment 1 (Phase 5v2) + Phase RC findings
**Scope:** All 3 bots (WeatherBot, MirrorBot, EsportsBot) — audit remediation + long-term elevation
**Timeline:** 8 months
**Previous:** S172 v6.0 (`S172_CONSOLIDATED_PLAN.md`), Phase 5v2 Amendment (`S172_PHASE5V2_AMENDMENT.md`)

---

## Changes v6.0 -> v7.0

1. **Phase RC inserted** between Phase 1 and Phase 2. Complete. Findings integrated.
2. **Phase 5 replaced** by Phase 5v2 (EB rebuild). EB v1 killed — 4/4 kill criteria met.
3. **Phase 6 gate updated** — gated on RC findings + post-fix WB data, not just 1B calibration.
4. **Phase 7 gate updated** — gated on RC findings + post-fix MB data, not just prediction count.
5. **Success criteria amended** — EB v2 evaluated separately. System may operate with 2 bots.
6. **Phase 8B** evaluates EB v2 data (model_version='v2-trinity'), not v1.
7. **Immediate WB/MB fixes** added as "Day 2" actions derived from RC findings.
8. All other phases unchanged from v6.0.

---

## Context

Six independent audits + gap analysis + handoff cross-reference + elevation roadmap identified 80+ issues and strategic improvements. All verified against live VPS (2026-04-12). Phase 1 complete (12/12 items deployed). Phase RC diagnostics (35 queries, invariant-checked, cross-validated against bot_pnl.py) revealed:

- **WB:** SIZING + SUBSET. Wins half the size of losses. YES side overwhelmingly negative. Confidence anti-calibrated. See `scripts/rc_diagnostic.py` output for numbers.
- **MB:** SIGNAL + SUBSET. Sub-40% WR. Crypto category and 5 specific wallets dominate losses. See `scripts/rc_diagnostic.py` and `scripts/rc_verify.py` output for numbers.
- **EB:** SIGNAL. Model uninformative — flat WR across all calibration buckets. Never profitable any week. 4/4 kill criteria met. Killed. See `scripts/rc_temporal.py` Q4 output.

---

## Operational Procedures (apply to ALL phases — unchanged from v6.0)

- **Rollback:** Every code change: `git revert <sha>` + `sudo systemctl restart <service>`. Every schema change: DROP INDEX + re-enable trigger or reverse migration. Risk/exit code changes (D7, stop-loss modifications): After reverting, immediately audit all positions exited in the last N minutes for false triggers. Re-enter if exit was erroneous and market conditions still favorable.
- **Maintenance windows:** Schema changes deploy during 02:00-04:00 UTC low-activity window.
- **Testing:** Each new subsystem requires integration tests before merge. "Existing tests pass" is necessary but not sufficient.
- **Shadow mode:** 1L protocol governs all model changes in Phases 5v2-7.
- **Capital during elevation:** Bots continue at current paper-trade sizes. Shadow mode governs.

---

## MASTER TIMELINE

```
Week:  1   2   3   4   5   6   7   8   9  10  11  12  ...  M3  M4  M5  M8
       |---|
       Day 1 + Phase 1 (COMPLETE)
       |---|
       Phase RC (COMPLETE — diagnostics run, findings applied)
       |---|
       Day 2 (WB/MB immediate fixes from RC)
           |-------------------|
           Phase 2 (infra, parallel)
                   |--------------------------------------|
                   Phase 5v2 (EB rebuild: A->B->C->D)
                       |---|
                       Phase 3 (VPS config)
                           |---|
                           Phase 4 (hygiene)
                       |-------------------------|
                       Phase 6 (WB elevation — gated on RC + post-fix data)
                               |-----------------------------|
                               Phase 7 (MB — gated on RC + prediction count)
                                               |--------------------------|
                                               Phase 8 (cross-bot)
                                                       |----------|
                                                       Phase 10 (strategic)
                                                               |-----------------|
                                                               Phase 12 (WB EMOS)
       |-----------------------------------------------------------  Phase 13 (ongoing)
```

---

## DAY 1 — Immediate (COMPLETE)

All D-items deployed. Deploy 20260413_172523. Details in v6.0.

Items: D0-a/b/c (pre-work), D5 (backup FIRST), D1 (PG OOM), D2 (systemd limits), D3 (dedup indexes), D4 (fail2ban), D6 (prune timer), D9 (PipelineGate removed), D10 (WB reentry TTL), D7 (shared hard stop), D8 (MB flat sizing + cooldown).

---

## PHASE 1 — P0 Data Integrity + Edge Verification (COMPLETE)

All 12 items deployed. 1892 tests pass. Details in v6.0.

Items: 1A (frozen_price fix), 1B (calibration rolling 90d), 1C (autovacuum), 1D (WB post-res price), 1E-a/b (market_aliases + gateway validation), 1F (EB tracemalloc — TabPFN=48-byte stub), 1G (prediction_log writes), 1I (edge verification — ALL 3 BOTS FAILED), 1J (orderbook collection — 13,194+ snapshots), 1K (SSH verifications), 1L (shadow mode protocol doc), 1M (strategy lifecycle schema migration 071).

---

## PHASE RC — Root-Cause Investigation (COMPLETE)

**Deliverables:** `scripts/rc_diagnostic.py` (35 diagnostics), `scripts/rc_verify.py`, `scripts/rc_temporal.py`. All invariant checks PASS. All-time P&L cross-validated against `bot_pnl.py`.

**Findings summary:** See Context section above. Full output on VPS.

**Decision framework applied:**

| Bot | Verdict | Root Cause | Action |
|-----|---------|------------|--------|
| WB | FIX | SIZING + SUBSET | Day 2 fixes + Phase 6 elevation |
| MB | FIX | SIGNAL + SUBSET | Day 2 fixes + Phase 7 elevation |
| EB | KILL | SIGNAL (uninformative model) | Phase 5v2 rebuild |

---

## DAY 2 — Immediate Fixes from RC Findings (NEW)

**Philosophy:** Fix sizing and block clearly toxic subsets. Do NOT remove entire sides (YES/NO) or restrict lead-time windows — insufficient data to confirm those restrictions improve outcomes long-term. Let the bots accumulate post-fix data with both sides active.

**Deploy order:** D2-1 (shared) -> D2-2/D2-3 (WB) -> D2-4/D2-5/D2-6 (MB) -> D2-7 (EB kill). Deploy during 02:00-04:00 UTC maintenance window. Rollback: revert config/code, `sudo systemctl restart <service>`.

### Cross-Bot (Shared)

- **D2-1:** Cap max position size at $200 across all bots via `BotBankrollManager.max_bet_usd`. The largest position bucket is the dominant loss driver for all 3 bots — see rc_diagnostic.py S5 for per-bot breakdown.

### WeatherBot

- **D2-2:** Flat sizing — decouple from confidence until calibration fixed. Cap at $100 max. The confidence column is anti-calibrated: highest-confidence bucket has the largest losses despite the highest WR, because Kelly sizes up massively and rare losses are catastrophic. See rc_diagnostic.py S12, WB-8, and rc_verify.py Q5 for stake-by-confidence data.
- **D2-3:** Blacklist worst cities: Dallas, NYC, Toronto, Atlanta, London, Seattle. These 6 cities have wl_ratio < 0.15 (losses 7-17x larger than wins) and account for the majority of city-attributable losses. See rc_diagnostic.py WB-1, WB-7.

**NOT doing:** Kill YES side (D2-3 old) or restrict to 48-120h lead-time (D2-4 old). YES side and short lead-times are underperforming, but we don't have enough post-fix data to confirm removing them improves outcomes. Both sides continue trading at reduced sizing ($100 cap). Re-evaluate at Phase 6 gate after 4+ weeks of post-fix data.

### MirrorBot

- **D2-4:** Block crypto category. Crypto loses on both YES and NO sides and accounts for the dominant share of MB losses. See rc_verify.py Q1 for category x side breakdown.
- **D2-5:** Blacklist 5 worst wallets (0x818F, 0xD84c, 0x732F, 0x6ac5, 0x88f4). All 5 are active in last 30 days with 654 combined entries. See rc_verify.py Q2 for recency, rc_diagnostic.py MB-3 for per-wallet P&L.
- **D2-6:** Require `whale_trade_usd >= $100`. Small whale trades ($0-25, 3,394 trades) dominate losses while trades above $100 are profitable across 1,680 trades. See rc_diagnostic.py MB-4 for bucket breakdown.

**NOT doing:** Block sports-NO side (D2-9 old). Sports-NO is a secondary loss driver but removing an entire side reduces data collection. Let it run at capped sizing. Re-evaluate at Phase 7 gate.

### EsportsBot

- **D2-7:** Kill EB v1. `BOT_ENABLED=false`, `systemctl disable polymarket-esports`. Code stays in repo. (This is also 5v2-A1.)

**Expected volume impact:** WB drops from ~80 trades/day to ~60-70 (city blacklist reduces ~15%). MB drops from ~150/day to ~80-100 (crypto block + whale filter removes ~40-50%). Both bots retain enough volume for statistically meaningful post-fix evaluation within 4 weeks.

---

## WEEKS 2-4 — Phase 2: P1 Operational Resilience + Feast Feature Store (unchanged)

| Commit | Item | File(s) | Notes |
|--------|------|---------|-------|
| 11 | 2A: asyncio.wait_for verification grep | grep | ALREADY FIXED S166. Verify. |
| 12 | 2B: Data retention (trades CREATE-AS-SELECT) | prune_old_data.py | |
| 13 | 2C: Structlog dedup (30s TTL) | logging_setup.py | |
| 14 | 2D: WatchedFileHandler + logrotate | logging_setup.py + logrotate.d | |
| 15 | 2E: RTDS seen_set dedup | mirror_bot.py | |
| 16 | 2F: Health check kill switch wiring | health_check.sh | |
| 17 | 2G: Pool tightening | .env files | Start: MB 10->8. EB v1 dead — skip EB pool change. EB v2 pool set at 5v2-D wiring. Monitor 48h. |
| 18 | 2I: Illiquidity exit validation + enable | Config | |
| 19 | 2H: Entry-time liquidity gate | order_gateway.py | Per-bot depth: WB 10x, MB 5x, EB 3x |
| 20 | 2H-b: Shared-token mutual exclusion | order_gateway | |
| 21 | 2J: Slippage monitoring refactor | slippage_check.py | |
| 22 | 2K: Feast feature store | pip install feast | |

---

## WEEK 4-5 — Phase 3: VPS Config (SSH only, unchanged)

- effective_cache_size=12GB, PgBouncer idle_txn timeout, sshd hardening, SSH port change, autovacuum_naptime=15

---

## WEEK 5-6 — Phase 4: Hygiene (unchanged + EB v1 archive)

- 4A: Archive handoff documents
- 4B: Archive orphaned scripts **(+ include EB v1 scripts)**
- 4C: Improve .gitignore
- 4D: Commit S170 test files (71 tests)
- 4E: trade_journal.py nested session fix

---

## WEEKS 3-12 — Phase 5v2: EsportsBot Rebuild (REPLACES Phase 5)

**EB v1 KILLED.** 4/4 kill criteria met: no profitable subset, never profitable any week, model uninformative across all calibration buckets. See rc_diagnostic.py EB-6 and rc_temporal.py Q4.

### Architecture: Rating Trinity + XGBoost + Conformal Filter

| Layer | Component | Purpose |
|-------|-----------|---------|
| 1. Ratings | Elo + Glicko-2 + OpenSkill | Three independent probability estimates |
| 2. Consensus | Trinity spread/mean/agreement | Confidence from agreement, abstain on divergence |
| 3. Meta-model | XGBoost | Combines ratings + game features -> raw probability |
| 4. Calibration | Venn-ABERS | Calibrated probability with validity guarantees |
| 5. Filter | MAPIE conformal (LAC, alpha=0.10) | Only bet singletons — skip uncertain matches |
| 6. Sizing | Quarter-Kelly, $100 cap, 5% bankroll max | Conservative sizing |

**Game scope:** CS2 + LoL only. Expand after edge demonstrated.

### Sub-Phase 5v2-A: Data + Ratings Foundation (Weeks 3-4)

| Item | Details |
|------|---------|
| A1 | Kill EB v1 (= D2-10) |
| A2 | Schema migration 072 — 6 new tables (esports_matches, esports_players, esports_ratings, esports_features, esports_predictions, esports_odds) |
| A3 | Oracle's Elixir loader (LoL 2024-2026) |
| A4 | GRID (primary) + HLTV (supplementary) loader (CS2 2024-2026) |
| A5 | Elo engine (team-level, K=32) |
| A6 | Glicko-2 engine (team-level, RD + volatility) |
| A7 | OpenSkill engine (player-level Plackett-Luce) |
| A8 | Trinity runner — process historical matches, snapshot ratings, compute features |

**Gate 5v2-A:** All 3 systems produce plausible probabilities. Known dominant teams rated highest. Trinity spread ~0.05-0.10. Unit tests pass.

### Sub-Phase 5v2-B: Backtester + Meta-Model (Weeks 5-6)

| Item | Details |
|------|---------|
| B1 | Walk-forward engine (train patches N-3..N-1, predict N) |
| B2 | XGBoost meta-model (trinity features + game-specific) |
| B3 | Venn-ABERS calibration (per-game) |
| B4 | MAPIE conformal filter (singleton at alpha=0.10) |
| B5 | CLV tracking (Pinnacle odds) |
| B6 | Metrics suite (accuracy, Brier, log loss, ECE, CLV, yield, drawdown, z-score) |
| B7 | Full backtest: CS2 + LoL, 2024-2026 |

**Gate 5v2-B (HARD):** Accuracy >58% singletons, Brier <0.23, CLV >+1.5% vs Pinnacle, singleton rate >30%, z-score >1.5, both games individually profitable. **If fails after 2 iterations -> stop.**

### Sub-Phase 5v2-C: Shadow Mode (Weeks 7-9)

| Item | Details |
|------|---------|
| C1 | Live data pipeline (GRID/HLTV real-time) |
| C2 | Market discovery (match -> Polymarket market_id mapping) |
| C3 | Shadow prediction engine (log to esports_predictions mode='shadow') |
| C4 | Live CLV tracking |

**Duration:** Min 2 weeks or 50 resolved predictions.
**Gate 5v2-C (HARD):** Shadow accuracy >55%, Brier <0.25, CLV >+2% vs Polymarket, backtest-to-shadow drop <5%.

### Sub-Phase 5v2-D: Paper Trading (Weeks 10-12+)

| Item | Details |
|------|---------|
| D1 | Wire to base_engine (bots/esports_bot_v2.py extending BaseBot) |
| D2 | Sizing: quarter-Kelly, $100 cap, D7 hard stop -50% |
| D3 | prediction_log writes (model_version='v2-trinity') |
| D4 | Enable: BOT_ENABLED=true, SIMULATION_MODE=true |

**Duration:** Min 4 weeks or 100 resolved predictions.
**Gate 5v2-D:** P(edge>0) >= 0.70 via edge_verification.py, accuracy >55%, wl_ratio >0.80, max drawdown <25%.

### Risk Controls

| Control | Value |
|---------|-------|
| Max position | $100 |
| Max bankroll/bet | 5% |
| Kelly fraction | 0.25 |
| Hard stop-loss | -50% (D7) |
| Min edge | 5% |
| Conformal filter | alpha=0.10, singleton |
| Trinity guard | spread < 0.15 |
| Max daily bets | 10 |
| Stale rating guard | skip if last match > 45 days |
| Patch guard | 50% sizing for 2 weeks post-patch |

### New Dependencies (pip)

```
openskill>=6.0.0       # Player-level ratings
venn-abers>=0.4.0      # Calibration
hltv-async-api>=0.8.0  # CS2 data (supplementary)
shap>=0.43.0           # Feature importance
# Already installed: xgboost, mapie
```

### Code Organization

```
esports_v2/           # Parallel to existing esports/
  ratings/            # elo.py, glicko2.py, openskill_engine.py, trinity.py
  features/           # cs2_features.py, lol_features.py, feature_registry.py
  model/              # meta_model.py, calibrator.py, conformal.py
  data/               # hltv_loader.py, oracle_loader.py, grid_loader.py, odds_loader.py, normalizer.py
  backtest/           # walk_forward.py, metrics.py, runner.py
  scripts/            # load_historical.py, run_backtest.py, run_shadow.py

bots/esports_bot_v2.py   # Bot class (extends BaseBot)
tests/esports_v2/         # Unit + integration tests
```

---

## WEEKS 4-10 — Phase 6: WeatherBot Elevation (unchanged from v6.0, gate updated)

**Gate:** Phase RC findings + post-Day-2 data. WB must show improvement on post-fix trades (D2-2, D2-3 applied) before elevation proceeds. Re-run edge_verification.py on trades after Day 2 deploy timestamp. Concrete thresholds:
- **P(edge>0) >= 0.30** on post-fix trades: proceed with Phase 6 (directionally positive, fixes are helping).
- **P(edge>0) < 0.10** after 4 weeks: Day 2 fixes didn't address enough. Phase 6 items must target remaining loss drivers (e.g., 6D calibration, 6F YES-side strategy) before general elevation.
- **Minimum sample:** 200+ closed trades on post-fix data before evaluating gate.

Station mapping MOVED HERE from Phase 12 (resolves 6N<->12A circular dependency).

**Suggested execution order:** Sub-phase A (Weeks 4-5): 6A, 6B, 6-STATION, 6G, 6H, 6O. Sub-phase B (Weeks 6-7): 6C || 6K, 6D, 6E. Sub-phase C (Weeks 8-10): 6L, 6F, 6J, 6M, 6N, 6P, 6Q, 6I.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 6A | S167 P0 reentry_check (full fix) | D10 interim deployed |
| 6B | Orphan reconciliation (ongoing) | 1E complete |
| 6-STATION | Station-to-model mapping (was 12A) | None |
| 6C | Ensemble forecast integration (GEFS + ECMWF ENS) | 1B calibration gate |
| 6K | AIFS ENS integration (51-member ML ensemble) | 6-STATION |
| 6L | Multi-model ensemble (GEFS + AIFS + HRRR, BMA) | 6C + 6K |
| 6D | Per-side beta calibration (3-param Kull) | 200-500 samples/side |
| 6E | EMOS recalibration | CRPS/PIT shows bias |
| 6F | YES-side graduated strategy | 6D |
| 6G | GHCN LRU cache | None |
| 6H | Shadow entry pruning | None |
| 6I | WebSocket upgrade | Architecture design |
| 6J | Agile city rotation (minimal, 2-3 week mini-product) | 6C + 6E + 6-STATION |
| 6M | Conformal prediction (MAPIE for brackets) | Any base model |
| 6N | Neural post-processing MLP | 6-STATION |
| 6O | Lead-time optimal window (backtest) | Historical data |
| 6P | Bayesian small-sample calibration | 6J |
| 6Q | WB position sizing upgrade (trigger: CRPS >= 5% improvement) | 6D or 6E confirmed |

---

## MONTH 2-3 — Phase 7: MirrorBot Elevation (unchanged from v6.0, gate updated)

**Gate:** 1G + 500+ logged predictions + RC findings. MB must show improvement on post-Day-2 data (D2-4 through D2-6 applied). Re-run edge_verification.py on trades after Day 2 deploy timestamp. Concrete thresholds:
- **P(edge>0) >= 0.30** on post-fix trades: proceed with Phase 7 (directionally positive).
- **P(edge>0) < 0.10** after 4 weeks: crypto block + wallet blacklist + whale filter didn't address enough. Investigate remaining loss drivers before elevation.
- **Minimum sample:** 500+ closed trades on post-fix data before evaluating gate.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 7A | Event-driven WebSocket | Architecture design |
| 7B | Wallet selection overhaul | 500+ prediction_log rows |
| 7C | Leader-exit signals | 7A |
| 7D | Basket consensus | 7B |
| 7E | Gate_score expectancy analysis | Prediction_log data |
| 7F | Slippage-adjusted paper evaluation | 2J data |
| 7G | Re-entry cooldown review (analytical) | Prediction_log accumulation |
| 7H | LLM-augmented signal (entry only, not real-time) | Architecture design |
| 7I | Hedge/MWU signal aggregation | 7H + 7B + existing signals |
| 7J | Concept drift (ADWIN-U) | 1G working |
| 7K | Venn-ABERS calibration (MB-specific) | Calibration data |

---

## MONTH 2-4 — Phase 8: Cross-Bot + Infrastructure (unchanged + EB v2 accounting)

### Month 2 — Infrastructure:

| Item | Details |
|------|---------|
| 8A | Central position registry (accounts for EB v2 flow) |
| 8B | Prediction gate decisions — **evaluates EB v2 data (model_version='v2-trinity'), not v1** |
| 8P | Evidently AI drift monitoring |

### Month 3-4 — Bot-specific:

| Item | Details |
|------|---------|
| 8C | Correlation_id propagation |
| 8D | Exit parameter sweep |
| 8E | Unpriced token dual-write |
| 8F | Unpriced token escalation |
| 8G | market_id_mapping table |
| 8H | Cross-bot calibration framework (Platt scaling) |
| 8I | pg_partman for trade_events |
| 8J | systemd service templates |
| 8K | Remove dead EnsembleBot code + **EB v1 prediction engine (after v2 proven)** |
| 8L | CLV scaling evaluation |
| 8M | RL Trade Timing Agent evaluation |
| 8R | Fractional Kelly portfolio controls |

---

## MONTH 3-4 — Phase 10: Strategic Foundation (unchanged)

| Item | Details | Prerequisite |
|------|---------|-------------|
| 10D | Telegram alert bot | 2F health check |
| 10F | Minimum viable backtester (largely subsumed by 5v2-B for EB; reuse engine for WB/MB) | 1J data |

---

## MONTH 4-8 — Phase 12: WeatherBot EMOS Transformation (unchanged)

| Item | Details | Prerequisite |
|------|---------|-------------|
| 12B | Ensemble data access (GEFS, ECMWF ENS, HRRR) | API integration |
| 12C | Station-specific EMOS training | 6-STATION + 12B |
| 12D | Multi-model blending | 12C |
| 12E | Automated edge pipeline | 12C + 12D |
| 12F | City mastery extension (extends 6J) | 12E + 6J |

---

## ONGOING — Phase 13: Compliance + Observability (unchanged)

| Item | Details | Prerequisite |
|------|---------|-------------|
| 13A | Tax position establishment | None |
| 13B | Trade logging completeness | None |
| 13C | State residency monitoring | None |
| 13D | Streamlit dashboard | 10D |
| 13E | PG LISTEN/NOTIFY | 13D |

---

## Decisions (v6.0 + amendments)

**From v6.0:**
- WeatherBot: KEEP RUNNING with guardrails
- market_prices: KILL (Option B, after pgBackRest)
- D7: Per-bot stop-loss, shared check
- D8: 24h re-entry cooldown, signal override in MB only
- D10: TTL = min(time_to_resolution, 6h) floor 1h
- PG OOMScoreAdjust: -900
- Pool settings: Investigate first, conservative reduction
- Calibration gate: WB immediate, MB/EB after prediction_log fix
- 1I as graduated gate
- NegRisk: REMOVED
- Maker orders: REMOVED

**From v7.0 (RC + 5v2):**
- **EB v1:** KILLED via 8B procedure. Code retained. Historical trade_events preserved.
- **EB v2:** Rating trinity (Elo + Glicko-2 + OpenSkill) + XGBoost + Venn-ABERS + MAPIE conformal.
- **EB v2 game scope:** CS2 + LoL only. Expand after edge demonstrated.
- **EB v2 methodology:** Backtest -> shadow -> paper -> conditional live. No phase skipping.
- **EB v2 backtest gate:** Accuracy >58% singletons, Brier <0.23, CLV >+1.5%, both games profitable.
- **EB v2 paper gate:** P(edge>0) >= 0.70, 100+ resolved predictions.
- **EB v2 position cap:** $100.
- **WB Day 2:** Flat sizing ($100 cap), blacklist 6 worst cities. Both sides keep trading — not enough data to kill YES. (See rc_diagnostic.py S5, S12, WB-1, WB-7.)
- **MB Day 2:** Block crypto, blacklist 5 wallets, require whale >= $100. Sports-NO keeps trading — not enough data to block a full side. (See rc_verify.py Q1/Q2, rc_diagnostic.py MB-3/MB-4.)
- **Cross-bot Day 2:** Max position $200 cap via BotBankrollManager. (See rc_diagnostic.py S5.)
- **WB/MB gates updated:** Post-Day-2 data must show improvement before elevation proceeds.

---

## Success Criteria (8-month plan exit — AMENDED)

- **WB and MB** have P(edge > 0) >= 0.7 on post-Day-2 trades (measured after 4+ weeks accumulation)
- **EB v2** has P(edge > 0) >= 0.7 on paper trades (measured at 5v2-D gate). If EB v2 fails its gate: kill via 8B procedure, disable data pipeline crons, systemctl disable polymarket-esports-v2. Tables retained for future analysis. System operates with 2 bots.
- Total portfolio maximum drawdown under 25% of deployed capital
- Zero unresolved P0 audit items
- All prediction gates met: WB calibrated, MB 500+ predictions, EB v2 100+ predictions
- Shadow mode protocol used for every model change
- Backups operational
- If any bot fails its gate: killed or retrained per defined criteria

---

## Verification (v6.0 + additions)

- pytest 1892+ pass after each commit (updated baseline from Phase 1)
- Every new subsystem has integration tests before merge
- Day 2 fixes: re-run edge_verification.py after 4 weeks of post-fix data
- 5v2-A: All 3 rating engines have unit tests. Integration test on 100 matches.
- 5v2-B: Walk-forward backtest with shuffle-label control. Reliability diagram.
- 5v2-C: Shadow predictions logged for min 2 weeks / 50 resolved.
- 5v2-D: P(edge>0) via edge_verification.py. rc_diagnostic.py on v2 trade_events.
- Phase RC scripts validated: rc_diagnostic.py (35 diagnostics), rc_verify.py, rc_temporal.py — all invariant checks PASS, cross-validated against bot_pnl.py.

---

## Critical Files

**Day 1:** risk_manager.py (D7), mirror_bot.py (D8), weather_bot.py (D10), config/env
**Phase 1:** frozen_price_check.py (1A), calibration_check.py (1B), order_gateway.py (1E-b), edge_verification.py (1I), shadow mode doc (1L), migration 071 (1M)
**Phase RC:** rc_diagnostic.py, rc_verify.py, rc_temporal.py
**Day 2:** bankroll_manager.py (D2-1 cap), weather_bot.py (D2-2 sizing, D2-3 city blacklist), mirror_bot.py (D2-4 crypto block, D2-5 wallet blacklist, D2-6 whale filter), .env.esports (D2-7 EB kill)
**Phase 2:** logging_setup.py, health_check.sh, slippage_check.py, Feast config
**Phase 5v2:** esports_v2/ (new), bots/esports_bot_v2.py, migration 072, tests/esports_v2/
**Phases 6-7:** Bot files, .env files, model files, rating systems
**Phases 8-13:** Position registry, drift monitoring, backtester, EMOS, dashboard
