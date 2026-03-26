# CONTINUATION PROMPT — EsportsBot Session 110

## CRITICAL: Read This First
You are continuing an EsportsBot-only session for the Polymarket AI V2 automated trading system. Read `CLAUDE.md` in the repo root — it is the prime directive. Then read `AGENT_HANDOFF_ESPORTS_SESSION109_2026_03_19.md` for the latest state.

**This is a SINGLE BOT session. ESPORTS SCOPE LOCK is active.** Only touch: `bots/esports_bot.py`, `bots/esports_live_bot.py`, `bots/esports_series_bot.py`, `esports/**`, esports tests, `config/settings.py` (ESPORTS_ keys only). Shared modules ONLY if required for an esports bug fix. NEVER commit changes to mirror_bot.py, weather_bot.py, or other non-esports files.

---

## System Overview

**Polymarket AI V2**: 14-bot automated prediction market trading system. Paper trading mode (`SIMULATION_MODE=true`) on Ubuntu VPS (34.251.224.21). Real capital architecture, $0 execution flag.

**EsportsBot**: Trades esports match-winner markets using:
- **Glicko-2 ratings** (per-game trackers for 8 games: LoL, CS2, Valorant, Dota2, SC2, CoD, R6, RL)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a·ln(p) - b·ln(1-p) + c)` — fits per-game, needs 30+ resolved samples
- **Conformal prediction**: prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Confluence scoring**: 65% edge weight + 35% freshness weight, gate at 0.55
- **Paper trading engine**: shared across 14 bots, fill probability, VWAP book walk, alpha decay

---

## What Session 109 Did (2 commits, both deployed)

### Commit 1: Anti-Churn Fix (`f4cf596`)
**Problem**: -$925 in 28 minutes from 13+5 stop-loss/re-entry cycles on 2 markets.
- **RC1 fix**: 900s post-exit reentry cooldown (`_recently_exited` dict + Redis persistence)
- **RC2 fix**: Clear `_prediction_cache[mid]` on stop-loss exit (forces fresh Glicko-2 on re-entry)
- **RC3 fix**: Rolling 12h entry cap, max 2 entries/market (`_market_entry_times` timestamp list)
- New config: `ESPORTS_EXIT_COOLDOWN_SECONDS=900`, `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2`, `ESPORTS_ENTRY_WINDOW_HOURS=12.0`
- New waterfall counters: `exit_cooldown`, `max_entries`

### Commit 2: WS Reactive Path Activation (`9f9ac4c`)
**Problem**: WS reactive trading has NEVER worked for EsportsBot (16+ days). WS subscribed to general market tokens, zero overlap with esports.
- **Fix**: After each scan populates `_market_token_map`, subscribe new esports tokens to `websocket_manager.subscribe_price_stream()`. Reconnect handler auto-re-subscribes.
- **Result**: `esportsbot_ws_trading_resumed` + `ws_trading=True` confirmed on VPS within 6 seconds of first subscription.
- EsportsBot now reacts to price moves in ~1-2s instead of ~10s scan cadence.

---

## Current State (as of Session 109 end)

### BetaCalibrator: 4/8 games FITTED
| Game | N | Status | Parameters |
|------|---|--------|------------|
| Valorant | 1,927 | **FITTED** | a=1.0029, b=1.0061, c=0.002 |
| LoL | 365-367 | **FITTED** | a=0.9927, b=1.0035, c=0.0075 |
| CS2 | 229 | **FITTED** (new S109) | a=0.9775, b=1.0132, c=0.0251 |
| SC2 | 52 | **FITTED** | a=1.0134, b=0.999, c=-0.0068 |
| Dota2 | ~40 | Not logging (time window, self-healing) | — |
| CoD/R6/RL | 0 | No data | — |

### P&L (all-time)
| Entry Bucket | Markets | P&L |
|---|---|---|
| 1 entry | 119 | +$762.66 |
| 2 entries | 10 | +$137.70 |
| 3 entries | 1 | +$3.83 |
| 4+ entries | 2 | -$967.82 (churn — now fixed) |

### VPS Config
```
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MIN_CONFIDENCE=0.48, ESPORTS_MIN_EDGE=0.05, ESPORTS_MAX_EDGE=0.35 (0.45 for unfitted)
ESPORTS_CONFLUENCE_MIN=0.60, ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_EXIT_COOLDOWN_SECONDS=900, ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2, ESPORTS_ENTRY_WINDOW_HOURS=12.0
SIMULATION_MODE=true
```

### Scan Summary (live)
```
markets=18, markets_by_game={'lol': 9, 'cs2': 6, 'cod': 2, 'valorant': 1}
live_matches=9, ws_trading=True
waterfall={'exposure_cap': 7, 'no_prediction': 6, 'low_edge': 1, 'edge_cap': 3, 'low_confidence': 1}
```

---

## Architecture — Key Code Locations

### bots/esports_bot.py (~5,600 lines)
| Lines | What |
|-------|------|
| 30-148 | `BetaCalibrator` class — `fit_from_db()`, L-BFGS-B optimization, identity priors |
| 270-360 | `__init__` — all instance vars: Glicko-2, caches, `_recently_exited`, `_market_entry_times`, `_beta_calibrators`, WS state |
| 580-608 | `start()` — market service init, Redis cooldown restore, super().start() |
| 624-670 | `on_price_update()` — WS event handler. Early exits non-esports. Token map lookup. Calls super() only for matched markets. |
| 740-815 | WS reactive trade path — cooldown check, entry cap check, position check, trade execution |
| 820-855 | `_cleanup_caches()` — evicts stale predictions, token maps, exit cooldowns, entry timestamps |
| 857-920 | `scan_and_trade()` top — exposure restore, daily P&L, position fetch, loss limit, stop-loss exits |
| 1080-1260 | `_analyze_one()` + scan results — waterfall filters, cooldown/cap checks, reentry logic, WS token subscription, scan summary log |
| 1290-1553 | `_check_and_execute_exits()` — stop-loss at 15%, max hold time, SELL execution, **S109: sets _recently_exited + clears cache + Redis save** |
| 1880-1917 | `analyze_opportunity()` — populates `_market_token_map` for WS, edge validation setup |
| 2290-2320 | Prediction pipeline — XGB + Glicko-2 blend via extremized geometric mean |
| 2955-3015 | `_compute_confluence_score()` — edge=0.65, freshness=0.35, agreement=0.0 |
| 3060-3130 | Sizing pipeline — conformal bounds, drawdown Kelly, CLV-gated |
| 3350-1387 | Redis cooldown save/restore (WeatherBot pattern) |
| 3892-3907 | BetaCalibrator fitting loop in `_check_monitoring_thresholds` |

### config/settings.py
All `ESPORTS_*` settings at lines 1008-1197. Key new settings from S109:
- `ESPORTS_EXIT_COOLDOWN_SECONDS` (line ~1124)
- `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` (line ~1125)
- `ESPORTS_ENTRY_WINDOW_HOURS` (line ~1126)

### base_engine/data/websocket_manager.py (377 lines)
- `connect()` → `_message_loop()` → `_dispatch_message()` → `_handle_price_change()` → `_handle_price_change_one()` → EventBus emit
- `_reconnect()` re-subscribes all tokens in `self.subscriptions` (lines 107-113)
- `subscribe_price_stream()` — adds `price:{token_id}` to subscriptions set

### base_engine/base_engine.py
- `register_bot_for_price_events()` (line 1227): wires `bot.on_price_update` to EventBus
- `_subscribe_active_markets()` (line 2062): subscribes 500 general markets at startup
- WS init (line 640): creates WebSocketManager with event_bus, market_index_resolver, on_reconnect callback

### main.py
- Bot registration loop (line 587-594): `register_bot_for_price_events(bot)` then `bot.start()` with 5s stagger

---

## Prediction Pipeline (in order)

1. **Glicko-2 rating lookup** → raw `model_prob` (team A win probability)
2. **BetaCalibrator** (if fitted for game) → calibrated probability
3. **Online Platt scaling** (if available) → override calibration
4. **RFLB correction** → favorites-longshot bias adjustment
5. **BO adjustment** → best-of-1 dampening
6. **Cross-game XGBoost blend** → extremized geometric mean with Glicko-2 (0.6/0.4)
7. **Conformal prediction** → uncertainty intervals
8. **Edge calculation** → `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **Confluence scoring** → 0.65*edge + 0.35*freshness, gate at 0.55
10. **BotBankrollManager sizing** → Kelly fraction with conformal bounds

---

## Sizing Pipeline (in order)

1. **BotBankrollManager.calculate_bot_position_size()** — Kelly with `kelly_fraction=0.25`
2. **Near-expiry confidence boost** (A5) — increases confidence for markets near resolution
3. **Conformal conservative bounds** (A6/S100b) — shrinks size by prediction interval width
4. **Drawdown Kelly reduction** (A8) — reduces Kelly when daily P&L is negative
5. **CLV-gated scaling** — tier-based size multiplier
6. **Per-market cap** — `MIRROR_MAX_PER_MARKET=400` (shared config key)
7. **Game exposure cap** — `ESPORTS_MAX_GAME_EXPOSURE=600` per game
8. **Daily cap** — `max_daily_usd=10000`

---

## Learning-Phase Suspensions (auto-deactivate when BetaCalibrator fits per game)

These protective limits are active for UNFITTED games and automatically relax when a game's BetaCalibrator fits:
- **Edge cap**: 0.45 (unfitted) → 0.35 (fitted)
- **Monitoring halt**: suspended while unfitted
- **Tournament phase**: observation mode on patch changes
- **Game Kelly multiplier**: suspended until fitted
- **Phi sizing floor**: conformal floor active only when fitted

---

## Critical Traps (DO NOT BREAK)

1. **trade_events is P&L authority** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`, NEVER "BUY"/"SELL"
3. **P&L math is UNIFORM**: `cost = entry_price * size`, `uPnL = (current - entry) * size` — NEVER invert for NO
4. **`_market_token_map`**: populated in `analyze_opportunity`, consumed by WS `on_price_update` and WS subscription
5. **`_prediction_cache`**: keyed by market_id, contains `{prob, ts, game, event_data}` — cleared on stop-loss exit (S109)
6. **`_recently_exited`**: market_id → monotonic time. Set on stop-loss, checked before entry. Redis-persisted.
7. **`_market_entry_times`**: market_id → [monotonic timestamps]. Rolling 12h window, max 2 entries. In-memory only.
8. **BetaCalibrator query uses `NOW() - N days`** — precise to hour, not midnight. Can exclude borderline samples.
9. **PatchDriftDetector**: `_patch_timestamps` set ONLY on genuine patch changes (`old is not None`)
10. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
11. **Python 3.13 scoping**: `from X import Y` inside function shadows module-level — causes UnboundLocalError
12. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
13. **`paper_trades` has NO `metadata` JSONB column**
14. **`positions` table has NO `closed_at` or `updated_at`** — use trade_events EXIT events
15. **`prediction_log` has NO `rejection_reason`** — use `trade_executed` bool
16. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables → uses WHERE NOT EXISTS
17. **Scope Lock**: NEVER add unsolicited features. Only fix what handoff or user explicitly requests.

---

## Outstanding Items (Priority Order)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | **Scan loop speed optimization** — 7-10s/cycle. Profile and reduce without losing functionality. WS now handles real-time trading, so scan is mainly cache-warming. | **TODO** | Profile bottlenecks (PandaScore API, DB queries, Glicko-2 compute), consider caching/batching |
| P2 | RC4: Entry price inflation — positions stores requested price not fill price | Deferred | Separate session — touches shared position_manager |
| P2 | Kelly degradation suspended (CS2 now fitted, Dota2/CoD/R6/RL block ALL-fitted) | Blocked | Wait for more data |
| P3 | LoL Brier=0.2842 (near 0.30 halt threshold) | Monitoring | Check trend |
| P3 | EsportsSeriesBot silent (no series markets on Polymarket) | Expected | No fix |
| P3 | WS reconnect stability — drops every ~40s-5min | Auto-recovers | Monitor |
| P4 | Dota2 Brier=0.3002 (over threshold, 77.5% WR) | Suspension active | Self-governs when fitted |
| P5 | taker_side dead code / PAPER_BOOK_WALK_ENABLED | No data source | Deferred |
| P5 | CoD/R6/RL — no BetaCalibrator data | Too few markets | Low priority |

---

## Feedback Rules (MANDATORY)

1. **Scope Lock** (`memory/feedback_scope_lock.md`): NEVER add unsolicited features. Only fix what handoff/user explicitly requests. "I noticed X could be improved" → mention in handoff, do NOT implement.
2. **Bot Sessions** (`memory/feedback_bot_sessions.md`): Esports sessions are hardcoded esports-only. Never commit non-esports changes.
3. **P&L Math** (`memory/feedback_pnl_math.md`): NEVER invert formulas for NO positions. `cost = entry_price * size` for ALL sides. Canonical script: `python3 scripts/bot_pnl.py BotName hours`.

---

## VPS Access

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# SSH
ssh -i $SSH_KEY -o StrictHostKeyChecking=no $VPS "COMMAND"

# Deploy (SCP + copy + restart)
scp -i $SSH_KEY bots/esports_bot.py $VPS:/tmp/
ssh -i $SSH_KEY $VPS "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo chown polymarket:polymarket /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai"

# Use python3 not python on VPS
# P&L query:
ssh -i $SSH_KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;\""

# Logs:
ssh -i $SSH_KEY $VPS "sudo journalctl -u polymarket-ai --since '5 min ago' | grep esportsbot_scan_summary | tail -3"
```

---

## Verification Commands

```bash
# Scan health
journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3

# Anti-churn (after stop-loss fires)
journalctl -u polymarket-ai -f | grep "exit_cooldown\|max_entries\|esportsbot_stop_loss"

# WS status
journalctl -u polymarket-ai --since "5 min ago" | grep "esportsbot_ws_subscribed\|ws_trading"

# BetaCalibrator
journalctl -u polymarket-ai --since "30 min ago" | grep beta_cal

# P&L
sudo -u polymarket psql -d polymarket -c "SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;"

# Brier scores
sudo -u polymarket psql -d polymarket -c "SELECT game, COUNT(*), ROUND(AVG((predicted_prob-COALESCE(actual_outcome,0))^2)::numeric,4) as brier FROM esports_prediction_log WHERE created_at>'2026-03-16' AND actual_outcome IS NOT NULL GROUP BY game ORDER BY brier;"
```

---

## Session 110 Start Checklist

1. Read `CLAUDE.md` (repo root)
2. Read `AGENT_HANDOFF_ESPORTS_SESSION109_2026_03_19.md`
3. State what you will work on (from outstanding items or user request)
4. List files you will touch (max 3 unless justified)
5. Grep for dependents before editing
6. Git snapshot before any edit
