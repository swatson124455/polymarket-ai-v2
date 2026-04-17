# S172 CONSOLIDATED PLAN v6.0 — FINAL (APPROVED)

**Session:** 172
**Date:** 2026-04-12
**Status:** APPROVED after 5 review cycles
**Scope:** All 3 bots (WeatherBot, MasterBot, EsportsBot) — audit remediation + long-term elevation
**Timeline:** 8 months
**Previous:** S171 (`AGENT_HANDOFF_S171_SHARED_MASTER.md`)

---

## Context

Six independent audits + gap analysis + handoff cross-reference + elevation roadmap identified 80+ issues and strategic improvements. All verified against live VPS (2026-04-12). Nothing deferred — every item has a phase assignment. Timeline: 8 months.

---

## Operational Procedures (apply to ALL phases)

- **Rollback:** Every code change: `git revert <sha>` + `sudo systemctl restart <service>`. Every schema change: DROP INDEX + re-enable trigger or reverse migration. Risk/exit code changes (D7, stop-loss modifications): After reverting, immediately audit all positions exited in the last N minutes for false triggers. Re-enter if exit was erroneous and market conditions still favorable. This applies to any risk/exit code change.
- **Maintenance windows:** Schema changes (1C, 1E, migrations) deploy during 02:00-04:00 UTC low-activity window.
- **Testing:** Each new subsystem (5H Glicko-2, 6J city rotation, 7H LLM signal) requires integration tests before merge. "Existing tests pass" is necessary but not sufficient.
- **Shadow mode:** Define shadow mode protocol as process document in Phase 1 (was 8O — promoted). All model changes in Phases 5-7 must use shadow mode.
- **Capital during elevation:** During Phases 5-7 model changes, bots continue at current paper-trade sizes. Shadow mode governs: candidate model predicts but doesn't trade until promoted.

---

## DAY 1 — Immediate (< 4 hours, SSH + 2 code commits)

⚠️ **FIRST ACTION: D5 (backup). Before any config changes. If PG dies during D1-D4, you lose everything.**

### Pre-Work Diagnostics (SSH, 30 min):

- **D0-a:** Fix logrotate failure
- **D0-b:** Verify ingestion NRestarts
- **D0-c:** Enable Redis AOF persistence

### Infrastructure (SSH):

- **D5:** FIRST. Stopgap pg_dump backup + cron (02:00 UTC daily, 7-day retention). Replaced by pgBackRest once Phase 1H stable.
- **D1:** PostgreSQL OOMScoreAdjust=-900 (kill last among userspace, not absolute -1000 which risks OOM thrash)
- **D2:** systemd MemoryMax + OOMScoreAdjust:
  - EB: 2G→2.5G (UP — 2.3GB RSS over current 2G limit. Contingent on 1F tracemalloc. If leak fixed, may tighten. If legitimate working set, bump to 3.0G)
  - MB: 3G→2.5G (DOWN — 1.9GB RSS, 31.5% headroom)
  - WB: 2G→2.0G (unchanged — burst ensemble loads justify headroom)
  - Ingestion: 1G→0.5G (DOWN — 0.3GB RSS)
  - OOMScoreAdjust: PG=-900, Redis=-500, WB=-200, MB=-100, EB=0, Ingestion=+100
- **D3:** RESOLUTION + EXIT dedup partial unique indexes. Dedup key: RESOLUTION unique on (market_id, bot_name) WHERE event_type='RESOLUTION'. EXIT unique on (market_id, bot_name) WHERE event_type='EXIT' (event_type in columns is redundant since WHERE already filters — drop it). Disable trigger → clean dupes (keep earliest per key) → create indexes CONCURRENTLY → re-enable trigger.
- **D4:** Fix fail2ban + sudo ufw limit ssh
- **D6:** Start prune timer
- **D9:** PipelineGate REMOVED. Gate is 2 hours (not 60s as originally claimed). Safety net for API outages, not a bottleneck.
- **D10:** WB reentry_check interim fix. TTL = min(time_to_resolution, 6h) with floor of 1h. Dynamic per-market. Data source: `markets.end_date_iso` (populated from Gamma API endDateISO). Calculation: `(end_date_iso - utcnow()).total_seconds() / 3600` — same pattern as `exit_strategy.py:307-308`.

### Code Commits:

- **D7:** Hard stop-loss in `base_engine/risk/risk_manager.py` — NEW shared check, REPLACES existing EB-specific edge override (esports_bot.py:2264). One code path, not two. Per-bot configurable:
  - WB: min_edge_hold=0.05, hard_stop=-25%
  - MB: min_edge_hold=0.03, hard_stop=-30%
  - EB: min_edge_hold=0.03 (carries over existing ESPORTS_MIN_EDGE_HOLD), hard_stop=-50% floor (volatile esports)
- **D8:** MirrorBot $30 flat sizing + 24h re-entry cooldown + override signal enhancement. $30 accounts for risk budget deductions post-sizing (~0.88x typical → $26.40, clears $25 dust gate). Override `apply_signal_enhancements()` in mirror_bot.py to return confidence unchanged (neutral passthrough). Do NOT modify base_bot.py:441 — other bots keep their 1.2x/0.6x. Design debt: risk budget deductions apply after flat sizing; worst-case (floor 0.15) gets dust-gated, which is acceptable in extreme drawdown.

---

## WEEK 1 — Phase 1: P0 Data Integrity + Edge Verification + Shadow Mode Protocol

| Commit | Item | File(s) | Notes |
|--------|------|---------|-------|
| 1 | 1A: frozen_price_check timestamp fix | frozen_price_check.py L29,30,39 | All bots |
| 2 | 1B: calibration_check rolling 90-day + CRPS/PIT | calibration_check.py | WB immediate. MB/EB: N/A until Phases 7/5 gates met (0 prediction_log rows, months-long dependency, not just sequencing) |
| 3 | 1C: Autovacuum tuning (positions 5.5% dead + mpl 14% dead + users) | New 067_vacuum_tuning.sql | Deploy during 02:00-04:00 UTC window |
| 4 | 1D: WB post-resolution price override | Resolution backfill path | WB primary |
| 5a | 1E-a: market_aliases migration | New migration (separate from gateway) | Schema only — zero blast radius |
| 5b | 1E-b: order_gateway pre-trade validation | order_gateway.py | All bots — separate commit from migration |
| 6 | 1G: prediction_log write fix | mirror_bot.py + esports_bot.py | MB/EB |
| 7 | 1F: EB tracemalloc SIGUSR1 handler | esports_bot.py | CONDITIONAL: If TabPFN > 1GB → execute removal in same session (makes Phase 5A a no-op, shortens Phase 5 timeline) |

### PROMOTED TO PHASE 1 (commits 8-10, pushing Phase 2 to start at commit 11):

| Commit | Item | Details | Notes |
|--------|------|---------|-------|
| 8 | 1I: Edge verification | Bootstrap P(edge > 0) and Kelly on existing trade_events. ~50 lines numpy. | HARD GATE for Phases 5-7. Graduated response: P(edge>0) ≥ 0.9 → full elevation. 0.7–0.9 → elevation at reduced scope (core items only, skip speculative). < 0.7 → root-cause investigation replaces elevation. |
| 9 | 1J: Orderbook collection | Cron polling best_bid/best_ask every 60s. | Specify rate limits. If 500+ markets exceed Polymarket API throttling, prioritize by volume. |
| — | 1K: Quick verifications (not a commit) | ArbitrageBot auto-start? EsportsLiveBot orphans? Canary stuck? | 5-minute SSH checks, no code change |
| — | 1L: Shadow mode protocol (not a commit) | Concrete document specifying: (a) candidate alongside live, (b) prediction_log with model_version flag, (c) min 50 resolved or 2 weeks, (d) promote if Brier < live by ≥5% + positive ROI, (e) reject if worse by any margin or negative ROI. | Process document, not code. Must exist before Phase 5-7. |
| 10 | 1M: Strategy lifecycle schema (was 10B) | 5 PG tables — schema only, no code dependency. Informs capital allocation from Day 1. | Migration commit. |

### VPS work:

- Orphan reconciliation for WB positions
- 1H: ALTER SYSTEM SET idle_in_transaction_session_timeout = '300000' (5 min server-level backstop). App-level 60s handles bot connections. This 5-min setting catches psql, cron, pg_dump sessions that bypass app-level timeout. NOT an increase to bot timeout — it's a separate safety net for non-app connections. VPS-only, no commit.
- pgBackRest setup (60-90min) — once stable, D5 pg_dump cron is superseded
- Post-1E: verify drawdown controller sees all 3 bots' exposure

**POST-COMMIT-2:** Run calibration_check.py WeatherBot (CRPS + Brier + PIT)

---

## WEEK 1-2 — Phase 2: P1 Operational Resilience + Feast Feature Store

| Commit | Item | File(s) | Notes |
|--------|------|---------|-------|
| 11 | 2A: asyncio.wait_for verification grep | `grep -rn "wait_for.*acquire\|wait_for.*execute\|wait_for.*fetch" --include="*.py"` | ALREADY FIXED S166. Verify no remaining instances. |
| 12 | 2B: Data retention (trades CREATE-AS-SELECT + recon_breaks) | New prune_old_data.py | |
| 13 | 2C: Structlog dedup (30s TTL) | logging_setup.py | |
| 14 | 2D: WatchedFileHandler + logrotate | logging_setup.py + new deploy/logrotate.d/polymarket | |
| 15 | 2E: RTDS seen_set dedup | mirror_bot.py | |
| 16 | 2F: Health check kill switch wiring | health_check.sh (EXISTS — enhance, don't recreate) | Review existing 6-layer script, add kill switch flag |
| 17 | 2G: Pool tightening — INVESTIGATE FIRST | .env files | Start: MB 10→8, EB 14→10. Monitor 48h. Rollback: if >5 events matching `pool_exhaustion\|TimeoutError\|semaphore\|QueuePool limit` in 48h, immediately revert. |
| 18 | 2I: Illiquidity exit validation + enable | Config | Deploy BEFORE 2H — handle exits from illiquid positions before filtering entries by liquidity. |
| 19 | 2H: Entry-time liquidity gate | order_gateway.py | Per-bot depth multiplier: WB 10×, MB 5×, EB 3× |
| 20 | 2H-b: Shared-token mutual exclusion | DB check in order_gateway | Enumerate the 5 tokens. Add discovery mechanism for new shared tokens. |
| 21 | 2J: Slippage monitoring refactor | slippage_check.py | |
| 22 | 2K: Feast feature store (was 8Q) | pip install feast, PG offline + Redis online | Initial setup 1-2 days. Delivers configured stores + one example feature view. Each elevation phase adds feature views incrementally. |

If market_prices Option B approved: Commit 20 (DROP TABLE + exclusion + ingestion disable)

---

## WEEK 2 — Phase 3: VPS Config (SSH only)

- effective_cache_size=12GB (planner hint, verify shared_buffers stays 4GB)
- PgBouncer idle_txn timeout
- sshd hardening (PasswordAuth=no, PermitRoot=no, MaxAuth=3, AllowUsers ubuntu)
- SSH port change (non-standard high port — document the chosen port)
- autovacuum_naptime=15

---

## WEEK 3 — Phase 4: Hygiene (5 commits)

- 4A: Archive handoff documents
- 4B: Archive orphaned scripts
- 4C: Improve .gitignore
- 4D: Commit S170 test files (71 tests) — verify they pass in CI first
- 4E: trade_journal.py nested session fix

---

## WEEKS 3-6 — Phase 5: EsportsBot Elevation (EB-scoped, 6-8 weeks realistic)

**Gate:** Phase 1I edge verification. If P(edge > 0) < 0.7 for EB, replace this phase with root-cause investigation.

### Core items (do first):

| Item | Details | Prerequisite |
|------|---------|-------------|
| 5A: TabPFN removal/API migration | If already done in 1F conditional, skip. | 1F results |
| 5B: Training data cleanup | Deterministic cleaning. Retraining gate April 17+ — this date is from the S169 7-day freeze (frozen April 10). | Retraining gate open |
| 5C+5O: Rating system evaluation | Evaluate Glicko-2 vs OpenSkill before building. Build winner. Specify: match winner AND map winner? Map-specific (5H) data requirements depend on this. | 5B |
| 5H: Map-specific ratings | Separate ratings per team per map. Acknowledge: 7 maps × ~50 teams = 350 rating pairs needing ~30 matches each. Sparse initially — use aggregate rating as fallback when map-specific has < N matches. Verify July 2025 CS2 economy update rules before building. | 5C winner |
| 5I: Economy state modeling | Deterministic CS2 economy. Verify against July 2025 shared kill income update ($50/team member per kill changed CT recovery). Build on current rules, not pre-July-2025. | 5B |
| 5N: Conformal prediction wrapper | Use MapieClassifier (not MapieTimeSeriesRegressor) for binary match outcomes. | Any base model from 5C |

### Deferred within Phase 5 (require data pipelines that don't exist yet):

| Item | Details | Hidden dependency |
|------|---------|------------------|
| 5-PREREQ: HLTV data pipeline | Build scraper/API integration for player stats, match data, roster changes. Required by 5J, 5K, 5L. Separate build item, estimate 1-2 weeks. | None |
| 5-PREREQ: LoL data pipeline | Oracle's Elixir CSVs + Riot API for solo queue. Required by 5M. | None |
| 5J: Player form EWMA | EWMA on KPR, ADR, KAST, opening kills. | 5-PREREQ HLTV |
| 5K: Meta-shift detection | Post-patch monitoring. During post-patch window: reduce position sizes (model uncertainty > market inefficiency until model re-calibrated on new patch data). | 5B + 5-PREREQ HLTV |
| 5L: Bayesian roster changepoint | BOCD for roster swap detection. | 5H + 5-PREREQ HLTV |
| 5M: LoL draft analysis | Embedding-based DNN for draft outcomes. | 5-PREREQ LoL |
| 5G: WebSocket upgrade | Architecture design needed. | Design session |

**EB prediction gate:** 300 predictions with wider CI.

---

## WEEKS 4-10 — Phase 6: WeatherBot Elevation (WB-scoped, extended for 6J)

**Gate:** Phase 1B calibration results (CRPS/PIT).

Station mapping MOVED HERE from Phase 12 (resolves 6N↔12A circular dependency).

**Suggested execution order:** Sub-phase A (Weeks 4-5): 6A, 6B, 6-STATION, 6G, 6H, 6O. Sub-phase B (Weeks 6-7): 6C ∥ 6K, 6D, 6E. Sub-phase C (Weeks 8-10): 6L, 6F, 6J, 6M, 6N, 6P, 6Q, 6I.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 6A: S167 P0 reentry_check (full fix) | 13-module refactor. | D10 interim deployed |
| 6B: Orphan reconciliation (ongoing) | Scheduled recurring. | 1E complete |
| 6-STATION: Station-to-model mapping (was 12A) | Map Polymarket cities → exact ICAO station codes. Start top 5. Moved here because 6N, 6K, 6L all depend on it. | None |
| 6C: Ensemble forecast integration | GEFS + ECMWF ENS. | 1B calibration gate |
| 6K: AIFS ENS integration | 51-member ML ensemble, free via Open-Meteo. Can parallelize with 6C — AIFS is just another data source. | 6-STATION |
| 6L: Multi-model ensemble | GEFS + AIFS + HRRR. BMA weighting. | 6C + 6K (parallel) |
| 6D: Per-side beta calibration | 3-param Kull. | 200-500 samples per side |
| 6E: EMOS recalibration | Ensemble Model Output Statistics. | CRPS/PIT shows bias |
| 6F: YES-side graduated strategy | Edge → Brier gating → beta cal → Murphy. | 6D |
| 6G: GHCN LRU cache | Replace permanent cache. | None |
| 6H: Shadow entry pruning | Review utility. | None |
| 6I: WebSocket upgrade | Real-time price feeds. | Architecture design |
| 6J: Agile city rotation (minimal) | Auto-discover new cities, dynamic station resolution (ICAO lookup + fallback), calibration cold-start (nearest-city prior), retirement logic. This is a mini-product — estimate 2-3 weeks alone. Sub-plan needed: discovery → resolution → cold-start → retirement. | 6C + 6E + 6-STATION |
| 6M: Conformal prediction | MAPIE wrapper for brackets. | Any base model |
| 6N: Neural post-processing MLP | Station-specific MLP, CRPS loss. | 6-STATION (dependency resolved by moving station mapping here) |
| 6O: Lead-time optimal window | Backtest existing WB trades to measure actual optimal lead time. Don't guess at 6-24h — measure it. | Historical trade data |
| 6P: Bayesian small-sample calibration | Beta-Binomial for <50 resolved markets per city. | 6J city rotation |
| 6Q: WB position sizing upgrade | If calibration improves (whole point of Phase 6), sizing should scale with calibration confidence. Implement simple confidence-scaled sizing for WB (not full 8R portfolio Kelly). Without this, WB runs improved calibration with old sizing for months. **Trigger threshold:** CRPS improvement ≥ 5% relative to pre-Phase-6 baseline OR Brier Score improvement ≥ 0.02 absolute, both statistically significant at p<0.05 on ≥100 resolved predictions. Below threshold: keep current sizing. | 6D or 6E calibration improvement confirmed per threshold |

---

## MONTH 2-3 — Phase 7: MirrorBot Elevation (MB-scoped)

**Gate:** 1G + 500+ logged predictions (~7 weeks at 80/week, not 6). If accumulation rate drops below 60/week, extend timeline accordingly — do not lower the 500 threshold.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 7A: Event-driven WebSocket | Sub-second copy-trading. | Architecture design |
| 7B: Wallet selection overhaul | Tighten copy criteria. | 500+ prediction_log rows |
| 7C: Leader-exit signals | Exit when leader exits. | 7A |
| 7D: Basket consensus | Multi-specialist agreement. | 7B |
| 7E: Gate_score expectancy analysis | Per-bucket. | Prediction_log data |
| 7F: Slippage-adjusted paper evaluation | Discount for slippage. | 2J data |
| 7G: Re-entry cooldown review (analytical) | Track re-entry accuracy. Inform whether to re-enable from D8's disable. | Prediction_log accumulation |
| 7H: LLM-augmented signal | RAG-based: news → LLM → probability. For entry decisions (pre-copy evaluation), NOT real-time copy timing — LLM inference takes 5-10s, incompatible with sub-second copy. | Architecture design |
| 7I: Hedge/MWU signal aggregation | Combine LLM + copy + market features. Last item in Phase 7 — requires 7H (LLM), 7B (wallets), and existing copy signal all active. | 7H + 7B + existing signals |
| 7J: Concept drift (ADWIN-U) | Unsupervised drift detection — correct for delayed resolution. | 1G working |
| 7K: Venn-ABERS calibration (MB-specific) | Provably calibrated prediction intervals for MirrorBot copy signals. | Calibration data |

---

## MONTH 2-4 — Phase 8: Cross-Bot + Infrastructure (split across 2 months)

### Month 2 — Infrastructure items (should precede bot-specific work):

| Item | Details |
|------|---------|
| 8A: Central position registry | Full cross-bot mutual exclusion. |
| 8B: Prediction gate decisions | MB 500+, EB 300. Kill = BOT_ENABLED=false in .env, systemctl disable service, keep code intact. Reversible if root-cause fix found. Retrain = unfreeze retraining with new training data. Proceed = proceed to live at 25% previous size. Gate: if calibration data insufficient at threshold, run on whatever's available + acknowledge wider CI. |
| 8P: Evidently AI drift monitoring | Data drift, concept drift, prediction drift. |

### Month 3-4 — Bot-specific items:

| Item | Details |
|------|---------|
| 8C: Correlation_id propagation | End-to-end trace. |
| 8D: Exit parameter sweep | Replay resolved positions. |
| 8E: Unpriced token dual-write to PG | |
| 8F: Unpriced token escalation | |
| 8G: market_id_mapping table | |
| 8H: Cross-bot calibration framework (Platt scaling) | Generalizes calibration across all bots. Venn-ABERS for MB done in 7K; this is the shared Platt scaling infrastructure for EB/WB. |
| 8I: pg_partman for trade_events | |
| 8J: systemd service templates | |
| 8K: Remove dead EnsembleBot code | EnsembleBot is DEAD (0 positions, last activity Mar 6, not in BOT_REGISTRY). Remove bot code, update 8 dependent test files to not import it. No revival path — if ensemble logic needed later, build fresh. |
| 8L: CLV scaling evaluation | |
| 8M: RL Trade Timing Agent evaluation | |
| 8R: Fractional Kelly portfolio controls | Requires calibration data for ≥2 of 3 bots. If only WB qualifies, implement single-bot Kelly for WB. Portfolio-level Kelly deferred until second bot qualifies. |

### REMOVED from Phase 8 (folded into bot phases):

- 8N (F-EDL) → fold into each bot's elevation phase as per-bot model output change
- 8O (Shadow mode) → promoted to Phase 1L
- 8Q (Feast) → promoted to Phase 2K

---

## MONTH 3-4 — Phase 10: Strategic Foundation

| Item | Details | Prerequisite |
|------|---------|-------------|
| 10D: Telegram alert bot | 5-min cron, ~50-100 lines. | 2F health check |
| 10F: Minimum viable backtester | Event-driven replay. Results advisory below 5,000 orderbook snapshots per market. At Month 3-4, ~2-3 months of 60s data = ~2,500 scans for WB — directional, not definitive. Treat as confidence-building, not validation. | 1J data (accumulating since Week 1) |

---

## MONTH 4-8 — Phase 12: WeatherBot EMOS Transformation

Station mapping already done in Phase 6. Build on it.

| Item | Details | Prerequisite |
|------|---------|-------------|
| 12B: Ensemble data access | GEFS, ECMWF ENS, HRRR. | API integration |
| 12C: Station-specific EMOS training | Gaussian EMOS, CRPS minimization. MVP: top 5 cities. | 6-STATION + 12B |
| 12D: Multi-model blending | Lead-time-dependent weights. | 12C |
| 12E: Automated edge pipeline | Market discovery → EMOS → edge → order. | 12C + 12D |
| 12F: City mastery extension | Extend Phase 6J's minimal rotation system with EMOS integration. Don't build it twice — Phase 6J is the foundation, Phase 12F extends it. | 12E + 6J |

---

## ONGOING — Phase 13: Compliance + Observability

| Item | Details | Prerequisite |
|------|---------|-------------|
| 13A: Tax position establishment | Consult tax professional. | None |
| 13B: Trade logging completeness | Verify trade_events for tax. | None |
| 13C: State residency monitoring | Track regulatory actions. | None |
| 13D: Streamlit dashboard | ~150-200 lines, 80-150MB RAM. | 10D |
| 13E: PG LISTEN/NOTIFY | Near real-time dashboard. | 13D |

---

## Decisions

- **WeatherBot:** KEEP RUNNING with guardrails
- **market_prices:** KILL (Option B, after pgBackRest + slippage refactor)
- **D7:** Per-bot stop-loss, shared check REPLACES EB-specific override (one code path)
- **D8:** 24h re-entry cooldown, signal override in MB only (don't touch base_bot)
- **D10:** TTL = min(time_to_resolution, 6h) floor 1h
- **PG OOMScoreAdjust:** -900 (not -1000)
- **Pool settings:** Investigate first, conservative reduction, 48h monitor with rollback trigger
- **Calibration gate:** WB immediate, MB/EB after prediction_log fix, May 12 hard deadline
- **EB prediction gate:** 300 (not 800)
- **1I as graduated gate:** ≥0.9 → full elevation, 0.7–0.9 → core items only, <0.7 → root-cause investigation
- **NegRisk:** REMOVED
- **Maker orders:** REMOVED
- **Phase 5/6J city rotation:** Incremental build — minimal in Phase 6J, extend in Phase 12F. Don't build twice.

---

## Changes v5.1 → v6.0

- D5 (backup) moved to FIRST ACTION — before any config changes
- PG OOMScoreAdjust → -900 (not -1000, avoids OOM thrash)
- D3 dedup key specified — RESOLUTION on (market_id, bot_name), EXIT on (market_id, bot_name)
- D5 marked as superseded by pgBackRest once 1H stable
- D7 REPLACES EB-specific check — one code path, not two
- D8 re-entry → 24h cooldown (not ambiguous "or disable entirely")
- D10 TTL → min(time_to_resolution, 6h) floor 1h (not arbitrary 4h)
- 1B MB/EB → "N/A until Phase 7/5 gates" (not "blocked" which implies temporary)
- 1E split into 1E-a (migration) + 1E-b (gateway) — separate commits
- 1I is HARD GATE for Phases 5-7
- 1J rate limits specified — prioritize by volume if API throttling exceeded
- 1L: Shadow mode protocol promoted to Phase 1 (was 8O Month 2-3)
- 1M: Strategy lifecycle schema promoted to Phase 1 (was 10B Month 3-4)
- 2K: Feast promoted to Phase 2 (was 8Q Month 2-3) — precedes elevation phases
- 2G rollback plan added — revert if >5 pool exhaustion events in 48h
- 2H per-bot multiplier — WB 10×, MB 5×, EB 3×
- Phase 5 scoped to 6-8 weeks (not 2 weeks). Core vs deferred split. Hidden data pipeline dependencies flagged (HLTV, Oracle's Elixir, Liquipedia).
- 5C+5O merged — evaluate before building
- 5N → MapieClassifier (not MapieTimeSeriesRegressor)
- 6-STATION moved from Phase 12 to Phase 6 — resolves circular dependency
- 6C ∥ 6K parallelized — AIFS is another data source, doesn't depend on basic ensemble
- 6J acknowledged as mini-product (2-3 weeks, needs sub-plan)
- 6O → backtest actual lead time (don't guess)
- 7H → entry decisions only (LLM too slow for real-time copy timing)
- 7I → last item in Phase 7 (needs all signals active)
- Phase 7 timeline → 7 weeks (not 6) for 500+ predictions
- Phase 8 split across 2 months — infrastructure Month 2, bot-specific Month 3-4
- 8N (F-EDL) folded into bot phases — per-bot model output change
- 12F extends 6J — don't build city rotation twice
- Operational procedures added — rollbacks, maintenance windows, testing requirements, shadow mode, capital during elevation
- 4D → verify tests pass first before committing

---

## Success Criteria (8-month plan exit)

- All 3 bots have P(edge > 0) ≥ 0.7 (measured via 1I methodology on post-fix data)
- Total portfolio maximum drawdown under 25% of deployed capital
- Zero unresolved P0 audit items
- All prediction gates met: WB calibrated (CRPS/PIT), MB 500+ predictions, EB 300+ predictions
- Shadow mode protocol used for every model change — no exceptions
- Backups operational (pgBackRest PITR or pg_dump daily minimum)
- If any bot fails its prediction gate at 8B: killed or retrained per the defined criteria, not left running with negative expectancy

## Verification

- pytest 1878+ pass after each commit
- Every new subsystem has integration tests before merge
- Rollback tested for schema changes
- D5 backup exists before ANY Day 1 config changes
- 1I edge verification gates Phases 5-7
- 1J orderbook collection running (check rate limit compliance)
- Shadow mode protocol documented and followed for all model changes
- Phase 5: EB core items complete, data pipeline dependencies enumerated
- Phase 6: Station mapping complete, CRPS/PIT delta measured, 6C∥6K parallelized
- Phase 7: 500+ prediction_log rows (7 weeks), LLM signal used for entry only
- Phase 8: Prediction gates at 300 (EB) / 500 (MB) — run on available data with explicit CI
- Phase 12: EMOS top 5 cities, city rotation extends (not rebuilds) Phase 6J

---

## Critical Files

- **Day 1:** risk_manager.py (D7), mirror_bot.py (D8), weather_bot.py (D10), config/env
- **Phase 1:** frozen_price_check.py (1A), calibration_check.py (1B), order_gateway.py (1E-b), weather_bot.py (1E-a), mirror_bot.py+esports_bot.py (1G), esports_bot.py (1F), new edge_verification.py (1I), orderbook cron (1J), shadow mode doc (1L), strategy lifecycle migration (1M)
- **Phase 2:** logging_setup.py (2C,2D), health_check.sh (2F enhance), slippage_check.py (2J), Feast config (2K)
- **Phases 5-7:** Bot files, .env files, model files, WebSocket handlers, rating system implementations
- **Phases 10-12:** Telegram bot, backtester, EMOS pipeline, city mastery system

---

## Corrections Log

### S180 (2026-04-17) — Retraction of S179 §3 "position_manager.py:930" bug claim

**Claim (in S179 handoff §3, now superseded):** Stage-2 illiquidity CLOB call at `base_engine/execution/position_manager.py:930` used wrong kwargs (`size=` instead of `trade_size=`, missing `market_id=`), raising TypeError silently, meaning stage-2 never executed in production. Allegedly blocked 2I (illiquidity exit enablement).

**Retraction:** The claim was a misread. Verified by S180 (2026-04-17) against code at HEAD:

```python
# base_engine/execution/position_manager.py:929-932
_liq_result = await asyncio.wait_for(
    _lg.check_liquidity(market_id=_mid, token_id=_token_id, trade_size=size, side="SELL"),
    timeout=5.0,
)
```

where `_mid = str(getattr(position, "market_id", ""))` (L917) and `_token_id = getattr(position, "token_id", "")` (L928). Kwargs match `check_liquidity()` signature at `base_engine/risk/liquidity_guardian.py:25-33`. `tests/unit/test_illiquidity_exit.py:173-192` exercises stage-2 and passes.

**Consequence:** 2I is CODE READY (not blocked). Any spawned "fix position_manager.py:930" task is a false positive — dismiss.

**Kept as durable record** because `AGENT_HANDOFF_*.md` is gitignored (`.gitignore:147`) — handoff retractions don't propagate cross-machine without a breadcrumb here.

### S180 Hygiene Backlog

**rollback.sh service-list drift.** `deploy/deploy.sh` step 6 starts 4 services (weather, mirror, esports, ingestion). `deploy/rollback.sh:41` restarts only 3 (missing ingestion). The bug isn't "missing line" — it's that `rollback.sh`'s service list has drifted from `deploy.sh`'s service list and there is no mechanism keeping them in sync. Fix options (prefer A):

- **A.** Shared constants file, e.g. `deploy/common.sh` exporting `BOT_SERVICES=(...)`, sourced by both `deploy.sh` and `rollback.sh`.
- **B.** Minimum viable: pinned comment at the top of each script pointing at the other with a note "keep service list in sync." Drift-detectable by grep.

Discovered S180 (2026-04-17). Safe to defer: none of the S180 commits touched the ingestion path, so rollback's ingestion gap was harmless for the 2026-04-17 deploy. Will become dangerous next time an ingestion-affecting commit ships and rollback is needed.
