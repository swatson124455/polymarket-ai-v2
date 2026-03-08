# CrossPlatformArbBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_CROSSPLATFORM=false) |
| Capital | $500 (BotBankrollManager) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | Unknown — has not traded in recent sessions |
| Blocker | Needs investigation: unclear why disabled, what prerequisites needed |

## Purpose & Strategy
Exploits price discrepancies for the same outcome across different prediction market platforms
(e.g., Polymarket vs Kalshi, Manifold, or other venues).

**Strategy:** When P(outcome) on Platform A differs from P(outcome) on Platform B by >threshold:
- Buy on the cheaper platform
- Sell (or hold off) on the more expensive platform
- Capture the spread when prices converge at resolution

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/cross_platform_arb_bot.py |
| Base bot | bots/base_bot.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | TBD — read file before working |
| Opportunity analysis | analyze_opportunity() | TBD |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | Primary platform |
| External platform API | LIKELY | Kalshi, Manifold, or other — needs investigation |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_CROSSPLATFORM | false | false | Enable gate |

## Known Issues & Debug History
- **[OPEN]** Status unknown: Bot disabled with no documented reason in sessions 1-53.
  Needs file read to understand prerequisites and current state.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Check if any trades ever placed
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM paper_trades WHERE bot_name='CrossPlatformArbBot';\""

# Run tests
pytest tests/ -k "cross_platform" -v
```

## Next Steps / Blockers
- [ ] Read `bots/cross_platform_arb_bot.py` to understand prerequisites
- [ ] Identify what external platform API(s) it uses and whether keys are configured
- [ ] Document why it was disabled and what needs to happen to enable it
