# EsportsSeriesBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_ESPORTS_SERIES=false) |
| Capital | $5,000 pool (shared EsportsBankrollManager with EsportsBot + EsportsLiveBot) |
| Max bet | $100 (ESPORTS_MAX_BET_USD) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Needs PANDASCORE_API_KEY + live BO3/BO5 series on Polymarket |

## Purpose & Strategy
Exploits series-level market mispricings in BO3/BO5 esports series.

**Three market inefficiencies targeted:**
1. **Momentum fallacy**: Market overweights current map score (e.g., 0-2 in BO5 ≠ statistically dead; comeback probability misvalued)
2. **Map veto ignorance**: Market ignores team-specific map win rates and veto patterns
3. **Conditional probability errors**: Market anchors on series score rather than computing correct P(team wins series)

**Conditional probability computation:**
- Per-map win rates (from HLTV scraper for CS2, PandaScore for other games)
- Current series score (maps won/lost)
- Map veto order (which maps selected)
- Binomial race calculation: P(team wins series) = Σ P(team wins exactly k of remaining maps)

**Multi-market correlated entry:** When edge detected, places correlated orders on:
- Match-winner market (series-level)
- Current-map-winner market (individual map level)

**Scan intervals:**
- 30s (SCAN_INTERVAL_ESPORTS_SERIES) when active series detected
- 300s otherwise (no active series)

**WS reactive path:** Caches (market_id → {prob, edge}) from last scan; reacts to WS price updates when price moves ≥ 1% (ESPORTS_SERIES_WS_PRICE_CHANGE_PCT) with 10s cooldown.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/esports_series_bot.py (~518 lines) |
| PandaScore client | esports/data/pandascore_client.py |
| HLTV scraper (optional) | esports/data/hltv_scraper.py |
| Market scanner | esports/markets/esports_market_scanner.py |
| Bankroll manager | esports/kelly/esports_bankroll_manager.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Bot startup | start() | ~50 |
| Main scan | scan_and_trade() | ~100 |
| WS reactive | on_price_update() | ~200 |
| Scan interval (dynamic) | _get_scan_interval_seconds() | ~480 |
| Cleanup | stop() | ~500 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| PANDASCORE_API_KEY | YES | Fail-fast ValueError on missing |
| PandaScore API | YES | Series state, map scores, team metadata |
| HLTV scraper | NO | Optional; CS2 map win rates; fails gracefully |
| EsportsMarketScanner | YES | Finds active series markets on Polymarket |
| Polymarket API | YES | Market discovery and pricing |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ESPORTS_SERIES | false | false | Enable gate |
| PANDASCORE_API_KEY | — | set | Required API key |
| ESPORTS_SERIES_MIN_EDGE | 0.10 | 0.10 | Minimum edge (10%) |
| ESPORTS_SERIES_REVERSE_SWEEP_FLOOR | 0.05 | 0.05 | Reverse sweep edge threshold |
| SCAN_INTERVAL_ESPORTS_SERIES | 30 | 30 | Scan during active series (s) |
| ESPORTS_SERIES_WS_PRICE_CHANGE_PCT | 0.01 | 0.01 | WS reaction threshold |
| ESPORTS_SERIES_WS_COOLDOWN_SECONDS | 10 | 10 | WS cooldown per market (s) |
| ESPORTS_TOTAL_CAPITAL | 5000.0 | 5000.0 | Shared pool (3 esports bots) |
| ESPORTS_MAX_BET_USD | 100.0 | 100.0 | Per-bet cap |
| ESPORTS_MAX_DAILY_USD | 500.0 | 500.0 | Daily spending cap |

## Known Issues & Debug History
- **[OPEN]** Never enabled. Requires PANDASCORE_API_KEY + live BO3/BO5 series.
- **[OPEN]** HLTV scraper optional: if HLTV blocks scraping, CS2 map win rates fall back to
  PandaScore data (less granular). Fails gracefully.
- **[OPEN]** Shares $5K capital pool. Coordinate with EsportsBot + EsportsLiveBot if all enabled.
- **[OPEN]** Prediction cache invalidation: cache is per-scan; stale if scan interval 300s and
  series state changes rapidly between scans.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs (when enabled)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep EsportsSeriesBot"

# Check active series tracking
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '1 hour ago' | grep -i 'series\|bo3\|bo5\|active_series'"

# Run tests
pytest tests/ -k "esports_series" -v
```

## Next Steps / Blockers
- [ ] Enable only after EsportsBot proves profitable with live esports markets
- [ ] Same seasonal blocker as EsportsBot: need live BO3/BO5 series on Polymarket
- [ ] PANDASCORE_API_KEY already set on VPS
- [ ] Monitor shared $5K pool daily cap when all 3 esports bots active simultaneously
- [ ] Consider HLTV scraper reliability: may need rate limiting or user-agent rotation
