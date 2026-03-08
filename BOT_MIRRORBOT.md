# MirrorBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | YES (BOT_ENABLED_MIRROR=true) — off in isolation mode |
| Capital | $1,000 (BotBankrollManager) |
| Max bet | $100 (max_bet_usd) |
| Max daily exposure | 15% of bankroll ($150) |
| Max concurrent positions | 20 |
| VPS State | DISABLED in current isolation mode |
| Last trade | Session 49 — 2 positions opened (first trades ever) |
| Blocker | None when isolation lifted; needs elite traders to generate signals |

## Purpose & Strategy
Mirrors trades from top N elite traders on Polymarket when ≥2 agree on the same side.

**Consensus flow:**
1. Fetch recent trades from top elite traders (filtered by HOT_TRADE_MAX_SECONDS=300)
2. Group by (market_id, token_id, side) — aggregate across all elites
3. Mirror only when ≥2 elites agree (MIRROR_MIN_CONSENSUS)
4. Reliability-weighted sizing via EliteReliabilityTracker (365-day rolling win rate)
5. Skip if mean elite reliability < 0.45 (MIRROR_MIN_RELIABILITY)

**Exit mirroring:** If MIRROR_EXIT_ENABLED=true, closes positions when source elites exit.

**Adaptive consensus per category (R5b):** `bot_market_params` DB table stores per-category
consensus thresholds. Fallback to global MIRROR_MIN_CONSENSUS if category unknown.

**Risk limits:**
- Daily exposure cap: 15% of bankroll per day (MIRROR_MAX_DAILY_EXPOSURE_PCT)
- Concurrent position limit: 20 (MIRROR_MAX_CONCURRENT_POSITIONS)
- Stop loss: 15% drawdown (MIRROR_STOP_LOSS_PCT)
- Max hold: 72h (MIRROR_MAX_HOLD_HOURS)

**Dedup:** In-memory set capped at 10,000 trades (MIRROR_MAX_TRACKED_TRADES); oldest purged when exceeded.

**Elite list refresh:** Reloads every 40 scans (~30 min at 45s scan interval).

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/mirror_bot.py (~780 lines) |
| Elite reliability tracker | base_engine/learning/elite_reliability.py |
| Base bot | bots/base_bot.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~159 |
| Load per-category consensus | _load_consensus_from_db() | ~86 |
| Aggregate elite trades | _aggregate_and_filter_trades() | ~300 |
| Mirror trade execution | _mirror_elite_trade() | ~400 |
| Exit mirroring | _check_exit_mirroring() | ~500 |
| Risk limit check | _apply_risk_limits() | ~600 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | Elite trade history |
| EliteReliabilityTracker | YES | 365-day win rate per trader |
| PostgreSQL bot_market_params | YES | Per-category consensus thresholds |
| PostgreSQL positions | YES | Open position tracking |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_MIRROR | true | false (isolation) | Enable gate |
| MIRROR_MIN_CONFIDENCE | 0.50 | 0.50 | Min confidence for mirror |
| MIRROR_MIN_CONSENSUS | 2 | 2 | Min elites agreeing to trigger |
| MIRROR_EXIT_ENABLED | true | true | Mirror exit when elites exit |
| MIRROR_MAX_DELAY_MINUTES | 30 | 30 | Max age of elite trade to mirror |
| MIRROR_MIN_RELIABILITY | 0.45 | 0.45 | Min elite reliability score |
| MIRROR_HOT_TRADE_MAX_SECONDS | 300 | 300 | Only mirror trades <5 min old |
| MIRROR_STOP_LOSS_PCT | 0.15 | 0.15 | Stop loss at 15% drawdown |
| MIRROR_MAX_HOLD_HOURS | 72 | 72 | Max position hold time |
| MIRROR_MAX_CONCURRENT_POSITIONS | 20 | 20 | Max open positions |
| MIRROR_MAX_DAILY_EXPOSURE_PCT | 0.15 | 0.15 | Daily cap (15% of bankroll) |
| MIRROR_MAX_TRACKED_TRADES | 10000 | 10000 | Dedup cache size |
| MIRROR_MAX_PER_MARKET | 400 | 400 | Max $ per market |
| ELITE_LOOKBACK_DAYS | 365 | 365 | Historical window for reliability |

## Known Issues & Debug History
- **[Session 49]** First trades placed: 2 positions opened. Bot was previously dormant due to
  high elite consensus threshold and limited signal.
- **[Session 47]** BotBankrollManager wired: per-bot $1k capital, no shared Kelly divisor.
- **[Session 46]** Previously 0 trades — elite consensus threshold too high for available signals.
- **[OPEN]** Elite signal sparsity: Mirror fires only when ≥2 elites agree in <5min window.
  May miss signals during low-activity periods.
- **[OPEN]** Reliability cold start: EliteReliabilityTracker needs 365 days of data per trader.
  New elites start with default reliability.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep MirrorBot"

# Recent mirror trades
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, market_id, side, size, price, realized_pnl
  FROM paper_trades WHERE bot_name='MirrorBot'
  ORDER BY created_at DESC LIMIT 20;\""

# Check per-category consensus thresholds
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT * FROM bot_market_params WHERE bot_name='MirrorBot' LIMIT 20;\""

# Run MirrorBot tests
pytest tests/ -k "mirror" -v
```

## Next Steps / Blockers
- [ ] Re-enable when isolation mode lifted (BOT_ENABLED_MIRROR=true)
- [ ] Monitor elite signal quality after re-enable: check if 2+ elites agree frequently enough
- [ ] Consider lowering MIRROR_MIN_CONSENSUS to 1 if signal too sparse
- [ ] Monitor stop loss triggers: 15% drawdown should rarely fire on mirrored trades
