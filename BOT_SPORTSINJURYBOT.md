# SportsInjuryBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_SPORTS_INJURY=false — or similar) |
| Capital | Shared SportsBankrollManager pool (with SportsBot + SportsLiveBot + SportsArbBot) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Missing SPORTSDATAIO_API_KEY + news/injury data feed |

## Purpose & Strategy
News-driven injury trading: detects key player injury announcements and bets on the impacted
team's market before the Polymarket price adjusts.

**Strategy:**
- Monitor sports news feeds for injury reports (key players: starters, quarterbacks, etc.)
- Assess injury severity and team impact
- If market hasn't priced in injury yet: trade the affected team's market
- Timing-sensitive: edge window closes as market reprices (minutes, not hours)

**Scan interval:** SCAN_INTERVAL_SPORTS_LIVE=10 (fast scan needed for news-driven edge)

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/sports_injury_bot.py |
| Sports bankroll manager | sports/kelly/sports_bankroll_manager.py |
| Sports news module | sports/news/ |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| SPORTSDATAIO_API_KEY | YES | Shared with all sports bots |
| News/injury feed | LIKELY | Additional API for real-time injury news |
| Polymarket API | YES | Sports market discovery and pricing |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_SPORTS_INJURY | false | false | Enable gate (verify exact key name) |
| SPORTSDATAIO_API_KEY | — | NOT SET | Required sports data API |

## Known Issues & Debug History
- **[OPEN]** Never enabled. Requires SPORTSDATAIO_API_KEY.
- **[OPEN]** Exact BOT_ENABLED key name needs verification from source file.

## Debugging Commands
```bash
# Run tests
pytest tests/ -k "sports_injury" -v
```

## Next Steps / Blockers
- [ ] Same blocker as SportsBot: get SPORTSDATAIO_API_KEY first
- [ ] Read `bots/sports_injury_bot.py` to confirm exact env var names and data requirements
