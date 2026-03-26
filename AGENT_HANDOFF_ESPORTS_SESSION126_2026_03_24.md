# AGENT HANDOFF — EsportsBot Session 126 (2026-03-24)

> **Scope**: EsportsBot ONLY. No bleed-over to other bots unless manual demand.
> **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
> **Codebase**: `C:\lockes-picks\polymarket-ai-v2\` (local Windows) → `/opt/polymarket-ai-v2/` (VPS)
> **Service**: `sudo systemctl restart polymarket-ai` — restarts ALL 15 bots
> **Current release**: `20260324_101757` + hot-patched `esports_bot.py` (13:38 UTC) + `position_manager.py` (17:02 UTC)

---

## WHAT WAS DONE THIS SESSION (S126)

### Fix 1: Deployed S125 code to VPS (`esports_bot.py`)
- **BetaCalibrator CS2 fitted**: a=0.9955, b=1.001, c=0.0045 with n=23 samples (8-day window). Near-identity transform — will sharpen as more data accumulates. Other games below min_samples=10 (LoL=2, Dota2=5, Valorant=2, CoD=1, R6/SC2/RL=0).
- **`_restore_market_game_from_db()`**: Working. Restored 27 positions' game tags on startup. EXIT/RESOLUTION events now carry game tags for all new trades.
- **Game tags verified**: All ENTRY events have game tags. Post-deploy EXIT/RESOLUTION events have game tags. Pre-deploy NULLs are historical.

### Fix 2: Position Manager root bug fix (`position_manager.py`) — ALL 15 BOTS
**Root cause discovered**: `_execute_stop_loss()` and `_execute_take_profit()` had ZERO failure handling:
- No cooldown on failure → infinite retry every 6-min cycle
- No ghost position detection → "Insufficient position" loops forever (MirrorBot pos 65655 proven)
- No zero-size guard
- `_execute_exit()` (line 630) already had all three — stop_loss and take_profit were never given the same treatment

**Churn loop mechanism proven on market `0x6113f19b38`**:
```
PM exits position → no bot-level cooldown set → EsportsBot re-enters 2 sec later
→ PM exits again → re-enters → 4 cycles in 2 hours → -$613 one market
```
166 uncontrolled PM exits all-time for EsportsBot. 93 churn exits across 15 markets.

**Fix applied**: Ported cooldown + ghost cleanup + zero-size guard from `_execute_exit` into both `_execute_stop_loss` (line 713) and `_execute_take_profit` (line 798).

**Belt-and-suspenders**: `PM_EXCLUDE_BOTS` setting (line 510-513 of position_manager.py) excludes EsportsBot/MirrorBot/WeatherBot from PM exit evaluation entirely. Default: `"EsportsBot,MirrorBot,WeatherBot"` in `config/settings.py` line 404.

**Verification**: 0 uncontrolled PM exits for EsportsBot since deploy. 1717 tests passed.

---

## CURRENT LIVE STATE (as of 17:10 UTC 2026-03-24)

### EsportsBot Health
- **Scanning**: 15 live matches, 7 markets (1 CS2, 6 LoL), ~180ms cycles
- **Open positions**: 34 (19 <24h active, 12 stale 1-3d, 3 stale 3-7d)
- **Waterfall**: `no_prediction:2, low_edge:2, low_confidence:1, passed:2, reentry_rejected:2`
- **Anti-churn**: `reentry_rejected:2` confirms `_recently_exited` working
- **BetaCalibrator**: CS2 fitted (n=23), all others below threshold
- **Glicko-2**: 8 games loaded (cs2:438 teams, lol:248, dota2:155, valorant:144, cod:15, r6:24, sc2:42, rl:53)

### P&L (all-time resolved)
| Game | Trades | Win Rate | P&L | Avg Edge |
|------|--------|----------|-----|----------|
| CS2 | 10 | 70% | +$556 | 0.223 |
| Dota2 | 1 | 100% | +$181 | 0.135 |
| LoL | 2 | 50% | -$196 | 0.223 |
| Unknown (pre-tags) | 131 | 45.8% | -$2,270 | 0.0 |
| **Total** | 144 | 47.9% | **-$1,729** | — |

### 7-Day P&L (all 3 bots)
| Bot | Trades | P&L | Win Rate |
|-----|--------|-----|----------|
| WeatherBot | 1,291 | +$693 | 61.7% |
| EsportsBot | 108 | -$1,218 | 30.6% |
| MirrorBot | 1,783 | -$4,920 | 41.3% |

---

## KNOWN ISSUES / BACKLOG

### Issue 1: NULL `end_date_iso` — blocks resolution backfill (ALL 3 bots)
- 691 of 705 open positions have NULL `end_date_iso`
- Resolution backfill orders `NULLS LAST` → these never get reached before timeout
- 15 stale EsportsBot positions (matches ended days ago, Polymarket unresolved)
- **Status**: Mirror session owns this fix. Resolution backfill IS running (Weather: 933→77, Mirror: 953→595) but NULL end_date positions are last in queue.
- **Quick fix if needed**: Change backfill ordering to `NULLS FIRST` or interleave — one-line change in shared infra.

### Issue 2: EsportsBot 30.6% win rate
- Worst of 3 bots. Heavily polluted by churn loop damage (166 PM exits baked in).
- PM exclusion now live → new trades should perform better.
- BetaCalibrator will improve calibration as per-game samples grow.
- **Action**: Wait for 50+ tagged resolutions per game, then evaluate which games to disable/retune.
- **Key insight from S124**: NO tokens below 30c are catastrophic (-$1,588). YES 50-70c is sweet spot (+$251).

### Issue 3: Confidence is useless (from S124 diagnostic)
- Every resolved trade clusters at 75%+ confidence with zero discrimination
- Graduation gate always auto-graduates (bypassed)
- Brier halt threshold at 1.0 (can never trigger)
- **Not yet addressed** — needs per-game data accumulation first.

### Issue 4: Sizing inversely correlated with quality (from S124)
- NO losers average 423 shares vs 131 for winners
- Bigger bets on worse trades
- **Not yet addressed** — blocked on confidence fix.

### Issue 5: 12 MirrorBot + 1 WeatherBot orphaned positions (no market row)
- Will never auto-resolve. Need manual cleanup.
- **Owner**: Mirror/Weather sessions.

---

## ARCHITECTURE REFERENCE

### File Map
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | 5,786 | Main bot: scan, predict, trade, exit, calibrate |
| `base_engine/execution/position_manager.py` | ~850 | Shared PM: stop-loss/take-profit/model reversal (excluded for esports) |
| `config/settings.py` | ~1300 | All settings including ESPORTS_* block (lines 1043-1233) |
| `base_engine/data/daily_counter.py` | — | Write-through persistence for `_game_exposure` |
| `tests/unit/test_esports_bot.py` | — | 115 tests (all passing) |
| `tests/unit/test_esports_series_model.py` | — | Series model tests |

### Class Hierarchy
```
bots/esports_bot.py:
  class BetaCalibrator          (L47)   — per-game Bayesian calibrator (Kull et al. 2017)
  class OnlinePlattCalibrator   (L151)  — online Platt scaling (secondary)
  class EsportsBot(BaseBot)     (L194)  — main bot class
```

### Key Methods (esports_bot.py)
| Method | Line | Purpose |
|--------|------|---------|
| `__init__` | 202 | Init all state dicts, models, calibrators |
| `start` | 421 | Start scanning, restore state from DB/Redis |
| `scan_and_trade` | 839 | Main loop: fetch markets, analyze, trade |
| `analyze_opportunity` | 1869 | Per-market prediction pipeline |
| `_enrich_prediction` | 2159 | Glicko-2 + ML model blending |
| `_get_model_prediction` | 2333 | Game-specific ML models (LoL, CS2, Dota2, Val) |
| `_execute_esports_trade` | 3107 | Place order with sizing, exposure tracking |
| `_check_and_execute_exits` | 1463 | Bot's own exit handler (15% stop-loss, max_hold, re-eval) |
| `_reevaluate_open_positions` | 1613 | Re-predict open positions, exit if edge gone |
| `_resolve_esports_from_clob` | 1652 | Resolution via CLOB price convergence |
| `_backfill_esports_outcomes` | 1789 | Backfill from prediction_log + markets table |
| `_restore_exposure_from_db` | 1293 | Restore `_game_exposure` from daily_counters |
| `_restore_market_game_from_db` | 1321 | S125: Restore `_market_game` from ENTRY events |
| `_restore_exit_cooldowns_from_redis` | 1405 | Restore `_recently_exited` from Redis TTL |
| `_save_exit_cooldown_to_redis` | 1393 | Persist exit cooldown to Redis |
| `_check_kelly_graduation` | 3455 | Kelly fraction graduation based on Brier score |
| `_init_glicko2_trackers` | 3533 | Load Glicko-2 ratings from DB for all 8 games |
| `_warm_form_cache` | 3690 | Pre-fetch team form data from PandaScore |
| `_train_in_background` | 3822 | Background model retraining |
| `_detect_game` | 3042 | Static: parse game from market question text |

### Prediction Pipeline Flow
```
scan_and_trade → _step_get_markets → _analyze_one (per market)
  → analyze_opportunity
    → _detect_game (parse question text)
    → _get_model_prediction (game-specific ML: LoL/CS2/Dota2/Val XGB, SC2/RL/CoD/R6 TabPFN)
    → _enrich_prediction
      → Glicko-2 expected score (primary baseline)
      → ML model prediction (if available)
      → Extremized Geometric Mean blend (EGM, d=1.5)
      → _pandascore_form_adjustment / _opendota_form_adjustment / _aligulac_sc2_blend / _ballchasing_rl_adjustment
    → BetaCalibrator.calibrate(prob) (per-game, if fitted)
    → ConformalPredictor uncertainty interval
    → OnlinePlattCalibrator (secondary)
    → confidence = min(model_conf, calibrated_conf, 1 - uncertainty_width)
    → edge = |calibrated_prob - market_price| - vig
  → if edge >= 0.05 and confidence >= 0.48: TRADE
```

### Sizing Pipeline
```
_execute_esports_trade:
  → BotBankrollManager.calculate_bet_size (Kelly criterion)
    → kelly_fraction = 0.25 (default), boosted/penalized by Brier score
    → max_bet_usd = $300 (EsportsBot cap)
  → Exposure checks: game ($5K), tournament ($8K), team ($2K), total ($15K)
  → daily_counters write-through for game_exposure
  → place_order(side="YES"/"NO", ...)
```

### State Persistence
| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_game_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_recently_exited` | Redis TTL (300s) | `_restore_exit_cooldowns_from_redis()` |
| `_market_game` | Restored from ENTRY trade_events | `_restore_market_game_from_db()` (S125) |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |
| `_open_positions` | positions table | Queried fresh each scan |
| Glicko-2 ratings | DB table | `_init_glicko2_trackers()` |

### Key Config (settings.py)
| Setting | Value | Notes |
|---------|-------|-------|
| `ESPORTS_MIN_EDGE` | 0.05 | Minimum edge to trade |
| `ESPORTS_MIN_CONFIDENCE` | 0.52 | Minimum confidence (but see Issue 3) |
| `ESPORTS_MAX_BET_USD` | $300 | Per-trade cap |
| `ESPORTS_MAX_GAME_EXPOSURE` | $5,000 | Per-game exposure limit |
| `ESPORTS_MAX_TOTAL_EXPOSURE_USD` | $15,000 | Total exposure cap |
| `ESPORTS_KELLY_DEFAULT_FRACTION` | 0.25 | Kelly fraction |
| `ESPORTS_STOP_LOSS_PCT` | 0.25 | 25% stop-loss (bot's own handler) |
| `ESPORTS_MAX_HOLD_HOURS` | 96 | Max hold before forced exit |
| `ESPORTS_EGM_D` | 1.5 | EGM extremization parameter |
| `PM_EXCLUDE_BOTS` | EsportsBot,MirrorBot,WeatherBot | Excluded from position_manager exits |
| `PHASE_MAX_BET_USD` | $1,000 | Global phase cap (BotBankrollManager $300 is real cap) |

---

## CRITICAL TRAPS (DO NOT FORGET)

1. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. Never pass "BUY"/"SELL". SELL is only for closing positions.
2. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
3. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).
4. **`risk_manager.calculate_position_size()`** is DEPRECATED — BotBankrollManager used instead.
5. **Paper trading IS production**: The ONLY difference is final order submission. All infrastructure must work identically.
6. **`asyncio.create_task()` forbidden for financial write-throughs** — always `await`. Fire-and-forget silently corrupts counters.
7. **Position `current_price`** auto-updated every 10s by `position_manager._update_current_prices()`.
8. **15 bots** in BOT_REGISTRY. EsportsBot is one of 3 active (with Mirror, Weather).
9. **One fix per commit. Preserve every function signature. No while-I'm-in-here refactors.**

---

## DEPLOY PROTOCOL

```bash
# 1. Run tests locally
python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_series_model.py -x -q

# 2. Upload to VPS
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/esports_bot.py

# 3. Deploy and restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai"

# 4. Verify (wait 30s for init)
ssh ... "sudo journalctl -u polymarket-ai --since '30 sec ago' -o cat --no-pager | grep -i 'esports'"

# 5. Check first scan
ssh ... "sleep 60 && sudo journalctl -u polymarket-ai --since '60 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3"
```

For shared modules (position_manager.py, etc.):
```bash
# Same pattern but copy to base_engine path:
scp -i ... base_engine/execution/position_manager.py ubuntu@34.251.224.21:/tmp/position_manager.py
ssh ... "sudo cp /tmp/position_manager.py /opt/polymarket-ai-v2/base_engine/execution/position_manager.py && sudo systemctl restart polymarket-ai"
```

---

## USEFUL DIAGNOSTIC QUERIES

```sql
-- Open positions by bot
SELECT bot_id, COUNT(*) FROM positions WHERE status='open' GROUP BY bot_id ORDER BY 2 DESC;

-- EsportsBot P&L by game (resolved)
SELECT COALESCE(event_data->>'game', 'unknown') AS game,
       COUNT(*), ROUND(SUM(realized_pnl), 2),
       ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END), 3) AS wr
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1 ORDER BY 1;

-- Scan summary (live check)
sudo journalctl -u polymarket-ai --since '5 min ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3

-- BetaCalibrator status
sudo journalctl -u polymarket-ai --since '5 min ago' -o cat --no-pager | grep 'beta_cal'

-- PM exit activity (should be zero for excluded bots)
SELECT bot_name, COUNT(*) FROM trade_events
WHERE event_type='EXIT' AND event_time > now()-interval '1h'
  AND (event_data->>'exit_reason' IS NULL OR event_data->>'exit_reason' = '')
GROUP BY bot_name;

-- Churn loop detection
SELECT LEFT(market_id, 12), COUNT(*) FROM trade_events
WHERE bot_name='EsportsBot' AND event_type='EXIT'
  AND (event_data->>'exit_reason' IS NULL OR event_data->>'exit_reason' = '')
GROUP BY market_id HAVING COUNT(*) >= 2 ORDER BY 2 DESC;

-- Resolution backfill progress
SELECT bot_name, COUNT(*) FROM trade_events
WHERE event_type='RESOLUTION' AND event_time > now()-interval '2h'
GROUP BY bot_name ORDER BY 2 DESC;
```

---

## NEXT SESSION PRIORITIES

### P0 — Monitor (no code change needed)
- Verify 0 PM exits continue for EsportsBot post-deploy
- Watch BetaCalibrator: CS2 should stay fitted, LoL/Dota2 should approach min_samples=10
- Track per-game P&L now that tags are working (need ~50 tagged resolutions per game for meaningful signal)

### P1 — Resolution backlog (shared infra, may be fixed by Mirror session)
- 15 stale EsportsBot positions with NULL `end_date_iso`
- If Mirror hasn't fixed the ordering: change backfill to `NULLS FIRST` or interleave

### P2 — Confidence discrimination (from S124)
- Confidence clusters at 75%+ with zero discrimination
- BetaCalibrator helps (if fitted) but underlying confidence calc needs work
- Graduation gate always auto-graduates — needs real gating
- Brier halt threshold at 1.0 → should be 0.30-0.35

### P3 — Sizing quality (from S124)
- NO losers average 423 shares vs 131 for winners
- Sizing inversely correlated with trade quality
- Blocked on P2 (confidence must discriminate first)

### P4 — Per-game model tuning
- Once 50+ tagged resolutions per game: evaluate Brier by game
- Current: Valorant best (0.153 Brier), LoL worst (0.308 Brier)
- May need to disable worst-performing games or adjust EGM parameter

### P5 — Liquidity awareness (from S124)
- Liquidity check explicitly skipped for esports
- Fill probability model doesn't exist
- Volume parameter dead throughout pipeline
- Lower priority — not losing money here, just leaving edge on table

---

## SESSION HISTORY (for context)

| Session | Date | Type | Key Outcome |
|---------|------|------|-------------|
| S120 | 2026-03-23 | Code | Game tags, series model, exposure tracking |
| S121 | 2026-03-23 | Code | ENTRY/EXIT game tagging in trade events |
| S124 | 2026-03-23 | Diagnostic | Full P&L audit: NO-side catastrophe, confidence useless, sizing backwards |
| S125 | 2026-03-23 | Code | BetaCalibrator min_samples fix, `_restore_market_game_from_db`, markets-table backfill fallback |
| **S126** | **2026-03-24** | **Code + Deploy** | **Deployed S125, fixed PM churn loop root cause, 3-bot review** |
