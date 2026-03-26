# EsportsBot Session 127 Prompt

You are continuing work on the EsportsBot module of a 15-bot Polymarket automated trading system. This is an ESPORTS-ONLY session — no bleed-over to other bots unless explicitly demanded.

## Quick Start
Read `AGENT_HANDOFF_ESPORTS_SESSION126_2026_03_24.md` for full context. Read `CLAUDE.md` for development rules.

## What Was Just Done (S126)
1. **Deployed S125 to VPS** — BetaCalibrator CS2 fitted (a=0.9955, n=23), game tag restore working (27 positions), game tags on all new events
2. **Fixed position_manager.py churn loop** — `_execute_stop_loss()` and `_execute_take_profit()` had zero failure handling (no cooldown, no ghost cleanup). Ported guards from `_execute_exit()`. Plus `PM_EXCLUDE_BOTS` excludes Esports/Mirror/Weather from PM exits entirely. Verified: 0 PM exits post-deploy.

## Current State
- 34 open positions (19 active, 15 stale waiting on resolution backfill)
- Scanning healthy: 15 live matches, 7 markets, ~180ms cycles
- Anti-churn working (`reentry_rejected:2`)
- CS2 is profitable (+$556, 70% WR). LoL/Dota2 data sparse. 131 pre-tag trades are "unknown"
- 7-day resolved P&L: -$1,218 (30.6% WR) — heavily polluted by PM churn loop damage

## Priority Queue
- **P0**: Monitor — verify PM fix holds, watch BetaCalibrator growth, track per-game P&L
- **P1**: Resolution backlog — 15 stale positions with NULL `end_date_iso` (Mirror session may fix)
- **P2**: Fix confidence discrimination — clusters at 75%+ with zero signal
- **P3**: Fix sizing quality — inversely correlated with trade quality (blocked on P2)
- **P4**: Per-game model tuning — once 50+ tagged resolutions per game
- **P5**: Liquidity awareness — skipped for esports currently

## Key Files
- `bots/esports_bot.py` (5,786 lines) — the bot
- `base_engine/execution/position_manager.py` — shared PM (esports excluded)
- `config/settings.py` — ESPORTS_* settings block at lines 1043-1233
- `tests/unit/test_esports_bot.py` — 115 tests

## VPS Access
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
# Logs: sudo journalctl -u polymarket-ai -f | grep -i esports
# DB: sudo -u postgres psql -d polymarket
```
