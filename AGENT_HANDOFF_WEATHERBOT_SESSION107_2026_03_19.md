# AGENT HANDOFF — WeatherBot Session 107 (2026-03-19)

## Session Summary
**Focus**: Munich investigation + bet sizing pipeline fix + re-entry cooldown
**Bot scope**: WeatherBot only (no cross-bot changes)
**Tests**: 1641 passed, 0 failed

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

## Files Modified
| File | Change |
|------|--------|
| `config/settings.py` | +3 settings: WEATHER_EXIT_COOLDOWN_SECS, WEATHER_BM_FLOOR, WEATHER_MIN_TRADE_USD |
| `bots/weather_bot.py` | R2: cooldown configurable (5 locations), R3A: min trade (3 locations), R3B: BM floor (1 location), R3C: drawdown removal (1 location) |
| `base_engine/execution/paper_trading.py` | S106: taker-side flat factor |
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
- **P3 (REVIEW)**: Munich city P&L — monitor 2 weeks, revisit if still negative after 30+ exits
- **P4**: Fill quality monitoring — verify partial fill distribution post-deploy
- **P5**: NO vs YES asymmetry (72% vs 39% WR) — monitor, no config change yet
- **P5**: Unit test for probability_engine degenerate fallback
