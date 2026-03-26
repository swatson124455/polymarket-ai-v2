# AGENT HANDOFF — MirrorBot Session 103 (2026-03-18)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`). Going live is flipping a boolean.

**MirrorBot** is the highest-performing bot. It copy-trades elite whale traders in real-time via RTDS WebSocket firehose. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99/S100/S101/S102/S103)
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  -> streams ALL trades on Polymarket (global firehose, no auth)
  -> EliteWatchlist does O(1) lookup: is trader in our 500-whale watchlist?
  -> YES -> log to whale_trades table + _execute_mirror_trade() with validation
  -> NO -> discard

Scan loop (45s interval) handles ONLY:
  - Stop-loss exits (15% default, graduated tightening at 48h/72h, force at 96h)
  - Take-profit (25%)
  - Housekeeping (position reconciliation, cache refresh, RTDS stale detection)
```

---

## 2. SESSION 103 CHANGES

### Problem Statement
Full diagnostic revealed `self.min_confidence` (set from `MIRROR_MIN_CONFIDENCE`) was **dead code** — never checked anywhere in the RTDS entry path. Confidence entered at 0.55 from watchlist, domain drift halved to ~0.275, calibration adjusted to ~0.38, and trades executed unchecked. Additionally, 30,000+ INFO log lines per 6h were saturating journald.

### Data-Driven Analysis (corrected SQL join)
Initial query showed 651 entries at 30-40% confidence with "zero resolutions" — seemed statistically impossible. Investigation revealed the SQL join was broken: **RESOLUTION events store `token_id=NULL`**, so joining on `(market_id, token_id, side)` produced zero matches. Corrected join on `(market_id, side)` revealed:

| Confidence | Resolved | Win Rate | P&L | $/position |
|---|---|---|---|---|
| **30-40%** | 11 | **9%** | **-$1,725** | **-$157** |
| **40-50%** | 11 | **18%** | **-$585** | **-$53** |
| **50-55%** | 337 | **36%** | **+$8,696** | **+$26** |
| **55%+** | 587 | **48%** | **+$7,415** | **+$13** |

The 30-40% bracket is catastrophically bad. The confidence gate is not optional — it's hemorrhaging money.

### Confidence Flow (how the number is calculated)
1. **EliteWatchlist** (`elite_watchlist.py:385`): `confidence = min(0.70, 0.55 + efficiency_bonus)` — hardcoded base 0.55 + efficiency (0-0.15). Range: 0.55-0.70.
2. **Domain drift** (`mirror_bot.py:1216`): If trader has < 10 resolved trades in this category → `confidence *= 0.50`. Halves to ~0.275.
3. **Calibration stack** (`mirror_bot.py:1253`): Domain/horizon bias adjustment. Bumps ~0.275 up to ~0.38.
4. **NEW: Confidence gate** (`mirror_bot.py:1261`): `if confidence < self.min_confidence: return False`

### What We Did

**A. Confidence gate (Bug Fix)**
- `self.min_confidence` was set at `__init__` (line 41) but NEVER checked in `_execute_mirror_trade()`. Dead code.
- Added gate AFTER all adjustments (domain drift + calibration) but BEFORE Kelly sizing.
- **TRIAL threshold: 0.45** — blocks all 30-40% trades (9% WR, -$157/pos) and lower half of 40-50% (18% WR, -$53/pos).
- Tunable via `MIRROR_MIN_CONFIDENCE` in `.env`.

**B. Entry confidence logging**
- Added `entry_confidence=round(confidence, 3)` to "Mirror trade executed" log line for ongoing P&L-by-confidence tracking.

**C. Log spam demoted to DEBUG**
- `mirror_price_bounds` (18,849 lines/6h) → DEBUG
- `skipping SELL (no position to close)` (5,995 lines/6h) → DEBUG
- ~25,000 INFO lines per 6h removed from journald.

**D. Dead `_conformal_interval` variable removed**
- Was set to `None` and passed as `None` everywhere. Cleaned up.

### Files Modified
| File | Change |
|------|--------|
| `bots/mirror_bot.py` | Confidence gate at 0.45, entry_confidence log, price_bounds/sell_no_pos → DEBUG, dead conformal var removed |
| `tests/unit/test_mirror_bot_logic.py` | Mock reliability tracker in test_entry_trade_success to avoid domain drift below gate |

### Tests
1631 passed, 0 failed, 8 skipped.

---

## 3. TRIAL: CONFIDENCE GATE AT 0.45

**THIS IS A TRIAL. MUST REVIEW NEXT SESSION.**

### What to check
Run `scripts/diag_confidence.py` on VPS (scp it first — it's a standalone script):
```bash
scp scripts/diag_confidence.py ubuntu@34.251.224.21:/tmp/
ssh ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && ./venv/bin/python /tmp/diag_confidence.py"
```

**IMPORTANT**: The script joins on `(market_id, side)` NOT `(market_id, token_id, side)` — RESOLUTION events have `token_id=NULL`.

### Decision matrix for next session
| Observation | Action |
|---|---|
| 40-50% bracket still negative | Raise threshold to 0.50 |
| Volume dropped too much (< 50 trades/6h) | Lower threshold to 0.40 |
| 50-55% bracket stays best $/pos | Threshold is in right range |
| New confidence data changes picture | Adjust accordingly |

### ~570 open positions at 30-40% confidence
These were entered BEFORE the gate. They'll resolve naturally or hit stop-loss. They're ticking time bombs (-$157/pos on resolved sample). Monitor but don't panic-exit — stop-loss handles the worst cases.

### VPS log verification post-deploy
```bash
# Gate firing:
journalctl -u polymarket-ai --since '10 min ago' | grep mirror_low_confidence | head -5
# Log spam gone:
journalctl -u polymarket-ai --since '10 min ago' | grep mirror_price_bounds | wc -l  # should be 0
# Entry confidence tracking:
journalctl -u polymarket-ai --since '10 min ago' | grep "Mirror trade executed" | grep entry_confidence | head -3
```

---

## 4. CURRENT STATE

### Config (live VPS values)
```
MIRROR_MIN_CONFIDENCE=0.45  (S103 TRIAL — was 0.50 default, never enforced)
MIRROR_HARD_MIN_PRICE=0.10
MIRROR_HARD_MAX_PRICE=0.95
MIRROR_FAVORITE_DAMPENER=0.40  (≥70c)
MIRROR_DEAD_ZONE_DAMPENER=0.50  (30-50c)
MIRROR_MIN_TRADE_USD=50.0
MIRROR_MAX_CONCURRENT_POSITIONS=200  (VPS .env — config/settings says 500, mismatch)
MIRROR_MAX_PER_MARKET=400
kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K
```

### P&L Snapshot (as of S103 diagnostic)
- **All-time**: ENTRY=3766, EXIT=694 (+$6,153), RESOLUTION=931 (+$13,616) = **+$19,769 realized**
- **24h**: 850 events, **-$212**
- **Open positions**: 197, exposure $14,539, unrealized +$138
- **Daily P&L trend**: Mar 17 -$553, Mar 18 -$343 (declining, confidence gate should help)

### Validation Pipeline (S103 — 24 checks)
Entry path:
1. Wash trader filter
2. Hard price bounds [0.10, 0.95]
3. Circuit breaker
4. Position cap (200 on VPS, 500 in config — MISMATCH)
5. Daily exposure cap
6. Category cap ($40K)
7. Market blocklist (in-memory)
8. Per-market cooldown (30min)
9. Category blocklist
10. Opposing-side dedup
11. Inactive market filter
12. Near-resolution filter (4h)
13. Current price correction (use market price, not trader fill)
14. Slippage cap (8%)
15. Reliability gate (LR ≥ 1.0)
16. Domain drift penalty (0.50x if < 10 trades in category)
17. Signal enhancements (skipped by default)
18. FTS calibration (domain + horizon)
19. **NEW: Confidence gate (≥ 0.45 TRIAL)**
20. Kelly sizing via BotBankrollManager
21. Single price dampener (30-50c: 0.50x, ≥70c: 0.40x)
22. Per-market USD cap ($400)
23. Daily remaining cap
24. Min trade USD ($50)

---

## 5. OPEN ITEMS

| Priority | Item | Notes |
|----------|------|-------|
| **P1** | **TRIAL: Review 0.45 confidence gate** | Run `diag_confidence.py`. Check if <45% trades blocked, volume acceptable, P&L improving. |
| P2 | Position cap mismatch | VPS `.env` says 200, config says 500. Bot hitting cap (431 rejections/6h). Verify and align. |
| P2 | ~570 positions at 30-40% confidence | Pre-gate entries. Will resolve naturally. Monitor for P&L impact. |
| P3 | NO vs YES WR asymmetry | 72% vs 39% WR. Confirmed, monitor. |
| P3 | `mirror_calibration.py` cleanup | FTS calibrator active. Conformal code dead. Could strip. |
| P4 | Hold-time analysis | <24h positions net losers (-$1,370). Consider minimum hold time. |
| P5 | Resolution NULL token_id | RESOLUTION events store `token_id=NULL`. Any P&L-by-entry query MUST join on `(market_id, side)` only. |

---

## 6. CRITICAL TRAPS (DO NOT BREAK)

- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- `trade_events` is P&L authority, NOT `paper_trades`.
- RTDS envelope: unwrap `data.get("payload", data)`.
- RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- `whale_trades` requires explicit `await session.commit()` (S101 fix).
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- Positions table: NO `closed_at`, NO `updated_at`, NO `bot_name` — use `source_bot` or `bot_id`.
- MirrorBot entry price: Uses CURRENT market price, NOT trader's fill price.
- **NEW: RESOLUTION events have `token_id=NULL`** — any join to ENTRY events must use `(market_id, side)` only, NOT `(market_id, token_id, side)`.
- **NEW: `self.min_confidence` is now enforced** — changing `MIRROR_MIN_CONFIDENCE` in `.env` actually affects trade flow (it didn't before S103).

---

## 7. KEY FILES

| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Core MirrorBot logic (~1380 lines) |
| `bots/elite_watchlist.py` | RTDS trade matching + confidence calculation |
| `bots/mirror_calibration.py` | FTS domain/horizon calibration |
| `base_engine/base_engine.py` | Shared engine (market index, order gateway) |
| `base_engine/execution/order_gateway.py` | Order execution + risk checks |
| `base_engine/data/database.py` | DB models + session management |
| `config/settings.py` | All config defaults |
| `scripts/diag_confidence.py` | P&L by confidence bracket (S103) |
| `scripts/bot_pnl.py` | Canonical P&L script |
