# SportsBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_SPORTS=false) |
| Capital | Shared SportsBankrollManager (pool with SportsInjuryBot + SportsLiveBot + SportsArbBot) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | None — never traded |
| Blocker | Missing SPORTSDATAIO_API_KEY environment variable on VPS |

## Purpose & Strategy
Pre-game sports market predictions using external sports data feeds for ML-driven edge detection.

**Strategy:** Similar to EnsembleBot but domain-specialized for sports:
- Fetch pre-game odds, team stats, injury reports from sports data API
- ML model predicts match outcome probability
- Compare vs Polymarket YES price → trade when edge ≥ threshold
- Uses SportsBankrollManager (separate Kelly pool from other bots)

**Scan interval:** SCAN_INTERVAL_SPORTS=120 (2 minutes)

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/sports_bot.py |
| Sports bankroll manager | sports/kelly/sports_bankroll_manager.py |
| Sports data module | sports/data/ |
| Sports markets | sports/markets/ |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | TBD — read file before working |
| Data fetch | sports/data/ modules | TBD |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| SPORTSDATAIO_API_KEY | YES | **Primary blocker** — not set on VPS |
| Polymarket API | YES | Sports market discovery |
| PostgreSQL | YES | Training data, prediction logging |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_SPORTS | false | false | Enable gate |
| SPORTSDATAIO_API_KEY | — | NOT SET | **Required** sports data API key |
| SCAN_INTERVAL_SPORTS | 120 | 120 | Scan interval (s) |

## Known Issues & Debug History
- **[Session 46+]** Bot disabled. Documented reason: missing SPORTSDATAIO_API_KEY.
  All 3 sports bots (SportsBot, SportsInjuryBot, SportsLiveBot, SportsArbBot) share this dependency.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Verify API key is missing
ssh -i "$KEY" "$VPS" "grep SPORTSDATAIO /opt/polymarket-ai-v2/.env"

# Run sports tests
pytest tests/ -k "sports_bot" -v
```

## Next Steps / Blockers
- [ ] **Get SPORTSDATAIO_API_KEY** from sportsdataio.com — enables all 4 sports bots at once
- [ ] Set key: `ssh -i "$KEY" "$VPS" "echo 'SPORTSDATAIO_API_KEY=your_key_here' | sudo tee -a /opt/polymarket-ai-v2/.env"`
- [ ] Enable: `BOT_ENABLED_SPORTS=true` in VPS .env + restart service
- [ ] Read `bots/sports_bot.py` and `sports/` modules to understand full data pipeline before enabling
