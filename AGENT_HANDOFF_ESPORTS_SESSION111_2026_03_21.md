# AGENT HANDOFF — EsportsBot Session 111 (2026-03-21)

## Session Type: EsportsBot-scoped (exposure caps + diagnostics + deploy)

## CRITICAL CONTEXT FOR NEXT AGENT

This is a **single-bot session for EsportsBot only**. Do not touch MirrorBot, WeatherBot, or any other bot's code/config unless explicitly requested. Read `CLAUDE.md` before any code change — it contains non-negotiable rules for this live trading system.

### System Architecture (EsportsBot-specific)
- **15 bots** total in BOT_REGISTRY, but this session is EsportsBot-scoped
- **EsportsBot** = pre-game match winner predictions using Glicko-2 ratings + GBM models
- **EsportsLiveBot** = live in-game trading using WS price feeds (shares `esports_bot.py`)
- **EsportsSeriesBot** = series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)
- **Paper trading mode**: `SIMULATION_MODE=true`. Paper trading IS production (see CLAUDE.md)
- **VPS**: Ubuntu at 34.251.224.21, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `scp` files to `/tmp/`, `sudo cp` to `/opt/polymarket-ai-v2/`, `sudo systemctl restart polymarket-ai`

---

## What Was Done This Session (S111)

### 1. S110 Committed and Deployed
- **Commit `85e3ba1`**: OPT-4 retrain parallelization, scan timing instrumentation, scan interval 10s→2s, `_churn_blocked()` helper for series scan, exit-failure cooldown gaps fixed
- Files: `bots/esports_bot.py`, `config/settings.py`

### 2. Brier Halt Disabled — ALL GAMES TRADING
- **Root cause**: CS2 was halted (Brier 0.334 > 0.30 threshold), blocking 8 markets per scan
- **Fix**: Set `ESPORTS_BRIER_HALT_THRESHOLD=999.0` in `config/settings.py` — effectively disables halting
- **Rationale**: User directive — "make sure all games are trading even if they are shit. we need to learn"
- **Rollback**: `export ESPORTS_BRIER_HALT_THRESHOLD=0.30` if ever needed
- Deployed to VPS. Verified: `halted_games=None` in scan summary, CS2 markets flowing through waterfall

### 3. Exposure Caps Raised — ROOT CAUSE OF LOW TRADE VOLUME FOUND AND FIXED
- **Root cause**: `exposure_cap` was blocking **27 out of 29 markets** on Friday night despite 7 live matches. The per-game cap of $600 exhausted after ~2 bets per game.
- **Commit `a916348`**: 6 config changes in `config/settings.py`:

| Setting | Before | After | Rationale |
|---------|--------|-------|-----------|
| `ESPORTS_MAX_GAME_EXPOSURE` | $600 | **$3,000** | Was the primary blocker. 15% of capital per game |
| `ESPORTS_MAX_TOURNAMENT_EXPOSURE` | $400 | **$5,000** | hierarchy: team < game < tournament. 25% of capital |
| `ESPORTS_MAX_TEAM_EXPOSURE` | $300 | **$1,000** | 3-4 positions per team. 5% of capital |
| `ESPORTS_MAX_DAILY_USD` | $10,000 | **$20,000** | Allow full daily cycling |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | 2 per 12h | **3 per 12h** | Winner pattern used 4 entries; 2 was too tight |
| `ESPORTS_MAX_TOTAL_EXPOSURE_USD` | $15,000 | **$15,000** | KEPT — 75% of $20K capital. Do not exceed capital. |

- **Deployed to VPS. Verified**: First scan post-deploy: `opportunities=8, trades=4, exposure_cap=0` (was `opportunities=0, trades=0, exposure_cap=27`)

### 4. Comprehensive VPS Health Check
- Bot scanning actively, no errors, WS connected
- BetaCalibrator fitted for 5 games (LoL, CS2, Valorant, SC2, Dota2)
- 6 open positions, $454 exposure
- CS2 Brier 0.334 (was halted, now trading), LoL Brier 0.284 (approaching 0.30, monitor)

### 5. Team Name Matching Investigation (P3: `no_prediction`)
- **Current state**: 5-6 `no_prediction` per scan (down from 12), mostly `tournament_winner` market type skips (correct behavior) + 1 unknown team (`GAMEHARMONY`)
- **Architecture**: 6 regex extraction patterns → `_clean_team_names()` normalization → 6-tier matching (`_match_team_name()`) with exact/alias/substring/fuzzy fallback → on-demand PandaScore backfill (3/scan budget)
- **Key files**: `bots/esports_bot.py` lines 4742-5084 (extraction + matching), lines 4961-5018 (`_TEAM_ALIASES` dict), lines 4308-4393 (backfill)
- **Not actionable now** — 5 correct skips + 1 obscure team is healthy. Monitor if count grows.

### 6. P&L Deep Dive and Audit
- **All-time EsportsBot**: -$643 realized (improving from -$1,400 at S110)
- **Last 24h (at time of audit)**: +$752 — driven by volatile match flipping (0x90e46f: 7 entries, +$997)
- **Churn analysis**: Pre-S110 churn (Mar 19) caused -$1,839 across 2 markets (14 entries in 28 min, 5 entries chasing dying team). Post-S110 churn gate working — max 3 entries per market.
- **Winner vs loser pattern**: Both used "buy dip, re-enter on same thesis" tactic. Winner's team came back (+$338 exit). Loser's team kept losing (-$294 total). The 15-min exit cooldown expired between stop-loss and re-entry (20 min gap). Decision: **monitor and review on handoffs** — don't change cooldown yet.

---

## Current VPS Config (LIVE as of S111 deploy)

```env
# Bankroll
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000          # S111: was 10000

# Exposure caps
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000  # 75% of capital — DO NOT exceed capital
ESPORTS_MAX_GAME_EXPOSURE=3000        # S111: was 600
ESPORTS_MAX_TOURNAMENT_EXPOSURE=5000  # S111: was 400
ESPORTS_MAX_TEAM_EXPOSURE=1000        # S111: was 300

# Trading thresholds
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MAX_EDGE=0.35
ESPORTS_STOP_LOSS_PCT=0.15

# Anti-churn
ESPORTS_EXIT_COOLDOWN_SECONDS=900     # 15 min — may need increase, monitor
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=3  # S111: was 2
ESPORTS_ENTRY_WINDOW_HOURS=12.0

# Scan
SCAN_INTERVAL_ESPORTS_LIVE=2          # S110: was 10

# Halt
ESPORTS_BRIER_HALT_THRESHOLD=999.0    # S111: effectively disabled

# Other
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
```

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | Exit cooldown 15 min may be too short — loser re-entered 20 min after stop-loss on same side | Monitoring | Review on next handoff. Consider same-side guard or 30 min cooldown |
| **P2** | RC4: Entry price inflation — positions table stores requested price not actual fill price | Deferred | Separate session — touches shared position_manager |
| **P2** | Kelly degradation suspended (CS2 now fitted, but needs ALL 8 games) | Blocked on CoD/R6/RL data | None — wait for data |
| **P3** | LoL Brier=0.284 (near old 0.30 threshold) | Monitoring | Halt disabled, but if Brier degrades further the model is miscalibrated |
| **P3** | CS2 Brier=0.334 — worst calibration of all games | Trading (halt disabled) | Learning data accumulating. Monitor for improvement |
| **P3** | `no_prediction: 5-6` per scan — mostly tournament_winner skips | Healthy | Only actionable if count grows significantly |
| **P3** | WS reconnect stability — drops every ~40s-5min | Working (auto-reconnects) | Monitor |
| **P3** | EsportsSeriesBot silent | No series markets on Polymarket | Expected |
| **P4** | Dota2 Brier=0.260 | Trading | Self-governs when fitted |
| **P5** | taker_side dead code / PAPER_BOOK_WALK_ENABLED | No data source | Deferred |

### Items RESOLVED This Session

| Item | Resolution |
|------|-----------|
| CS2 halted by Brier threshold | Disabled halt (`BRIER_HALT_THRESHOLD=999.0`). All games now trading. |
| `exposure_cap` blocking 27/29 markets (Friday night zero trades) | Raised per-game from $600→$3K, tournament $400→$5K, team $300→$1K. First scan post-deploy: 4 trades, 0 exposure_cap blocks. |
| Low daily throughput ($10K cap) | Raised to $20K — allows full position cycling. |
| Entry window too tight (2 per 12h) | Raised to 3 per 12h. Winner pattern needed 4 attempts. |

---

## Key Files (EsportsBot)

| File | Purpose |
|------|---------|
| `bots/esports_bot.py` | Main bot (~5000 lines). Scan loop, predictions, trade execution, WS reactive, series scan |
| `config/settings.py` | All ESPORTS_* config with env var overrides |
| `esports/models/glicko2.py` | Glicko-2 rating algorithm |
| `esports/data/pandascore_client.py` | PandaScore API wrapper (team search, match data) |
| `esports/data/esports_data_collector.py` | Feature extraction from PandaScore matches |
| `scripts/esports_diag.py` | Diagnostic script (waterfall, P&L, exposure) |
| `scripts/bot_pnl.py` | Canonical P&L script: `python scripts/bot_pnl.py EsportsBot 24` |

---

## Critical Traps (EsportsBot-specific, DO NOT BREAK)

1. **`_game_exposure` is tracked in USD** (`price * size`), not shares. Do not mix units.
2. **`_churn_blocked()` must gate ALL paths to `_execute_esports_trade()`** — scan path (line 1138), WS reactive path (line 759), AND series path (line 5161/5174). Missing any one creates a churn backdoor.
3. **`_recently_exited` persists to Redis** via `_save_exit_cooldown_to_redis()`. Survives restarts.
4. **`_market_entry_times` does NOT persist** — resets on restart. Acceptable because 12h window means old entries expire anyway.
5. **BetaCalibrator** runs per-game. Calibrator status logged as `beta_cal_*`. Check `n=` count — needs ~50+ samples to be meaningful.
6. **PandaScore rate limit**: 1000/hr budget. Current usage ~400/hr. `_refresh_live_matches()` has 15s time guard regardless of scan interval.
7. **`paper_trades` has NO `metadata` column** — never assume it exists.
8. **`positions` table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`. Use `source_bot` not `bot_name` to filter.
9. **trade_events is P&L authority** — `paper_trades` is legacy. EXIT P&L only in trade_events.
10. **PatchDriftDetector**: `_patch_timestamps` set only on genuine patches (`old is not None`). Setting on first check falsely triggers 48h observation mode.
11. **Rolling 24h queries are unreliable for comparisons** — use fixed date ranges instead.
12. **Exposure cap hierarchy**: team ($1K) < game ($3K) < tournament ($5K) < total ($15K). Total must not exceed capital ($20K).

---

## Scan Waterfall (what blocks trades, in order)

1. `no_game` — can't detect game from question
2. `halted` — Brier halt (currently DISABLED)
3. `exposure_cap` — per-game/tournament/team cap exceeded
4. `observation` — patch drift detector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited this market (15 min)
7. `max_entries` — 3 entries per market per 12h window
8. `low_confidence` — below 0.48
9. `low_edge` — below 0.05
10. `edge_cap` — above 0.35 (suspicious)
11. `reentry_rejected` — has position, wrong direction or insufficient edge
12. `passed` → goes to `_execute_esports_trade()`

---

## BetaCalibrator Status (as of S111)

| Game | Samples | Brier | Status |
|------|---------|-------|--------|
| SC2 | 57 | 0.0195 | Excellent |
| Valorant | 7,782 | 0.2083 | Good |
| Dota2 | 53 | 0.2601 | OK |
| LoL | 371 | 0.2842 | OK — approaching concern |
| CS2 | 239 | 0.3340 | Poor — trading for learning data |
| CoD | ~15 | N/A | Insufficient data |
| R6 | ~10 | N/A | Insufficient data |
| RL | ~5 | N/A | Insufficient data |

---

## P&L Summary (as of S111)

### All-Time
| Event | Count | Realized |
|-------|-------|----------|
| ENTRY | 189+ | $0 |
| EXIT | 112+ | -$8.37 |
| RESOLUTION | 125+ | -$634.86 |
| **Total** | | **~-$643** |

### Trend: Recovering. Mar 19 was -$1,535 (pre-S109 churn). Mar 20 was +$752. Post-S111 caps should increase trade volume significantly.

---

## Commits This Session

| SHA | Description |
|-----|-------------|
| `85e3ba1` | S110: OPT-4 retrain parallelization, scan timing, interval 10s→2s, churn fix |
| `a916348` | S111: Raise exposure caps (game $3K, tournament $5K, team $1K, daily $20K, entries 3/12h) |

---

## Verification Commands

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Scan health (should see exposure_cap=0 or very low, opportunities>0)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep esportsbot_scan_summary | tail -3"

# P&L (use fixed date ranges, NOT rolling 24h)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;\""

# Open positions
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FROM positions WHERE source_bot='EsportsBot' AND status='open';\""

# Brier scores by game
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT game, COUNT(*), ROUND(AVG((predicted_prob-COALESCE(actual_outcome,0))^2)::numeric,4) as brier FROM esports_prediction_log WHERE created_at>'2026-03-16' AND actual_outcome IS NOT NULL GROUP BY game ORDER BY brier;\""

# Errors
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'EsportsBot.*error\|EsportsBot.*exception' | tail -5"
```

---

## S115 Cross-Bot Change: Shadow Fill Tracking (affects EsportsBot)

**Session**: S115 (same day, separate scope — all bots)
**Full handoff**: `AGENT_HANDOFF_SHADOW_FILLS_SESSION115_2026_03_21.md`

### What changed for EsportsBot:
- **paper_trading.py**: All theoretical slippage models REMOVED. BUY orders now fill at real VWAP from L2 orderbook walk.
- **order_gateway.py**: Pre-trade book walk + edge-at-VWAP gate. If `confidence <= VWAP`, trade rejected (paper AND live).
- **esports_bot.py**: Added `self._scan_start_mono = _now` at `scan_and_trade()` entry (line 869). Added `"scan_start_mono"` to `_event_data` dict (line 2440).
- **shadow_fills table**: Every BUY signal recorded with full book snapshot + VWAP + edge. Resolution backfill computes retroactive P&L.
- **Confluence gate KEPT**: `_compute_confluence_score()` is a signal quality measure, not an execution model. Unchanged.
- **Net effect**: EsportsBot trades fill at real book prices. Latency now tracked. Edge gate catches stale prices before order submission. Esports books are chronically thin — shadow_fills data will show how often `fill_fraction < 1.0`.

### Review items:
- [ ] After 24h: `SELECT COUNT(*), AVG(latency_ms), AVG(fill_fraction) FROM shadow_fills WHERE bot_name='EsportsBot'` — verify latency + check how thin esports books really are
- [ ] WebSocket orderbook upgrade — deferred, review if shadow data shows >1 cent avg staleness cost

## User Directives (carry forward)

1. **"All games trade, even if they are shit. We need to learn."** — Do not re-enable Brier halting without explicit user request.
2. **"Paper trading is production"** — Never cut corners because SIMULATION_MODE=true. Max total exposure must not exceed capital.
3. **"Monitor exit cooldown and review on handoffs"** — The 15-min cooldown question is deferred. Collect data, don't change yet.
4. **Scope lock** — Fix only what is requested. No unsolicited features, refactors, or "while I'm in here" changes.
