# ArbitrageBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | YES (BOT_ENABLED_ARBITRAGE=true) — off in isolation mode |
| Capital | $1,000 (BotBankrollManager) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED in current isolation mode |
| Last trade | Session 48-49 era |
| Blocker | None when isolation lifted |

## Purpose & Strategy
Exploits market pricing inefficiencies across 4 arb strategies (7 execution paths, all Kelly-sized):

1. **Binary arbitrage**: YES + NO prices sum ≠ 1.0 → buy underpriced side
2. **Cross-market correlated arbitrage**: related markets with divergent probabilities (Pearson r ≥ 0.70)
3. **Bond strategy**: buy near-certain outcomes (YES > 0.95 or NO < 0.05) approaching resolution
4. **NegRisk multi-outcome arb**: exploit negative risk markets where sum of mutually exclusive outcomes ≠ 1.0

**Scan flow:**
1. Fetch 50 markets (ARB_MAX_MARKETS_PER_SCAN) with price history (50 recent trades each)
2. Semaphore(2) parallel analysis (conservative to avoid DB session exhaustion)
3. Sub-scans for cross-market (30s timeout), bond (20s), negrisk (20s)
4. Dedup via Redis (TTL 60s, ARB_OPPORTUNITY_DEDUP_TTL_SECONDS)
5. Execute top 10 opportunities (ARB_MAX_OPPORTUNITIES_PER_SCAN) with 0.5s delay between orders

**WS reactive path:**
- Reacts to price updates when move ≥ 0.8% (ARB_WS_PRICE_CHANGE_PCT)
- Binary arb only triggers if BOTH YES+NO prices are fresh (<5s old, ARB_MAX_PRICE_AGE_SECONDS)
- Kill switch verification on every WS reactive trade (C1 fix Session 49)
- Cooldown 2-5s per market (ARB_WS_COOLDOWN_SECONDS)

**Price staleness guard:** Returns false (market dead) if price history < 3 data points.

**NegRisk gate:** Only trades if question contains "negrisk", "negative risk", "no risk", "risk-free" (exact substring match).

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/arbitrage_bot.py (~1290 lines) |
| Arb coordinator | base_engine/coordination/arbitrage_coordinator.py |
| ID resolver | base_engine/data/id_resolver.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~274 |
| Binary arb analysis | analyze_opportunity() | ~369 |
| Bundle / multi-token | _analyze_bundle_arbitrage() | ~450 |
| Cross-market arb | _scan_cross_market_arbitrage() | ~550 |
| Bond strategy | _scan_bond_opportunities() | ~650 |
| NegRisk arb | _scan_negrisk_arbitrage() | ~750 |
| Price movement check | _has_recent_price_movement() | ~850 |
| WS reactive | on_price_update() | ~900 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | Markets + price history |
| Redis | YES | Opportunity dedup (TTL 60s) |
| ArbitrageTransactionCoordinator | YES | Multi-leg order coordination |
| PostgreSQL | YES | Positions, market data |
| statistics module (stdlib) | YES | stdev for price movement |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ARBITRAGE | true | false (isolation) | Enable gate |
| ARB_MIN_PROFIT_THRESHOLD | 0.01 | 0.01 | Minimum profit margin |
| ARB_MAX_PROFIT_THRESHOLD | 0.05 | 0.05 | Max profitable spread cap |
| ARB_DEFAULT_ORDER_SIZE | 100.0 | 100.0 | Base bet size |
| ARB_MAX_MARKETS_PER_SCAN | 500 | 50 | Market limit per scan |
| ARB_MIN_NET_EDGE | 0.005 | 0.005 | Min net edge (0.5pp) |
| ARB_OPPORTUNITY_DEDUP_TTL_SECONDS | 60 | 60 | Redis dedup cache TTL |
| ARB_MAX_PRICE_AGE_SECONDS | 5 | 5 | Max YES/NO price age gap for WS binary arb |
| ARB_MAX_OPPORTUNITIES_PER_SCAN | 10 | 10 | Max concurrent orders per scan |
| ARB_ORDER_DELAY_SECONDS | 0.5 | 0.5 | Delay between order submissions |
| ARB_WS_PRICE_CHANGE_PCT | 0.008 | 0.008 | WS reaction threshold |
| ARB_WS_COOLDOWN_SECONDS | 2 | 5 | WS cooldown per market (s) |
| ARB_NEGRISK_CONFIDENCE_BOOST | 0.1 | 0.1 | Extra confidence for negrisk |
| ARB_NEGRISK_SIZE_MULTIPLIER | 1.2 | 1.2 | Size boost for negrisk |
| ARB_CORRELATION_MIN | 0.7 | 0.7 | Pearson r threshold for cross-market |
| ARB_CORRELATION_LOOKBACK_DAYS | 30 | 30 | Cross-market correlation window |
| SCAN_INTERVAL_ARBITRAGE | 10 | 10 | Scan interval (s) |

## Known Issues & Debug History
- **[Session 49 — FIXED]** WS kill switch bypass: C1 fix — kill switch verification now runs
  before every WS reactive binary arb trade.
- **[Session 48 — FIXED]** Multiple P1-P8 root cause fixes applied. ArbitrageBot stable.
- **[OPEN]** Sub-scan timeouts: cross-market 30s, bond 20s, negrisk 20s. Total must stay <120s
  (BOT_SCAN_TIMEOUT_SECONDS). Monitor if new strategies added.
- **[OPEN]** NegRisk gate string matching: only exact substrings "negrisk", "negative risk",
  "no risk", "risk-free" trigger negrisk path. New market phrasings may be missed.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep ArbitrageBot"

# Recent arb trades
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, side, size, price, realized_pnl
  FROM paper_trades WHERE bot_name='ArbitrageBot'
  ORDER BY created_at DESC LIMIT 20;\""

# Check arb opportunity dedup keys in Redis
ssh -i "$KEY" "$VPS" "redis-cli -a 78psiRhepTgrmWSoy3cgNEIr KEYS 'arb:*' | head -20"

# Run ArbitrageBot tests
pytest tests/ -k "arbitrage or arb_bot" -v
```

## Next Steps / Blockers
- [ ] Re-enable when isolation mode lifted (BOT_ENABLED_ARBITRAGE=true)
- [ ] Monitor sub-scan timeouts: verify all 4 strategies stay under 120s total
- [ ] Consider expanding NegRisk gate patterns if new market phrasings appear
