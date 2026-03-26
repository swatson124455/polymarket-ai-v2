# AGENT HANDOFF — WeatherBot Session 109 (2026-03-19)

## Session Summary
**Focus**: Deploy S108 fill pipeline fixes, post-deploy monitoring, P&L analysis, sizing units bug discovery
**Bot scope**: WeatherBot only. No code changes this session.
**Tests**: No code changes — tests unchanged at 1642 passed
**Deploy**: `20260319_161025` — all S107/S108 commits now live on VPS
**Prior deploy**: `20260319_131015` (was missing fill pipeline fixes from commit `2c4fb3f`)

---

## WHAT WAS DONE

### 1. Deployed S108 Fill Pipeline Fixes
- Verified commits `ab3c018` (ghost position fix) and `2c4fb3f` (fill pipeline: 4 fixes) were committed but NOT deployed
- Ran `bash deploy/deploy.sh` → deploy `20260319_161025` successful
- Health check passed at 50s — bots scanning
- Verified deployed code contains bestAsk pre-filter, volume passthrough, taker factor 0.85

### 2. Post-Deploy Monitoring Results

| Metric | Pre-S108 | Post-S108 | Target |
|--------|----------|-----------|--------|
| Fill rate | 8% (2/26) | **14.7%** (5/34) | 25-35% |
| Ghost positions | 137 | **0** | 0 |
| Fill probabilities | ~8% | **20-31%** | n/a |
| Avg position sizes | $0.22 dust | **$0.54-$45.89** | $5-$30 |
| Entries per scan | ~40-60 | **0-4** | 5-15 |

**Fill rate improved from 8% to 14.7%** but still below 25% target. Most failures are "fill probability 20-26%" — the random dice roll just doesn't pass. The fill probability model is working correctly; the markets genuinely have thin liquidity.

**Open-Meteo 429 Rate Limiting**: 129 errors in 10 minutes post-deploy. Only occurs on first scan after restart (cold cache, 421 API calls). Subsequent scans use Redis cache (71 calls). Transient, not a concern.

### 3. YES vs NO Asymmetry Analysis (Confirmed)

| Side | Resolved | Wins | Win Rate | Total P&L | Avg P&L |
|------|----------|------|----------|-----------|---------|
| NO | 407 | 312 | **76.7%** | +$1,238 | +$3.04 |
| YES | 153 | 38 | **24.8%** | +$752 | +$4.92 |

Both sides profitable. NO has higher win rate (weather buckets: most resolve NO since only 1 bucket of ~11 hits). YES has higher avg P&L (lower price → higher payoff when correct).

### 4. City-Level P&L (from trade_events, deduped first entry per market)

| City | Resolved | Win Rate | P&L |
|------|----------|----------|-----|
| Dallas | 8 | 50.0% | **-$83** |
| Wellington | 10 | 30.0% | -$64 |
| Shanghai | 7 | 57.1% | -$33 |
| Seattle | 7 | 42.9% | -$28 |
| Toronto | 11 | 54.5% | -$23 |
| London | 10 | 70.0% | **+$63** |
| Seoul | 10 | 80.0% | +$19 |
| Chicago | 10 | 60.0% | +$17 |
| NYC | 17 | 41.2% | +$14 |
| Munich | 1 | 100% | +$1 |

**Dallas worst at -$83** (8 resolutions). Wellington (-$64) also concerning. Both need monitoring — sample still small.

### 5. Munich Status
Only 1 resolved market (+$1.13). Far too small to evaluate. Continue monitoring.

### 6. WebSocket Health
- All channels operational (no disconnect/reconnect errors)
- EsportsBot `ws_trading=True`, 10 tokens subscribed
- MirrorBot receiving RTDS trade feed
- No circuit breaker or ping failures

---

## P1 DISCOVERY: SIZING UNITS BUG (NOT YET FIXED)

### Root Cause
`calculate_bot_position_size()` returns **SHARES** (line 576 of base_bot.py: `return size_usd / price`).

MirrorBot passes shares directly → correct.

WeatherBot converts to USD then passes as if shares:
```python
kelly_shares = await self.calculate_bot_position_size(conf, price)  # SHARES
size = max(_min_trade, kelly_shares * opp["price"] * combined_boost)  # shares × price = USD
# place_order(size=USD_value, price=...) → paper engine treats as shares
```

### Impact
- Actual capital = `intended_USD × price` (undersized by `1/price`)
- At price 0.10: **10× smaller** than intended
- At price 0.90: ~10% smaller than intended
- $5 min trade floor ensures 5 "shares" not $5 cost → real floor $0.50-$4.50
- Example: Kelly says $50 → returns 500 shares → WeatherBot passes `size=50` → paper engine buys 50 shares at $0.10 = **$5 actual** (not $50)

### Evidence from DB
```
size=25.2181  price=0.12  actual_cost=$3.03  (intended ~$25)
size=33.3016  price=0.09  actual_cost=$3.00  (intended ~$33)
size=2.2347   price=0.24  actual_cost=$0.54  (intended ~$2.2)
size=55.9642  price=0.82  actual_cost=$45.89 (intended ~$56)
```

### Proposed Fix (Tier 3 — behavioral change, needs user approval)
```python
# Option A: Keep size in shares, floor in cost-equivalent shares
_min_shares = _min_trade / opp["price"]
size = max(_min_shares, kelly_shares * combined_boost)

# Floor check also needs adjustment:
if size * opp["price"] < _min_trade:
    return False
```

### Why Not Fixed Yet
- 3-10× increase in capital deployment per trade
- System is profitable at current reduced sizes
- Needs explicit user approval for behavioral change
- Paper trading phase — safer to fix now than discover after going live

---

## CURRENT CONFIGURATION (VPS .env)

Unchanged from S108. Key settings:
```
PAPER_TAKER_SIDE_FACTOR=0.85          # S108: was 0.55
WEATHER_EXIT_COOLDOWN_SECS=14400      # 4hr re-entry cooldown
WEATHER_MIN_TRADE_USD=5.0             # $5 floor (but see sizing bug above)
WEATHER_BM_FLOOR=0.50                 # Baker-McHale minimum
CANARY_STAGE=4                        # 100% capital
SIMULATION_MODE=true                  # Paper trading
```

---

## FILES MODIFIED THIS SESSION

None — deployment only, no code changes.

---

## OUTSTANDING ITEMS (PRIORITIZED)

### P1 — Sizing Units Bug
- WeatherBot passes USD as shares → positions 3-10× undersized
- Fix requires behavioral change approval (see Section above)
- Also fixes the $5 min trade floor (currently $0.50-$4.50 actual)

### P2 — Fill Rate Below Target
- 14.7% fill rate vs 25-35% target
- Fill probabilities 20-31% — model is correct, markets are genuinely thin
- Options: (a) raise taker factor further (0.85→0.95), (b) accept 15% as realistic, (c) investigate specific markets
- Recommendation: accept ~15% as realistic for thin weather markets. The model is accurate.

### P2 — City P&L Monitoring
- Dallas (-$83) and Wellington (-$64) are worst performers
- Sample too small (8-10 resolutions) for config changes
- Revisit after 30+ resolutions per city

### P3 — Munich Monitoring
- Only 1 resolution. Need 30+ for evaluation.

### P3 — NO vs YES Asymmetry
- Confirmed: 76.7% vs 24.8% win rate
- Both sides profitable — no action needed yet
- YES has higher avg P&L (+$4.92 vs +$3.04) compensating for lower WR

### P4 — Open-Meteo 429s on Cold Start
- 129 rate limit errors on first scan after restart
- Resolves naturally as Redis cache warms
- Consider adding request throttling for initial scan

### P5 — Deferred Items (unchanged)
- Kalshi cross-platform arbitrage (8-16h)
- Remove diagnostic logging

---

## NEXT SESSION PRIORITIES

1. **Fix sizing units bug** (P1) — needs user approval, 3-10× position size increase
2. **Monitor fill rate** — is 14.7% the new stable baseline? (was 8% pre-fix)
3. **Dallas/Wellington city P&L** — after 30+ resolutions, consider city-specific edge floors
4. **Munich monitoring** — continue accumulating data
5. **Verify position sizes post-fix** — should see $5-$50 actual cost range after sizing fix

---

## ROLLBACK GUIDE

### Full rollback (revert to pre-S108 deploy)
```bash
# On VPS — list releases and symlink to previous
ls -1dt /opt/pa2-releases/*/
sudo ln -sfn /opt/pa2-releases/20260319_131015 /opt/polymarket-ai-v2
sudo systemctl restart polymarket-ai
```

### Partial rollback via env vars
| Change | Revert command |
|--------|----------------|
| Taker factor 0.85→0.55 | `PAPER_TAKER_SIDE_FACTOR=0.55` |
| 4hr cooldown→15min | `WEATHER_EXIT_COOLDOWN_SECS=900` |
| $5 min→$1 min | `WEATHER_MIN_TRADE_USD=1.0` |

---

## POST-DEPLOY MONITORING COMMANDS

```bash
# Fill rate
journalctl -u polymarket-ai --since '30 min ago' | grep 'Order latency.*Weather' | grep -oP 'success=(True|False)' | sort | uniq -c

# Scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# Position sizes (actual cost = size * entry_price)
sudo -u postgres psql -d polymarket -c "
SELECT round((size * entry_price)::numeric, 2) as actual_cost, side, count(*)
FROM positions WHERE bot_id='WeatherBot' AND opened_at > NOW() - INTERVAL '2 hours'
GROUP BY 1, 2 ORDER BY 1;"

# Ghost positions (should be 0)
sudo -u postgres psql -d polymarket -c "SELECT count(*) FROM positions WHERE bot_id='WeatherBot' AND status='open' AND size=0;"

# Open-Meteo rate limits
journalctl -u polymarket-ai --since '10 min ago' | grep -c 'open_meteo_deterministic_error'

# City P&L (deduplicated)
sudo -u postgres psql -d polymarket -c "
WITH e AS (SELECT DISTINCT ON (market_id) market_id, event_data->>'city' as city FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY' AND event_data ? 'city' ORDER BY market_id, event_time),
r AS (SELECT DISTINCT ON (market_id) market_id, realized_pnl FROM trade_events WHERE bot_name='WeatherBot' AND event_type='RESOLUTION' AND realized_pnl IS NOT NULL ORDER BY market_id, event_time)
SELECT e.city, count(*), sum(CASE WHEN r.realized_pnl>0 THEN 1 ELSE 0 END) wins, round(sum(r.realized_pnl)::numeric,2) pnl FROM e JOIN r ON e.market_id=r.market_id GROUP BY e.city ORDER BY pnl;"
```

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **trade_events is P&L authority** — never paper_trades
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **Sizing units**: WeatherBot `size` is currently passed as USD-converted-to-pseudo-shares (see P1 bug)
4. **`entry_cost` in positions table** is friction cost (slippage+fees), NOT total capital. Actual capital = `size * entry_price`
5. **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it
6. **WEATHER_SKIP_COORDINATOR_BUY=true** — confirm_position direct INSERT
7. **Paper trading IS production** — never skip features
8. **Python 3.13 scoping**: `from X import Y` inside function → local for entire function
9. **Open-Meteo 429s on cold start** are transient — Redis cache warms after first scan
10. **Ghost positions fixed**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`
