# AGENT HANDOFF — EsportsBot Session 112 (2026-03-21)

## Session Type: EsportsBot-scoped (prediction_log dedup + edge cap removal + diagnostics)

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

## What Was Done This Session (S112)

### 1. Prediction Log Dedup — ROOT CAUSE FIX
- **Root cause**: `esports_prediction_log` had no unique constraint. The bare `INSERT` combined with in-memory dedup (10-min TTL, 1-hour eviction, resets on restart) produced **~97 duplicate rows per unique market** (19,860 rows for 200 markets).
- **Impact**: All accuracy/Brier queries were garbage. The "100% accuracy on 70 samples at edge>0.30" reported in investigation was actually **2 unique SC2/CoD matches** logged 35 times each.
- **Fix (migration 057)**:
  - Deleted 19,660 duplicate rows (kept newest per market_id+bot_name)
  - Added unique index `idx_esports_pred_log_market_bot ON (market_id, bot_name)`
  - Changed `INSERT` in `esports/data/esports_db.py` to `INSERT ... ON CONFLICT (market_id, bot_name) DO UPDATE SET predicted_prob, market_price, side, edge, created_at`
- **Result**: 200 rows for 200 unique markets (1:1). Future scans upsert instead of duplicate.
- **Note**: In-memory `_prediction_log_cache` left in place as a performance optimization (saves DB round-trips) — correctness no longer depends on it.

### 2. Edge Cap Removed
- **Was**: `ESPORTS_MAX_EDGE=0.35` blocking 6-7 markets per scan (biggest waterfall blocker after positions)
- **User directive**: "remove edge cap, but report anything over .40 in handoff if we have a negative trend"
- **Changes in `bots/esports_bot.py`**:
  - Removed `self._max_edge` attribute (line 370)
  - Removed edge cap gate from **scan path** (was lines 1972-1986) — replaced with INFO log at edge>0.40
  - Removed edge cap gate from **WS reactive path** (was lines 732-740) — replaced with INFO log at edge>0.40
  - Removed `edge_cap` from waterfall counter dict
- **New log lines**: `esportsbot_high_edge` (scan path) and `esportsbot_ws_high_edge` (WS path) — INFO level, logs market_id, game, edge, side, model_prob, price
- **Post-deploy verification**: `edge_cap` gone from waterfall. 3 new opportunities passing through. All were tail-price LoL markets (prices 0.065-0.185 or 0.93-0.955) with fill probabilities 15-22% — paper engine correctly rejected as illiquid.

### 3. Investigation Findings (report only, no code changes)

#### Edge Cap Analysis
- Edge>0.30 bucket had only **2 unique markets** (not 70) — both correct, but zero statistical power
- Edge 0.20-0.30 bucket: 19 unique markets, 83.3% accuracy — most meaningful signal
- Current high-edge markets are all **tail-price** (near 0 or near 1) with no liquidity. Real value of removing the cap will show on moderate-edge liquid markets.

#### Exit Cooldown Review
- 15-min cooldown IS enforced post-S110
- Same-side re-entries happening 15-20 min post-loss — mixed results
- Mar 19 pre-S110: catastrophic churn on `0x284aaa...` (13 re-entries in 28 min, -$500+)
- Mar 20 post-S110: profitable re-entries on `0x90e46f...` (+$997 after 5 re-entries)
- **Decision**: No change. Monitor same-side re-entries for future handoffs.

#### Fill Failures
- Market `0xf4ed5d...` (LoL BO1): 31-33% fill probability, repeated failures with exponential backoff
- Paper engine correctly simulating illiquidity on tail-price markets
- **No action needed** — working as designed.

---

## P&L Summary (as of S112)

### All-Time
| Event | Count | Realized |
|-------|-------|----------|
| ENTRY | 207 | $0 |
| EXIT | 123 | **+$400.94** |
| RESOLUTION | 138 | **+$134.06** |
| **Total** | | **+$535.00** |

### Daily Trend (recovery arc)
| Day | Entries | Exit P&L | Resolution P&L | Net |
|-----|---------|----------|----------------|-----|
| Mar 19 | 38 | -$592.68 | -$942.85 | -$1,535.53 |
| Mar 20 | 34 | +$670.22 | +$1,043.12 | **+$1,713.34** |
| Mar 21 | 11 | +$129.44 | -$72.09 | **+$57.35** |

### Trend: Positive. Flipped from -$643 (S111) to +$535. Mar 20 was the big recovery day driven by S111 exposure cap changes.

---

## Current VPS Config (LIVE as of S112 deploy)

```env
# Bankroll
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000

# Exposure caps
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=3000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=5000
ESPORTS_MAX_TEAM_EXPOSURE=1000

# Trading thresholds
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
# ESPORTS_MAX_EDGE — REMOVED in S112. No upper edge cap.
ESPORTS_STOP_LOSS_PCT=0.15

# Anti-churn
ESPORTS_EXIT_COOLDOWN_SECONDS=900
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=3
ESPORTS_ENTRY_WINDOW_HOURS=12.0

# Scan
SCAN_INTERVAL_ESPORTS_LIVE=2

# Halt
ESPORTS_BRIER_HALT_THRESHOLD=999.0  # effectively disabled

# Other
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
```

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | Exit cooldown 15 min — same-side re-entries 15-20 min after stop-loss | Monitoring | Review data on next handoff. Consider same-side 30 min guard if losses mount |
| **P2** | RC4: Entry price inflation — positions table stores requested price not actual fill price | Deferred | Separate session — touches shared position_manager |
| **P2** | Kelly degradation suspended (needs ALL 8 games fitted) | Blocked on CoD/R6/RL data | None — wait for data |
| **P2** | High-edge trade outcomes — monitor edge>0.40 for negative trend | **NEW** | Check `esportsbot_high_edge` logs on next handoff. If high-edge trades show negative P&L trend, consider re-adding a cap at 0.50+ |
| **P3** | LoL Brier=0.284 approaching concern | Monitoring | Halt disabled, but model may be miscalibrated |
| **P3** | CS2 Brier=0.334 — worst calibration | Trading for learning data | Monitor |
| **P3** | `no_prediction: 4-6` per scan — mostly tournament_winner skips | Healthy | Only actionable if count grows |
| **P3** | WS reconnect drops every ~40s-5min | Auto-reconnects working | Monitor |
| **P3** | EsportsSeriesBot silent | No series markets on Polymarket | Expected |
| **P3** | Tail-price markets (edge>0.50) consistently failing fills (15-22%) | Expected | Paper engine correctly simulating illiquidity. No fix needed. |

### Items RESOLVED This Session

| Item | Resolution |
|------|-----------|
| Prediction log duplicates (97x per market) | Migration 057: unique index + ON CONFLICT upsert. 19,660 dupes deleted. |
| Edge cap blocking 6-7 markets per scan | Removed entirely. High edges logged for monitoring. |
| Misleading accuracy metrics (100% on n=70) | Root cause: 2 unique markets logged 35x each. Fixed by dedup. |

---

## Key Files (EsportsBot)

| File | Purpose |
|------|---------|
| `bots/esports_bot.py` | Main bot (~5000 lines). Scan loop, predictions, trade execution, WS reactive, series scan |
| `esports/data/esports_db.py` | Prediction logging (now with ON CONFLICT upsert) |
| `config/settings.py` | All ESPORTS_* config with env var overrides |
| `esports/models/glicko2.py` | Glicko-2 rating algorithm |
| `esports/data/pandascore_client.py` | PandaScore API wrapper |
| `schema/migrations/057_esports_prediction_log_dedup.sql` | Dedup migration |
| `scripts/bot_pnl.py` | Canonical P&L script: `python scripts/bot_pnl.py EsportsBot 24` |

---

## Critical Traps (EsportsBot-specific, DO NOT BREAK)

1. **`_game_exposure` is tracked in USD** (`price * size`), not shares.
2. **`_churn_blocked()` must gate ALL paths to `_execute_esports_trade()`** — scan, WS reactive, AND series.
3. **`_recently_exited` persists to Redis** via `_save_exit_cooldown_to_redis()`. Survives restarts.
4. **`_market_entry_times` does NOT persist** — resets on restart. Acceptable (12h window).
5. **BetaCalibrator** runs per-game. Needs ~50+ samples to be meaningful.
6. **PandaScore rate limit**: 1000/hr budget. Current usage ~400/hr.
7. **`paper_trades` has NO `metadata` column**.
8. **`positions` table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`. Use `source_bot` not `bot_name`.
9. **trade_events is P&L authority** — `paper_trades` is legacy.
10. **PatchDriftDetector**: `_patch_timestamps` set only on genuine patches (`old is not None`).
11. **Exposure cap hierarchy**: team ($1K) < game ($3K) < tournament ($5K) < total ($15K < capital $20K).
12. **`esports_prediction_log` now has unique index on (market_id, bot_name)** — INSERT must use ON CONFLICT or will fail on duplicates.
13. **Edge cap is REMOVED** — no `_max_edge` attribute, no `edge_cap` waterfall counter. Do not re-add without user request.

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
10. `reentry_rejected` — has position, wrong direction or insufficient edge
11. `passed` → goes to `_execute_esports_trade()`

*Note: `edge_cap` removed in S112. High edges now trade through and are logged.*

---

## Commits This Session

| SHA | Description |
|-----|-------------|
| `68ba29b` | S112: prediction_log dedup (migration 057, ON CONFLICT upsert) + edge cap removed |

---

## Verification Commands

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Scan health (edge_cap should NOT appear in waterfall)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep esportsbot_scan_summary | tail -3"

# High-edge monitoring (should see esportsbot_high_edge lines)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep esportsbot_high_edge | tail -10"

# Prediction log dedup verification (total_rows should equal unique_markets)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) as total_rows, COUNT(DISTINCT market_id) as unique_markets FROM esports_prediction_log;\""

# P&L
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;\""

# Open positions
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FROM positions WHERE source_bot='EsportsBot' AND status='open';\""

# Errors
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'EsportsBot.*error\|EsportsBot.*exception' | tail -5"
```

---

## User Directives (carry forward)

1. **"All games trade, even if they are shit. We need to learn."** — Do not re-enable Brier halting without explicit user request.
2. **"Remove edge cap, but report anything over .40 in handoff if we have a negative trend."** — Edge cap removed. Monitor `esportsbot_high_edge` logs on handoffs.
3. **"Paper trading is production"** — Never cut corners because SIMULATION_MODE=true.
4. **"Monitor exit cooldown and review on handoffs"** — 15-min cooldown question deferred. Collect data.
5. **Scope lock** — Fix only what is requested. No unsolicited features, refactors, or "while I'm in here" changes.
