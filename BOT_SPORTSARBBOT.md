# SportsArbBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_SPORTS_ARB=false — verify exact key) |
| Capital | Shared SportsBankrollManager pool |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Missing SPORTSDATAIO_API_KEY |

## Purpose & Strategy
Cross-platform sports market arbitrage: detects the same sports outcome priced differently on
Polymarket vs other platforms (sportsbooks, Kalshi, etc.) and captures the spread.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/sports_arb_bot.py |
| Sports bankroll manager | sports/kelly/sports_bankroll_manager.py |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| SPORTSDATAIO_API_KEY | YES | Shared with all sports bots |
| External platform data | LIKELY | Sportsbook odds or Kalshi API |
| Polymarket API | YES | Primary platform |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_SPORTS_ARB | false | false | Enable gate (verify exact key name) |
| SPORTSDATAIO_API_KEY | — | NOT SET | Required sports data API |

## Known Issues & Debug History
- **[OPEN]** Never enabled. Requires SPORTSDATAIO_API_KEY.

## Debugging Commands
```bash
pytest tests/ -k "sports_arb" -v
```

## Next Steps / Blockers
- [ ] Same blocker as SportsBot: get SPORTSDATAIO_API_KEY first
- [ ] Read `bots/sports_arb_bot.py` to confirm exact env var names, external platforms used
