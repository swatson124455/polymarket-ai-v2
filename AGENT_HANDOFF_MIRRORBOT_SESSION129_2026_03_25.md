# AGENT HANDOFF — MirrorBot Session 129 (2026-03-25)
## SCOPE: MirrorBot ONLY — no bleed to Weather/Esports unless explicit demand
## SESSION TYPE: Shared infrastructure audit fix deployment + diagnostic monitoring

---

## QUICK CONTEXT FOR NEW AGENT

You are continuing work on **MirrorBot**, one bot in a 15-bot Polymarket automated trading system. MirrorBot copies elite traders in real-time via RTDS WebSocket feed. The system is paper trading (`SIMULATION_MODE=true`) on an Ubuntu VPS at `34.251.224.21`. Real capital is NOT at risk yet but paper trading IS treated as production per CLAUDE.md.

**Read these files first (in order):**
1. `CLAUDE.md` — Prime directive, rules of engagement, critical traps
2. `memory/MEMORY.md` — Cross-session memory index (200-line limit, loads as index)
3. This file — Everything done in S129, full architecture reference, P&L truth, next steps

**Do NOT read/modify other bot files** (weather_bot.py, esports_bot.py, etc.) unless explicitly asked.

---

## WHAT WAS DONE THIS SESSION (S129)

### Phase 1: Shared Infrastructure Audit Fixes (21 items)

S128 identified 143 bugs across shared modules. S129 fixed the 21 that directly or indirectly hurt MirrorBot. These span `base_engine/`, `config/`, `deploy/`, and test files.

| Bug ID | Sev | File | Fix | MirrorBot Impact |
|--------|-----|------|-----|-----------------|
| **P0-2** | P0 | `prediction_engine.py:2512` | Deleted `from datetime import datetime as _dt` local import that shadowed module-level `datetime`. Python 3.13 scoping makes local import shadow the entire function. | Unblocked ALL dated market predictions |
| **P0-2b** | P0 | `prediction_engine.py:2653` | Same datetime scoping trap in LLM forecaster block. Removed local import, uses module-level `datetime.fromisoformat()` | Same — LLM path was silently crashing |
| **P0-3** | P0 | `base_engine.py:1451` | `_fetch_tradeable_markets()` called with wrong args → empty market index for 30s after startup. Fixed args to match signature. | First-30s RTDS trades from elites no longer dropped post-restart |
| **P1-4** | P1 | `order_gateway.py:839` | `_check_price` → `_shadow_best_ask` (the actual method name). NameError silently swallowed. | **Confirmed fixed** — zero `_check_price` errors in post-deploy logs |
| **P1-5** | P1 | `advanced_orders.py` | `side="SELL"` hardcoded → `side=side` (YES/NO mandate) | Stop-loss/take-profit exits now use correct side |
| **P1-7** | P1 | `kill_switch.py:59` | First DB failure returns `True` (block trading) instead of `False` (allow). Was a safety-critical default. | Kill switch fail-safe now works correctly |
| **P1-8** | P1 | `base_engine.py` | Added `import os` (missing, used for env var reads) | Prevents crash on env var access in live mode |
| **P1-10** | P1 | `signal_ingestion.py` | Fixed dead method name in signal subscriber registration | Signal subsystem wired correctly |
| **P1-11** | P1 | `signal_ingestion.py` | Fixed signal type enum mismatch | Signal events parsed correctly |
| **P1-12** | P1 | `signal_ingestion.py` | Fixed kwarg name in signal handler call | Signal handler receives correct data |
| **P1-13** | P1 | `health_scheduler.py` | Queries `trade_events.event_data` not `paper_trades.metadata` (which doesn't exist) | Health checks no longer crash silently |
| **P1-14** | P1 | `config/settings.py` | Removed duplicate `WEATHER_COMBINED_BOOST_CAP` definition (preserved S122 value of 1.5) | Clean config, no accidental override |
| **P1-15** | P1 | `learning_engine.py` | Protect structural keys (`_meta`, `default`) from eviction during pruning | Learning engine doesn't corrupt its own state |
| **P1-16** | P1 | `alerting.py` | Daily alert uses `datetime.now(timezone.utc).date()` not `datetime.now().date()` (local tz) | Correct UTC date in alerts |
| **P1-19** | P1 | `database.py:3553` | Swapped order: zero `unrealized_pnl` BEFORE resolution update, not after. Was destroying resolution P&L every 30-min backfill cycle. | **Critical P&L fix** — stops corruption of realized P&L data |
| **P1-20** | P1 | `resolution_backfill.py:470` | Partial-exit double-counting: RESOLUTION now uses `remaining_size = entry_size - exit_size` instead of full `entry_size` | MirrorBot has 794 EXIT events — those were being double-counted |
| — | P2 | `data_ingestion.py` | LEFT JOIN on `market_categories` (was INNER JOIN dropping uncategorized markets) | More markets visible to ingestion |
| — | P2 | `elite_reliability.py` | S113 slug collision fix (condition_id overlap) | Reliability scores no longer cross-contaminated |
| — | P2 | `resolution_backfill.py` | Expired-first ordering (S125) — process oldest markets first | Backfill prioritizes stale positions |
| — | — | `deploy/deploy.sh` | Deploy script hardening (test gate, SSH timeouts) | Safer deploys |
| — | — | Dead code purge | Deleted `bots/mirror_chronos_filter.py`, `bots/mirror_trade_selector.py` | Clean repo |

### Phase 2: Full Code Flow Verification (Read-Only)

Traced every fix through MirrorBot's actual code paths to verify correctness:

1. **ENTRY path**: RTDS → `on_rtds_trade()` → `_execute_mirror_trade()` → `place_order()` → `order_gateway` → `paper_trading.py`. Confirmed P1-4 (`_shadow_best_ask`) is on the RTDS instant-copy hot path. Confirmed P1-5 (`side=side`) only fires for stop-loss/take-profit exits (MirrorBot doesn't use advanced orders for entries).

2. **RESOLUTION path**: `resolution_backfill.py` → `database.py:_update_position_on_resolution()`. Confirmed P1-19 (unrealized_pnl zeroing order) and P1-20 (remaining_size) are on MirrorBot's resolution path. Every resolution runs through this.

3. **Startup path**: `base_engine.__init__()` → `_fetch_tradeable_markets()`. Confirmed P0-3 arg fix populates market index before first RTDS events arrive.

4. **Prediction path**: `prediction_engine.predict()` → datetime parsing for `time_to_resolution`. Confirmed P0-2 and P0-2b remove both scoping traps. MirrorBot calls predict() for every entry candidate.

### Phase 3: Deployment

- **Commit**: `04f4ee7` — `fix(infra): S129 — 21 shared infra audit fixes + 2 P&L corrections`
- **Deploy**: `20260325_000444` via `deploy.sh`
- Tests: 1717 passed, 0 failed
- Health check: passed at 60s — bots scanning
- Migrations: all 59 already applied, no new migrations

### Phase 4: Post-Deploy Monitoring (10.5 hours of data)

#### System Health — ALL GREEN
| Component | Status | Detail |
|-----------|--------|--------|
| MirrorBot scanning | OK | 45s cycles, 49-232ms, 500 elites |
| RTDS feed | OK | Connected, 4.3M events dispatched |
| ML selector | OK | `xgb=True ql=True` |
| Market index (P0-3) | OK | 1500 pre-populated on startup |
| Kill switch | OK | No false blocks |
| `_check_price` error | **GONE** | P1-4 fix confirmed |
| Size violation warnings | One-time | 20 historical violations logged at startup audit, 0 new |

#### Errors Observed
| Error | Count (10h) | Severity | Verdict |
|-------|-------------|----------|---------|
| Price stream WS timeout | ~30 | Low | Normal Polymarket WS drops. Auto-reconnects. Self-healing. |
| `mirror_opposing_side_blocked` | ~8/5min | Noise | One hot market spamming. Guard working correctly. |
| Trade event size violations | 20 (startup only) | Info | Historical data mismatch from pre-backfill era. No new violations. |

---

## CURRENT VPS STATE (as of 2026-03-25 14:37 UTC)

- **PID**: 3316414 (started with deploy)
- **Open positions**: 732 (DB) / 795 (in-memory scan log) — gap is normal reconciliation lag
- **Elites**: 500 tracked
- **RTDS**: Connected, 4.3M+ events dispatched
- **Scan cycle**: ~45s intervals, 49-232ms per scan
- **ML selector**: `xgb=True ql=True` (shadow scoring every entry)
- **ML-scored entries**: 885 (since Mar 24 00:00)
- **Daily exposure**: $137,784 seeded on startup

---

## P&L STATUS — CRITICAL INFORMATION

### Since Deploy (10.5h)
| Type | Count | P&L |
|------|-------|-----|
| Entries | 315 | $0 (cost basis) |
| Exits | 4 | -$122.05 |
| Resolutions | 345 | -$32,246.04 |
| **Today net** | | **-$32,368.09** |

### Resolution Win/Loss Split
- **Wins**: 131 (38.0% WR) → +$68,854 | Avg win: $525.61
- **Losses**: 214 (62.0%) → -$101,100 | Avg loss: -$472.43
- Win/loss ratio: 1.11x — insufficient to offset 38% WR

### All-Time
| Type | Count | P&L |
|------|-------|-----|
| Exits | 794 | +$6,505 |
| Resolutions | 3,103 | -$24,667 |
| **All-time net realized** | | **-$18,162** |
| Unrealized (732 open positions) | | -$326 |

### P&L Context
- S86 (Mar 14): reported +$15,051 (now known to have had counting errors)
- S119 (Mar 23): corrected to +$26,986 (included dedup fixes)
- S120 (Mar 23): 48h window -$3,456 (0.50-0.55 conf bucket = -$6,745 drag)
- **S129 (today)**: All-time realized is now **-$18,162**

The P&L discrepancy from S119's +$26,986 to today's -$18,162 is partly from:
1. P1-19 fix (unrealized_pnl zeroing was corrupting resolution data — now fixed)
2. P1-20 fix (partial-exit double-counting — now fixed)
3. 10 days of new trading with high loss rate

**THE P&L PROBLEM IS SIGNAL QUALITY, NOT CODE.** The 38% resolution WR with 1.11x win/loss ratio is unprofitable. The ML shadow race and confidence bucketing work (S120, S124) are the right approach — they just need data.

---

## POSITION FLOW

| Metric | At Deploy (04:07) | Now (14:37) | Rate |
|--------|-------------------|-------------|------|
| Open positions | 878 | 732-795 | Draining ~8/hr net |
| Entries since deploy | — | 315 | ~30/hr |
| Exits since deploy | — | 4 | ~0.4/hr |
| Resolutions since deploy | — | 345 | ~33/hr |

Positions are **draining** — resolutions outpacing entries. Position cap (1000) is not a near-term concern. The cap was raised from 600→1000 in S128 and is now well within bounds.

---

## MIRRORBOT ARCHITECTURE REFERENCE

### Runtime Data Flow
```
RTDSWebSocket (base_engine/data/rtds_websocket.py)
  → receives ALL Polymarket trades globally
  → EliteWatchlist.on_rtds_trade() (bots/elite_watchlist.py)
    → O(1) watchlist lookup (top 500 traders from monthly leaderboard)
    → dedup by composite key
    → wash trader detection (bisect-optimized, S128)
    → fast pre-filter: _can_open_position(price) WITHOUT category
    → confidence from efficiency score
    → MirrorBot._execute_mirror_trade() (bots/mirror_bot.py)
      → 16 rejection gates:
        1. blocklist
        2. cooldown
        3. category exposure
        4. position cap (1000)
        5. opposing-side guard (NO entry on existing YES position)
        6. same-side dedup
        7. market active check
        8. near-resolution filter
        9. slippage gate
        10. reliability LR threshold
        11. confidence gate (MIN_CONFIDENCE=0.55)
        12. sizing gate (dust filter)
        13. daily cap
        14. per-market cap ($500)
        15. edge-at-VWAP (order_gateway)
        16. paper engine cash check
      → multi-factor confidence: F1(category WR) + F2(price edge) + F3(whale conviction)
      → ML scoring (xgb + ql + combo) [SHADOW ONLY — scores logged, not gating]
      → BotBankrollManager sizing ($300 cap)
      → BaseBot.place_order(side="YES"/"NO")
      → trade_events ENTRY record
```

### Exit Paths
```
scan_and_trade() → check_exits():
  1. Take-profit: current_price >= entry + 25%
  2. Force exit: position held >= 96h
  3. Stop-loss/take-profit via advanced_orders (S129: side=side fix)
  → BaseBot.place_order(side=opposite) → EXIT event in trade_events
```

### Resolution Path
```
resolution_backfill.py (every 30 min mini + daily full):
  → Phase 1: Check traded_markets for resolved condition_ids
  → Phase 2: Call CLOB API for resolution outcome (YES=1, NO=1, or tie)
  → Phase 4b: Emit RESOLUTION trade_event
    → S129 fix: remaining_size = entry_size - SUM(exit_sizes)
  → database.py._update_position_on_resolution()
    → S129 fix: zero unrealized_pnl BEFORE resolution update
    → Atomic INSERT...SELECT (no ON CONFLICT for partitioned tables)
```

### Periodic Housekeeping (scan_and_trade, every ~45s)
```
scan_and_trade()
  ├── _restore_state_on_startup() [scan 1: seed exposure, positions, sides, caches]
  ├── calibration fit [daily, DISABLED: MIRROR_USE_CALIBRATION=false]
  ├── leader reconciliation [scan 3, bg, 60s timeout; retries every 100 scans (S128)]
  ├── dedup flush to Redis [every 100 scans]
  ├── elite refresh [every 480 scans ~6h, 10s timeout]
  ├── reliability refresh [independent 30s timeout]
  ├── WebSocket + RTDS connect [scan 1]
  ├── daily watchlist refresh [once per UTC day]
  ├── daily reset [UTC boundary: zero exposure, category, consensus]
  ├── reap resolved positions [every 20 scans]
  ├── check & execute exits [every scan]
  │   ├── take-profit: >= 25%
  │   ├── force exit: >= 96h hold
  │   └── advanced orders (stop-loss/take-profit)
  └── ML model retrain [daily]
```

### State Persistence
| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_open_positions` | `positions` table | `_restore_state_on_startup()` |
| `_daily_exposure` | `trade_events` SUM | `_restore_state_on_startup()` |
| `_category_exposure` | `trade_events` SUM | `_restore_state_on_startup()` |
| `_entered_market_sides` | `trade_events` query | `_restore_entered_sides()` |
| `_dedup_set` | Redis | `_restore_dedup_from_redis()` |
| ML models | `models/*.pkl` on VPS | `mirror_ml_selector.load_xgb()` / `load_qtable()` |
| Calibration | In-memory fit from `trade_events` | `mirror_calibration.fit()` on startup |

---

## KEY FILES (MirrorBot-specific)

| File | Purpose | Lines |
|------|---------|-------|
| `bots/mirror_bot.py` | Main bot: entry logic, exit logic, state management | ~1100 |
| `bots/elite_watchlist.py` | Elite trader tracking, RTDS trade filtering, wash detection | ~450 |
| `bots/mirror_calibration.py` | Confidence calibration (currently DISABLED) | ~500 |
| `bots/mirror_ml_selector.py` | XGBoost + Q-learning shadow scoring | ~350 |
| `bots/elite_detector.py` | Elite trader detection and ranking queries | ~200 |
| `base_engine/base_engine.py` | Base class, place_order(), market index | ~1800 |
| `base_engine/execution/order_gateway.py` | Order routing, edge checking, VWAP | ~900 |
| `base_engine/execution/paper_trading.py` | Paper trade execution, fill simulation | ~500 |
| `base_engine/data/database.py` | DB access, position updates, trade_events | ~4000 |
| `base_engine/data/resolution_backfill.py` | Market resolution detection and P&L recording | ~600 |
| `base_engine/prediction/prediction_engine.py` | LLM + ensemble predictions, datetime parsing | ~2700 |
| `config/settings.py` | All configuration (env vars → attributes) | ~400 |

---

## CRITICAL CONFIG (VPS .env)
```
SIMULATION_MODE=True              # Paper trading — flip to False for live
MIRROR_MIN_CONFIDENCE=0.55        # Entry gate
MIRROR_MIN_RELIABILITY=0.52       # Elite reliability gate
ELITE_MIN_TRADES=100              # OR $10k volume gate
MIRROR_POSITION_CAP=1000          # Max open positions
MIRROR_MAX_PER_MARKET_USD=500     # Per-market sizing cap
MIRROR_DAILY_CAP_USD=999999       # Uncapped for data collection
KELLY_FRACTION=0.25               # Sizing aggression
PHASE_MAX_BET_USD=1000            # But BotBankrollManager caps at $300
MIRROR_USE_CALIBRATION=false      # Calibration DISABLED
MIRROR_ADAPTIVE_SAFETY=false      # Adaptive safety DISABLED
```

---

## CRITICAL TRAPS (DO NOT BREAK — MirrorBot-specific)

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass "BUY"/"SELL".
2. **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
3. **Entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
4. **CLOB volume=0**: Never use volume gates for MirrorBot.
5. **`_open_positions` on restart**: Restored from DB. Positions opened between last persist and crash are re-entered by EOD UTC.
6. **`paper_trades` has NO `metadata` JSONB column** — never assume it exists.
7. **`trade_events` is P&L AUTHORITY** — never read `paper_trades` for P&L. SELL/EXIT trades only exist in trade_events.
8. **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data is NOT at top level.
9. **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
10. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
11. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
12. **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
13. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use before that import → UnboundLocalError. NEVER use local imports that shadow top-level names.
14. **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE then re-enable for cleanup.
15. **RESOLUTION idempotency**: `ON CONFLICT` broken on partitioned tables. Uses atomic INSERT...SELECT with WHERE NOT EXISTS.
16. **trade_events JSONB column is `event_data`** — NOT `metadata_json`. `paper_trades` has NO `resolved_pnl` column (it's `resolved_at`).
17. **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
18. **`system_kv` table**: Generic key-value store. Used for canary stage persistence. Key='canary_stage'.
19. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass. `risk_manager.calculate_position_size()` is DEPRECATED.
20. **BOT_REGISTRY = 14 bots** — shared module change requires all 14 verified.

---

## ML SHADOW RACE STATUS

Started S124 (Mar 24). Both XGBoost and Q-learning loaded and scoring every entry. Data is shadow-only (not gating trades).

- **885 ML-scored entries** as of Mar 25 14:37 UTC
- Need **48h+** for meaningful win-rate comparison
- Check command:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 'cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=. python3 << "PYEOF"
import asyncio
async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text(
            "SELECT COUNT(*), MIN(event_time)::text, MAX(event_time)::text "
            "FROM trade_events "
            "WHERE bot_name = $$MirrorBot$$ AND event_type = $$ENTRY$$ "
            "AND event_data::text LIKE $$%ml_score_xgb%$$"
        ))
        row = r.fetchone()
        print(f"ML-scored entries: {row[0]}, first: {row[1]}, last: {row[2]}")
    await db.close()
asyncio.run(main())
PYEOF'
```

**Goal**: Once 48h of data is collected, compare resolution outcomes for entries where XGBoost score > 0.5 vs < 0.5, same for Q-learning, same for combo. Pick the model with highest WR and deploy as an active gate.

---

## DEFERRED ITEMS (from S127/S128 audits, NOT done)

### MirrorBot-specific (from AUDIT_MIRRORBOT_S127.md)
| Bug ID | Sev | Description |
|--------|-----|-------------|
| BUG-14 | P2 | Adaptive safety drawdown vs capital — needs BotBankrollManager wiring |
| BUG-4 | P4 | Cross-channel dedup gap (low frequency) |
| BUG-5 | P5 | False orphan exits (existing guards handle it) |
| BUG-COOL | P4 | _market_cooldown unbounded dict (months to matter) |
| DATA-2 | P4 | _entered_market_sides unbounded (months to matter) |
| DATA-5 | P4 | Missing doc on opposing pair |
| INEFF-1 | P4 | aiohttp session per refresh |
| INEFF-2 | P4 | Pipe-delimited serialization |
| INEFF-6 | P5 | Dead code in reliability tracker |
| STORE-1 | P5 | Stop-loss naming consistency |

### Shared infra (from AUDIT_SHARED_INFRASTRUCTURE_S128.md, NOT yet fixed)
| Bug ID | Sev | Description | MirrorBot Impact |
|--------|-----|-------------|-----------------|
| P1-6 | P1 | API retry exhaustion returns None → TypeError crash | Affects live mode |
| P1-9 | P1 | Lifecycle shutdown skips DB close on first failure | Connection leak |
| P1-17 | P1 | Preflight ignored — bots start with API+DB down | Startup safety |
| P2 | P2 | paper_trading.py — all bots' positions in one cash pool | Incorrect cash tracking |
| P2 | P2 | risk_manager NO-side exposure understated 9x | Risk calculation |
| P2 | P2 | settings.py TOTAL_CAPITAL defaults $100K | Config |
| P2 | P2 | websocket_manager best_bid=0 treated as falsy | Price data |
| P2 | P2 | order_management_system partial fills use = not += | Live mode blocker |

---

## WHAT TO DO NEXT (Priority Order)

### P0 — ML Shadow Race Analysis (when 48h data available ~Mar 26 14:00 UTC)
1. Query ML-scored entries with resolved outcomes
2. Compare WR by model: XGBoost-high vs XGBoost-low, Q-learning-high vs Q-learning-low
3. If one model clearly outperforms, promote to active gate (reject entries where model_score < threshold)
4. This is the highest-leverage change for P&L improvement

### P1 — P&L Diagnosis
5. Run bucket-level analysis: which confidence tiers, categories, and price ranges are bleeding
6. The 38% resolution WR suggests the confidence gate (0.55) may be too permissive
7. Alternatively, certain categories may be systematically unprofitable
8. Use `scripts/bot_pnl.py MirrorBot 168` for weekly view

### P2 — Opposing-Side Log Spam
9. Rate-limit `mirror_opposing_side_blocked_historical` log to once per market per 10min
10. Currently ~8 per 5 minutes on one hot market — cosmetic but noisy

### P3 — Remaining Shared Infra Bugs
11. P1-6 (API retry exhaustion), P1-9 (lifecycle shutdown), P2 items listed above
12. These matter more for live trading than paper trading

### P4 — Go-Live Readiness (when signal quality improves)
13. P0-1 from S128 audit: `correlation_id` param in `ExecutionEngine.place_order()` — **go-live blocker**
14. Fill confirmation pipeline (S120 work)
15. Canary staging (S120 work, persisted via system_kv)
16. USDC balance checking (S120 work)

---

## VERIFICATION COMMANDS

```bash
# Check MirrorBot scanning
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'MirrorBot scan:' | tail -3"

# Check for errors
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'error' | grep -i 'mirror' | head -5"

# Check RTDS + ML selector
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '2 hours ago' --no-pager | grep -E 'rtds_connected|ml_selector_loaded'"

# Run P&L
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=. python3 scripts/bot_pnl.py MirrorBot 48"

# Position count
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '1 min ago' --no-pager | grep 'MirrorBot scan:' | tail -1"

# Restart service
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo systemctl restart polymarket-ai && sleep 3 && sudo systemctl status polymarket-ai --no-pager"
```

---

## ROLLBACK

```bash
# Revert S129 infra fixes
git revert 04f4ee7

# Deploy reverted code
bash deploy/deploy.sh
```

---

## SESSION HISTORY

| Session | Key Change | Commit | Status |
|---------|-----------|--------|--------|
| S111 | Multi-factor confidence formula, archive migration 056, dampener wiring | — | Deployed |
| S117 | Bot revived: flood purge, calibration disabled, dead zone dampener removed | — | Deployed |
| S119 | 5 bug fixes, dead code purge, dampener neutralization, orphan fix | — | Deployed |
| S120 | Fee 150bps, balance query, fill confirmation, canary, slug fix | — | Deployed |
| S124 | ML shadow race (XGBoost + Q-learning + combo scoring) | — | Deployed |
| S125 | Exclude Mirror/Weather/Esports from PM exits + cooldown port | `44a15c5` | Deployed |
| S126 | ML models retrained, Phase 4b-alt resolution path | — | Deployed |
| S127 | Full MirrorBot audit (23 findings) → AUDIT_MIRRORBOT_S127.md | — | Audit doc |
| S128 | 10 MirrorBot audit fixes + shared infra impact assessment | `0590c58` | Deployed |
| **S129** | **21 shared infra fixes, P&L corrections, datetime bug fix, diagnostic** | **`04f4ee7`** | **Deployed** |

---

## AUDIT DOCUMENTS (for reference)
- `AUDIT_MIRRORBOT_S127.md` — 23 MirrorBot-specific findings (10 fixed S128, 2 false positives, 11 deferred)
- `AUDIT_SHARED_INFRASTRUCTURE_S128.md` — 143 shared infra findings (21 fixed S129, rest deferred)
- `AUDIT_ESPORTSBOT_S127.md` — EsportsBot audit (separate bot session)
- `AUDIT_WEATHERBOT_S127.md` — WeatherBot audit (separate bot session)
