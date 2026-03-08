# OracleBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (BOT_ENABLED_ORACLE=false) |
| Capital | $500 (BotBankrollManager) |
| Max bet | $100 (max_bet_usd) |
| VPS State | DISABLED |
| Last trade | Unknown — has not traded in recent sessions |
| Blocker | Needs investigation: unclear why disabled, what oracle data it uses |

## Purpose & Strategy
Trades markets approaching resolution using oracle/resolution data to determine the correct outcome
before the market fully prices it in.

**Strategy:** When oracle data (e.g., sports scores, official results, API resolution feeds)
confirms the outcome before Polymarket updates, buy the correct side while market still offers
favorable pricing.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/oracle_bot.py |
| Base bot | bots/base_bot.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | TBD — read file before working |
| Opportunity analysis | analyze_opportunity() | TBD |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | Near-resolution markets |
| Oracle data source | LIKELY | External resolution feed — needs investigation |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ORACLE | false | false | Enable gate |

## Known Issues & Debug History
- **[OPEN]** Status unknown: Bot disabled with no documented reason in sessions 1-53.
  Needs file read to understand oracle data source and prerequisites.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Check if any trades ever placed
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM paper_trades WHERE bot_name='OracleBot';\""

# Run tests
pytest tests/ -k "oracle" -v
```

## Next Steps / Blockers
- [ ] Read `bots/oracle_bot.py` to understand oracle data source and strategy
- [ ] Identify what external data feed it requires and if keys/access are configured
- [ ] Document why it was disabled and what needs to happen to enable it
