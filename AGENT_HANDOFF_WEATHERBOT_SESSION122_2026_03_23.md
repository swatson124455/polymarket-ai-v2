# WeatherBot Session 122 — Cap Uncapping, Shadow Entries, Data Analysis

## Deploy: `20260323_130618` | Commit: `c338f8f`

## What Changed

### Cap Adjustments (let Kelly self-regulate)
| Cap | Old | New |
|-----|-----|-----|
| MAX_PER_GROUP_USD | $1,000 | **$10,000** |
| MAX_CORRELATED_EXPOSURE | $2,000 | **$5,000** |
| COMBINED_BOOST_CAP | 2.0x | **1.5x** |
| MAX_BUCKETS_PER_GROUP | 3 | **5** |
| NO_MAX_ENTRY_PRICE | 0.65 | **1.0 (removed)** |
| BM max_bet_usd | $300 | **$600** |
| BM max_daily_usd | $10,000 | **$20,000** |
| MAX_POSITIONS | 500 | **1,000** |

### Confidence Penalty Removals
- **NO confidence discount (0.80x at >70c)** — REMOVED. NO 60-80c had 58.8% WR (positive!) but negative P&L because discount shrank bets below break-even threshold.
- **Boundary risk 50% discount** — REMOVED. Kelly already accounts for edge via (p*b - q)/b.

### Shadow Entry Logging (NEW)
Sub-$5 trades now logged as `SHADOW_ENTRY` events in trade_events. Fields:
- `raw_size_usd`: what Kelly wanted to bet
- `combined_boost`: sizing multiplier
- `city`: city name
- `reason`: `sub_min_trade` (Kelly < $5) or `exposure_cap` (group/city cap hit)

Query: `SELECT * FROM trade_events WHERE event_type = 'SHADOW_ENTRY' AND bot_name = 'WeatherBot'`

## Data Analysis (3,884 entries, 1,185 resolutions)

### Weekly Edge Decay
| Week | Entries | Avg Edge |
|------|---------|----------|
| 03-02 | 26 | 23.4% |
| 03-09 | 1,496 | 11.6% |
| 03-16 | 2,183 | 10.3% |
| 03-23 | 11 | 9.8% |

Edge declining but stabilizing around 10%.

### Weekly P&L
| Week | Resolved | WR | P&L | $/trade |
|------|----------|-----|------|---------|
| 03-09 | 252 | 53.6% | +$1,768 | $7.02 |
| 03-16 | 694 | 63.5% | +$599 | $0.86 |
| 03-23 | 239 | 61.9% | +$162 | $0.68 |

WR improving but $/trade declining. Volume dilution.

### Brier by Side x Price
**NO side winners:** 20-40c (+$544), 40-60c (+$761), 80-100c (+$348)
**NO side loser:** 60-80c (-$164) — 58.8% WR but payoff asymmetry (avg win $10, avg loss $15)
**YES side winners:** 20-40c (+$811), 40-60c (+$116)
**YES side losers:** 0-20c (-$175, 38.3% WR), 60-80c (-$203, 25% WR, 16 trades)

### Per-City
**Losers:** Buenos Aires (-$355, 51.1% WR), London (-$76), Paris (-$50), Ankara (-$34)
**Winners:** Seattle (+$251), Wellington (+$233), Sao Paulo (+$195), Toronto (+$181), NYC (+$158)

### Why So Many $1 Bets
Paper fill model (`fill_frac`) only filling 40-50% of orders on thin weather books. Kelly sizes correctly, then partial fills slash it. 75% of trades get <60% filled. This is the paper fill model simulating realistic liquidity — in live trading, maker orders would likely fill fully.

## Pending Decision: Shadow Entries

**REVIEW shadow entries after 24-48h of data collection.** Query:
```sql
SELECT
    event_data->>'reason' AS reason,
    COUNT(*) AS n,
    ROUND(AVG(CAST(event_data->>'raw_size_usd' AS FLOAT))::numeric, 2) AS avg_raw_size,
    ROUND(AVG(confidence)::numeric, 4) AS avg_conf,
    ROUND(AVG(confidence - price)::numeric, 4) AS avg_edge
FROM trade_events
WHERE event_type = 'SHADOW_ENTRY' AND bot_name = 'WeatherBot'
GROUP BY reason;
```

Decision needed: should min trade go below $5, or are these correctly filtered dust? Shadow data will show if these trades have real edge.

## Files Modified
- `bots/weather_bot.py` — cap defaults, confidence penalties removed, shadow entry logging
- `config/settings.py` — cap value updates
- `base_engine/risk/bankroll_manager.py` — WeatherBot max_bet 300→600, max_daily 10k→20k

## Prior Session
- S121: paper trading realism (SELL VWAP walk, book depletion, live retry), model freshness
- S120: EMOS 90-day window fix, quiet hours edge mult, edge cap removed
- S119: NO price trap, correlated blowup, position stacking fixes
