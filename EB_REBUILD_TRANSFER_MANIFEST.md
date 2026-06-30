# EB Rebuild — Transfer Manifest

**What this is:** the complete, completeness-verified list of EsportsBot items to carry into a rebuild, each with a one-line TL;DR of what it does. Compiled + verified 2026-06-23 against ground truth (`git ls-files` / `psql` / `ls`). Every esports/glicko file, table, data file, and migration is accounted for (here = transfer; the dead ratings-model set is in `EB_ASSET_LEDGER.md`).

**Companion docs (same folder):**
- `EB_REBUILD_DATA_ONLY.md` — raw verified facts with reproduce-commands; functional-check results (§8); bug actions (§9).
- `EB_ASSET_LEDGER.md` — full asset inventory incl. the EXCLUDED dead-ratings set, with ownership.
- `EB_REBUILD_CARRYFORWARD.md` — postmortem (186-instance error audit) + guardrails for the rebuild.
- `EB_CLEAN_DATA_QUARANTINE.md` — data landmines / clean-substrate filters.
- `EB_SESSION_ERROR_AUDIT.md` — the full itemized error list.

**State as of compile:** EsportsBot HALTED (`ESPORTS_ENTRY_HALT=true`), V2 off. eb/main has commits `00b856b` (V1 quarantine) + `d5234ab` (removed mis-oriented brier/clv from `get_shadow_stats`). 35 contaminated `esports_predictions` rows DELETED (backup `/tmp/eb_contaminated_backup_20260623.csv`).

**Verified functional:** 16 carry modules import OK; 359 esports tests pass; all data files parse; `shin` 0.2.2 installed on the prod venv.

---

## DATABASE TABLES (with data)
- **esports_matches** — 32,369 rows (historical match results: teams, winner, scores, best_of, patch, date)
- **esports_training_data** — 17,729 rows (per-match labeled rows: game_state_json + win/loss outcome)
- **esports_predictions** — 1,438 rows (V2 prediction log: p_model, conformal_set, market_price, pinnacle_odds, edge, actual_winner) ⚠ market_price↔p_model orientation unreliable
- **esports_prediction_log** — 1,234 rows (V1 prediction log: predicted_prob, market_price, edge, actual_outcome, closing_price)
- **esports_unmatched_predictions** — 1,453 rows (predictions with no Polymarket market: team names + closest_question/score)
- **esports_team_aliases** — 1,777 rows (team-name → canonical-name map for market matching)
- **esports_calibration** — 4 rows (per-game/market-type running Brier + Kelly fraction)
- **esports_odds** — 0 rows (empty; schema for external/Pinnacle odds: team_a/b_odds, is_closing)
- **glicko2_ratings** — 1,205 rows (per-team Glicko-2 state: mu/phi/sigma/match_count)
- **positions** (EsportsBot) — 984 rows (open/closed position ledger)
- **trade_events** (EsportsBot) — bot_pnl.py canonical: 103 entries / 46 exits / 144 resolutions, 0 open, all-time clean realized −$1,562.63 (trade ledger; bot_pnl.py is the canonical reader)

## FILES
- **data/lol/*.csv** — 175M (Oracle's Elixir per-player-per-game LoL stats, 2024–2026)
- **data/esports_matches_bulk.jsonl** — 13M (28,213 historical match results)
- **data/cs2/pandascore_cs2.json** — 2.3M (5,000 PandaScore CS2 matches)
- **data/paper_trading.log** — 3.3G (full paper-trade execution log)

## CODE — esports_v2/
- **backtest/metrics.py** (computes Brier/log-loss/ECE/CLV/z-score/ROI/drawdown + pass-gate)
- **backtest/walk_forward.py** (temporal-fold backtest harness with look-ahead leakage control)
- **model/clv.py** (Shin-devig — strips vig from Pinnacle odds → fair prob; computes closing-line value) ⚠ requires `shin` pkg or silently falls back to simple normalization
- **model/calibrator.py** (Venn-ABERS / isotonic probability calibration)
- **model/conformal.py** (MAPIE conformal filter — gates "confident enough to bet" singletons)
- **model/pipeline.py** (wires model → calibration → conformal → Kelly sizing)
- **shadow/db.py** (async DB read/write for shadow-mode predictions + stats; brier/clv removed in `d5234ab`)
- **shadow/match_converter.py** (converts match objects between formats)
- **shadow/metrics.py** (shadow-gate pass/fail criteria on the stats)
- **scripts/run_backtest.py** (end-to-end: load → walk-forward → metrics → shuffle-control → gate → DB)
- **scripts/fetch_data.py** (fetches CS2 from PandaScore + Pinnacle odds from OddsPapi)
- **scripts/load_historical.py** (loads matches from Oracle/GRID/HLTV → feature records)
- **scripts/load_matches_to_db.py** (bulk JSONL → esports_matches loader)
- **scripts/shadow_report.py** (queries shadow stats, runs the gate, prints report)
- **data/odds_loader.py** (fetches historical Pinnacle closing odds via OddsPapi → lookup dict)
- **data/normalizer.py** (RawMatch intermediate type + team-name normalization)
- **data/oracle_loader.py** (parses Oracle's Elixir LoL CSVs → matches)
- **data/pandascore_loader.py** (fetches CS2/LoL matches from PandaScore API)
- **data/grid_loader.py** (loads CS2 match data from GRID/HLTV exports)
- **7× __init__.py** (package markers — needed for imports)

## CODE — esports/
- **backtest/walk_forward.py** (V1 walk-forward backtest harness)
- **calibration/bias_decomposition.py** (per-game bias decomposition + logistic recalibration)
- **calibration/metaculus_benchmark.py** (calibration validation vs a benchmark)
- **data/oddspapi_client.py** (OddsPapi/Pinnacle closing-line client + devig + CLV)
- **data/esports_db.py** (DB layer — log_prediction, resolve, calibration, CLV, P&L)
- **data/pandascore_client.py** (live PandaScore match-data API client)
- **data/riot_api_client.py** (LoL patch-version + schedule client)
- **data/esports_data_collector.py** (historical match collector)
- **kelly/esports_bankroll_manager.py** (Kelly bet-sizing with hard caps + drawdown halts)
- **markets/esports_market_scanner.py** (finds Polymarket esports markets by team/keyword) ⚠ carry with `_recheck_awaiting_markets`
- **markets/esports_market_service.py** (esports market discovery + CLOB price refresh)
- **models/series_model.py** (BO3/BO5 series-win prob from per-map win rate)
- **models/venn_abers_calibrator.py** (Venn-ABERS calibration with finite-sample intervals)
- **models/conformal_wrapper.py** (MAPIE conformal prediction intervals)
- **models/cot_validator.py** (LLM chain-of-thought sanity-gate for high-edge trades)
- **models/patch_drift.py** (detects model-accuracy drift after game patches)
- **models/onnx_compiler.py** (export/load models to ONNX)
- **live/esports_event_detector.py** (classifies live in-game events) ⚠ in-play, not pre-match
- **live/esports_game_monitor.py** (polls PandaScore for live match state) ⚠ in-play
- **live/esports_live_trigger.py** (cooldown + per-match caps for live bets) ⚠ in-play
- **7× __init__.py** (package markers)

## BOT
- **bots/esports_bot.py** (V1 orchestrator — scan/predict/execute/resolve/position lifecycle)

## SCRIPTS
- **seed_esports_team_aliases.py** (populates the alias table via fuzzy match)
- **seed_esports_data.py** (bootstraps historical match data)
- **backfill_esports_resolution_events.py** (backfills missing RESOLUTION trade_events)
- **esports_diag.py** (read-only diagnostic — trades/positions/exposure)
- **esports_charts.py / esports_48h_charts.py / esports_48h_visual.py** (P&L + trade visualizations)
- **esports_v2_shadow_eval.py** (Brier/accuracy eval on shadow predictions)
- **cleanup_eb_resolution_mismatches_2026_05_26.py** (one-time resolution-mismatch cleanup)
- **eb_resolution_backlog.py** (⚠ DO-NOT-RUN — injects phantom-position P&L)

## MIGRATIONS / SCHEMA / CONFIG
- **Migrations — 12 up + 5 down (all git-tracked):**
  - `024_esports_tables.sql` (creates esports_teams/players/matches/match_maps/market_map/calibration/live_events/patch_history)
  - `029_esports_training_data.sql` (creates esports_training_data)
  - `030_esports_prediction_log.sql` (creates esports_prediction_log)
  - `031_glicko2_ratings.sql` (creates glicko2_ratings)
  - `047_brin_indexes.sql` (BRIN index on esports_prediction_log.created_at) ← was missing from earlier "11" count
  - `053_esports_schema_fixes.sql` (adds closing_price + tournament_phase to esports_prediction_log)
  - `057_esports_prediction_log_dedup.sql` (dedup + unique index on esports_prediction_log)
  - `060_glicko2_player_ratings.sql` (creates glicko2_player_ratings) [+ down]
  - `061_add_raw_model_prob.sql` (adds raw_model_prob to esports_prediction_log) [+ down] ← name not "esports*", caught by content
  - `072_esports_v2.sql` (LIVE V2 schema: esports_matches/players/ratings/features/predictions/odds) [+ down]
  - `074_esports_team_aliases.sql` (creates esports_team_aliases + esports_unmatched_predictions) [+ down]
  - `075_esports_default_now.sql` (DEFAULT NOW() on alias tables) [+ down]
  - Incidental (NOT EB schema, listed for completeness): `048_trade_model_linkage.sql` (mentions esports_prediction_log in a string; the table it created was dropped in 052), `071_strategy_lifecycle.sql` (uses 'EsportsBot_glicko2_xgb_v3' only as an example comment).
- **Empty-table schemas (9, no data, arrive via migrations):** esports_features, esports_market_map, esports_match_maps, esports_live_events, esports_patch_history, esports_players, esports_teams, esports_ratings, glicko2_player_ratings
- **Config (config/settings.py):** ESPORTS_PINNACLE_ENABLED (Pinnacle toggle), ESPORTS_ENTRY_HALT (trading halt), ESPORTS_V1_MODEL_ENABLED (V1-model gate) + model-agnostic sizing/risk/Kelly/edge-threshold flags

---

## NOT transferred (dead ratings model — detail in EB_ASSET_LEDGER.md)
esports_v2/model/meta_model.py + ratings/{elo,glicko2,openskill_engine,trinity}.py · esports/models/{catboost_draft,cod,cs2_economy,dota2,draft_features,esports_trainer,glicko2,lol_win,r6,rl,sc2,tabpfn_ensemble,valorant}.py · esports/data/{aligulac,ballchasing,hltv_scraper,opendota}_client.py · bots/esports_bot_v2.py · bots/esports_live_bot.py · data/model_cache.pkl · data/catboost_info/ · 35 deleted contaminated rows.
