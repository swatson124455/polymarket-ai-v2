# EsportsBot (EB) Asset Ledger

**Author:** EB session, 2026-06-23. Freshly verified against the live DB + git this date (8-agent enumeration + completeness critic + honesty audit, workflow `w8fj321pa`). All audit corrections applied.

## Ownership principle
**EB owns anything it reads or writes.** "Shared with other bots" is a *change-authority* note, never an ownership denial. Per RULE FOUR, EB owns the shared-module **code** it needs (can change on the `eb/main` splinter). Per RULE THREE, EB's authority over shared **runtime infra** (live PG, PgBouncer, ingestion/`elite_detector`, `/opt/pa2-shared/.env`) is propose-only — but EB still owns its dependency/stake. The only assets *not* EB's are name-pattern false positives EB never touches.

**Ownership:** `EB` = EB-exclusive · `EB-shared` = EB owns its stake, other bots use it too · `NOT-EB` = name-match only, EB never reads/writes.
**Disposition:** 🟢 KEEP-INFRA (reusable) · 🔵 KEEP-DATA · 🔴 DEAD-SIGNAL (ratings model, no reuse) · ⚪ EMPTY/STALE · 🟡 QUARANTINE (exclude from analysis).

## Context for verdicts
EB is HALTED (`ESPORTS_ENTRY_HALT=true`), V2 disabled, pivoting from a dead ratings model to a sharp-line (Pinnacle) strategy. Verified live: `ESPORTS_ENTRY_HALT=true`, `BOT_ENABLED_ESPORTS=true`, `_V2=false`, `_LIVE=false`, `ESPORTS_MAX_MODEL_DIVERGENCE=0.10`. The ratings model has no signal edge; model-agnostic infrastructure (backtest/devig/CLV/calibration/execution) is the value. Markets ARE liquid (operator-confirmed) — never judge liquidity from `markets.liquidity`/`volume` or `orderbook_snapshots`.

---

## A. EsportsBot DB tables (EB-owned, fresh counts 2026-06-23)

| Table | Own | Rows | Disp | Note |
|---|---|---|---|---|
| `esports_matches` | EB | 32,369 (lol 24,382 / cs2 7,987) | 🔵 | Labeled outcomes 2024–2026. Most valuable dataset; backtest ground truth. |
| `esports_training_data` | EB | 17,729 | 🔵 | Labeled (game_state, outcome) snapshots. Feature derivation dead; raw labels retainable. |
| `esports_predictions` (V2 shadow) | EB | 1,473 (955 resolved; **1,438 clean** v2-trinity, 35 contaminated) | 🔵 | Has `pinnacle_odds`/`market_price`/`actual_winner` = sharp-line schema. Keep as template + calib data. |
| `esports_prediction_log` (V1) | EB | 1,234 (633 resolved) | 🟢 | **Active** — feeds live calibrator (`esports_bot.py:82-97`). Model-agnostic cols. |
| `esports_unmatched_predictions` | EB | 1,453 | 🟢 | Resolver-failure diagnostic; tunes match→market mapping. |
| `esports_team_aliases` | EB | 1,777 | 🟢 | The resolver. Pinnacle↔Polymarket name mapping. Directly reusable. |
| `esports_calibration` | EB | 4 (lol/cs2 updated today; dota2/valorant stale Mar-29) | 🟢 | Per-game Brier+Kelly. Aggregator runs independent of entry-halt. |
| `glicko2_ratings` | EB | 1,205 | 🔴 | Glicko has no edge (autopsy). Ratings state. Drop after pivot locks. |
| `esports_odds` | EB | 0 | 🟢¹ | **Pinnacle landing-zone** (`is_closing`/`source`). Empty but first table the rebuild writes. |
| `esports_market_map` | EB | 0 | ⚪ | Resolver-output landing-zone (match→token). Keep schema. |
| `esports_match_maps` | EB | 0 | ⚪ | Per-map BO3/5 results. Marginal forward value (map-winner markets). |
| `esports_features` | EB | 0 | 🔴 | Ratings-only feature cache (p_elo/p_glicko/trinity). No reuse. |
| `esports_ratings` | EB | 0 | 🔴 | Ratings state. No reuse. |
| `glicko2_player_ratings` | EB | 0 | 🔴 | Player ratings. Dead. |
| `esports_live_events` | EB | 0 | ⚪ | In-play feature, never ran; pre-match thesis won't use. |
| `esports_players` | EB | 0 | ⚪ | Roster; only player-ratings need it. |
| `esports_teams` | EB | 0 | ⚪ | Empty; aliases table covers the need. |
| `esports_patch_history` | EB | 0 | 🔴 | Ratings-retrain trigger. No sharp-line use. |

¹ Empty but rebuild-critical — the explicit Pinnacle CLV target.

## B. Shared DB tables EB uses (EB owns its stake)

| Table | Own | EB footprint | Disp | Note |
|---|---|---|---|---|
| `trade_events` | EB-shared | EB rows present; **canonical bot_pnl.py 2400h (verified 2026-06-23): 103 entries / 46 exits / 144 resolutions, 0 open, all-time clean realized −$1,562.63** | 🟢 | EB's trade ledger. Raw psql counts drift (partitioned) — bot_pnl.py is canonical. |
| `positions` | EB-shared | EsportsBot 984 / V2 4 | 🟢 | EB's position lifecycle (restore-on-startup). |
| `shadow_fills` | EB-shared | EsportsBot 7,890 / V2 5 | 🟡 | EB's fills — but microstructure cols broken (Landmine 5); exclude from slippage/capacity. |
| `prediction_log` (fleet) | EB-shared | EsportsBot 15 / V2 216 = 231 (table total 2,517,840) | 🟢 | EB's esports rows + calibration read-path. WB cutover coordinates schema only. |
| `trade_signals` | EB-shared | EsportsBot 505 (total 17,821) | 🟢 | EB's signal-attribution rows. |
| `traded_markets` | EB-shared | 636 EB | 🟢 | EB's per-market trade-state. |
| `equity_snapshots` | EB-shared | EsportsBot 67 / V2 4 | 🔵 | EB's daily equity curve. |
| `markets` | EB-shared | ~17,435 cat=esports (~60% politics-polluted; **true content 6,998**) | 🟢 | EB's universe. Ingestion-populated. Use content-filter, not category. Don't read liquidity. |
| `market_categories` | EB-shared | ~11,213 cat=esports | 🟢 | EB's universe (secondary). |
| `market_prices_latest` | EB-shared | 39,368 total; **338 EB-market** | 🟢 | EB's price source. |
| `market_prices` | EB-shared | **63GB — count UNVERIFIED** (no-scan) | 🟢 | EB's price history for CLV backtest. |
| `orderbook_snapshots` | EB-shared | **count UNVERIFIED** (no-scan) | 🟢 | EB's liquidity source. Don't judge capacity from it while halted. |
| `market_aliases` | EB-shared | 5,263 (no EB partition) | 🟢 | condition_id aliases; resolver uses indirectly. |

## C. NOT EB — name-match false positives (EB never reads/writes; listed for completeness)

| Table | Rows | Owner |
|---|---|---|
| `signals` | 424,169 | shared, no bot_name; EB doesn't write |
| `predictions` | 0 | legacy, unused |
| `strategy_predictions` | 0 | legacy |
| `signal_quality` | 0 | EB's `signal_quality` refs are an in-code metric, NOT this table |
| `confidence_calibration` | 0 | shared, EB doesn't write |
| `sports_calibration` | 0 | superseded by `esports_calibration` |
| `prediction_log_pre_clamp_snapshot` | 8,720 | WB clamp-migration artifact |
| `momentum_false_signals` | 0 | DELETED MomentumBot |
| `mirror_rejected_signals` | 15,034,100 | MirrorBot (RULE ONE) |
| `weather_calibration` | 23,922 | WeatherBot |
| `weather_tail_calibration` | 0 | WeatherBot |

---

## D. Filesystem data

| Path | Own | Size | Disp | Note |
|---|---|---|---|---|
| `data/lol/` (3 Oracle CSVs 2024/25/26) | EB | 175 MB | 🔵 | Primary LoL training/backtest data. |
| `data/paper_trading.log` | EB-shared | 3.3 GB | 🔵 | Full operational history (mtime Jun-22). |
| `data/esports_matches_bulk.jsonl` | EB | 13 MB / 28,213 lines | 🔵 | Historical matches (mtime May-15). |
| `data/cs2/pandascore_cs2.json` | EB | 2.3 MB | 🔵 | CS2 training data. |
| `data/model_cache.pkl` | EB | 9.9 MB | 🔴 | Trinity weights. No edge. |
| VPS `model_cache.pkl` + `.bak` | EB | 82 MB | 🔴 | Delete from VPS. |
| VPS `esports_cs2/lol_model.pkl` | EB | 179 KB | 🔴 | Per-game Glicko snapshots. |
| VPS `paper_trading.log*` | EB-shared | 318 MB | 🔵 | Log archive. |
| `data/catboost_info/` | EB | **UNVERIFIED** (timeout) | 🔴? | Likely CatBoost training artifacts. |

---

## E. Code — `esports/` (V1 library, ~45 files, all `EB`)

**🔴 DEAD-SIGNAL (per-game ML / ratings, 16):** `models/lol_win_model.py`, `cs2_economy_model.py`, `dota2_model.py`, `valorant_model.py`, `cod_model.py`, `r6_model.py`, `sc2_model.py`, `rl_model.py`, `catboost_draft_model.py`, `draft_features.py`, `tabpfn_ensemble.py`, `esports_trainer.py`; `data/aligulac_client.py`, `ballchasing_client.py`, `hltv_scraper.py`, `opendota_client.py`.

**🟢 KEEP-INFRA (model-agnostic):** `models/conformal_wrapper.py`, `venn_abers_calibrator.py`, `cot_validator.py`, `patch_drift.py`, `series_model.py` (BO3/5 math); `calibration/bias_decomposition.py`, `metaculus_benchmark.py`; `backtest/walk_forward.py`; `data/esports_db.py` (844L DB layer), `esports_data_collector.py`, `pandascore_client.py`, `riot_api_client.py`, **`oddspapi_client.py` (Pinnacle/CLV devig client — most rebuild-critical file)**; `kelly/esports_bankroll_manager.py`; `markets/esports_market_scanner.py`, `esports_market_service.py`; the 8 `__init__.py` package markers (`esports/__init__.py` + 7 subpackages).

**Borderline / other:**
- `models/glicko2.py` — 🔴 engine code is reusable *only* if ratings revived; Glicko signal has no edge (autopsy); sharp-line won't use it.
- `models/onnx_compiler.py` — ⚪ no active XGBoost.
- `live/esports_event_detector.py`, `esports_game_monitor.py`, `esports_live_trigger.py` — ⚪ model-agnostic polling/cooldown infra, but pre-match thesis won't use in-play.

## F. Code — `esports_v2/` (~32 files, all `EB`, on `eb/main`)

**🟢 KEEP-INFRA (the ~80%-built rebuild core):** `backtest/metrics.py` (Brier/CLV/ECE/z-score), `backtest/walk_forward.py` (leakage control), **`model/clv.py` (Shin-devig — EXISTS)**, `model/calibrator.py` (Venn-ABERS), `model/conformal.py` (MAPIE), `model/pipeline.py` (swap XGBoost→sharp signal), `shadow/db.py`, `shadow/match_converter.py`, `shadow/metrics.py`, `scripts/run_backtest.py` (shuffle-control), `scripts/fetch_data.py`, `load_historical.py`, `load_matches_to_db.py`, `shadow_report.py`, `data/odds_loader.py` (Pinnacle/OddsPapi), `data/normalizer.py`, + 7–8 `__init__.py`.

**🔵 KEEP-DATA tooling:** `data/oracle_loader.py`, `data/pandascore_loader.py`, `data/grid_loader.py` (historical match ingest).

**🔴 DEAD-SIGNAL:** `ratings/trinity.py`, `ratings/elo.py`, `ratings/glicko2.py`, `ratings/openskill_engine.py`, `model/meta_model.py` (XGBoost on Trinity features). *(`ratings/__init__.py` itself is 🟢 — empty package marker; mark the modules dead, not the marker.)*

## G. Bot entrypoints (all `EB`)

| File | Lines | Disp | Note |
|---|---|---|---|
| `bots/esports_bot.py` (V1) | 7,633 | 🟢 | **Hybrid** — dead model inside, but the only working scan/exec/resolution/position harness. Keep, swap signal. |
| `bots/esports_bot_v2.py` | 1,048 | 🔴 | Trinity+XGBoost entry; disabled. Holds matcher fix `09ecf91` (`_recheck_awaiting_markets`) on eb/main. |
| `bots/esports_live_bot.py` | 351 | 🔴 | Per-game ML + live events; never enabled. |
| *EsportsSeriesBot* | — | — | **merged into EsportsBot** (`main.py:351` stub, heartbeat suppressed). Live remnant = `series_model.py` 🟢. Not a separate bot. |

## H. Scripts/tools

| Script | Own | Disp | Note |
|---|---|---|---|
| `scripts/bot_pnl.py` | EB-shared | 🟢 | EB's canonical P&L. RULE FOUR: EB can modify on splinter. |
| `scripts/seed_esports_team_aliases.py` | EB | 🟢 | Seeds the resolver (rapidfuzz, idempotent). |
| `scripts/backfill_esports_resolution_events.py` | EB | 🟢 | Idempotent RESOLUTION backfill. |
| `scripts/esports_diag.py` | EB | 🟢 | Read-only diagnostic. |
| `scripts/esports_72h_cohort.py` | EB | 🟢 | Cohort analysis. |
| `scripts/esports_charts.py`, `esports_48h_charts.py`, `esports_48h_visual.py` | EB | 🟢 | Visualization/monitoring. |
| `scripts/seed_esports_data.py` | EB | 🔵 | One-shot (already run); data kept. |
| `scripts/esports_v2_shadow_eval.py` | EB | 🔴 | Trinity evaluator; keeps the S209 corrected-Brier formula as reference. |
| `scripts/eb_resolution_backlog.py` | EB | ⚠️🔴 | **DO-NOT-RUN** — injects phantom positions (one-sided P&L). |
| `scripts/esports_48h_charts.png` | EB | ⚪ | Regenerable artifact. |
| `scripts/redeem_and_retrade.py` | NOT-EB | — | MirrorBot's; "esports-compatible" but EB doesn't invoke it → not EB's until wired. |

## I. Schema migrations (all `EB`, all 🟢 KEEP — definitions/landing-zones, downs present where noted)

`024_esports_tables.sql` (legacy V1 BIGSERIAL, superseded by 072 — keep for rollback), `029_esports_training_data.sql`, `030_esports_prediction_log.sql`, `031_glicko2_ratings.sql`, `053_esports_schema_fixes.sql` (adds `closing_price` = CLV infra), `057_esports_prediction_log_dedup.sql` (unique idx), `060_glicko2_player_ratings.sql` (+down), `061_add_raw_model_prob.sql` (+down), `072_esports_v2.sql` (**LIVE schema**, +down), `074_esports_team_aliases.sql` (+down), `075_esports_default_now.sql` (+down).

## J. Config flags (all `EB`, all verified in `config/settings.py` this run)

**🟢 KEEP — live gates / rebuild toggles:**
- `ESPORTS_ENTRY_HALT` (eb/main only, :1442) — the primary halt gate. **Live=true.**
- `BOT_ENABLED_ESPORTS` / `_V2` / `_LIVE` (:1294-1296 master) — process/variant toggles.
- `ESPORTS_MAX_MODEL_DIVERGENCE` / `_LOW_SAMPLE_DIV_CAP` (:1449-1450) — the brake; **live=0.10.**
- `ESPORTS_PINNACLE_ENABLED` (:1541 both) — **the pivot toggle** (currently false).
- `ESPORTS_V1_MODEL_ENABLED` / `_V1_DATA_CLIENTS_ENABLED` (eb/main :1287-1288) — kill-switches for the dead model (default off).
- `ESPORTS_STALL_STARTUP_GRACE_S` (eb/main :59) — watchdog grace (reliability).
- Edge family: `ESPORTS_MIN_EDGE` / `_ENTRY` / `_HOLD` / `_SERIES_MIN_EDGE` / `_REENTRY_MIN_EDGE` / `_MARKET_FALLBACK_MIN_EDGE` / `_COT_EDGE_THRESHOLD`.
- **~90 model-agnostic** sizing/risk/exposure/Kelly/stop-loss/drawdown/exit-cooldown/WS/monitor/scan flags (master ~131 unique `ESPORTS_*`, eb/main ~135).

**🔴 DEAD-SIGNAL flags:** `ESPORTS_GLICKO2_TAU_{CS2,LOL,DOTA2,VALORANT,SC2,DEFAULT}` (:1502-1507), `ESPORTS_CATBOOST_*` (:1519-1522), `ESPORTS_DRAFT_*` (:1514-1516), `ESPORTS_LOL_HEURISTIC`/`BLUE_SIDE`/`GOLD_DIFF`/`TOWER` (:1330,1496,1410-1411), `ESPORTS_RFLB_STRENGTH` (:1494), `ESPORTS_CONFORMAL_*` (:1468,1491-1492), `ESPORTS_MODEL_MIN_ACCURACY`/`MAX_BRIER`/`RETRAIN` (:1326-1329).

## K. Shared core EB runs on (`EB-shared`, RULE FOUR — EB owns + can change on splinter)
Documented dependencies from CLAUDE.md (not individually file-enumerated this run): `base_engine/data/database.py`, `base_engine/execution/order_gateway.py`, `position_manager.py`, `base_engine/risk/risk_manager.py`, `BotBankrollManager`, `base_engine/data/websocket_manager.py`, `base_engine/coordination/trade_coordinator.py`, `base_bot.py`, `main.py`. EB depends on all of these; change-authority = splinter (code) / propose-only (shared runtime infra per RULE THREE).

## L. Docs (`EB`, KEEP — institutional memory)
`EB_CLEAN_DATA_QUARANTINE.md` (🟢 mandatory backtest reference), `BOT_ESPORTSBOT.md`, `BOT_ESPORTSLIVEBOT.md`, `BOT_ESPORTSSERIESBOT.md`, `EB_COORDINATION_*` (incl. elite-detector storm memos), `S203_EB_ROUTING_AUDIT.md`, `AUDIT_ESPORTSBOT_S127.md`, + the `AGENT_HANDOFF_ESPORTS_SESSION*` / `PROMPT_ESPORTSBOT_SESSION*` corpus (44+ tracked `.md`). Not enumerated line-by-line; keep as handoff/audit history.

## M. UNVERIFIED (honest gaps — no value claimed)
- **Redis keys:** auth failed (WRONGPASS). No counts — zero is a false-negative, not a real count. Code-level inference: EB writes ~no esports-specific Redis keys.
- `market_prices` size / `orderbook_snapshots` count / VPS `catboost_info/` — not scanned (perf/timeout).

---

## Bottom line
- **Keep (rebuild foundation):** all labeled match/prediction data, the resolver (`esports_team_aliases`), the ~80%-built model-agnostic `esports_v2/` pipeline (Shin-devig, CLV, backtest, calibration), the execution harness in `esports_bot.py`, all migrations/config/shared-core, the Pinnacle landing-zone (`esports_odds`).
- **Dead (ratings model):** Trinity/Glicko/Elo/OpenSkill/XGBoost + per-game ML predictors + their data clients, weights (`.pkl`), and tuning flags.
- **Quarantine:** `shadow_fills` microstructure; the `category='esports'` filter.
- **Genuine gap:** `pinnacle_odds` is empty — the sharp signal must be collected forward.
- **Not EB:** only the ~11 name-match false positives in §C + `redeem_and_retrade.py`. Everything else EB touches is EB's.
