# AGENT HANDOFF — S115: Shadow Fill Tracking
**Date**: 2026-03-21
**Scope**: All bots (paper_trading.py, order_gateway.py, database.py)
**Status**: Code complete, tests passing (1626/0), not yet deployed

---

## What Changed

Replaced ~8 theoretical slippage models with real L2 orderbook data. Every trade signal now snapshots the actual book, walks it for VWAP, and records everything to `shadow_fills` for retroactive P&L analysis.

### Removed from `paper_trading.py` (~300 lines):
- Alpha decay (exponential latency formula)
- Kyle's lambda (adverse selection estimate from DB)
- Size-dependent slippage tiers (35/50/75/120 bps buckets)
- Square-root market impact (σ√(Q/V))
- Cross-scan cumulative impact (60s in-memory tracker)
- Resolution proximity penalty (hours-to-resolution multiplier)
- Fill probability model (5-factor multiplicative heuristic → random rejection)
- Slippage-eats-edge rejection gate (replaced by real edge-at-VWAP gate)
- Time-of-day liquidity multiplier
- Fill-failure cooldown tracker
- `_get_kyle_lambda()` method
- `MarketImpactEstimator` import

### Added:

**`order_gateway.py` — Pre-trade book walk (paper AND live)**
- Snapshots L2 book via `OrderBookTracker.snapshot_order_book()`
- Walks book with `_vwap_from_book()` for VWAP fill price
- Edge-at-VWAP gate: rejects if `confidence <= VWAP` (edge eroded by real book movement)
- Records shadow fill for rejected trades (`trade_executed=False`)
- Records shadow fill for live executed trades
- Passes book walk results to paper_trading via event_data

**`paper_trading.py` — Simplified BUY path**
- Reads VWAP from order_gateway's book walk (via event_data)
- Fills at real VWAP instead of theoretical slippage-adjusted price
- Records shadow fill for executed paper trades
- Partial fill from book depth (fill_fraction from book walk)

**`database.py` — New methods**
- `insert_shadow_fill()` — records signal + book + VWAP + edge + execution data
- `backfill_shadow_resolution()` — UPDATE shadow_fills with resolution P&L

**`resolution_backfill.py` — Phase 4c**
- Backfills `shadow_fills.shadow_pnl` when markets resolve

**`base_engine.py` — Wiring**
- Wired `OrderBookTracker` to `PaperTradingEngine._orderbook_tracker` (was never wired — book walk was dead code)
- Wired `OrderBookTracker` to `OrderGateway._orderbook_tracker`

**Bot latency tracking (scan_start_mono)**
- MirrorBot: set at `scan_and_trade()` entry AND RTDS receipt in `elite_watchlist.py`
- EsportsBot: set at `scan_and_trade()` entry
- WeatherBot: already had it (S100)

**`schema/migrations/058_shadow_fills.sql`** — New table + 3 indexes

**`scripts/shadow_analysis.py`** — Retroactive P&L analysis script

---

## New Table: `shadow_fills`

Every BUY signal gets a row — executed or not.

| Column | Purpose |
|--------|---------|
| `book_snapshot` | Full L2 asks (top 20 levels) at signal time |
| `vwap_fill_price` | VWAP from walking the book for order size |
| `book_walk_slippage` | VWAP - best_ask (cost of walking) |
| `edge_at_vwap` | confidence - VWAP (true edge after real slippage) |
| `trade_executed` | Did we take this trade? |
| `execution_price` | Price we actually entered at |
| `shadow_pnl` | Retroactive P&L (backfilled on resolution) |
| `latency_ms` | scan_start → order placement |

---

## Trade Flow (Paper + Live Identical)

```
Bot signal → place_order() → ORDER GATEWAY
  │
  ├─ Snapshot L2 book (REST API, ~30-50ms)
  ├─ Walk book → VWAP
  ├─ Edge check: confidence > VWAP?
  │     NO → reject, log shadow_fill (executed=False)
  │     YES ↓
  ├─ Paper mode: fill at VWAP, log shadow_fill (executed=True)
  └─ Live mode: submit to CLOB, log shadow_fill (executed=True)
```

---

## Files Modified

| File | Lines Changed | Change |
|------|--------------|--------|
| `paper_trading.py` | -300, +80 | Remove theoretical models, simplify to book walk |
| `order_gateway.py` | +90 | Pre-trade book walk + edge gate + shadow fill recording |
| `base_engine.py` | +4 | Wire OrderBookTracker to paper_trading + order_gateway |
| `database.py` | +120 | `insert_shadow_fill()` + `backfill_shadow_resolution()` |
| `resolution_backfill.py` | +10 | Phase 4c shadow_fills backfill |
| `mirror_bot.py` | +2 | `scan_start_mono` at scan entry + in event_data |
| `esports_bot.py` | +2 | `scan_start_mono` at scan entry + in event_data |
| `elite_watchlist.py` | +1 | `scan_start_mono` at RTDS receipt for MirrorBot |
| `test_paper_fill_probability.py` | rewritten | Tests for VWAP flow + edge gate |
| `test_paper_trading.py` | -3 | Remove patches of deleted functions |
| `test_paper_is_production.py` | +1 | Update SIMULATION_MODE ref count (2→3) |

---

## Deploy Steps

1. Run migration: `psql -f schema/migrations/058_shadow_fills.sql`
2. Deploy code
3. Verify: `journalctl -u polymarket-ai -f | grep "paper_book_walk\|order_edge_eroded"`
4. After 24h: `python scripts/shadow_analysis.py`

---

## Known Gaps

| # | Gap | Impact | Resolution |
|---|-----|--------|------------|
| 1 | OrderBookTracker uses REST (~30-50ms stale) not WebSocket | On active markets, ~1-2% chance best level changed between snapshot and execution | **Retroactive review**: shadow_fills records `vwap_fill_price` vs `execution_price` — gap quantifies staleness cost. If >1 cent avg on live trades, upgrade to WebSocket book |
| 2 | Paper mode latency doesn't include CLOB network RTT (~60-70ms) | Paper latency_ms underestimates live by ~60-70ms | **Resolved in retro**: live `execution_price` vs paper `vwap_fill_price` comparison shows the real impact |
| 3 | No minimum edge threshold | Bot trades with any positive edge (even 0.1 cent) | **Calibrate from data**: after shadow_fills has resolution data, analyze `edge_at_vwap` vs `shadow_pnl` to find optimal minimum edge |

---

## Verification Checklist (Post-Deploy)

- [ ] `shadow_fills` table exists: `SELECT COUNT(*) FROM shadow_fills`
- [ ] Book walks firing: `grep "paper_book_walk" /var/log/polymarket-ai.log | head`
- [ ] Edge rejections logging: `grep "order_edge_eroded" /var/log/polymarket-ai.log | head`
- [ ] Latency tracked for all 3 bots: `SELECT bot_name, COUNT(*), AVG(latency_ms) FROM shadow_fills GROUP BY bot_name`
- [ ] No trades with book_snapshot=NULL (means OrderBookTracker not wired): `SELECT COUNT(*) FROM shadow_fills WHERE book_snapshot IS NULL`
- [ ] Resolution backfill running: `SELECT COUNT(*) FROM shadow_fills WHERE resolved_at IS NOT NULL` (after 24h+)

---

## Rollback

`git revert <sha>` — no migration rollback needed (shadow_fills is additive, no existing tables modified). Dead config settings in settings.py are harmless.

---

## Future Work (Not This Session)

- **WebSocket orderbook** — upgrade from REST polling to live WebSocket book maintenance. Only justified if shadow_fills data shows staleness cost >1 cent average
- **Empirical edge gate** — use resolved shadow_fills to calibrate minimum edge threshold (currently: any positive edge trades)
- **Empirical latency budget** — use shadow_fills latency_ms vs win rate to set per-bot max latency
- **Book depth minimum** — use shadow_fills fill_fraction data to set minimum depth requirement
