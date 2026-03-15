# AGENT HANDOFF — EsportsBot Session 78 (2026-03-12)
# CARBON COPY: Full context for seamless continuation

---

## WHAT THIS IS

A **live automated Polymarket trading system** with 15 bots (5 active, 9 disabled, 1 deleted). **EsportsBot** trades esports prediction markets (CS2, LoL, Dota2, Valorant, CoD, R6, SC2, Rocket League) using Glicko-2 ratings, XGBoost cross-game model, and 4 game-specific ML models. It runs 24/7 on a VPS in Dublin. All trading is currently **paper trading** (`SIMULATION_MODE=true`).

This handoff covers the EsportsBot-focused work from Sessions 76-78 (2026-03-09 through 2026-03-12), including bottleneck diagnosis, false positive elimination, waterfall diagnostics, and the cross-pollination plan from MirrorBot/WeatherBot.

---

## SYSTEM ARCHITECTURE (Key Facts)

- **Repo**: `C:\lockes-picks\polymarket-ai-v2` (local Windows dev) → deploys to VPS
- **VPS**: Ubuntu-3, `34.251.224.21`, 16GB/4vCPU, eu-west-1 (Dublin)
- **DB**: PostgreSQL on VPS localhost, user=`polymarket`, db=`polymarket`
- **Redis**: localhost on VPS, enabled for caching/cooldowns
- **Service**: `sudo systemctl restart polymarket-ai` — reads `EnvironmentFile=/opt/pa2-shared/.env`
- **Deploy**: Atomic symlink swap via `deploy/deploy.sh`. `/opt/polymarket-ai-v2` → latest release in `/opt/pa2-releases/`
- **Shared state persists across deploys**: `/opt/pa2-shared/{data,saved_models,venv,.env}`
- **Branch**: `master` (PR target: `main`)
- **Tests**: `pytest` — 1400+ tests, must all pass before deploy

### Deploy Command
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

### VPS .env Hierarchy (CRITICAL)
- **Systemd reads**: `/opt/pa2-shared/.env` (persists across deploys)
- **Deploy copies**: `deploy/env.vps` → `/opt/polymarket-ai-v2/.env` (overwritten each deploy)
- **To change env vars**: Update BOTH `deploy/env.vps` (source of truth for future deploys) AND `/opt/pa2-shared/.env` (live runtime)
- **To change live env only**: `sudo bash -c 'echo KEY=VALUE >> /opt/pa2-shared/.env'` then `sudo systemctl restart polymarket-ai`

---

## 5 ACTIVE BOTS (as of 2026-03-12)

| Bot | Capital | Kelly | Max Bet | Max Daily | Status |
|-----|---------|-------|---------|-----------|--------|
| **WeatherBot** | $5,000 | 0.25 | $500 | $2,000 | Active, +$461 P&L (140 resolved) |
| **MirrorBot** | $3,000 | 0.30 | $250 | $10,000 | Active, +$230 P&L (14 resolved). Blocked by $20k exposure cap (legacy positions) |
| **EsportsBot** | $5,000 | 0.25 | $100 | $500 | Active, trading |
| **EsportsLiveBot** | $1,000 | 0.25 | $100 | $500 | Active |
| **EsportsSeriesBot** | $1,000 | 0.25 | $100 | $500 | Active |

9 bots disabled: ArbitrageBot, CrossPlatformArbBot, OracleBot, SportsBot, LLMForecasterBot, SportsInjuryBot, SportsLiveBot, SportsArbBot, LogicalArbBot. MomentumBot DELETED. EnsembleBot ARCHIVED (-$5.6k).

---

## ESPORTSBOT DEEP DIVE

### Signal Generation Pipeline
1. **Market Discovery** (`esports/markets/esports_market_service.py`):
   - Queries DB for ALL active unresolved markets with `yes_price BETWEEN 0.03 AND 0.97`
   - Keyword-gates via `_is_real_esports()` using both substring matching (long keywords) and word-boundary regex (short acronyms like `\blec\b`, `\bpgl\b`)
   - Background CLOB price refresh every 5 min
   - Cache TTL: 120s

2. **Game Detection** (`_detect_game()` in both `esports_bot.py` and `esports_market_service.py`):
   - Uses `_ESPORTS_GAME_KEYWORDS` dict for substring matches (safe long keywords)
   - Uses `_BOUNDARY_KEYWORDS` / `_WB_*` compiled regex for short acronyms requiring `\b` word boundaries
   - Games: lol, cs2, dota2, valorant, cod, r6, sc2, rl

3. **Market Classification** (`_classify_market_type()`):
   - `match_winner` (default) — requires two teams, produces Glicko-2 prediction
   - `map_winner` — "game 1/2/3", "map" keywords
   - `tournament_winner` — "tournament", "championship", "season" (single-team outright, NO Glicko-2)
   - `total_maps` — over/under
   - `first_blood` — first kill
   - `props` — "mvp", "kills", "assists", "be said", "signs for"
   - **Skipped types** (no Glicko-2 possible): `props`, `first_blood`, `tournament_winner`

4. **Glicko-2 Prediction** (`_get_glicko2_prediction()`):
   - Extracts team names from "A vs B" question pattern
   - Looks up teams in `_team_name_to_id` dict (populated from `glicko2_ratings` table, NOT `esports_teams` which is EMPTY)
   - Uses longest-substring-first matching for fuzzy names
   - Returns `model_prob` for team A winning

5. **Cross-Game XGB Model** (`_predict_cross_game()`):
   - 9 features: team ratings, RDs, volatilities, recent form (2 features added Session 72)
   - Loaded from `/opt/pa2-shared/saved_models/cross_game_xgb.json`
   - Retrained every 24h via `LearningScheduler.esports_trainer`

6. **Game-Specific Models**:
   - LoL: `LoLModel` (gold diff, tower diff, dragon)
   - CS2: `CS2Model` (round diff, economy)
   - Dota2: `Dota2Model` + OpenDota form adjustment (±3%)
   - Valorant: (basic Glicko-2 only currently)

7. **Confluence Scoring** (`_compute_confluence_score()`):
   - Weighted: edge 55%, freshness 30%, agreement 15%
   - Freshness decay: 30s for live, 120s default (changed Session 74 from 120→30 for live)
   - Min confluence: 0.60

### Analyze Opportunity Pipeline (with waterfall counters)
```
Market → _detect_game() → [no_game if unknown]
       → price check 0.03-0.97 → [no_price]
       → token extraction → [no_token]
       → game halted check → [halted]
       → per-game exposure cap → [exposure_cap]
       → 48h observation window → [observation]
       → _classify_market_type → [no_prediction if props/first_blood/tournament_winner]
       → _get_model_prediction → [no_prediction if Glicko-2 can't match teams]
       → edge check (>= min_edge) → [low_edge]
       → edge cap check (<= max_edge) → [edge_cap]
       → confidence check (>= min_confidence) → [low_confidence]
       → confluence check (>= min_confluence) → [low_confluence]
       → [passed] → trade execution
```

### Current Waterfall State (2026-03-12 00:57 UTC)
```
markets=34, markets_by_game={'lol':10, 'cs2':11, 'valorant':10, 'cod':3}
waterfall={'observation':10, 'no_prediction':13, 'low_edge':4, 'edge_cap':1, 'low_confidence':5}
```
- `observation=10`: Markets in 48h observation window (normal — they'll graduate)
- `no_prediction=13`: 11 props/tournament skips + 2 genuine Glicko-2 misses (Contra not in DB, BIG "qualify" not a match)
- `low_edge=4`: Edge below 0.05 threshold
- `edge_cap=1`: Edge above 0.25 max
- `low_confidence=5`: Confidence below 0.52 (genuinely uncertain predictions, working as designed)

### Key Config (VPS live values)
```
ESPORTS_MIN_EDGE=0.05         # Was 0.08, lowered this session
ESPORTS_MIN_CONFIDENCE=0.52   # Was 0.55, lowered Session 76
ESPORTS_MAX_EDGE=0.25         # Was 0.20 (code default), raised this session
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0  # Was 120, changed Session 74
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0
```

---

## WHAT WAS DONE THIS SESSION (Session 78, 2026-03-12)

### Problem: EsportsBot scanning 171 markets but producing ~0 trades

### Root Causes Found & Fixed

**1. False-Positive Game Detection (171→34 markets)**
- Short acronyms (`lec`, `lcs`, `lpl`, `msi`, `esl`, `pgl`, `iem`, `dpc`, `cdl`, `gsl`, `asl`) were matching inside common words ("election", "stablecoins", "councils")
- 135 non-esports markets (politics, crypto, etc.) falsely classified as esports
- **Fix**: Replaced bare substring checks with pre-compiled word-boundary regex (`\blec\b`, `\bpgl\b`, etc.)
- **Files**: `bots/esports_bot.py` (class-level `_WB_LOL`, `_WB_CS2`, `_WB_DOTA2`, `_WB_COD`, `_WB_SC2` tuples), `esports/markets/esports_market_service.py` (`_BOUNDARY_KEYWORDS` dict + `_game_matches()` helper)

**2. "The International" False Positive**
- `"the international"` as a dota2 keyword matched "International Court of Justice" markets
- **Fix**: Changed dota2 keywords from `("dota", "the international", " ti ")` to `("dota 2", "dota2", "dota:")` + boundary regex `\bthe international\s+\d` and `\bti\b`
- **Files**: Same two files

**3. Props/Non-Match Markets Wasting Glicko-2 Lookups**
- 7× Valorant "Will X be said during Grand Finals" markets have no teams
- CS2 "Nocries signs for a pro organization" — not a match
- Tournament outright "Will Team X win Tournament Y" — single-team, no opponent for Glicko-2
- **Fix**: Added `"be said"`, `"signs for"` to props classifier. Added `"tournament_winner"` to early-skip list.
- **File**: `bots/esports_bot.py` (`_classify_market_type()` and `analyze_opportunity()`)

**4. MIN_EDGE Too High (0.08→0.05)**
- 0.08 was filtering out legitimate small-edge opportunities
- **Fix**: `ESPORTS_MIN_EDGE=0.05` in `deploy/env.vps` and VPS `/opt/pa2-shared/.env`

**5. MAX_EDGE Too Low (0.20→0.25)**
- CS2 market with legitimate 0.2172 edge was being capped
- **Fix**: `ESPORTS_MAX_EDGE=0.25` added to `deploy/env.vps` and VPS `/opt/pa2-shared/.env`
- Code reads via `getattr(settings, "ESPORTS_MAX_EDGE", 0.20)` — env var overrides the 0.20 default

**6. Waterfall Diagnostic Logging (B4 from cross-pollination plan)**
- Added `self._wf` dict tracking every filter stage in `analyze_opportunity()`
- Non-zero counters logged in `esportsbot_scan_summary` as `waterfall={...}`
- Full pipeline visibility: `no_game → no_price → no_token → halted → exposure_cap → observation → no_prediction → low_edge → edge_cap → low_confidence → low_confluence → passed`

**7. Temporary INFO-Level Debug Logs** (should be reverted to DEBUG later)
- `esportsbot_glicko2_miss` — when prediction returns None
- `esportsbot_team_match_fail` — team name extraction/matching details
- `esportsbot_low_confidence` — confidence, model_prob, edge, side, price
- `esportsbot_edge_cap` — edge, max_edge, model_prob, price

### Files Modified This Session
| File | Changes |
|------|---------|
| `bots/esports_bot.py` | +94 lines: `import re`, word-boundary patterns, waterfall counters, props/tournament skip, debug→INFO logs, expanded `_classify_market_type()` |
| `esports/markets/esports_market_service.py` | +45 lines: `_BOUNDARY_KEYWORDS` dict, `_game_matches()` helper, refactored `_is_real_esports()` and `_detect_game()` |
| `deploy/env.vps` | ESPORTS_MIN_EDGE=0.05, ESPORTS_MIN_CONFIDENCE=0.52, ESPORTS_MAX_EDGE=0.25 |

### NOT YET COMMITTED TO GIT
All changes above are **deployed to VPS** but **not committed locally**. The git diff shows:
```
 bots/esports_bot.py                          | 94 ++++++++++++++++++++++++----
 deploy/env.vps                               |  5 +-
 esports/markets/esports_market_service.py    | 45 +++++++++----
 base_engine/data/ingestion_error_capture.txt |  2 +-  (test artifact, ignorable)
```

---

## CROSS-POLLINATION PLAN (MirrorBot & WeatherBot → EsportsBot)

### Status: Phase B4 (Waterfall Diagnostics) DONE. Everything else NOT started.

### Phase 1: Critical Risk Guardrails — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A1+A8** | Daily loss limit + drawdown halt. No daily P&L tracking, no circuit breaker. WeatherBot pattern: `_daily_pnl <= -_daily_loss_limit` + 10%/20% drawdown. | **HIGH** |
| **B1** | Stop-loss exits. No stop-loss at all. MirrorBot pattern: 15% stop-loss via `place_order(side="SELL")`. | **HIGH** |

### Phase 2: Position Intelligence — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A2** | Position re-evaluation. Never updates open position predictions. WeatherBot re-runs forecast every scan. | **HIGH** |
| **A10** | Pre-update exposure before order. Current: updates after `place_order()`. Race condition with concurrent WS+scan trades. | **MED** |
| **B3** | Exit exposure decrement. `_game_exposure` only increments, never decrements on exits. | **MED** |

### Phase 3: Sizing Upgrades — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A5** | Near-expiry confidence boost. <6h: 1.5×, <24h: 1.2×. | **MED** |
| **A6** | Uncertainty-scaled sizing (Baker-McHale). Use Glicko-2 φ as uncertainty proxy. | **MED** |
| **A3** | Dynamic Kelly graduation. 50+ resolved + Brier<0.24 → Kelly 0.30. | **MED** |

### Phase 4: Diagnostics — PARTIALLY DONE
| Item | Description | Status |
|------|-------------|--------|
| **B4** | Waterfall diagnostic logging | **DONE** this session |
| **A4** | Tournament-aware scan interval (60s near match, 120s default) | NOT DONE |

### Deferred Items
- **B5** (Per-model reliability): Needs 50+ resolved per game. Blocked on resolution data.
- **A7** (Slippage-adjusted edge): CLOB liquidity API unreliable for esports.
- **A9** (Lead-time-graduated edge cap): Low priority, flat 0.25 is conservative enough.
- **B2** (Max hold time exit): Esports markets resolve within 24-48h typically.

---

## ESPORTSBOT KEY CODE LOCATIONS

| What | File | Line/Function |
|------|------|---------------|
| Bot entry point | `bots/esports_bot.py` | `class EsportsBot(BaseBot)` |
| `scan_and_trade()` | `bots/esports_bot.py` | Main scan loop, calls `analyze_opportunity()` per market |
| `analyze_opportunity()` | `bots/esports_bot.py` | Full pipeline: game detect → model predict → edge/confidence → confluence |
| `_detect_game()` | `bots/esports_bot.py` ~line 1390 | Keyword + regex game detection |
| `_classify_market_type()` | `bots/esports_bot.py` ~line 1435 | Market type classification |
| `_get_model_prediction()` | `bots/esports_bot.py` | Dispatches to Glicko-2, XGB, game-specific models |
| `_get_glicko2_prediction()` | `bots/esports_bot.py` ~line 1860 | Team extraction, Glicko-2 rating lookup, prob computation |
| `_predict_cross_game()` | `bots/esports_bot.py` ~line 1940 | XGBoost 9-feature model |
| `_build_glicko2_game_state()` | `bots/esports_bot.py` ~line 1966 | Feature dict from Glicko-2 ratings |
| `_compute_confluence_score()` | `bots/esports_bot.py` ~line 1450 | Weighted confluence: edge + freshness + agreement |
| `_execute_esports_trade()` | `bots/esports_bot.py` | Position sizing + `place_order()` |
| `_check_monitoring_thresholds()` | `bots/esports_bot.py` | Runs every 10 min, Brier/accuracy checks |
| `_backfill_esports_outcomes()` | `bots/esports_bot.py` | Every 10 scans, resolves predictions from paper_trades |
| Market service | `esports/markets/esports_market_service.py` | DB query + CLOB price refresh |
| `_is_real_esports()` | `esports/markets/esports_market_service.py` | Double-gate soccer/football filter |
| Word-boundary patterns | `esports_bot.py` class attrs `_WB_LOL` etc. | Pre-compiled regex for short acronyms |
| Word-boundary patterns | `esports_market_service.py` `_BOUNDARY_KEYWORDS` | Same patterns for market service |
| Esports DB queries | `esports/data/esports_db.py` | P&L summary (DISTINCT ON CTE), predictions |
| PandaScore client | `esports/data/pandascore_client.py` | API client with class-level shared rate counter |
| Glicko-2 ratings | `esports/models/glicko2_engine.py` | Rating system, stored in `glicko2_ratings` table |
| Cross-game XGB | `esports/models/cross_game_model.py` | XGBoost training + inference |
| LoL model | `esports/models/lol_model.py` | LoL-specific features |
| CS2 model | `esports/models/cs2_model.py` | CS2-specific features |
| Dota2 model | `esports/models/dota2_model.py` | Dota2-specific features |
| OpenDota client | `esports/data/opendota_client.py` | Dota2 enrichment data |

---

## KNOWN ISSUES & OUTSTANDING WORK

### EsportsBot Specific
1. **LoL 0 opportunities**: 10 LoL markets scanned consistently → 0 opportunities. Team name extraction works (no `glicko2_miss` for LoL). Likely ALL LoL markets are either in observation window or have low edge/confidence. **Not a bug** — monitor as markets mature.

2. **Temporary INFO logs**: `esportsbot_glicko2_miss`, `esportsbot_team_match_fail`, `esportsbot_low_confidence`, `esportsbot_edge_cap` are at INFO for diagnosis. Should be reverted to DEBUG after bottleneck analysis is complete to reduce log noise.

3. **`esports_teams` table is EMPTY**: The `_team_name_to_id` dict is populated solely from `glicko2_ratings.team_key → team_key` self-references. This works but means no PandaScore external_id mapping exists. Not blocking.

4. **Uncommitted changes**: All Session 78 code changes are deployed to VPS but not committed to git. Need: `git add bots/esports_bot.py esports/markets/esports_market_service.py deploy/env.vps && git commit`.

### Cross-Bot
5. **MirrorBot blocked**: $20k exposure from 49 legacy positions exceeds $20k cap. Will self-heal as positions exit/resolve. Monitor.
6. **WeatherBot `_log_weather_prediction()`**: Method exists but NOT wired to scan loop. Needed for calibration tracking.
7. **CANARY_STAGE=0**: Paper trading only. See `LIVE_READINESS.md` for Stage 0→1 gate criteria.

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` requires `side="YES"` or `side="NO"`. NEVER pass "BUY"/"SELL".
2. **`paper_trades` has NO `metadata` JSONB column** — never assume it exists.
3. **Resolution backfill MUST exclude SELL trades** (`AND LOWER(pt.side) != 'sell'`). SELL P&L computed by paper engine at exit time.
4. **`ESPORTS_MIN_EDGE` default in `settings.py` is 0.08** — but VPS env overrides to 0.05. The `getattr(settings, "ESPORTS_MIN_EDGE", 0.05)` fallback in `esports_bot.py` never fires because settings defines the attribute.
5. **`ESPORTS_MAX_EDGE` is NOT in settings.py** — only read via `getattr(settings, "ESPORTS_MAX_EDGE", 0.20)`. Must be set as env var to override.
6. **asyncpg JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`.
7. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python date strings.
8. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**. Both must pass.
9. **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager is the real sizer.
10. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
11. **Position `current_price` auto-updated every 10s** by `position_manager._update_current_prices()`.
12. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).
13. **VPS shared .env** (`/opt/pa2-shared/.env`) is the REAL config. Deploy copies `deploy/env.vps` to release dir but systemd reads the shared one.
14. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
15. **BOT_REGISTRY has 14 bots** — shared module changes require all 14 verified.

---

## STATE PERSISTENCE (All Gaps Closed)

| State | Mechanism | Bot |
|-------|-----------|-----|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | All |
| `_game_exposure` | `daily_counters` write-through + `_restore_exposure_from_db()` | EsportsBot |
| `_group/_city_exposure` | `_restore_exposure_from_db()` | WeatherBot |
| `_daily_exposure` | `_restore_state_on_startup()` paper_trades SUM | MirrorBot |
| Exit cooldowns | Redis TTL `_save/_restore_exits_from_redis()` | WeatherBot |
| Open positions | `order_gateway.seed_positions_from_db()` | All |
| XGB model | `/opt/pa2-shared/saved_models/cross_game_xgb.json` | EsportsBot |
| Glicko-2 ratings | `glicko2_ratings` table | EsportsBot |

---

## RECENT GIT HISTORY (last 30 commits)

```
5b686f1 fix(weather): zombie position cleanup — 20h age + resolved paper_trade check
674ff4d fix(mirror+engine): stale entry pricing + resolution backfill SELL overwrite
af25abf config(mirror): raise max_daily_usd $3k→$10k
339d8d0 feat(cross-bot): WeatherBot prediction logging + EsportsBot debug logging
259b3f4 fix(mirror): P1-P7 — phantom trade dedup, exposure logging, daily cap $3k
900fcbd fix(weather): 7 silent bugs — monitoring, wind trades, exposure, logging
799b5ac fix(mirror): stop-loss exits use SELL to bypass risk price bounds
46a0f70 fix(weather): position dedup in trade execution, 30-min resolution backfill, reconciler accuracy
71c3ff8 perf(weather_bot): parallelize precip/snow/wind scans + NWS alerts + add phase timing
4516455 fix(engine): suppress RPC 401 noise, stale sync_log, task lifecycle
a311fec fix(esports): bounded cache cleanup prevents unbounded memory growth
aed0ba3 fix(weather): await _save_exit_to_redis — no more fire-and-forget
4af9b5d fix(esports): match.match_id instead of match.get("id") on dataclass
e8f23b2 fix(pandascore): use class-level counter in 429 rate-limit log
5c3c451 fix(reconciler): use ANY(:ids) parameterized binding — no SQL injection
f905fbe feat(reconciler): H2 — schedule position reconcile every 30 min
2b85073 feat(paper): H1 — correlation_id idempotency guard prevents double-fills
8c25779 feat(reconciliation): paper trading position reconciler
862db1a feat(paper): order state machine PENDING→SUBMITTED→FILLED + migration 039
5604f61 feat(ws): REST resync callback after WebSocket reconnect
da2b214 feat(kill_switch): B1 — mark open positions halted on kill switch engage
adfdae4 feat(alerting): daily PnL summary alert via Slack/Discord
5b5cdec feat(pandascore): shared class-level rate counter across all 3 esports bots
f3896b3 feat(base_engine): startup logs for shared PolymarketClient and CANARY_STAGE
95900de refactor(elite_reliability): extract _build_beta_rec helper, add category cache
e54f524 docs(migration): confirm 035_positions_trader_addresses has no naming collision
d025f41 docs(esports_bot): document _ws_pending_trades bounded lifetime + unit tests
a2efcd8 fix(kill_switch): warn on engage that open positions are NOT auto-cancelled
518cda4 fix(scheduler): log exceptions from fire-and-forget asyncio tasks
65e4946 feat(mirror_bot): category-aware reliability, OrderedDict dedup, test coverage
```

---

## WHAT TO DO NEXT (Recommended Priority)

### Immediate (this session)
1. **Commit uncommitted changes** — `git add bots/esports_bot.py esports/markets/esports_market_service.py deploy/env.vps && git commit`

### Short-term (next 1-2 sessions)
2. **Phase 1: Daily loss limit + drawdown halt** (A1+A8) — Most critical risk guardrail missing from EsportsBot
3. **Phase 1: Stop-loss exits** (B1) — No way to exit losing positions before resolution
4. **Revert INFO debug logs to DEBUG** — Reduce log noise once bottleneck analysis is confirmed stable

### Medium-term
5. **Phase 2: Position re-evaluation** (A2) — Update open position predictions with fresh Glicko-2 data
6. **Phase 2: Pre-update exposure** (A10) — Fix race condition with concurrent WS+scan trades
7. **Phase 3: Sizing upgrades** (A5, A6, A3) — Near-expiry boost, uncertainty scaling, Kelly graduation

### Long-term / Deferred
8. **CANARY_STAGE 0→1** — Meet all gate criteria in `LIVE_READINESS.md`
9. **Per-model reliability tracking** (B5) — Needs 50+ resolved predictions per game
10. **Slippage-adjusted edge** (A7) — Blocked by CLOB liquidity API reliability

---

## DEVELOPMENT RULES (from CLAUDE.md)

1. **One fix per commit**. No "while I'm in here" refactors.
2. **Preserve function signatures**. Change callers if you change a signature.
3. **Read the entire file** before modifying, not just the target function.
4. **No new dependencies** without justification.
5. **Test before deploy**: `pytest` all pass → deploy → verify in VPS logs.
6. **Cross-bot verification**: If touching shared modules, verify ALL 14 bots.
7. **State the bug in one sentence** before writing any code.
8. **Forbidden**: Band-aid try/except, shotgun fixes, scope creep, silent migrations, optimistic rewrites.

---

## P&L SUMMARY (as of 2026-03-12)

| Bot | Resolved Trades | P&L | Win Rate |
|-----|----------------|-----|----------|
| WeatherBot | 140 | +$461.74 | 44% (62W/78L, avg win $11.38, avg loss $3.13) |
| MirrorBot | 14 | +$230.59 | 50% (7W/7L) |
| EsportsBot | ~47 | pending resolution | — |
| **Total** | **~201** | **+$692.33** | — |

---

## KEY ENVIRONMENT VARIABLES (full list in deploy/env.vps)

### EsportsBot-Specific
```
ESPORTS_MIN_EDGE=0.05
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MAX_EDGE=0.25
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_FRESHNESS_DECAY_SECONDS=120.0  (code default; VPS may override to 30.0)
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0
ESPORTS_MAKER_FALLBACK_TIMEOUT_S=3.0
```

### System-Wide
```
SIMULATION_MODE=true
PAPER_TRADING=true
LIVE_TRADING=false
LOG_LEVEL=INFO
DB_POOL_SIZE=40
DB_MAX_OVERFLOW=5
SCAN_MARKET_LIMIT=200
BOT_SCAN_TIMEOUT_SECONDS=180
```
