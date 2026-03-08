# LLMForecasterBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | NO (disabled) |
| Capital | N/A — **does NOT place trades** |
| VPS State | DISABLED |
| Last trade | Never — data collection only |
| Blocker | N/A — no trades by design |

## Purpose & Strategy
**DATA COLLECTION ONLY — THIS BOT NEVER PLACES TRADES.**

Collects LLM probability forecasts for markets and logs them to the prediction pipeline as
training signal. LLM forecasts can be used to improve EnsembleBot's predictions over time.

**Strategy:**
- Fetch active markets from Polymarket
- For each market: query LLM (via LLM_CONSENSUS_MODE setting) for probability estimate
- Log prediction to prediction_log table WITHOUT executing any trade
- These forecasts become training data for the ML ensemble

**LLM consensus mode:** `LLM_CONSENSUS_MODE=fallback` (VPS current) — uses LLM when other
signals unavailable; `full` mode uses LLM for all markets.

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/llm_forecaster_bot.py |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| LLM API | YES | Claude or OpenAI API key required |
| Polymarket API | YES | Market discovery |
| PostgreSQL prediction_log | YES | Where forecasts are logged |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_LLM | false | false | Enable gate |
| LLM_CONSENSUS_MODE | fallback | fallback | fallback / full |

## Known Issues & Debug History
- **[DESIGN]** Never trades. Capital=N/A. Only logs to prediction_log.
- **[OPEN]** Not enabled on VPS — no LLM API key configured.

## Debugging Commands
```bash
# Check if any LLM forecasts in prediction_log
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM prediction_log WHERE bot_name='LLMForecasterBot';\""
```

## Next Steps / Blockers
- [ ] Not a priority — EnsembleBot's ML models are the primary prediction source
- [ ] If enabling: configure LLM API key and set BOT_ENABLED_LLM=true
- [ ] Ensure LLM forecasts don't pollute training data (TRAIN_ON_PREDICTION_LOG=false currently)
