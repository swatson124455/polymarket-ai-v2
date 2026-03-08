# EsportsLiveBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_ESPORTS_LIVE=false) |
| Capital | $5,000 pool (shared EsportsBankrollManager with EsportsBot + EsportsSeriesBot) |
| Max bet | $100 (ESPORTS_MAX_BET_USD) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Needs PANDASCORE_API_KEY + live esports markets on Polymarket |

## Purpose & Strategy
Real-time in-game event detection and betting during live esports matches.

**Strategy:**
- Background `EsportsGameMonitor` task continuously polls PandaScore for live game state
- `EsportsEventDetector` classifies events: kills, round score changes, objective captures, etc.
- `EsportsLiveTrigger` enforces cooldowns + position caps + places orders
- Scan interval: 10s during live games, 60s otherwise
- Only trades if graduated LoL/CS2 model available (falls back gracefully if not)

**Pattern:** Exact mirror of SportsLiveBot but for esports via PandaScore API.

**Components initialized at start():**
1. PandaScoreClient — live match data
2. EsportsGameMonitor — background asyncio.Task (monitors live games)
3. EsportsEventDetector — classifies in-game events
4. EsportsLiveTrigger — order placement with cooldowns and exposure caps
5. EsportsBankrollManager — Kelly sizing (shared pool with EsportsBot + EsportsSeriesBot)
6. LoLWinModel + CS2EconomyModel — optional; loaded best-effort, skipped if unavailable

**Game update queue:** asyncio.Queue(maxsize=200) drains in scan_and_trade()

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/esports_live_bot.py (~256 lines) |
| Game monitor | esports/live/esports_game_monitor.py |
| Event detector | esports/live/esports_event_detector.py |
| Live trigger | esports/live/esports_live_trigger.py |
| PandaScore client | esports/data/pandascore_client.py |
| Bankroll manager | esports/kelly/esports_bankroll_manager.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Bot startup | start() | ~50 |
| Main scan (drain queue) | scan_and_trade() | ~100 |
| WS price reaction | on_price_update() | ~150 |
| Scan interval (dynamic) | _get_scan_interval_seconds() | ~200 |
| Cleanup | stop() | ~240 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| PANDASCORE_API_KEY | YES | Fail-fast ValueError on missing |
| PandaScore API (live) | YES | Real-time game state updates |
| LoLWinModel | NO | Optional; skipped if not graduated |
| CS2EconomyModel | NO | Optional; skipped if not graduated |
| Polymarket API | YES | Live esports market pricing |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ESPORTS_LIVE | false | false | Enable gate |
| PANDASCORE_API_KEY | — | set | Required API key |
| SCAN_INTERVAL_ESPORTS_LIVE | 10 | 10 | Scan interval during live games (s) |
| ESPORTS_TOTAL_CAPITAL | 5000.0 | 5000.0 | Shared pool (3 esports bots) |
| ESPORTS_MAX_BET_USD | 100.0 | 100.0 | Per-bet cap |
| ESPORTS_MAX_DAILY_USD | 500.0 | 500.0 | Daily spending cap |

## Known Issues & Debug History
- **[OPEN]** Never enabled. Requires PANDASCORE_API_KEY + live esports markets.
- **[OPEN]** ML models optional: LoL (51% accuracy) and CS2 (58%, not graduated) available
  but below graduation threshold. EsportsLiveBot proceeds with fallback prediction if models unavailable.
- **[OPEN]** Shares $5K capital pool with EsportsBot and EsportsSeriesBot. Ensure combined
  daily spend (ESPORTS_MAX_DAILY_USD=500) is not exceeded if multiple bots enabled simultaneously.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs (when enabled)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep EsportsLiveBot"

# Check game monitor task health
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '30 min ago' | grep -i 'game_monitor\|live_game\|EsportsLive'"

# Run tests
pytest tests/ -k "esports_live" -v
```

## Next Steps / Blockers
- [ ] Enable only after EsportsBot proves profitable with live esports markets
- [ ] Same PANDASCORE_API_KEY required (already set on VPS)
- [ ] Same seasonal blocker as EsportsBot: wait for live matches on Polymarket
- [ ] Monitor shared $5K pool: if EsportsBot is active, EsportsLiveBot shares the daily $500 cap
- [ ] Verify EsportsGameMonitor background task doesn't conflict with EsportsBot's live match polling
