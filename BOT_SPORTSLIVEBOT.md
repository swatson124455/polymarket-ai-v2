# SportsLiveBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_SPORTS_LIVE=false) |
| Capital | Shared SportsBankrollManager pool (with SportsBot + SportsInjuryBot + SportsArbBot) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Missing SPORTSDATAIO_API_KEY + live sports event data |

## Purpose & Strategy
Live in-game event detection and betting during active sports games.

**Strategy:**
- Background task monitors live sports game state via data API
- Classifies in-game events (goals, touchdowns, turnovers, etc.)
- Computes updated win probability from game state
- Trades when market price diverges significantly from updated probability
- Scan interval: 10s during active games, slower otherwise

**Pattern:** Mirrors EsportsLiveBot pattern exactly but for sports.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/sports_live_bot.py |
| Sports live modules | sports/live/ |
| Sports bankroll manager | sports/kelly/sports_bankroll_manager.py |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| SPORTSDATAIO_API_KEY | YES | Shared with all sports bots |
| Live game event stream | LIKELY | Real-time game state updates |
| Polymarket API | YES | Sports market discovery |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_SPORTS_LIVE | false | false | Enable gate |
| SPORTSDATAIO_API_KEY | — | NOT SET | Required sports data API |
| SCAN_INTERVAL_SPORTS_LIVE | 10 | 10 | Scan interval during live games (s) |

## Known Issues & Debug History
- **[OPEN]** Never enabled. Requires SPORTSDATAIO_API_KEY.

## Debugging Commands
```bash
pytest tests/ -k "sports_live" -v
```

## Next Steps / Blockers
- [ ] Same blocker as SportsBot: get SPORTSDATAIO_API_KEY first
- [ ] Enable only after SportsBot is profitable (SportsLiveBot is higher risk — live trading)
- [ ] Read `bots/sports_live_bot.py` and `sports/live/` before enabling
