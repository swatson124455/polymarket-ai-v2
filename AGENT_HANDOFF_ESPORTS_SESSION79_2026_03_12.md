# AGENT HANDOFF — EsportsBot Ecosystem Session 79 (2026-03-12)
# CARBON COPY: Complete context for seamless agent continuation

---

## PURPOSE OF THIS DOCUMENT

This is a **complete knowledge transfer** for a new agent to continue building the EsportsBot trading system. It contains every file, function, config, trap, plan, lesson, and architectural decision accumulated across Sessions 63-78. Nothing is omitted. Read this document in full before writing any code.

**Why this handoff exists**: The conversation context window was approaching limits. Rather than lose information through compression, we created this document to preserve 100% fidelity. The new agent should behave identically to the previous one.

---

## SYSTEM OVERVIEW

**What**: Three esports bots within a 15-bot Polymarket automated trading system. Real capital at risk (currently paper trading). Bots predict esports match outcomes and trade YES/NO tokens on Polymarket.

**Where**:
- **Local dev**: `C:\lockes-picks\polymarket-ai-v2` (Windows, git branch `master`, PR target `main`)
- **VPS**: Ubuntu-3 at `34.251.224.21`, 16GB/4vCPU, eu-west-1 (Dublin)
- **DB**: PostgreSQL on VPS localhost, user=`polymarket`, db=`polymarket`
- **Redis**: localhost on VPS

**Status**: `SIMULATION_MODE=true` (paper trading). `CANARY_STAGE=0`. All three esports bots enabled and scanning.

---

## THE THREE ESPORTS BOTS

### 1. EsportsBot (`bots/esports_bot.py` — ~2,100 lines)
**Role**: Pre-game prediction + live in-play analysis across 8 games (LoL, CS2, Dota2, Valorant, CoD, R6, SC2, Rocket League).

**Core pipeline**:
1. Market discovery via `EsportsMarketService` (DB-backed, keyword-gated)
2. Game detection: keyword + word-boundary regex (`_detect_game()`)
3. Market classification: `match_winner`, `map_winner`, `tournament_winner`, `total_maps`, `first_blood`, `props`
4. Prediction dispatch:
   - **Glicko-2** for pre-game match_winner (team strength ratings)
   - **XGBoost cross-game model** (9-feature meta-model)
   - **Game-specific models**: LoL (8-feature XGBoost), CS2 (3-tier economy chain), Dota2 (hero pool + form), Valorant (Glicko-2 only)
5. Confluence scoring: edge 55% + freshness 30% + agreement 15%
6. Position sizing via `EsportsBankrollManager` (Kelly-based with drawdown compression)
7. Trade execution via `place_order(side="YES"/"NO")`

**Scan interval**: 120s default, 10s during live matches.

**Config** (live VPS values):
```
ESPORTS_MIN_EDGE=0.05             # Was 0.08, lowered Session 78
ESPORTS_MIN_CONFIDENCE=0.52       # Was 0.55, lowered Session 76
ESPORTS_MAX_EDGE=0.25             # Was 0.20, raised Session 78
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0
ESPORTS_MAKER_FALLBACK_TIMEOUT_S=3.0
```

### 2. EsportsLiveBot (`bots/esports_live_bot.py` — ~299 lines)
**Role**: Real-time in-game event detection and betting.
- `EsportsGameMonitor` polls PandaScore every 15s for live matches
- `EsportsEventDetector` classifies state changes into events
- `EsportsLiveTrigger` enforces cooldowns + places orders
- **Config**: `BOT_ENABLED_ESPORTS_LIVE=false`, scan 10s

### 3. EsportsSeriesBot (`bots/esports_series_bot.py` — ~793 lines)
**Role**: BO3/BO5 series-level conditional probability + correlated entry.
- Exploits momentum fallacy (market overreacts to map score)
- Map veto-adjusted probabilities per team
- Correlated entry: match-winner + current-map-winner hedging
- Smoczynski-Tomkins allocation for simultaneous series
- **Config**: `BOT_ENABLED_ESPORTS_SERIES=false`, scan 30s

---

## DIRECTORY STRUCTURE (29 files, ~10,500 lines)

```
esports/
├── __init__.py
├── data/
│   ├── esports_db.py                    — DB helpers: upsert matches/teams, log predictions, P&L summary (DISTINCT ON CTE)
│   ├── esports_data_collector.py        — Historical data ingestion
│   ├── pandascore_client.py             — PandaScore REST API (shared class-level rate counter, 1000 req/hr)
│   ├── riot_api_client.py               — LoL patch detection
│   ├── opendota_client.py               — Dota2 hero pool + team form
│   ├── aligulac_client.py               — SC2 Elo ratings
│   ├── ballchasing_client.py            — RL replay stats
│   ├── hltv_scraper.py                  — CS2 per-map win rates (fallback)
│   └── oddspapi_client.py               — Odds API (experimental)
├── models/
│   ├── glicko2.py (279 lines)           — Glicko-2 rating system + Glicko2Tracker
│   ├── lol_win_model.py (456 lines)     — 8-feature XGBoost for LoL
│   ├── cs2_economy_model.py (526 lines) — 3-tier economy predictor (round→map→match)
│   ├── dota2_model.py (180 lines)       — Dota2 hero pool + form
│   ├── valorant_model.py (180 lines)    — Valorant (Glicko-2 fallback)
│   ├── series_model.py (276 lines)      — BO3/BO5 conditional probability math
│   ├── patch_drift.py (272 lines)       — Patch version tracking + observation mode
│   └── esports_trainer.py (790 lines)   — Model training pipeline + cross-game XGB
├── kelly/
│   └── esports_bankroll_manager.py (270 lines) — Kelly sizing + daily caps + drawdown compression
├── markets/
│   ├── esports_market_service.py (445 lines) — DB-backed market discovery + CLOB price refresh
│   └── esports_market_scanner.py (268 lines) — Market finder by match/team (with cache)
└── live/
    ├── esports_game_monitor.py (342 lines)   — PandaScore polling → EsportsGameState queue
    ├── esports_event_detector.py (288 lines) — Classify state changes → EsportsLiveEvents
    └── esports_live_trigger.py (212 lines)   — Cooldown + cap enforcement + order placement
```

**Test Files** (~128K lines):
- `tests/unit/test_esports_bot.py` (23K)
- `tests/unit/test_esports_live_bot.py` (35K)
- `tests/unit/test_esports_series_bot.py` (43K)
- `tests/unit/test_esports_bankroll.py` (16K)
- `tests/unit/test_esports_series_model.py` (12K)

---

## ANALYZE OPPORTUNITY PIPELINE (WATERFALL)

```
Market
  → _detect_game()                    [no_game if unknown]
  → price check 0.03-0.97            [no_price]
  → token extraction                  [no_token]
  → game halted check                 [halted]
  → per-game exposure cap ($300)      [exposure_cap]
  → 48h observation window            [observation]
  → _classify_market_type()           [no_prediction if props/first_blood/tournament_winner]
  → _get_model_prediction()           [no_prediction if Glicko-2 can't match teams]
  → edge check >= 0.05               [low_edge]
  → edge cap check <= 0.25           [edge_cap]
  → confidence check >= 0.52         [low_confidence]
  → confluence check >= 0.60         [low_confluence]
  → [passed] → trade execution
```

**Waterfall dict** (`self._wf`) tracks rejections at each stage. Logged every scan in `esportsbot_scan_summary`.

**Current state** (2026-03-12 00:57 UTC):
```
markets=34, markets_by_game={'lol':10, 'cs2':11, 'valorant':10, 'cod':3}
waterfall={'observation':10, 'no_prediction':13, 'low_edge':4, 'edge_cap':1, 'low_confidence':5}
```
- `observation=10`: 48h window (normal, will graduate)
- `no_prediction=13`: 11 props/tournament skips + 2 genuine Glicko-2 misses
- `low_edge=4`, `edge_cap=1`, `low_confidence=5`: working as designed

---

## GLICKO-2 RATING SYSTEM

**File**: `esports/models/glicko2.py`

**Constants**: `mu_default=1500, phi_default=350, sigma_default=0.06, scale=173.7178`

**Key functions**:
- `expected_score(rating_a, rating_b)` → P(a beats b)
- `update_rating(player, opponents, outcomes)` → new Glicko2Rating
- `Glicko2Tracker.process_match(team_a, team_b, winner)`
- `Glicko2Tracker.strength_diff(team_a, team_b)` → expected_score - 0.5

**Storage**: `glicko2_ratings` table (game, team_key, mu, phi, sigma, match_count, updated_at)

**Rating data loaded**:
- lol=1875, cs2=3751, dota2=1757, valorant=182, cod=367, r6=26, sc2=550 matches

**Team name lookup** (`_team_name_to_id` dict):
- Populated from `glicko2_ratings.team_key` (lowercased names → self-referencing keys)
- `esports_teams` table is EMPTY — all lookups from glicko2_ratings
- Matching: exact first, then longest-substring-first fuzzy

**Glicko-2 Misses: 16 → 2 (Session 78 Achievement)**

Before Session 78: 16 markets were hitting `no_prediction` due to Glicko-2 team lookup failures. Root cause: short acronyms like `lec`, `pgl`, `esl` were matching inside common words ("election", "stablecoins"), causing 135 non-esports markets to be falsely classified as esports. These bogus markets had no real team names → Glicko-2 couldn't find them → `no_prediction`.

After Session 78 word-boundary regex fixes: Only 2 genuine Glicko-2 misses remain:
1. **Contra**: Team not in `glicko2_ratings` DB (genuinely minor team, no historical data)
2. **BIG "qualify"**: Market asks "Will BIG qualify for...?" — classified as `tournament_winner` (single-team, no opponent) → skipped before Glicko-2 lookup. This is correct behavior, not a bug.

**Fix applied**: Pre-compiled word-boundary regex patterns (`\blec\b`, `\bpgl\b`, etc.) in both `esports_bot.py` (`_WB_LOL`, `_WB_CS2`, `_WB_DOTA2`, `_WB_COD`, `_WB_SC2` class attrs) and `esports_market_service.py` (`_BOUNDARY_KEYWORDS` dict).

---

## GAME DETECTION (Dual Implementation)

Both `esports_bot.py` and `esports_market_service.py` have parallel game detection logic:

**Long keywords** (safe substring matching):
```python
_ESPORTS_GAME_KEYWORDS = {
    "lol": ["league of legends", "lol:", "lol ", " lol "],
    "cs2": ["counter-strike", "cs2", "csgo", "blast premier"],
    "dota2": ["dota 2", "dota2", "dota:"],
    "valorant": ["valorant", "vct ", "champions tour"],
    "cod": ["call of duty", "cod ", "call of duty league"],
    "r6": ["rainbow six", "r6 ", "six invitational", "r6 siege"],
    "sc2": ["starcraft", "sc2", "brood war"],
    "rl": ["rocket league", "rlcs"],
}
```

**Short acronyms** (word-boundary regex, prevents false positives):
```python
_BOUNDARY_KEYWORDS = {
    "lol": [r"\blck\b", r"\blec\b", r"\blpl\b", r"\blcs\b", r"\bmsi\b"],
    "cs2": [r"\besl\b", r"\bpgl\b", r"\biem\b"],
    "dota2": [r"\bdpc\b", r"\bthe international\s+\d", r"\bti\b"],
    "cod": [r"\bcdl\b"],
    "sc2": [r"\bgsl\b", r"\basl\b"],
}
```

---

## MARKET SERVICE (esports/markets/esports_market_service.py)

**Problem solved**: Polymarket Gamma API returns ZERO esports markets. 1,593 esports markets exist in DB from CLOB API backfill, all with `liquidity=0, volume=0`.

**Solution**:
1. Query DB for `category='esports'` with `yes_price BETWEEN 0.03 AND 0.97`
2. Double-gate filter: `_is_real_esports(question)` checks for game keywords + boundary regex
3. Background CLOB price refresh every 5 min
4. Cache TTL: 120s

---

## PANDASCORE API

**File**: `esports/data/pandascore_client.py`

**Rate limiting**: Shared class-level counter across all 3 bots. Free tier: 1000 req/hr. Baseline usage ~360/hr. Exponential backoff on 429.

**Game slugs** (PandaScore internal):
```
lol → "lol", cs2 → "csgo", dota2 → "dota2", valorant → "valorant"
cod → "codmw", r6 → "r6siege", sc2 → "starcraft-2", rl → "rl"
```

---

## ML MODELS

### Cross-Game XGB (`esports/models/esports_trainer.py`)
- 9 features: team_a_mu, team_b_mu, team_a_phi, team_b_phi, team_a_sigma, team_b_sigma, strength_diff, team_a_recent_form, team_b_recent_form
- Retrained every 24h via `LearningScheduler`
- Stored: `/opt/pa2-shared/saved_models/cross_game_xgb.json`

### LoL Model (`esports/models/lol_win_model.py`)
- 8 features: game_time, gold_pct_blue, tower_kills_diff, dragon_kills_diff, dragon_soul, herald, inhib_down_diff, baron_buff_diff
- Plus 5 Glicko-2 features: matchup_uncertainty, rd_asymmetry, team_a_volatility, team_b_volatility, strength_diff
- Patch weighting: current=1.0, prev=0.7, 2ago=0.5, 3+ago=0.3

### CS2 Model (`esports/models/cs2_economy_model.py`)
- 3-tier chain: Round → Map → Match
- 12 round features: money, equip, scores, CT rate, loss streaks, bomb, alive counts
- Map-side defaults: Nuke 57%CT, Dust2 48%CT (T-favored), etc.

### Series Model (`esports/models/series_model.py`)
- Pure math (no ML): `bo3_match_prob()`, `bo5_match_prob()`, `series_prob_with_map_veto()`
- Map veto-adjusted probabilities per team

### Bankroll Manager (`esports/kelly/esports_bankroll_manager.py`)
- Kelly formula with drawdown compression: 0 losses=1.0x, 3=0.75x, 5=0.50x, 8+=0.25x
- Daily cap across all 3 esports bots

---

## DATABASE TABLES (Esports-Specific)

| Table | Purpose | Migration |
|-------|---------|-----------|
| `esports_teams` | Team registry (EMPTY) | 024 |
| `esports_players` | Player registry | 024 |
| `esports_matches` | Match schedule + series state | 024 |
| `esports_match_maps` | Per-map state in BO3/BO5 | 024 |
| `esports_market_map` | Match → Polymarket market mapping | 024 |
| `esports_calibration` | Per-(game, market_type) accuracy | 024 |
| `esports_live_events` | In-game events detected | 024 |
| `esports_patch_history` | Game patch versions | 024 |
| `esports_training_data` | Historical match snapshots | 029 |
| `esports_prediction_log` | Prediction accuracy + CLV tracking | 030 |
| `glicko2_ratings` | Persisted Glicko-2 ratings | 031 |
| `paper_trades` | All trades (shared, `bot_name` column) | 012 |
| `positions` | Open positions (shared) | 016 |
| `daily_counters` | State persistence across restarts | 036 |

---

## STATE PERSISTENCE (All Gaps Closed)

| State | Mechanism | Bot |
|-------|-----------|-----|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | All |
| `_game_exposure` | `daily_counters` write-through (`increment_counter()`) + `_restore_exposure_from_db()` | EsportsBot |
| Open positions | `order_gateway.seed_positions_from_db()` | All |
| Glicko-2 ratings | `glicko2_ratings` table | EsportsBot |
| XGB model | `/opt/pa2-shared/saved_models/cross_game_xgb.json` | EsportsBot |

**daily_counters patterns** (DO NOT MIX):
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## FALSE TRADE PURGE STATUS

### Already Purged (Session 76)
1. **152 corrupted `esports_prediction_log` entries**: Set `actual_outcome=NULL, resolved_at=NULL` — outcome backfill had inverted 152 entries from pre-YES/NO SELL trades
2. **SELL trade filter added**: `AND pt.side IN ('YES', 'NO')` in `_backfill_esports_outcomes()` prevents future corruption
3. **P&L summary CTE fix**: `DISTINCT ON (market_id)` prevents JOIN fanout (was reporting 153 trades/-$521 instead of 31/-$146)

### Remaining Historical Artifacts
- **31 old SELL paper_trades** from pre-YES/NO mandate (2025 era): Show -$146.38 total. These are NOT false trades — they're real historical data from before the system switched to YES/NO sides. They are excluded from all active processing by the `side IN ('YES', 'NO')` filter. Deleting them would violate data integrity and provide no benefit.
- **All 152 corrupted prediction_log entries**: Already NULL'd. Will be correctly re-populated as markets resolve.

### Why We Purged
The false trades distorted every metric:
- **P&L** was -$521 (fake) vs -$146 (real) — a 3.5x error from JOIN fanout
- **Accuracy** reported 32% / Brier 0.2665 (fake) — inverted outcomes from SELL trade processing
- **Alert fatigue**: Spurious `accuracy_below` warnings every 2 minutes triggered by corrupted data
- **Decision quality**: Config tuning based on bad metrics would have made the system worse

After purge, honest metrics: 31 resolved trades (all old SELL), 47 YES/NO trades pending resolution.

---

## SESSION HISTORY (Sessions 63-78)

### Session 68 (2026-03-09)
- HLTV fallback for CS2 map veto restoration
- 212 esports tests passing, 1333 system-wide

### Session 70 (2026-03-09)
- P5: Training data quality, smart retraining, phase calibration
- Glicko-2 DB loaded: lol=1875, cs2=3751, dota2=1757, valorant=182, cod=367, r6=26, sc2=550
- Cross-game XGB model trained and deployed

### Session 71 (2026-03-09) — P6 Complete
- **P6.1**: MIN_CONFIDENCE 0.55→0.52, phase multiplier 0.85→0.90
- **P6.3**: EsportsLiveBot team names fix (was matching any game market)
- **P6.4**: Series correlated entry (match-winner + map-winner hedging)
- **P6.5**: Cross-game XGB recent form features
- **P6.6**: PandaScore retry jitter

### Sessions 74-75 (2026-03-10) — P7 Complete
- **P7.1**: Freshness decay 120s→30s
- **P7.2**: Series hedge log_prediction
- **P7.3**: Recent form features at inference
- **P7.4**: BOT_ENABLED_ESPORTS_SERIES=true
- **P7.5**: PandaScore hourly rate counter

### Session 76 (2026-03-11) — 4 Critical Bugs Fixed
- **Bug 1**: `compute_pnl_summary()` JOIN fanout (153 vs 31 trades)
- **Bug 2**: SELL trades inverting 152 prediction outcomes
- **Bug 3**: Near-resolved markets wasting scan cycles (234→182)
- **Bug 4**: VPS MIN_CONFIDENCE stuck at 0.55

### Session 78 (2026-03-12) — Bottleneck Eliminated
- **Root cause**: 171 markets scanned → ~0 trades
- **Fix 1**: False-positive game detection via word-boundary regex (171→34 markets)
- **Fix 2**: "The International" matching "International Court of Justice"
- **Fix 3**: Props/non-match markets wasting Glicko-2 lookups
- **Fix 4**: MIN_EDGE 0.08→0.05
- **Fix 5**: MAX_EDGE 0.20→0.25
- **Added**: Waterfall diagnostic logging (B4 from cross-pollination plan)
- **Result**: Glicko-2 misses 16→2 (Contra not in DB, BIG "qualify" is tournament_winner)

---

## P&L STATUS (as of 2026-03-12)

| Metric | Value |
|--------|-------|
| Resolved trades | 31 (all old SELL, pre-YES/NO era) |
| P&L (resolved) | -$146.38 (irrelevant, old system) |
| YES/NO trades in flight | ~47 (placed 2026-03-10 to 2026-03-12, pending resolution) |
| Open positions | 4 (Dota2×2, Valorant×1, CS2×1) |
| Win rate | TBD (no YES/NO trades resolved yet) |

---

## GIT STATUS (Uncommitted Changes)

Session 78 changes deployed to VPS but NOT committed:
```
M  bots/esports_bot.py                      (+94 lines)
M  esports/markets/esports_market_service.py (+45 lines)
M  deploy/env.vps                            (edge thresholds)
```

**Commit needed**:
```bash
git add bots/esports_bot.py esports/markets/esports_market_service.py deploy/env.vps
git commit -m "fix(esports): false-positive game detection, edge thresholds, waterfall diagnostics"
```

---

## CROSS-POLLINATION PLAN (MirrorBot/WeatherBot → EsportsBot)

### Phase 1: Critical Risk Guardrails — NOT IMPLEMENTED (HIGH PRIORITY)
| Item | What | Pattern Source |
|------|------|----------------|
| **A1+A8** | Daily loss limit + drawdown halt. No daily P&L tracking, no circuit breaker. | WeatherBot: `_daily_pnl <= -_daily_loss_limit` + 10%/20% drawdown |
| **B1** | Stop-loss exits. No stop-loss at all. | MirrorBot: 15% stop-loss via `place_order(side="SELL")` |

### Phase 2: Position Intelligence — NOT IMPLEMENTED (HIGH PRIORITY)
| Item | What | Pattern Source |
|------|------|----------------|
| **A2** | Position re-evaluation every scan | WeatherBot re-runs forecast every scan |
| **A10** | Pre-update exposure before order (race condition fix) | — |
| **B3** | Exit exposure decrement (`_game_exposure` only increments) | — |

### Phase 3: Sizing Upgrades — NOT IMPLEMENTED (MEDIUM PRIORITY)
| Item | What | Pattern Source |
|------|------|----------------|
| **A5** | Near-expiry confidence boost (<6h: 1.5×, <24h: 1.2×) | WeatherBot |
| **A6** | Uncertainty-scaled sizing (Baker-McHale using Glicko-2 φ) | — |
| **A3** | Dynamic Kelly graduation (50+ resolved + Brier<0.24 → kelly 0.30) | — |

### Phase 4: Diagnostics — PARTIALLY DONE
| Item | What | Status |
|------|------|--------|
| **B4** | Waterfall diagnostic logging | **DONE** (Session 78) |
| **A4** | Tournament-aware scan interval (60s near match, 120s default) | NOT DONE |

### Deferred
- **B5**: Per-model reliability (needs 50+ resolved per game)
- **A7**: Slippage-adjusted edge (CLOB liquidity API unreliable)
- **A9**: Lead-time-graduated edge cap (flat 0.25 conservative enough)
- **B2**: Max hold time exit (markets resolve 24-48h)

---

## KEY CODE LOCATIONS (Quick Reference)

| What | File | Line/Function |
|------|------|---------------|
| Bot entry | `bots/esports_bot.py` | `class EsportsBot(BaseBot)` |
| Scan loop | `bots/esports_bot.py` ~438 | `scan_and_trade()` |
| Pipeline | `bots/esports_bot.py` ~703 | `analyze_opportunity()` |
| Waterfall dict | `bots/esports_bot.py` ~600 | `self._wf` |
| Game detection | `bots/esports_bot.py` ~1389 | `_detect_game()` |
| Word-boundary regex | `bots/esports_bot.py` ~1383 | `_WB_LOL`, `_WB_CS2`, etc. |
| Market classification | `bots/esports_bot.py` ~1434 | `_classify_market_type()` |
| Model prediction | `bots/esports_bot.py` | `_get_model_prediction()` |
| Glicko-2 prediction | `bots/esports_bot.py` ~1849 | `_get_glicko2_prediction()` |
| Team name cleanup | `bots/esports_bot.py` ~2020 | `_clean_team_names()` |
| Team name matching | `bots/esports_bot.py` ~2053 | `_match_team_name()` |
| Cross-game XGB | `bots/esports_bot.py` ~1940 | `_predict_cross_game()` |
| Confluence scoring | `bots/esports_bot.py` ~1450 | `_compute_confluence_score()` |
| Trade execution | `bots/esports_bot.py` | `_execute_esports_trade()` |
| Outcome backfill | `bots/esports_bot.py` ~678 | `_backfill_esports_outcomes()` |
| Market service | `esports/markets/esports_market_service.py` | `EsportsMarketService` |
| Keyword gate | `esports/markets/esports_market_service.py` ~82 | `_is_real_esports()` |
| Boundary keywords | `esports/markets/esports_market_service.py` ~55 | `_BOUNDARY_KEYWORDS` |
| Market scanner | `esports/markets/esports_market_scanner.py` | `EsportsMarketScanner` |
| Glicko-2 engine | `esports/models/glicko2.py` | `Glicko2Tracker`, `expected_score()` |
| LoL model | `esports/models/lol_win_model.py` | `LoLWinModel` |
| CS2 model | `esports/models/cs2_economy_model.py` | `CS2EconomyModel` |
| Series model | `esports/models/series_model.py` | `bo3_match_prob()`, `bo5_match_prob()` |
| Bankroll manager | `esports/kelly/esports_bankroll_manager.py` | `EsportsBankrollManager` |
| Game monitor | `esports/live/esports_game_monitor.py` | `EsportsGameMonitor` |
| Event detector | `esports/live/esports_event_detector.py` | `EsportsEventDetector` |
| Live trigger | `esports/live/esports_live_trigger.py` | `EsportsLiveTrigger` |
| PandaScore client | `esports/data/pandascore_client.py` | `PandaScoreClient` |
| Esports DB helpers | `esports/data/esports_db.py` | `upsert_esports_match()`, `log_prediction()` |
| Trainer | `esports/models/esports_trainer.py` | `EsportsModelTrainer` |

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
2. **`paper_trades` has NO `metadata` JSONB column** — never assume it exists.
3. **Resolution backfill MUST exclude SELL trades** (`AND LOWER(pt.side) != 'sell'`).
4. **ESPORTS_MIN_EDGE default in settings.py is 0.08** — VPS env overrides to 0.05.
5. **ESPORTS_MAX_EDGE NOT in settings.py** — only via `getattr(settings, "ESPORTS_MAX_EDGE", 0.20)`.
6. **PandaScore uses "csgo" slug for CS2** — not "cs2".
7. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
8. **asyncpg DATE**: `CURRENT_DATE` as SQL literal, NOT Python date strings.
9. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**. Both must pass.
10. **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager is the real sizer.
11. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
12. **Position `current_price` auto-updated every 10s** by `position_manager._update_current_prices()`.
13. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).
14. **VPS shared .env** (`/opt/pa2-shared/.env`) is the REAL config at runtime.
15. **BOT_REGISTRY has 14 bots** — shared module changes require all 14 verified.
16. **`esports_teams` table is EMPTY** — `_team_name_to_id` from `glicko2_ratings` only.
17. **Temporary INFO logs** should be reverted to DEBUG: `esportsbot_glicko2_miss`, `esportsbot_team_match_fail`, `esportsbot_low_confidence`, `esportsbot_edge_cap`.
18. **daily_counters**: ADDITIVE (EsportsBot `game_*`) vs ABSOLUTE-SET (OrderGateway `daily_exposure_usd`). Never mix.
19. **All 3 esports bots share one PandaScore rate counter** — 1000 req/hr total.

---

## DEPLOYMENT

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

### Rollback
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

### VPS Operations
```bash
# Service control
sudo systemctl restart polymarket-ai
journalctl -u polymarket-ai -f | grep -E "EsportsBot|EsportsLiveBot|EsportsSeriesBot"

# Live env changes (BOTH files must be updated)
# 1. deploy/env.vps (source of truth for future deploys)
# 2. /opt/pa2-shared/.env (live runtime, persists across deploys)
```

### VPS Architecture
- `/opt/polymarket-ai-v2` → symlink to latest in `/opt/pa2-releases/`
- `/opt/pa2-shared/{data,saved_models,venv,.env}` — persists across deploys
- Atomic swap via `mv -T`. Health check 90s.

---

## DEVELOPMENT RULES (from CLAUDE.md — MANDATORY)

1. **One fix per commit**. No "while I'm in here" refactors.
2. **Preserve function signatures** unless the signature IS the bug.
3. **Read the entire file** before modifying.
4. **No new dependencies** without justification.
5. **Grep for dependents** before changing any shared module.
6. **Test before deploy**: `pytest` all pass → deploy → verify VPS logs.
7. **State the bug in one sentence** before writing any code.
8. **Forbidden**: Band-aid try/except, shotgun fixes, scope creep, silent migrations, optimistic rewrites.

---

## OUTSTANDING ISSUES

1. **LoL 0 opportunities**: 10 LoL markets scanned → 0 opps. Not a bug — markets likely in observation window or low edge/confidence. Monitor as they mature.
2. **Uncommitted git changes**: Session 78 code deployed but not committed.
3. **INFO debug logs**: Should revert to DEBUG after analysis period.
4. **No daily loss limit** (A1+A8): Highest priority unimplemented risk guardrail.
5. **No stop-loss exits** (B1): No way to exit losing positions before resolution.
6. **`_game_exposure` never decrements** (B3): Only increments on entry, never decrements on exit.

---

## RECOMMENDED NEXT ACTIONS (Priority Order)

1. **Commit Session 78 changes** (immediate)
2. **Phase 1: Daily loss limit + drawdown halt** (A1+A8) — highest risk gap
3. **Phase 1: Stop-loss exits** (B1) — second highest risk gap
4. **Revert INFO logs to DEBUG** — reduce noise
5. **Phase 2: Position re-evaluation** (A2) — improve prediction accuracy for open positions
6. **Phase 3: Sizing upgrades** (A5, A6, A3) — improve capital efficiency
7. **CANARY_STAGE 0→1** — meet gate criteria for live trading
