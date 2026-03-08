# EsportsBot Session 55 — Complete Agent Handoff

> **Purpose**: Carbon-copy handoff for a new agent to continue EsportsBot development seamlessly.
> **Scope**: Single-bot session (EsportsBot ecosystem = 3 bots). No bleed to other modules.
> **Date**: 2026-03-06

---

## 1. WHAT WAS DONE THIS SESSION

### Commits Made
```
8a385b2  fix: EsportsBot WS reactive spam — token confusion, race guard, edge cap
8472936  fix: esports model improvements — label leakage + complexity reduction + graduation
9f2cceb  docs: EsportsBot Session 55 — WS fix, CS2 graduation, model improvements
ae0c4e1  fix: prediction logging, market discovery, graduation gate removal
```

### Uncommitted Changes (LOCAL ONLY — not deployed)
```
BOT_ESPORTSBOT.md                            — updated docs (all fixes reflected)
base_engine/data/ingestion_error_capture.txt  — test artifact noise (revert with git checkout)
base_engine/execution/paper_trading.py        — side=BUY fix (stores YES/NO for entries)
```

### Fix 1: WS YES/NO Token Confusion (commit 8a385b2)
**Root cause:** `on_price_update()` stored prices keyed by `market_id` only. YES token (0.84) and NO token (0.06) alternately overwrote each other → `edge = model_prob - new_price` compared YES probability against NO price → fake 44-63% edges → 50 reactive trades in 2 hours.
**Fix:** Added `_market_token_map: Dict[str, Dict[str, str]]` mapping market_id → {yes: token_id, no: token_id}. Populated during `analyze_opportunity()`. `on_price_update()` now looks up which side the token_id belongs to, uses per-token pricing via `(market_id, token_id)` key, and converts NO token prices to YES-equivalent.
**File:** `bots/esports_bot.py` — `on_price_update()` rewrite (~40 lines)

### Fix 2: WS Position Guard + Cooldown (commit 8a385b2)
**Root cause:** Race condition: `has_open_position()` checks in-memory state, but position registration happens AFTER paper trade completes. A 2nd WS event during execution sees no position and fires again.
**Fix:** Added `_ws_pending_trades: Set[str]`. Market_id added before execution, removed in finally block. Post-trade cooldown increased to 120s (one full scan cycle vs 10s default).
**File:** `bots/esports_bot.py`

### Fix 3: Edge Sanity Cap (commit 8a385b2)
**Fix:** `ESPORTS_MAX_EDGE` setting (default 0.20). If `abs(edge) > max_edge`, log warning and skip. Applied in both `on_price_update()` and `analyze_opportunity()`.
**File:** `bots/esports_bot.py`

### Fix 4: Label Leakage Neutralization (commit 8472936)
**Root cause:** Training features derived FROM match outcomes. LoL: `gold_pct_blue=0.55 if blue wins`. CS2: `round_score_a=8, round_score_b=5` for winner. Models learned tautological outcome proxies.
**Fix:** Set all outcome-derived features to neutral defaults:
- LoL: `gold_pct_blue=0.5, tower/dragon/herald/inhib/baron diffs=0.0`
- CS2: `round_score_a/b=6.0` (neutral)
Post-load sanitizer in `get_training_data()` neutralizes existing DB rows.
**Files:** `esports/data/esports_data_collector.py` (collection), `esports/models/lol_win_model.py`, `esports/models/cs2_economy_model.py`

### Fix 5: XGBoost Complexity Reduction (commit 8472936)
**Rationale:** With only 1-2 features having real signal, deeper models overfit.
- CS2: `max_depth=5→3, n_estimators=150→80`. Brier improved 0.2507→0.2473.
- LoL: `max_depth=6→3, n_estimators=200→80`. Accuracy unchanged at 51%.
**Files:** `esports/models/cs2_economy_model.py`, `esports/models/lol_win_model.py`

### Fix 6: Prediction Logging (commit ae0c4e1)
**Root cause:** `esports_db.py log_prediction()` called `await db.execute()` — Database class has no `execute()` method. Needs `async with db.get_session() as session:` pattern. Also bare `except Exception: pass` hid the error.
**Fix:** Rewrote to use `db.get_session()` + `session.execute(text(...))`. Changed log level to WARNING. Fixed bare except in `esports_bot.py` and `esports_series_bot.py`.
**Post-deploy issue:** `StringDataRightTruncationError` — VARCHAR(64) too short for 128+ char market_id hashes. Fixed on VPS: `ALTER TABLE esports_prediction_log ALTER COLUMN match_id TYPE VARCHAR(256); ALTER TABLE esports_prediction_log ALTER COLUMN market_id TYPE VARCHAR(256);`
**Files:** `esports/data/esports_db.py`, `bots/esports_bot.py`, `bots/esports_series_bot.py`

### Fix 7: Market Discovery (commit ae0c4e1)
**Root cause:** `esports_market_service.py` SQL filtered `WHERE category = 'esports'` — but Polymarket miscategorizes 252/535 real esports markets as politics/crypto/weather/etc. DB audit showed 364 "active esports" markets were ALL 2028 presidential election markets. Volume gate ($100 default) also blocked CLOB markets which have volume=0 by design.
**Fix:** Removed `WHERE category = 'esports'` from SQL. Rely on `_is_real_esports()` keyword matching as sole gate. LIMIT 500→5000. Set `ESPORTS_MIN_VOLUME_USD=0` on VPS.
**File:** `esports/markets/esports_market_service.py`

### Fix 8: Graduation Gate Removal (commit ae0c4e1)
**User demand:** "delete this graduation BS. i will say when to go live. ONLY THE GRADUATION NONSENSE!!!!"
**Fix:** `esports_trainer.py`: `result["graduated"] = True` always. Models always saved after training (was only saving when graduated). `esports_bot.py`: removed `if result.get("graduated"):` checks on model reload.
**Files:** `esports/models/esports_trainer.py`, `bots/esports_bot.py`

### Fix 9: Paper Trade side=BUY (LOCAL ONLY — NOT DEPLOYED)
**Root cause:** `paper_trading.py` line 385 stored `side=side` where `side` is "BUY"/"SELL" (from `order_gateway.py` line 561: `paper_side = "SELL" if side.upper() == "SELL" else "BUY"`). ALL entry trades stored as "BUY" regardless of whether bot bet YES or NO.
**Impact:** Downstream PnL SQL uses `LOWER(pt.side) IN ('yes', 'buy')` to identify YES bets and `IN ('no', 'sell')` for NO bets. A NO bet stored as BUY matches the YES branch → PnL computed as if it were a YES bet (profit/loss inverted for NO bets).
**Fix:** Compute `_db_side = original_side` (YES/NO) for entries, keep SELL for exits:
```python
_db_side = side  # SELL stays SELL for exit trades
if side != "SELL" and original_side in ("YES", "NO"):
    _db_side = original_side
```
**Verification:** 1130 tests passed, 0 failures.
**Blast radius:** `paper_trading.py` is shared by ALL 15 bots. Downstream SQL verified:
- `LOWER(pt.side) IN ('yes', 'buy')` → 'yes' matches ✓
- `LOWER(pt.side) IN ('no', 'sell')` → 'no' matches ✓
- `WHERE pt.side = 'SELL'` → SELL preserved for exits ✓
**File:** `base_engine/execution/paper_trading.py`
**Status:** Code changed locally, tests pass. **NEEDS: commit, deploy to VPS, restart.**

---

## 2. CURRENT VPS STATE

### Environment
```
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true
ESPORTS_MIN_VOLUME_USD=0
ESPORTS_MODEL_MAX_BRIER=0.248
ESPORTS_MAX_EDGE=0.20
ESPORTS_MIN_EDGE=0.08
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MIN_CONFIDENCE=0.55
ESPORTS_MIN_ACCURACY_TO_TRADE=0.52
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
```

### Bot Status (last verified)
- **EsportsBot**: Finding markets, placed 2 CS2 paper trades (edges 12.3%, 19.3%)
- **EsportsLiveBot**: Active, detecting 2 live games, WS price moves logged
- **EsportsSeriesBot**: Scanning (no BO3+ series active at time of check)

### Model Status
| Model | Accuracy | Brier | ECE | Saved | Key Feature |
|-------|----------|-------|-----|-------|-------------|
| CS2 | 58.2% | 0.2473 | 0.0953 | YES | team_strength_diff (importance=1.0) |
| LoL | 51% | 0.25 | 0.0106 | YES | team_strength_diff=0.538, game_time=0.462 |
| Glicko-2 | N/A | N/A | N/A | N/A | 629 teams tracked (248 LoL + 392 CS2) |

### DB Schema Changes Applied on VPS
```sql
ALTER TABLE esports_prediction_log ALTER COLUMN match_id TYPE VARCHAR(256);
ALTER TABLE esports_prediction_log ALTER COLUMN market_id TYPE VARCHAR(256);
```

### VPS Connection
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"
```

---

## 3. THREE-BOT ARCHITECTURE

### EsportsBot (bots/esports_bot.py — 988 lines, 43% test coverage)
**Role:** Full ML pipeline — pregame + live match-level trading.
**ML:** LoL XGBoost + CS2 XGBoost + Glicko-2 + 5-factor confluence scoring.
**Scan flow:**
1. Auto-retrain models if interval elapsed (2h default)
2. Check rolling accuracy per game; auto-disable if below 0.52
3. Patch drift check via Riot API (optional)
4. Refresh live match data from PandaScore (15s interval)
5. Fetch esports markets from DB (keyword-gated, LIMIT 5000)
6. Per market: classify game+type → get prediction → check confluence ≥0.60 → trade

**Key methods:**
| Method | Line | Purpose |
|--------|------|---------|
| `scan_and_trade()` | ~243 | Main scan loop |
| `analyze_opportunity()` | ~339 | Per-market analysis |
| `on_price_update()` | ~138 | WS reactive trade (token-mapped) |
| `_get_model_prediction()` | ~475 | ML + fallback prediction |
| `_compute_confluence_score()` | ~622 | 5-factor edge scoring |
| `_detect_game()` | ~592 | Keyword game classification |
| `_classify_market_type()` | ~606 | Market type detection |
| `_execute_esports_trade()` | ~806 | Trade execution |
| `_init_glicko2_trackers()` | ~837 | Glicko-2 initialization |
| `_get_glicko2_prediction()` | ~904 | Glicko-2 expected score |

**5-Factor Confluence Score:**
- Model edge (37%) — ML probability vs market YES price
- Whale direction alignment (23%) — Redis whale_alerts signal
- Orderbook imbalance (18%) — CLOB bid/ask depth ratio
- Prediction freshness (14%) — exponential decay from last PandaScore refresh
- Model agreement (8%) — Glicko-2 vs ML model agreement

**Key instance variables:**
- `_market_token_map: Dict[str, Dict[str, str]]` — market_id → {yes: token_id, no: token_id}
- `_ws_pending_trades: set` — race condition guard
- `_prediction_cache: Dict[str, Dict]` — market_id → {prob, ts, game}
- `_live_matches: Dict[str, Dict]` — match_id → PandaScore match data
- `_glicko2_trackers: Dict[str, Any]` — game → Glicko2Tracker

### EsportsLiveBot (bots/esports_live_bot.py — 257 lines, 0% coverage)
**Role:** Event-driven in-game betting during live matches.
**ML:** Loads saved pkl models from EsportsBot for confidence scoring. No own training.
**Architecture:** EsportsGameMonitor (background task) → EsportsEventDetector → EsportsLiveTrigger
**Scan:** Drains game update queue (20/cycle max). 10s interval during live games, 60s idle.

**Pipeline components:**
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| EsportsGameMonitor | esports/live/esports_game_monitor.py | 342 | Polls PandaScore every 15s, emits EsportsGameState |
| EsportsEventDetector | esports/live/esports_event_detector.py | 275 | Classifies game state → EsportsLiveEvent list |
| EsportsLiveTrigger | esports/live/esports_live_trigger.py | 207 | Cooldowns + caps + order placement |

**Events detected:**
- LoL: gold_lead (5000+ diff), tower_advantage (3+ diff), baron_take
- CS2: round_streak (5+ round diff), map_clinch (12+ rounds with 3+ lead)
- Generic: blowout (2+ map lead in BO3+)

**All events currently set market_side="YES"** — EsportsLiveTrigger passes this directly to place_order.

### EsportsSeriesBot (bots/esports_series_bot.py — 519 lines, 0% coverage)
**Role:** Series-level market mispricings in BO3/BO5 matches.
**ML:** ZERO. Pure math — binomial conditional probability via series_model.py.
**Strategy:** Exploits 3 inefficiencies:
1. Momentum fallacy — market overweights current map score
2. Map veto ignorance — ignores team-specific map win rates
3. Conditional probability errors — anchors on series score instead of computing P(team wins series)

**Key method chain:**
`scan_and_trade()` → `_refresh_series()` → `_analyze_series()` → `_simple_series_prob()` / `series_prob_with_map_veto()` → `_execute_series_trade()`

**Settings:**
- `ESPORTS_SERIES_MIN_EDGE=0.10`
- `ESPORTS_SERIES_REVERSE_SWEEP_FLOOR=0.05`
- Scan interval: 30s when active series, 300s otherwise

---

## 4. COMPLETE FILE MAP

### Bot Files
| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | 988 | Main EsportsBot — ML pipeline, WS reactive, confluence |
| `bots/esports_live_bot.py` | 257 | Event-driven live bot — game monitor + event detector |
| `bots/esports_series_bot.py` | 519 | Series bot — binomial math, no ML |

### Model Files
| File | Lines | Purpose |
|------|-------|---------|
| `esports/models/lol_win_model.py` | 439 | LoL XGBoost — 9 features, patch weighting, Glicko-2 blend |
| `esports/models/cs2_economy_model.py` | 527 | CS2 XGBoost — 14 features, 3-tier (round→map→match) |
| `esports/models/esports_trainer.py` | 429 | Training orchestrator — retrain interval, evaluation, saving |
| `esports/models/series_model.py` | 277 | Binomial race functions — BO3/BO5 prob, map veto, momentum |
| `data/esports_lol_model.pkl` | — | Trained LoL model (on VPS) |
| `data/esports_cs2_model.pkl` | — | Trained CS2 model (on VPS) |

### Data Files
| File | Lines | Purpose |
|------|-------|---------|
| `esports/data/pandascore_client.py` | 418 | PandaScore API — live/past matches, team stats, caching |
| `esports/data/esports_data_collector.py` | 522 | Historical data collection — feature extraction, Glicko-2 |
| `esports/data/esports_db.py` | 416 | DB operations — prediction logging, calibration, accuracy |
| `esports/data/riot_api_client.py` | — | Riot API — patch drift detection (optional, not set) |

### Live Trading Infrastructure
| File | Lines | Purpose |
|------|-------|---------|
| `esports/live/esports_game_monitor.py` | 342 | Background poller — PandaScore live matches → game state queue |
| `esports/live/esports_event_detector.py` | 275 | Event classification — gold leads, round streaks, blowouts |
| `esports/live/esports_live_trigger.py` | 207 | Order execution — cooldowns, per-match/map caps |

### Market Discovery
| File | Lines | Purpose |
|------|-------|---------|
| `esports/markets/esports_market_service.py` | 421 | DB query + keyword filter + CLOB price refresh |

### Bankroll / Kelly
| File | Lines | Purpose |
|------|-------|---------|
| `esports/kelly/esports_bankroll_manager.py` | 211 | Kelly sizing — $5K pool, drawdown compression |

### Shared Files Modified This Session
| File | Lines Changed | Impact |
|------|--------------|--------|
| `base_engine/execution/paper_trading.py` | +6 (side fix) | ALL 15 bots |
| `config/settings.py` | +1 (ESPORTS_MODEL_MAX_BRIER) | EsportsBot only |

### Documentation
| File | Purpose |
|------|---------|
| `BOT_ESPORTSBOT.md` | Bot reference — status, config, debugging, known issues |
| `BOT_ESPORTSLIVEBOT.md` | LiveBot reference |
| `BOT_ESPORTSSERIESBOT.md` | SeriesBot reference |
| `ESPORTS_SESSION55_HANDOFF.md` | This file |

---

## 5. MODEL DETAILS

### LoL Win Model (lol_win_model.py)
**Features (9 total, only 2 have real signal):**
```python
FEATURE_NAMES = [
    "game_time_minutes",     # importance=0.462 (REAL)
    "gold_pct_blue",         # 0.0 (neutralized — was label-leaked)
    "tower_kills_diff",      # 0.0 (neutralized)
    "dragon_kills_diff",     # 0.0 (neutralized)
    "dragon_soul_blue",      # 0.0 (neutralized)
    "herald_blue",           # 0.0 (neutralized)
    "inhib_down_diff",       # 0.0 (neutralized)
    "baron_buff_count_diff", # 0.0 (neutralized)
    "team_strength_diff",    # importance=0.538 (REAL — Glicko-2)
]
```
**XGBoost:** `n_estimators=80, max_depth=3, lr=0.1, subsample=0.8, colsample=0.8`
**Calibration:** `CalibratedClassifierCV` with isotonic regression
**Heuristic fallback:** Logistic regression: `z = 8.0*(gold-0.5) + 0.08*tower + 0.06*dragon + 0.10*baron + 2.0*team_str`
**Adaptive blend:** Rolling Brier error tracking (deque maxlen=50) computes ML vs Glicko-2 weight

### CS2 Economy Model (cs2_economy_model.py)
**Features (14 total, only team_strength_diff has real signal):**
```python
ROUND_FEATURES = [
    "team_a_money", "team_b_money",           # constant defaults (no PandaScore data)
    "team_a_equip_value", "team_b_equip_value", # constant defaults
    "round_score_a", "round_score_b",          # neutralized to 6.0/6.0
    "map_ct_rate",                              # real (from MAP_SIDE_RATES dict)
    "team_a_is_ct",                             # constant default
    "team_a_loss_streak", "team_b_loss_streak", # constant defaults
    "bomb_planted",                             # constant default
    "team_a_alive", "team_b_alive",            # constant defaults
    "team_strength_diff",                       # importance=1.0 (REAL — Glicko-2)
]
```
**XGBoost:** `n_estimators=80, max_depth=3, lr=0.1, subsample=0.8`
**Calibration:** `IsotonicRegression(out_of_bounds="clip")`
**3-tier hierarchy:** `predict_round()` → `predict_map()` (binomial race to 13) → `predict_match()` (BO series)
**Heuristic fallback:** `base_prob = 0.3*side_rate + 0.7*equip_sigmoid()`, adjusted by round_score

### Training Data Reality
- PandaScore FREE tier: match-level outcomes only, no round-level economy data
- ALL economy features are constant placeholder defaults
- Only `team_strength_diff` (Glicko-2) provides real variance across both models
- `_get_team_strength()` always returns 0.5 (API returns 403) → custom Glicko-2 used instead
- 629 teams tracked: 248 LoL + 392 CS2, built from `esports_match_results` table
- Graduation gate REMOVED — models always saved, user controls go-live

### Series Model (series_model.py — pure functions, no ML)
- `bo3_match_prob(game_win_rate, maps_a, maps_b)` — conditional series probability
- `bo5_match_prob(game_win_rate, maps_a, maps_b)` — same for BO5
- `series_prob_with_map_veto(...)` — per-map win rates with veto order
- `detect_momentum_fallacy(map_margin, market_adj)` — edge detection

---

## 6. DATABASE TABLES (ESPORTS-SPECIFIC)

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `esports_teams` | external_id, name, game, region | Team registry (PandaScore) |
| `esports_matches` | external_id, game, team_a/b, score_a/b, status | Match results |
| `esports_prediction_log` | match_id(256), market_id(256), game, predicted_prob, market_price, side, edge, actual_outcome | Prediction tracking |
| `esports_calibration` | game, market_type, bet_count, brier_score, kelly_fraction | Per-game calibration |
| `esports_training_data` | match_id, game, game_state_json, outcome | ML training rows |
| `esports_match_results` | — | Glicko-2 rating source |
| `paper_trades` | bot_name, market_id, side(YES/NO/SELL), size, price, realized_pnl | Trade records |
| `markets` | id, question, category, yes/no_token_id, yes/no_price, active, resolved | Market universe |

---

## 7. CRITICAL PATTERNS / TRAPS

### Code Patterns
- **Database access:** `async with db.get_session() as session:` → `session.execute(text(...))` → `session.commit()`. Direct `db.execute()` does NOT exist.
- **BUY/SELL vs YES/NO:** Bots pass `side="YES"/"NO"` to `place_order()`. Paper trading engine now stores YES/NO for entries, SELL for exits (Fix 9).
- **Polymarket categories unreliable:** Real esports markets tagged as politics/crypto/weather. Use keyword matching, never category filter.
- **CLOB markets have volume=0:** Don't use volume gates for market discovery.
- **paper_trades schema:** `bot_name` column (NOT `bot_id`). `positions` schema: `bot_id` (NOT `bot_name`).
- **websockets.exceptions:** Must `import websockets.exceptions` explicitly — v15 lazy-loads.
- **VPS .env can drift:** Always `grep` to verify. Duplicate keys: python-dotenv first-wins.

### Settings Traps
- `ESPORTS_MODEL_MAX_BRIER` — metric only now (graduation gate removed). Still logged.
- `ESPORTS_MIN_ACCURACY_TO_TRADE=0.52` — triggers auto-retrain if rolling accuracy drops below.
- `RISK_MIN_PRICE=0.015` — system-wide price floor (was 0.005, enabled 1.85% bets).
- `PSEUDO_LABEL_ENABLED=false` — DO NOT enable.

### EsportsBot-Specific Traps
- `_market_token_map` populated during `analyze_opportunity()`. If a market hasn't been scanned yet, WS events for it are silently skipped (no token mapping available). This is by design.
- `_ws_pending_trades` guards against concurrent execution but is in-memory only — lost on restart. Not a problem because positions are also re-seeded on restart.
- `ESPORTS_MAX_EDGE=0.20` — any edge above 20% is treated as suspicious (Glicko-2 artifact on thin markets).
- `_refresh_live_matches()` has a 15s rate limit via `ESPORTS_PANDASCORE_REFRESH_INTERVAL`.
- EsportsBot scan interval: 120s pregame, 10s when live matches active.

---

## 8. WHAT NEEDS DOING NEXT

### Immediate (deploy pending fix)
1. **Commit + deploy `paper_trading.py` side=BUY fix** — local code changed, 1130 tests pass
   ```bash
   # Deploy command:
   scp -i "$KEY" -o StrictHostKeyChecking=no "C:/lockes-picks/polymarket-ai-v2/base_engine/execution/paper_trading.py" "$VPS:/tmp/"
   ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp /tmp/paper_trading.py /opt/polymarket-ai-v2/base_engine/execution/paper_trading.py && sudo systemctl restart polymarket-ai'
   ```

### Short-term (this session or next)
2. **Monitor paper trade PnL** — verify resolved trades compute correct profit/loss after side fix
3. **Check existing paper_trades data** — old trades stored as BUY may need migration:
   ```sql
   -- Check how many BUY entries exist vs YES/NO
   SELECT side, COUNT(*) FROM paper_trades WHERE bot_name LIKE 'Esports%' GROUP BY side;
   ```

### Medium-term
4. **LoL model improvement** — stuck at 51% (coin flip). Options:
   - Premium PandaScore tier → unlocks round-level features + Glicko-2 endpoint
   - Alternative data sources (Riot API timeline for live game features)
   - Bypass XGBoost entirely, use Glicko-2 expected score directly
5. **Test coverage** — EsportsLiveBot (0%) and EsportsSeriesBot (0%) have zero tests
6. **Glicko-2 endpoint 403** — PandaScore free tier blocks team rating API. Custom implementation works but premium would be better.

### Monitoring
7. **Auto-retrain fires if rolling accuracy < 0.52** — watch for repeated retrains
8. **Confluence thresholds** — use paper trade outcomes as proxy for tuning
   - Current: `ESPORTS_CONFLUENCE_MIN=0.60`, `ESPORTS_MIN_EDGE=0.08`
   - May need adjustment once more trades execute and resolve

---

## 9. DIAGNOSTIC COMMANDS

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# All 3 bots live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep -iE 'EsportsBot|EsportsLiveBot|EsportsSeriesBot'"

# Recent paper trades (verify side=YES/NO after deploy)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, bot_name, side, size, price, realized_pnl
  FROM paper_trades
  WHERE bot_name IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot')
  ORDER BY created_at DESC LIMIT 20;\""

# Prediction logging working?
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*), bot_name FROM esports_prediction_log GROUP BY bot_name;\""

# Model accuracy
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT game_title, COUNT(*) as predictions,
         ROUND(AVG(CASE WHEN was_correct THEN 1.0 ELSE 0.0 END)::numeric, 3) as accuracy
  FROM prediction_log
  WHERE bot_name='EsportsBot' AND was_correct IS NOT NULL
  GROUP BY game_title;\""

# Model pkl files
ssh -i "$KEY" "$VPS" "ls -la /opt/polymarket-ai-v2/data/esports_*.pkl 2>/dev/null || echo 'No esports pkl files'"

# Training data volume
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"SELECT COUNT(*) FROM esports_match_results;\""

# Market discovery check
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '10 min ago' | grep -i 'esports.*market' | tail -10"

# Check .env for esports settings
ssh -i "$KEY" "$VPS" "grep -i esports /opt/polymarket-ai-v2/.env"

# Run tests locally
pytest tests/ -k "esports" -v
pytest tests/ -x -q  # full suite
```

---

## 10. DEPLOY PATTERN

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Single file deploy
scp -i "$KEY" -o StrictHostKeyChecking=no "C:/lockes-picks/polymarket-ai-v2/path/to/file.py" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/to/file.py && sudo systemctl restart polymarket-ai'

# Multi-file deploy
scp -i "$KEY" -o StrictHostKeyChecking=no file1.py file2.py "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp /tmp/file1.py /opt/polymarket-ai-v2/path1/ && sudo cp /tmp/file2.py /opt/polymarket-ai-v2/path2/ && sudo systemctl restart polymarket-ai'

# Add env var on VPS
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'echo "NEW_VAR=value" | sudo tee -a /opt/polymarket-ai-v2/.env'

# Verify service running
ssh -i "$KEY" "$VPS" "sudo systemctl status polymarket-ai | head -5"
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '2 min ago' | tail -20"
```

---

## 11. USER PREFERENCES / COMMUNICATION STYLE

- **Direct and blunt.** User curses freely ("you clown"). Match their energy with honesty.
- **"No optimism no lies"** — never sugarcoat model performance or capabilities.
- **Single-bot focus.** "This is a session for a single bot and there should be no bleed over unless it is a manual demand."
- **User controls go-live.** "I will say when to go live." Don't automate graduation/enable decisions.
- **Fix what's broken, don't add features.** Follow CLAUDE.md prime directive.
- **Always verify on VPS.** Code changes mean nothing until deployed and verified in logs.
- **Deploy pattern is sacred.** Always scp → cp → restart. Never git pull on VPS.
