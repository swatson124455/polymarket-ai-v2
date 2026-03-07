# EsportsBot — Bot Reference

## Status (as of 2026-03-06 Session 55 continuation)
| Field | Value |
|-------|-------|
| Enabled | YES — all 3 esports bots running |
| Capital | $5,000 pool (shared EsportsBankrollManager with EsportsLiveBot + EsportsSeriesBot) |
| Max bet | $100 (ESPORTS_MAX_BET_USD) |
| Max daily | $500 (ESPORTS_MAX_DAILY_USD) |
| Kelly fraction | 0.25 default |
| VPS State | RUNNING — scanning, finding markets, placing paper trades |
| Last trade | 2 CS2 paper trades (post-fix, edges 12.3% and 19.3%) + 13 pre-fix era |
| Blocker | None — markets found, trades placing |

## 3-Bot Architecture
| Bot | ML Models | Status | Test Coverage |
|-----|-----------|--------|---------------|
| **EsportsBot** | LoL XGBoost + CS2 XGBoost + Glicko-2 + 5-factor confluence | Trading (2 CS2 paper trades placed) | 43% (109 tests) |
| **EsportsLiveBot** | Loads saved pkl models for confidence scoring; event-driven | Active (detecting 2 live games) | 0% |
| **EsportsSeriesBot** | Zero ML — binomial conditional probability math only | Scanning (no BO3+ series active) | 0% |

## Purpose & Strategy
Pre-game and live in-play trading across 4 esports titles: **LoL, CS2, Dota 2, Valorant**.

**Edge source (5-factor confluence score, ≥0.60 required to trade):**
- Model edge (37%) — XGBoost ML model probability vs Polymarket YES price
- Whale direction alignment (23%) — whale_alerts Redis signal
- Orderbook imbalance (18%) — CLOB bid/ask depth ratio
- Prediction freshness (14%) — exponential decay from last PandaScore refresh
- Model agreement (8%) — Glicko-2 team strength vs ML model agreement

**Scan flow:**
1. Auto-retrain models if interval elapsed (LoL every 2h, CS2 every 2h)
2. Check rolling accuracy per game; auto-disable if below threshold
3. Patch drift check via Riot API (if RIOT_API_KEY set) → skip live trading during observation
4. Refresh live match data from PandaScore (rate-limited, 15s interval)
5. Fetch esports markets from DB (keyword-gated, no category filter — LIMIT 5000)
6. For each market: classify game + type → get prediction → check confluence → trade

**Live trading:**
- Scan interval drops to 10s when live matches active
- WS price updates trigger reactive trades when price moves >1% with edge > threshold
- WS uses `_market_token_map` to correctly identify YES/NO token prices

**Market types classified:** `map_winner`, `tournament_winner`, `total_maps`, `first_blood`, `props`, `match_winner`

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/esports_bot.py |
| Live bot | bots/esports_live_bot.py |
| Series bot | bots/esports_series_bot.py |
| LoL model | esports/models/lol_win_model.py |
| CS2 model | esports/models/cs2_economy_model.py |
| Model trainer | esports/models/esports_trainer.py |
| PandaScore client | esports/data/pandascore_client.py |
| Riot API client | esports/data/riot_api_client.py |
| Data collector | esports/data/esports_data_collector.py |
| Prediction logging | esports/data/esports_db.py |
| Market discovery | esports/markets/esports_market_service.py |
| Bankroll manager | esports/kelly/esports_bankroll_manager.py |
| LoL model pkl | data/esports_lol_model.pkl (trained, saved) |
| CS2 model pkl | data/esports_cs2_model.pkl (trained, saved) |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~243 |
| Per-market analysis | analyze_opportunity() | ~339 |
| Game title detection | _detect_game() | ~592 |
| Market type classification | _classify_market_type() | ~606 |
| ML + fallback prediction | _get_model_prediction() | ~475 |
| LoL prediction | _lol_model.predict() | in lol_win_model.py |
| CS2 prediction | _cs2_model.predict_match() | in cs2_economy_model.py |
| CS2 heuristic fallback | _predict_round_heuristic() | in cs2_economy_model.py |
| Confluence scoring | _compute_confluence_score() | ~622 |
| Live match refresh | _refresh_live_matches() | ~549 |
| WS reactive trade | on_price_update() | ~138 |
| Live status check | _is_live() | ~573 |
| Latency stats | get_latency_stats() | ~577 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| PANDASCORE_API_KEY | YES | Fail-fast ValueError on missing; no fallback |
| RIOT_API_KEY | NO | Optional; enables patch drift check; skipped if missing |
| PandaScore API | YES | Live match data, team ratings, series scores |
| Polymarket API | YES | Esports market discovery (keyword-gated, no category filter) |
| esports DB tables | YES | Training data, prediction logging, accuracy tracking |
| Glicko-2 endpoint | NO | Returns 403 on free tier; fallback: raw_strength=0.0 |
| Redis whale_alerts | NO | Confluence score whale factor; 0.0 weight if unavailable |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ESPORTS | false | true | Enable gate |
| BOT_ENABLED_ESPORTS_LIVE | false | true | EsportsLiveBot enable gate |
| BOT_ENABLED_ESPORTS_SERIES | false | true | EsportsSeriesBot enable gate |
| PANDASCORE_API_KEY | — | set | Required API key |
| RIOT_API_KEY | None | not set | Optional patch drift detection |
| ESPORTS_MIN_EDGE | 0.08 | 0.08 | Minimum edge (8%) |
| ESPORTS_MIN_CONFIDENCE | 0.55 | 0.55 | Minimum model confidence |
| ESPORTS_CONFLUENCE_MIN | 0.60 | 0.60 | Minimum confluence score (0-1.0) |
| ESPORTS_MIN_ACCURACY_TO_TRADE | 0.52 | 0.52 | Rolling accuracy gate; retrain if below |
| ESPORTS_MIN_VOLUME_USD | 100 | 0 | Volume gate (0 for CLOB markets) |
| SCAN_INTERVAL_ESPORTS | 120 | 120 | Pre-game scan interval (s) |
| SCAN_INTERVAL_ESPORTS_LIVE | 10 | 10 | Live match scan interval (s) |
| ESPORTS_PANDASCORE_REFRESH_INTERVAL | 15 | 15 | PandaScore live match fetch rate (s) |
| ESPORTS_WS_PRICE_CHANGE_PCT | 0.01 | 0.01 | WS price move threshold to react |
| ESPORTS_WS_COOLDOWN_SECONDS | 10 | 10 | WS cooldown per market (s); 120s after trade |
| ESPORTS_MAX_EDGE | 0.20 | 0.20 | Edge sanity cap (reject >20% as suspicious) |
| ESPORTS_MODEL_MAX_BRIER | 0.24 | 0.248 | Brier threshold (graduation gate removed — metric only) |
| ESPORTS_MAKER_FALLBACK_TIMEOUT_S | 3.0 | 3.0 | Maker order timeout before taker |
| ESPORTS_TOTAL_CAPITAL | 5000.0 | 5000.0 | Shared pool (3 esports bots) |
| ESPORTS_MAX_BET_USD | 100.0 | 100.0 | Per-bet cap |
| ESPORTS_MAX_DAILY_USD | 500.0 | 500.0 | Daily spending cap |
| ESPORTS_KELLY_DEFAULT_FRACTION | 0.25 | 0.25 | Kelly multiplier |
| ESPORTS_OBSERVATION_HOURS | 48 | 48 | Observation mode duration after patch drift |
| RISK_MIN_PRICE | 0.015 | 0.015 | System-wide price floor (was 0.005) |

## Model Status
| Model | Accuracy | Brier | ECE | Saved? | Notes |
|-------|----------|-------|-----|--------|-------|
| CS2 Economy (cs2_economy_model.py) | 58.2% | 0.2473 | 0.0953 | YES | team_strength_diff importance=1.0 |
| LoL Win (lol_win_model.py) | 51% | 0.25 | 0.0106 | YES | team_strength_diff=0.538, game_time=0.462 |
| CS2 heuristic (_predict_round_heuristic) | N/A | N/A | N/A | N/A | Fallback when CS2 model not loaded |
| Universal fallback (prediction_engine.py) | 53% | 0.2418 | N/A | N/A | Used when game model unavailable |

**Session 55 model changes:**
- Label leakage fixed: outcome-derived features neutralized to prevent tautological training
- XGBoost complexity reduced: depth 5→3, estimators 150→80 (CS2); depth 6→3, estimators 200→80 (LoL)
- Graduation gate REMOVED: models always saved after training, always reloaded after retrain
  (user controls go-live timing, not automated graduation)

**Training data reality:**
- ALL economy features are constant placeholder defaults (PandaScore free tier limitation)
- Only `team_strength_diff` (Glicko-2) has real variance; other features are noise
- CS2 model uses team_strength_diff exclusively (importance=1.0)
- LoL model splits: team_strength_diff=0.538, game_time_minutes=0.462

## Known Issues & Debug History
- **[Session 55 cont — FIXED]** Paper trade side=BUY bug: `paper_trading.py` stored BUY/SELL
  for all entry trades, losing YES/NO distinction. NO bets stored as BUY → PnL calculated as
  YES bet (profit/loss inverted). Fixed: store `original_side` (YES/NO) for entries, SELL for exits.
- **[Session 55 cont — FIXED]** Prediction logging: `esports_db.py` called `db.execute()` (no such
  method) instead of `db.get_session()` pattern. Also VARCHAR(64) too short for 128+ char market IDs.
  Fixed: proper session pattern + ALTER TABLE to VARCHAR(256). Commit: ae0c4e1.
- **[Session 55 cont — FIXED]** Market discovery: `esports_market_service.py` filtered
  `WHERE category='esports'` but Polymarket miscategorizes 252/535 real esports markets. Volume gate
  ($100) blocked CLOB markets with volume=0. Fixed: removed category filter, rely on `_is_real_esports()`
  keyword gate, LIMIT 500→5000, ESPORTS_MIN_VOLUME_USD=0. Commit: ae0c4e1.
- **[Session 55 cont — FIXED]** Graduation gate: removed from esports_trainer.py. Models always
  saved, always reloaded. `result["graduated"] = True` always. Commit: ae0c4e1.
- **[Session 55 — FIXED]** WS reactive spam (50 trades/2h): `on_price_update()` confused YES/NO
  token prices (keyed by market_id only). Fixed: token map, per-token pricing, pending trades
  guard, edge cap (20%), 120s post-trade cooldown. Commit: 8a385b2.
- **[Session 55 — FIXED]** CS2 model: Label leakage neutralized, XGBoost depth 5→3,
  Brier 0.2507→0.2473. Commit: 8472936.
- **[Session 55 — FIXED]** LoL label leakage: Outcome-derived features neutralized. 51% unchanged.
- **[Session 53 — FIXED]** Scan timeout (64 min): asyncio.sleep(4.0) → 0. Now ~140s. Commit: f85e2d1.
- **[Session 53 — FIXED]** CS2 degenerate model (100% accuracy): DELETED pkl. Commit: dc154bf.
- **[OPEN]** LoL model 51%: Needs premium PandaScore or live game data for improvement.
- **[OPEN]** Glicko-2 endpoint 403: Free PandaScore tier blocks team rating endpoint.
  Fallback: raw_strength=0.0. Real Glicko-2 ratings require premium tier.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs (all 3 bots)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep -iE 'EsportsBot|EsportsLiveBot|EsportsSeriesBot'"

# Check scan results and model accuracy logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '1 hour ago' | grep -i 'esports\|pandascore\|LoL\|CS2' | tail -40"

# Check prediction logging works
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*), bot_name FROM esports_prediction_log GROUP BY bot_name;\""

# Check recent esports paper trades (all 3 bots)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, bot_name, market_id, side, size, price, realized_pnl
  FROM paper_trades
  WHERE bot_name IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot')
  ORDER BY created_at DESC LIMIT 20;\""

# Check model pkl files on VPS
ssh -i "$KEY" "$VPS" "ls -la /opt/polymarket-ai-v2/data/esports_*.pkl 2>/dev/null || echo 'No esports pkl files'"

# Run EsportsBot tests locally
pytest tests/ -k "esports" -v

# Check training data volume
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM esports_match_results;\""

# Check esports markets found by discovery
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '10 min ago' | grep -i 'esports.*market' | tail -10"
```

## Next Steps / Blockers
- [ ] Deploy paper_trading.py side=BUY fix to VPS (stores YES/NO instead of BUY for entries)
- [ ] Monitor paper trade PnL after side fix — verify resolved trades compute correct profit/loss
- [ ] Improve LoL model: need premium PandaScore API or live game data for round-level features
- [ ] Consider upgrading PandaScore tier to unlock Glicko-2 endpoint + round-level data
- [ ] Add test coverage for EsportsLiveBot and EsportsSeriesBot (currently 0%)
- [ ] Monitor `ESPORTS_MIN_ACCURACY_TO_TRADE` — auto-retrain fires if rolling accuracy drops below 0.52
- [x] Fix prediction logging (Session 55 cont): db.get_session() + VARCHAR(256)
- [x] Fix market discovery (Session 55 cont): removed category filter, keyword gate only
- [x] Remove graduation gate (Session 55 cont): models always saved/reloaded
- [x] Enable all 3 bots (Session 55 cont): EsportsBot + EsportsLiveBot + EsportsSeriesBot
- [x] Graduate CS2 model (Session 55): Brier 0.2473, saved
- [x] Fix WS reactive spam (Session 55): token map, edge cap, cooldowns
