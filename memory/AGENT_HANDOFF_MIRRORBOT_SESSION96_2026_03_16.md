# Agent Handoff — MirrorBot Session 96 (2026-03-16)

## Session Scope
**MirrorBot-only session.** Overnight operational review + position cap fix.

## What Was Done

### P1: Position Cap Raised 200→400 (DEPLOYED)
- **Issue**: MirrorBot hit 200/200 position cap overnight, blocking all new trades. RTDS feed showed 30-50 actionable trades/hour being rejected by `_can_open_position()`.
- **Fix**: Raised both position cap settings:
  - `MIRROR_MAX_CONCURRENT_POSITIONS`: 50→400 (code default; VPS .env already set to 400)
  - `MIRROR_MAX_POSITIONS`: 200→400 (risk_manager position count check)
- **Files modified**: `config/settings.py` (2 lines), VPS `.env` (1 line)
- **Commit**: `2a87cdc`

### Investigation: 10 Service Restarts in 90min (NON-ISSUE)
- All 10 restarts were deploy-triggered SIGTERM (from S95 deploys), not crashes
- SIGTERM → graceful shutdown → systemd restart = expected behavior
- No OOM kills, no unhandled exceptions

### Investigation: WebSocket Disconnects Every ~2min (KNOWN, LOW PRIORITY)
- 229 disconnects in 8h on the **per-market PRICE WebSocket** (`websocket_manager.py`)
- Uses library auto-ping at 30s (`ping_interval=30, ping_timeout=10`)
- **RTDS trade-copy WebSocket is rock solid** — 0 disconnects, manual 5s ping loop
- Root cause: Polymarket server-side connection management. Reconnects are fast (exponential backoff).
- **Not blocking any trades.** RTDS (the one that matters for copy trading) is unaffected.
- P5 enhancement: reduce market WS `ping_interval` from 30s→10s to reduce disconnects

### Investigation: 74s+ Slow Scan Cycles (IDENTIFIED, NOT FIXED)
- **Root cause**: `_check_and_execute_exits()` does sequential `get_user_activity()` calls per tracked trader. No concurrency, no caching.
- With 200 positions × ~150 unique traders × ~200ms/call = ~30s per scan cycle
- Entry path uses `asyncio.gather()` + `Semaphore(20)` + 90s cache — exit path doesn't
- P3 enhancement: parallelize exit checks like entry scan

### Investigation: AS Logging Data Gap (DIAGNOSED, NOT FIXED)
- **Symptom**: All `paper_fill_as_baseline` logs show `volume_24h=0.0` and `spread=0`
- **Root cause (spread=0)**: `bid`/`ask` params never reach `paper_trading.place_order()`. Call chain: `MirrorBot.place_order()` → `BaseBot.place_order()` → `base_engine.place_order()` → `order_gateway.place_order()`. Neither BaseBot nor base_engine accept/forward bid/ask. The `_market_index` has token mid-prices but no order book bid/ask (would need CLOB API calls we skip for latency).
- **Root cause (volume=0)**: Volume lookup in `order_gateway` works (lines 679-684), BUT MirrorBot's RTDS-traded markets are mostly NOT in `_market_index`. Index is only populated at startup with top 300 markets by liquidity. MirrorBot never calls `update_market_index()`.
- **Impact**: Diagnostic only — doesn't affect fill realism, pricing, or P&L
- **Fix options**: (a) Have MirrorBot populate market index during scan, (b) Have order_gateway do DB volume lookup when index misses. Both are P5 enhancements.

## Config Changes
| Setting | Old | New | Tier |
|---------|-----|-----|------|
| MIRROR_MAX_CONCURRENT_POSITIONS | 50 (code) / 200 (env) | 400 | Tier 2 |
| MIRROR_MAX_POSITIONS | 200 | 400 | Tier 2 |

## Files Modified
- `config/settings.py` — 2 lines (position cap defaults)
- VPS `.env` — 1 line (MIRROR_MAX_CONCURRENT_POSITIONS=400)

## Tests
1616 passed, 8 failed (pre-existing: deleted `ui/dashboard` + `ui/async_worker` modules), 8 skipped.

## Outstanding Items (Priority Order)
- **P3**: Parallelize `_check_and_execute_exits()` trader activity fetches (saves ~20s per scan cycle)
- **P5**: Reduce market WS `ping_interval` 30s→10s
- **P5**: AS logging volume/spread data — populate `_market_index` for MirrorBot trades
- **P5**: Clean up 8 pre-existing test failures (deleted UI module references)

## Rollback
```bash
# Revert position cap
export MIRROR_MAX_CONCURRENT_POSITIONS=200
export MIRROR_MAX_POSITIONS=200
sudo systemctl restart polymarket-ai

# Or full code revert
git revert 2a87cdc
```
