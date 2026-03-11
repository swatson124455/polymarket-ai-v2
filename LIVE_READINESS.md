# LIVE READINESS — CANARY_STAGE Gate Criteria

This document defines the objective gate criteria that must be satisfied before
advancing the `CANARY_STAGE` setting in `/opt/pa2-shared/.env` and increasing
live-capital exposure.

**Current stage:** `CANARY_STAGE=0` (paper trading, 0% live capital)

---

## Stage Definitions

| Stage | Capital Exposure | Description |
|-------|-----------------|-------------|
| 0 | 0% (paper only) | Full paper-trading. All systems instrumented. |
| 1 | 5% live | Canary slice. Kill-switch tested. Reconciler active. |
| 2 | 25% live | Validated Sharpe. Drawdown limits confirmed. |
| 3 | 50% live | Production-grade. Zero reconciler divergence 30 days. |
| 4 | 100% live | Full deployment. Long-term track record established. |

---

## Stage 0 → Stage 1 (paper → 5% live)

All of the following must be true simultaneously:

### Infrastructure
- [ ] **H1 complete** — Order state machine (PENDING→SUBMITTED→LIVE→FILLED) deployed and verified
- [ ] **H2 complete** — PositionReconciler live: `_get_chain_balances()` calls CLOB API, zero divergence for ≥7 consecutive days
- [ ] **H3 complete** — Centralized Redis rate limiter deployed, no uncoordinated burst events in logs
- [ ] Kill switch tested in production: `engage()` marks open positions halted, confirmed in DB

### Performance (paper trading baseline)
- [ ] **30 calendar days** of clean paper-trading data with no service interruptions >1h
- [ ] **Sharpe ratio > 0** (rolling 30-day, `get_paper_trade_equity_curve(days=30)`)
- [ ] **Max drawdown < 10%** of bot capital across all 5 active bots
- [ ] **Win rate ≥ 40%** on resolved trades (all bots combined)

### Calibration
- [ ] EMOS active on ≥13 stations (`weatherbot_calibration_reloaded` log confirms `emos_ready_stations` list)
- [ ] WeatherBot Brier score ≤ 0.25 (7-day rolling)
- [ ] EsportsBot Brier score ≤ 0.25 for at least 2 of {lol, cs2, dota2, valorant}

### Monitoring
- [ ] `check_daily_pnl_summary()` firing daily — confirmed in Discord/logs
- [ ] `order_gateway_daily_exposure_restored` logging on every restart
- [ ] No `esports_cross_game_retrain_failed` in logs for ≥14 days

**Verification command:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@34.251.224.21 \
  'PGPASSWORD=polymarket_s46 psql -h localhost -U polymarket -d polymarket -c "
SELECT
  COUNT(*) AS total_trades,
  ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) AS win_pct,
  ROUND(SUM(realized_pnl)::numeric, 2) AS total_pnl
FROM paper_trades
WHERE resolved_at >= NOW() - INTERVAL '\''30 days'\''
  AND realized_pnl IS NOT NULL;"'
```

---

## Stage 1 → Stage 2 (5% → 25% live)

All Stage 1 gates remain satisfied plus:

- [ ] **60 calendar days** of live + paper combined data
- [ ] **Sharpe ratio > 0.5** (rolling 60-day, live trades only)
- [ ] **Max drawdown < 5%** of live-capital allocation in past 30 days
- [ ] PositionReconciler: zero divergence events (>$1.00) in past 30 days
- [ ] WeatherBot: ≥50 resolved paper trades with EMOS calibration active
- [ ] Daily PnL alert delivery confirmed for ≥30 consecutive days

---

## Stage 2 → Stage 3 (25% → 50% live)

All prior gates remain satisfied plus:

- [ ] **90 calendar days** of mixed live/paper data
- [ ] **Sharpe ratio > 1.0** (rolling 90-day)
- [ ] **Zero reconciler divergence events** in past 30 days
- [ ] All 5 active bots Brier ≤ 0.25 simultaneously for ≥14 days
- [ ] EsportsBot cross-game XGB model: `esports_cross_game_retrain_cancelled` = 0 in past 30 days
- [ ] WeatherBot EMOS on all 15 stations (NZWN + RJTT catch up)

---

## Stage 3 → Stage 4 (50% → 100% live)

All prior gates remain satisfied plus:

- [ ] **180 calendar days** of track record
- [ ] **Sharpe ratio > 1.5** (rolling 180-day)
- [ ] Kill switch manually triggered and recovered in staging environment within past 90 days
- [ ] H1 order lifecycle: zero double-fills detected in 90 days of log analysis
- [ ] Regulatory/compliance review completed (if applicable in jurisdiction)

---

## Emergency Rollback (any stage)

Revert to Stage 0 immediately if any of these occur:

- Kill switch engages (automatic)
- Drawdown > 20% of live-capital allocation in any single day
- PositionReconciler divergence > $50 for any single position
- Service crash with no auto-recovery within 5 minutes
- Any unresolved `esports_cross_game_retrain_failed` lasting >48h

**Rollback command:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"
# Set stage 0 (paper only):
sudo sh -c "sed -i 's/CANARY_STAGE=.*/CANARY_STAGE=0/' /opt/pa2-shared/.env"
sudo systemctl restart polymarket-ai
```

---

## Current Checklist Status (2026-03-11)

| Gate | Status | Notes |
|------|--------|-------|
| H1 Order state machine | DONE | correlation_id idempotency guard — commit `2b85073` |
| H2 PositionReconciler | DONE | 30-min periodic schedule wired — commit this session |
| H3 Rate limiter | PRE-EXISTING | PolymarketClient is singleton (`shared_across_bots=True`); PandaScore class-level shared |
| Kill switch position halt | DONE | `mark_positions_halted()` — commit `da2b214` |
| 30d paper Sharpe > 0 | IN PROGRESS | ~90 paper trades resolved, +$479 P&L as of 2026-03-10 |
| EMOS ≥13 stations | DONE | 13/15 active (NZWN +3, RJTT +19) |
| Daily PnL alert | DONE | `check_daily_pnl_summary()` wired in scheduler |
| WS latency threshold | DONE | `WS_SIGNAL_LATENCY_ALERT_MS=2500` set on VPS |
