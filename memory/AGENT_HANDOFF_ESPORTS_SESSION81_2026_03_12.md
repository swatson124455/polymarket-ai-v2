# AGENT HANDOFF — EsportsBot Session 81 (2026-03-12)
## Scope: EsportsBot ONLY — No bleed to Mirror/Weather/other bots

---

## 1. SYSTEM OVERVIEW

**Polymarket AI V2** — 15-bot automated trading system on Polymarket (prediction markets on Polygon). Paper trading mode (`SIMULATION_MODE=true`). Real capital structure, fake execution.

- **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU, eu-west-1)
- **DB**: PostgreSQL localhost, user=polymarket, db=polymarket
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f`
- **Deploy**: `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
- **Rollback**: Same with `deploy/rollback.sh`
- **Hot-patch** (when 70MB archive upload fails): `scp` single file + `sudo cp` into release dir + restart
- **Current release**: `/opt/pa2-releases/20260312_144542` (symlinked from `/opt/polymarket-ai-v2`)

**5 active bots**: WeatherBot, MirrorBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot. 9 disabled. MomentumBot DELETED, EnsembleBot ARCHIVED.

---

## 2. ESPORTSBOT ARCHITECTURE

**File**: `bots/esports_bot.py` (2,569 lines)
**Inherits**: `BaseBot` (from `bots/base_bot.py`)
**Games**: LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League

### Scan Loop Flow (`scan_and_trade()`)
```
1. Restore exposure counters (first scan only)
2. Restore daily P&L (refreshes every 10th scan for mid-day resolutions)
3. Fetch positions (once, shared between exit check + re-evaluation)
4. Daily loss limit check → if hit, only run exits, then return
5. Stop-loss + max-hold exits (_check_and_execute_exits)
6. Re-evaluate open positions (every 5th scan)
7. Kelly graduation check (every 10th scan)
8. Monitoring thresholds (every 10 min)
9. Patch drift check
10. PandaScore live match refresh
11. Get esports markets from service
12. Per-market analysis loop → analyze_opportunity() → execute trade
13. Scan summary log
```

### Scan Interval
- **120s** default
- **60s** with open positions (A4: tighter monitoring for stop-loss)
- **10s** during live matches

### Prediction Pipeline
```
Market question text
  → regex team name extraction (_get_glicko2_prediction)
  → _clean_team_names() (strip "league of legends:", "(bo3)", etc.)
  → _match_team_name() (exact match → substring fuzzy match)
  → If miss: _backfill_unknown_team() (PandaScore lookup, 2 API calls, capped at 5/scan)
  → Glicko-2 expected_score(team_a, team_b)
  → Bayesian prior blending (phi-based: high uncertainty → blend toward 0.50)
  → Game-specific adjustments:
      - LoL: ML model blend (predict_with_glicko2)
      - CS2: Economy model + Glicko-2 adjustment
      - Dota2/Valorant: 60% ML + 40% Glicko-2
      - SC2: Aligulac Elo blend (50/50)
      - Dota2: OpenDota form adjustment (±3%)
      - RL: Ballchasing stats adjustment (±3%)
  → Cross-game XGBoost (40% weight, all games)
  → Clamp to [0.05, 0.95]
  → Cache in _prediction_cache (1h TTL)
```

### Sizing Pipeline
```
Edge = abs(predicted_prob - market_price)
  → Confidence check (>= ESPORTS_MIN_CONFIDENCE 0.52)
  → Edge check (>= ESPORTS_MIN_EDGE 0.08, <= ESPORTS_MAX_EDGE 0.20)
  → Exposure checks (per-game $300, per-tournament $200, per-team $150)
  → BotBankrollManager.get_bet_size() (Kelly fraction × edge × bankroll)
  → Uncertainty scaling: size × (1 - phi/500) — high Glicko-2 uncertainty → smaller bet
  → Near-expiry boost: <6h: confidence × 1.5, <24h: × 1.2
  → Drawdown reduction: at 20% capital loss, Kelly × 0.5
  → Hard cap: $100/trade (ESPORTS_MAX_BET_USD)
```

### Exit Paths
- **Stop-loss** (B1): Exit at 25% unrealized loss (`ESPORTS_STOP_LOSS_PCT=0.25`)
- **Max-hold** (B1): Force exit after 96h (`ESPORTS_MAX_HOLD_HOURS=96`)
- **Both use `side="SELL"`** — bypasses risk_manager confidence check (fixed in Session 81)
- **Daily loss limit** (A1): Halt all new entries after $500 daily loss, but exits still fire
- **Drawdown halt** (A8): At 40% capital loss, halt trading entirely

### Key Instance Variables
```python
_game_exposure: Dict[str, float]           # game → USD (write-through to daily_counters)
_tournament_exposure: Dict[str, float]     # tournament → USD
_team_exposure: Dict[str, float]           # team → USD
_live_matches: Dict[str, Dict]             # match_id → PandaScore data
_prediction_cache: Dict[str, Dict]         # market_id → {prob, ts, game, ml_raw, glicko2_est}
_market_token_map: Dict[str, Dict]         # market_id → {"yes": token_id, "no": token_id}
_glicko2_trackers: Dict[str, Any]          # game → Glicko2Tracker
_team_name_to_id: Dict[str, str]           # lowercased name → PandaScore ID
_backfill_attempted: set                   # "game:name" keys (session-scoped dedup)
_backfill_calls_this_scan: int             # reset each scan, capped at 5
_daily_pnl: float                          # today's realized P&L (refreshes every 10 scans)
_daily_pnl_date: str                       # UTC date string for midnight reset
_drawdown_halted: bool                     # 40% drawdown flag
_kelly_graduated: bool                     # True after 50+ trades + Brier < 0.24
_models_graduated: bool                    # accuracy >= 55% + brier <= 0.24 on holdout
_scan_count: int                           # monotonic scan counter
```

---

## 3. EXTERNAL DEPENDENCIES

| API | Rate Limit | Purpose | Fallback |
|-----|-----------|---------|----------|
| **PandaScore** | 1000/hr shared across 3 esports bots | Live matches, team stats, team search | Circuit breaker at 950/hr |
| **Riot API** | Variable | LoL patch drift detection | Graceful degrade |
| **OpenDota** | ~60/min free | Dota2 team form (±3%) | Returns base_prob unchanged |
| **Aligulac** | Free key | SC2 Elo blend (50/50) | Returns base_prob unchanged |
| **Ballchasing** | Free key | Rocket League stats (±3%) | Returns base_prob unchanged |
| **Polymarket CLOB** | Market order API | Order placement | 3s maker → taker fallback |

**PandaScore budget**: Live refresh every 15s = 240 req/hr/bot. Backfill capped at 5 calls/scan (10 API requests). Circuit breaker at 950/hr.

---

## 4. KEY FILES MAP

| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | 2,569 | Main bot — scan loop, prediction, sizing, exits, Glicko-2 |
| `bots/esports_live_bot.py` | 154 | Live in-play bot (inherits BaseBot, NOT EsportsBot) |
| `bots/esports_series_bot.py` | 385 | BO series bot (inherits BaseBot, NOT EsportsBot) |
| `esports/models/glicko2.py` | 280 | Glicko-2 algorithm (mu, phi, sigma per team) |
| `esports/data/pandascore_client.py` | 532 | Async HTTP client + shared rate limiter |
| `esports/markets/esports_market_scanner.py` | 269 | Market discovery + game classification |
| `esports/data/esports_data_collector.py` | ~300 | PandaScore → training data ETL |
| `bots/base_bot.py` | 536 | Base class — place_order(), bankroll, scan loop |
| `base_engine/execution/order_gateway.py` | ~500 | Single order path — kill switch, risk, liquidity |
| `config/settings.py` | 1000+ | All config (60+ ESPORTS_* keys) |
| `base_engine/data/daily_counter.py` | ~100 | Write-through daily counters |

---

## 5. LIVE CONFIG (VPS values as of Session 81)

```
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_MAX_EDGE=0.20
ESPORTS_STOP_LOSS_PCT=0.25
ESPORTS_MAX_HOLD_HOURS=96
ESPORTS_DAILY_LOSS_LIMIT=500.0
ESPORTS_DRAWDOWN_HALT_PCT=0.40
ESPORTS_DRAWDOWN_REDUCE_PCT=0.20
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0
ESPORTS_PANDASCORE_REFRESH_INTERVAL=15
ESPORTS_FRESHNESS_DECAY_SECONDS=120.0
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_WS_PRICE_CHANGE_PCT=0.01
ESPORTS_WS_COOLDOWN_SECONDS=10
ESPORTS_MAKER_FALLBACK_TIMEOUT_S=3.0
ESPORTS_LOL_HEURISTIC_ENABLED=true
ESPORTS_PINNACLE_ENABLED=false
SIMULATION_MODE=true
```

### Kelly Graduation Thresholds (not in env, hardcoded or defaults)
```
ESPORTS_KELLY_GRADUATION_TRADES=50
ESPORTS_KELLY_GRADUATION_BRIER=0.24
ESPORTS_KELLY_GRADUATED=0.30
ESPORTS_EXPIRY_BOOST_6H=1.5
ESPORTS_EXPIRY_BOOST_24H=1.2
```

---

## 6. SESSION 81 CHANGES (ALL DEPLOYED)

### Commit `d0535f8` — SELL Exit Fix
**Bug**: Exit orders passed `side="NO"/"YES"` (opposite-side BUY) instead of `side="SELL"`. Risk manager blocked them permanently (confidence=0.0 < 45% threshold). 3 legacy positions stuck in infinite exit loop.
**Fix**: Lines 858-862 → `side="SELL"`, `price=current`. Order gateway line 448 explicitly skips risk checks for SELL.
**File**: `bots/esports_bot.py`

### Commit `36a2b60` — LoL + CS2 Auto-Collection
**Bug**: Line 577 listed games for Glicko-2 data collection but omitted `"lol"` and `"cs2"`. Their trackers never initialized → `_team_name_to_id` empty → all LoL/CS2 markets returned `no_prediction`.
**Fix**: Added `"lol", "cs2"` to the tuple. Data already existed in DB (1875 LoL matches, 3754 CS2 matches).
**File**: `bots/esports_bot.py` (1 line)

### Commit `00dde26` — Positions Query Dedup + Daily P&L Refresh
**Bug**: `_check_and_execute_exits()` and `_reevaluate_open_positions()` both queried `get_open_positions_for_bot()`. 2 identical DB round trips (200-500ms) every 5th scan.
**Fix**: Fetch once in `scan_and_trade()`, pass via `positions=None` kwarg to both methods. Also force `_daily_pnl_date = None` every 10th scan so P&L captures mid-day resolutions.
**File**: `bots/esports_bot.py`

### Commit `c0c3e2b` — Backfill Rate Budget
**Bug**: `_backfill_unknown_team()` costs 2 PandaScore calls per unknown team. First scan with many unknowns could spike the shared 1000/hr quota.
**Fix**: `_backfill_calls_this_scan` counter, reset each scan, capped at 5. Logged in scan summary as `backfills_this_scan`.
**File**: `bots/esports_bot.py`

---

## 7. POST-DEPLOY STATUS (Session 81)

### Scan Summary (live on VPS)
```
markets=40 (lol:14, valorant:10, cs2:10, cod:3, dota2:2, other:1)
live_matches=15
skipped_has_position=3
opportunities=0, trades=0
waterfall: observation=14, no_prediction=9, low_edge=3, edge_cap=3, low_confidence=7, no_game=1
backfills_this_scan=1 (first scan), then 0
```

### Open Positions (3 from before Session 81)
| Market | Side | Size | Entry | Current | Unrealized P&L | Status |
|--------|------|------|-------|---------|----------------|--------|
| `0x7f92` | NO | 13.70 | 0.36 | 0.29 | -$0.96 | open |
| `0x5b07` | YES | 10.31 | 0.48 | 0.505 | +$0.26 | open |
| `0xed49` | YES | 10.99 | 0.46 | 0.455 | -$0.05 | open |

### Realized P&L (Session 81 deploy period, 6 trades)
- 3 closed: +$20.71 net realized
- 3 open: +$2.61 unrealized
- **Net since deploy: approximately +$23**

### Glicko-2 Team Coverage (loaded at startup)
```
lol: 1875 matches, 248 teams
cs2: 3754 matches, 395 teams
dota2: 1757 matches, 153 teams
valorant: 189 matches, 61 teams
cod: 367 matches, 12 teams
r6: 28 matches, 24 teams
sc2: 550 matches, 42 teams
rl: 722 matches, 53 teams
Total: 925 unique teams rated
```

---

## 8. CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` for entries requires `side="YES"/"NO"`. For exits use `side="SELL"`.
2. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass for entries.
3. **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager is the real sizer.
4. **EsportsLiveBot and EsportsSeriesBot inherit BaseBot, NOT EsportsBot.** Changes to EsportsBot do NOT affect them.
5. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable. Only resolution labels are correct.
6. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
7. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
8. **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available.
9. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
10. **`_game_exposure` is ADDITIVE write-through** to `daily_counters`. Use `increment_counter()`. Do NOT use absolute-set.
11. **PandaScore rate limit is SHARED** across EsportsBot + EsportsLiveBot + EsportsSeriesBot (class-level counter).
12. **Backfill budget**: `_backfill_calls_this_scan` caps at 5. Don't remove this — prevents quota exhaustion.
13. **48h observation mode**: New LoL/CS2 markets won't trade for 48h after first discovery (patch drift observation window).
14. **VPS SSH**: 70MB archive uploads fail intermittently (connection reset). Use hot-patch for single-file changes.

---

## 9. KNOWN ISSUES & OUTSTANDING WORK

### P0 — Immediate
- **LoL 48h observation**: 14 LoL markets discovered but in 48h observation mode. Will start generating predictions ~2026-03-14. Monitor with: `journalctl -u polymarket-ai -f | grep "esportsbot.*lol"`
- **9 markets with no_prediction**: Some markets still fail team name extraction. Check: `journalctl -u polymarket-ai -f | grep "esportsbot_team_match_fail"`

### P1 — Near-Term
- **Calibration tracking**: No system measures "when bot says 60% confident, does it win 60%?" Kelly is garbage-in without this.
- **Per-game performance tracking**: Bot treats all games equally. Should track win rate / Brier per game and weight Kelly accordingly.
- **Edge decay modeling**: No tracking of how edge erodes after entry. Holds until stop-loss or max-hold.
- **LoL team name extraction**: Regex-based extraction may fail for non-standard market question formats. The `_clean_team_names()` and `_match_team_name()` functions are the weak link.

### P2 — Medium-Term
- **Line movement signals**: Price changes after entry are ignored except for stop-loss.
- **Feature importance feedback**: Which Glicko-2 features predict wins? Not tracked.
- **Parallel market analysis**: `analyze_opportunity()` runs sequentially across 500+ markets. Could use `asyncio.gather()` with concurrency limit.
- **Glicko-2 ratings are static at runtime**: Loaded once at startup, never updated during scan loop. New teams mid-run get no rating.

### P3 — Future
- **Dynamic Kelly graduation**: Currently threshold-based (50+ trades + Brier < 0.24 → 0.30). Could be continuous.
- **Cross-game XGBoost retraining**: Runs in background every 24h. Consider more frequent + incremental.
- **WebSocket reactive path**: EsportsBot has WS handlers for price changes (`ESPORTS_WS_PRICE_CHANGE_PCT=0.01`) but they depend on market subscription coverage.

---

## 10. BOTTLENECK ANALYSIS (from Session 81 exploration)

### Resolved in Session 81
| Bottleneck | Fix | Commit |
|-----------|-----|--------|
| Exit orders permanently blocked | `side="SELL"` bypass | `d0535f8` |
| LoL/CS2 zero predictions | Added to auto-collection tuple | `36a2b60` |
| Duplicate positions query (200-500ms/scan) | Fetch once, pass to both methods | `00dde26` |
| Unbounded backfill API calls | Cap at 5/scan | `c0c3e2b` |

### Remaining Bottlenecks
| Bottleneck | Impact | Severity |
|-----------|--------|----------|
| **PandaScore 10s timeout** on live refresh | Blocks entire scan if API slow | HIGH |
| **Sequential market analysis** (500+ markets × 5ms) | 2.5-5s serial processing | MEDIUM |
| **Glicko-2 static at runtime** | Stale ratings after multi-day run | LOW |
| **Daily P&L query** | Now refreshes every 10 scans, not redundant | RESOLVED |

---

## 11. DATABASE TABLES USED

| Table | Usage |
|-------|-------|
| `paper_trades` | Trade records. Columns: order_id, market_id, token_id, bot_name, side (YES/NO), size, price, confidence, status, realized_pnl, resolution |
| `positions` | Open positions. Columns: market_id, bot_id, side, size, entry_price, current_price (updated 10s), unrealized_pnl, status (open/closed) |
| `daily_counters` | Game exposure write-through. Pattern: `increment_counter(bot, key, amount)` |
| `glicko2_ratings` | Persisted team ratings: game, team_key, mu, phi, sigma, match_count |
| `esports_training_data` | Historical match data: game, team_a, team_b, outcome, patch, game_state_json, tournament, scheduled_at |
| `markets` | Market metadata from Polymarket (question, category, tokens, etc.) |

---

## 12. TESTING

- **Full suite**: `python -m pytest tests/ -x -q --timeout=30` → 1446 passed, 6 skipped
- **Esports only**: `python -m pytest tests/unit/test_esports_bot.py -v`
- **After any change**: ALL 1446+ tests must pass before commit
- **Post-deploy verification**:
```bash
# Exits working
journalctl -u polymarket-ai -f | grep "esportsbot_exit_executed.*SELL"
# LoL collection
journalctl -u polymarket-ai -f | grep "collect.*lol\|Glicko-2.*lol"
# Rate budget
journalctl -u polymarket-ai -f | grep "esports_rate_budget\|backfills_this_scan"
# Scan summary
journalctl -u polymarket-ai -f | grep "esportsbot_scan_summary"
# Team match failures
journalctl -u polymarket-ai -f | grep "esportsbot_team_match_fail"
```

---

## 13. GIT STATE

```
Latest commits (newest first):
e76df44 fix(backfill): add end_date_iso to SELECT DISTINCT for ORDER BY clause
b8f8b26 feat(weather): traded_markets table + prediction logging + backfill priority fix
c0c3e2b fix(esports): cap backfill API calls to 5 per scan cycle
00dde26 perf(esports): share positions query between exit check and re-evaluation
1419ca5 fix(mirror): RTDS health guard, Up/Down mapping, dedup, log rate-limit
36a2b60 fix(esports): add lol+cs2 to Glicko-2 auto-collection loop
d0535f8 fix(esports): exit via SELL order instead of opposite-side BUY
94d0227 feat(esports): risk guardrails + stop-loss + sizing upgrades (A1-A10, B1-B3)
```

**VPS is hot-patched** — only `bots/esports_bot.py` was updated in-place. Full deploy archive (70MB) was failing due to SSH connection resets. Next deploy should retry the full `deploy.sh` or continue hot-patching individual files.

---

## 14. GOVERNANCE RULES (from CLAUDE.md)

- **One fix per commit**. No "while I'm in here" refactors.
- **Preserve every function signature** unless the signature IS the bug.
- **Preserve every external interface** (API paths, DB columns, config keys, message formats).
- **No silent behavior changes**. State what changed from X to Y.
- **Never delete code you don't understand.**
- **No new dependencies without justification.**
- **No structural refactors during bug fixes.**
- **Cross-bot verification**: Shared module changes require all 14 bots verified.
- **Mandatory change log** after every fix.
- **"Can't Fully Verify" rule**: State what you verified, what you couldn't, and exact commands for the operator.

---

## 15. ADAPTIVE SYSTEMS INVENTORY

| System | Type | Description | Config |
|--------|------|-------------|--------|
| Kelly sizing | Formula | `fraction × edge × bankroll`. Not adaptive — fixed fraction. | `KELLY_DEFAULT_FRACTION=0.25` |
| Kelly graduation | Threshold gate | After 50+ resolved + Brier < 0.24 → bumps to 0.30 | Hardcoded thresholds |
| Uncertainty scaling | Formula | `size × (1 - phi/500)`. Uses Glicko-2 phi. | Hardcoded |
| Near-expiry boost | Formula | <6h: conf × 1.5, <24h: × 1.2 | Hardcoded |
| Drawdown halt | Threshold | 20% → Kelly × 0.5. 40% → halt all. | `DRAWDOWN_REDUCE_PCT=0.20`, `HALT_PCT=0.40` |
| Daily loss limit | Threshold | Stop entries after $500 daily loss. | `DAILY_LOSS_LIMIT=500.0` |
| Stop-loss | Threshold | Exit at 25% unrealized loss. | `STOP_LOSS_PCT=0.25` |
| Max-hold | Threshold | Force exit after 96h. | `MAX_HOLD_HOURS=96` |
| Bayesian prior blend | Formula | High phi → blend toward 0.50. | Hardcoded phi thresholds |
| Cross-game XGBoost | ML model | 40% weight in prediction blend. Retrains every 24h background. | `saved_models/cross_game_xgb.json` |
| LoL ML model | ML model | XGBoost with 9 features (gold, towers, dragons, etc.) | `saved_models/` |
| CS2 economy model | ML model | 14 features (money, rounds, maps, CT rate) | `saved_models/` |

**None are continuously adaptive at runtime.** All are either fixed formulas, threshold gates, or batch-retrained models. The bot needs 100+ resolved trades before any meaningful performance measurement.

---

## 16. QUICK START FOR NEXT SESSION

```bash
# 1. Read this handoff
# 2. Read CLAUDE.md governance rules
# 3. Check current bot status
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  "journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# 4. Check open positions
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  "sudo -u polymarket psql -d polymarket -c \"SELECT market_id, side, size, entry_price, current_price, unrealized_pnl, status FROM positions WHERE bot_id='EsportsBot' AND status='open' ORDER BY opened_at DESC;\""

# 5. Check P&L
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*), COALESCE(SUM(realized_pnl),0) as total_pnl FROM paper_trades WHERE bot_name='EsportsBot' AND side IN ('YES','NO') AND realized_pnl IS NOT NULL;\""

# 6. Run tests locally
cd C:/lockes-picks/polymarket-ai-v2
python -m pytest tests/ -x -q --timeout=30
```
