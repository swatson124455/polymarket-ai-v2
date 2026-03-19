# AGENT HANDOFF — WeatherBot Session 107 (2026-03-19)

## Session Summary
**Focus**: Munich investigation + bet sizing pipeline fix + re-entry cooldown + ghost position bug fix
**Bot scope**: WeatherBot only (cross-bot fix in order_gateway + paper_trading — shared modules)
**Tests**: 1054 passed, 0 failed (1 pre-existing mirror test failure from unstaged local changes)

## Changes Made

### S106 (carried from prior session, first deploy)
1. **Taker-side flat factor** — `PAPER_TAKER_SIDE_FACTOR=0.55` applied when no taker_side data in event (paper_trading.py)
2. **Probability engine fallback** — degenerate distributions (total≤0.01) return `{}` instead of uniform (probability_engine.py)
3. **Stale position cleanup** — `_purge_stale_positions()` now queries `trade_events EXIT` instead of `paper_trades realized_pnl` (weather_bot.py)
4. **Negative counter clamp** — daily_counters with value<0 clamped to 0 on startup (weather_bot.py)

### S107 (this session)
5. **R2: Re-entry cooldown 15min→4hr** — `WEATHER_EXIT_COOLDOWN_SECS=14400` (was hardcoded 900). Per market_id, NOT per city. Redis TTL matches. Prevents the re-entry loop that cost -$79 on a single Munich market (4 re-entries, all losers).
6. **R3A: Min trade floor $1→$5** — `WEATHER_MIN_TRADE_USD=5.0`. Eliminates dust positions (67% of entries were <10 cents). Applied to S-T override path, Kelly path, and exposure-clamped size check.
7. **R3B: Baker-McHale floor 0.50** — `WEATHER_BM_FLOOR=0.50`. High ensemble spread (>3°F) was crushing sizing to 0.26x. Floor prevents extreme shrinkage while preserving uncertainty signal.
8. **R3C: Drawdown compression removed from combined_boost** — Was already applied in BotBankrollManager via `compress` factor. Double-counting produced 0.25x on 3-loss streaks (0.50×0.50). `_compute_weather_drawdown_factor()` retained for monitoring.
9. **R4: Ghost position bug fix (CRITICAL)** — `paper_trade_idempotent_memory` returned `{"success": True, "filled": 0}`, causing `confirm_position` to insert positions with `size=0`. These ghost positions blocked re-entry on 137 markets. **Two fixes**:
   - `paper_trading.py:562`: Idempotent memory now returns `success: False` (duplicate = no-op, no position created)
   - `order_gateway.py:795`: Guard `_filled_size > 0` before calling `confirm_position`. Zero-fill successes release reservation instead.
   - **DB cleanup**: 137 ghost positions (status='open', size=0) closed via `UPDATE positions SET status='closed'`

## Files Modified
| File | Change |
|------|--------|
| `config/settings.py` | +3 settings: WEATHER_EXIT_COOLDOWN_SECS, WEATHER_BM_FLOOR, WEATHER_MIN_TRADE_USD |
| `bots/weather_bot.py` | R2: cooldown configurable (5 locations), R3A: min trade (3 locations), R3B: BM floor (1 location), R3C: drawdown removal (1 location) |
| `base_engine/execution/paper_trading.py` | S106: taker-side flat factor; R4: idempotent memory returns success=False |
| `base_engine/execution/order_gateway.py` | R4: guard _filled_size>0 before confirm_position; zero-fill releases reservation |
| `base_engine/weather/probability_engine.py` | S106: degenerate distribution returns empty |

## Munich Investigation (TABLED FOR REVIEW)

### Findings
- **Station**: EDDM (Munich Airport) — proper setup, ICON-D2 local model, correct config
- **All-time P&L**: 6 exits, 2W/4L, -$78.13
- **Root cause**: Single market `0x65ce...` (Munich temp, target 2026-03-19) entered 6 times over 3 days. 4 consecutive losing re-entries at worsening prices (-$17.61, -$18.65, -$19.72, -$23.31).
- **Not a station problem** — it's a re-entry loop on a wrong thesis. Fixed by R2 (4hr cooldown).

### European city comparison (all-time)
| City | Exits | W/L | P&L |
|------|-------|-----|-----|
| Munich | 6 | 2/4 | -$78 |
| Ankara | 28 | 18/10 | -$11 |
| Paris | 27 | 18/9 | +$3 |
| London | 33 | 21/12 | +$31 |

### Recommendation
Monitor Munich for 2+ weeks. If negative after 30+ exits, consider city exclusion. Current sample too small (6 exits) to condemn.

## Sizing Impact Estimate
Before S107: avg position $0.22, 67% under 10 cents, ~$100/day deployed
After S107 (estimated):
- $5 floor eliminates all dust positions → fewer but meaningful trades
- BM floor 0.50 doubles most high-spread trades (was 0.26x, now 0.50x minimum)
- Drawdown de-duplication roughly doubles sizes during loss streaks
- Net: expect avg position $10-30, ~$300-800/day deployed, 30-80 positions/day instead of 450

## Config (new settings with defaults)
```
WEATHER_EXIT_COOLDOWN_SECS=14400   # 4 hours (was hardcoded 900s)
WEATHER_BM_FLOOR=0.50              # Baker-McHale minimum factor
WEATHER_MIN_TRADE_USD=5.0          # Minimum position size (was $1)
```

## Rollback Guide

### Full rollback (revert entire S107)
```bash
git revert 1f60153   # Reverts all S107 changes, keeps S106
sudo systemctl restart polymarket-ai
```

### Partial rollback via env vars (no code change, just restart)
Each S107 change can be independently reverted to pre-S107 behavior:

| Change | Revert command | Effect |
|--------|----------------|--------|
| R2: 4hr cooldown → 15min | `export WEATHER_EXIT_COOLDOWN_SECS=900` | Restore fast re-entry (15min) |
| R3A: $5 min → $1 min | `export WEATHER_MIN_TRADE_USD=1.0` | Allow dust positions again |
| R3B: BM floor → uncapped | `export WEATHER_BM_FLOOR=0.0` | Restore aggressive BM shrinkage |
| R3C: Drawdown dedup | **Code revert required** | Re-enable double drawdown compression |

**To apply partial rollback**: edit `/opt/polymarket-ai-v2/.env` (or `export` in systemd override), then:
```bash
sudo systemctl restart polymarket-ai
```

### S106 rollback (if S106 changes cause issues)
```bash
git revert 10c5f23   # Reverts taker-side factor + probability engine + stale cleanup
sudo systemctl restart polymarket-ai
```

### Parameter change log (before → after)
| Parameter | Before (S106) | After (S107) | Location |
|-----------|---------------|--------------|----------|
| Re-entry cooldown | 900s (hardcoded) | 14400s (configurable) | weather_bot.py ×5, settings.py |
| Min trade size | $1 (hardcoded) | $5 (configurable) | weather_bot.py ×3, settings.py |
| Baker-McHale floor | none (uncapped, min ~0.10) | 0.50 (configurable) | weather_bot.py ×1, settings.py |
| Drawdown in combined_boost | applied (0.25-1.0x multiplier) | removed (comment only) | weather_bot.py ×1 |
| Redis exit TTL | 900s (hardcoded) | matches WEATHER_EXIT_COOLDOWN_SECS | weather_bot.py ×1 |
| Redis restore elapsed calc | hardcoded 900.0 | uses self._exit_cooldown_secs | weather_bot.py ×1 |
| PAPER_TAKER_SIDE_FACTOR | (new, S106) | 0.55 default | settings.py, paper_trading.py |
| Prob engine degenerate | uniform distribution | empty dict `{}` | probability_engine.py |
| Stale cleanup query | paper_trades.realized_pnl | trade_events EXIT | weather_bot.py |
| Negative counter clamp | no clamp | clamp to 0 on startup | weather_bot.py |

## Post-Deploy Monitoring
```bash
# Verify WeatherBot scanning and trading
journalctl -u polymarket-ai -f | grep -i "weather"

# Check new cooldown is working (should see 4hr blocks)
journalctl -u polymarket-ai -f | grep "recently_exited\|exit_cooldown"

# Verify position sizes are $5+ (no dust)
sudo -u postgres psql -d polymarket -c "
SELECT round(entry_cost::numeric, 2) as cost, count(*)
FROM positions WHERE bot_id='WeatherBot' AND opened_at > NOW() - INTERVAL '2 hours'
GROUP BY 1 ORDER BY 1;"

# Verify entries per hour (expect ~5-15, not ~40-60)
sudo -u postgres psql -d polymarket -c "
SELECT date_trunc('hour', opened_at) as hr, count(*), round(avg(entry_cost)::numeric, 2) as avg_cost
FROM positions WHERE bot_id='WeatherBot' AND opened_at > NOW() - INTERVAL '6 hours'
GROUP BY 1 ORDER BY 1;"
```

## Outstanding Items
- ~~**P1**: Ghost position bug~~ — **RESOLVED**: R4. 137 ghosts closed, idempotent memory + order gateway fixed.
- **P3 (REVIEW)**: Munich city P&L — monitor 2 weeks, revisit if still negative after 30+ exits
- **P3**: `volume_24h=0.0` passed to fill probability model — causes overly conservative fills (9-27%). Needs investigation.
- **P4**: Fill quality monitoring — verify partial fill distribution post-deploy
- **P5**: NO vs YES asymmetry (72% vs 39% WR) — monitor, no config change yet
- **P5**: Unit test for probability_engine degenerate fallback
