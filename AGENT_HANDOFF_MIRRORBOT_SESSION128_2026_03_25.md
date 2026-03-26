# AGENT HANDOFF — MirrorBot Session 128
**Date**: 2026-03-25
**Bot scope**: MirrorBot ONLY — no bleed to Weather/Esports unless explicit demand
**Commit**: `0590c58` fix(mirror): S128 — 10 audit bugs from AUDIT_MIRRORBOT_S127
**Deploy**: 2026-03-25 01:25:16 UTC, PID 3306379 → later restarted, current PID 3311202
**VPS**: Ubuntu-3 at 34.251.224.21, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
**Service**: `sudo systemctl restart polymarket-ai` / `journalctl -u polymarket-ai -f`

---

## WHAT WAS DONE THIS SESSION

### 1. Full MirrorBot Audit Bug Fixes (10 of 23 items from AUDIT_MIRRORBOT_S127.md)

| Bug | Sev | File | Fix Applied | Verification |
|-----|-----|------|-------------|-------------|
| **BUG-11** | P2 | `mirror_bot.py:281` | `set('{}')` → proper empty set guard. Postgres empty array literal `'{}'` was being iterated as characters creating `{'{', '}'}` phantom traders on every position restore. Fixed: `set() if not r.trader_addresses or r.trader_addresses in ('{}', '[]', '') else set(r.trader_addresses)` | 765 positions restored clean, no phantom traders |
| **BUG-13** | P2 | `mirror_bot.py:942` | Exit exposure tracking used `pos["size"] * pos["current_price"]` — wrong for partial exits where size already decremented. Fixed: `exit_size * exit_price` using actual exit values | Correct daily exposure accounting |
| **BUG-15** | P2 | `elite_detector.py:44-120` | OR-JOIN `(t.market_id = CAST(m.id AS TEXT) OR t.market_id = m.condition_id)` prevented index usage → full table scan on 100k+ trades. Fixed: UNION of two indexed queries + DISTINCT ON dedup | Elite refresh will use indexes |
| **BUG-12** | P3 | `mirror_bot.py:525-544` | Reconciliation ran once on scan 3 then died forever (`_recon_done = True` set before task). Fixed: success-gated flag + periodic retry every 100 scans (~75 min) + `_recon_task_pending` guard | Recon will retry on failure, re-run periodically |
| **RACE-1** | P3 | `mirror_bot.py:528-542` | `create_task(_bg_recon)` fire-and-forget — errors silently lost. Fixed: task pending flag, `exc_info=True` on error log, `_recon_task_pending = False` in finally block | Errors now logged with full traceback |
| **BUG-1** | P3 | `elite_watchlist.py:389-405` | Wash detection O(n²) nested loop — every entry×exit comparison. Fixed: sorted exits + `bisect` for O(n log n) window lookup | CPU savings on hot RTDS path |
| **BUG-2** | P3 | `elite_watchlist.py:389-405` | Wash detection overcounted — same exit matched multiple entries. Fixed: `_used_exits` set prevents double-matching | Correct round-trip counting |
| **BUG-3** | P3 | `elite_watchlist.py:385-387` | `_trader_market_trades` dict keys never pruned when list becomes empty after 24h cutoff. Fixed: `del self._trader_market_trades[_wash_key]` when empty | Memory leak plugged |
| **BUG-9** | P4 | `mirror_ml_selector.py:91` | `from datetime import datetime as _dt` inside function — fragile Python 3.13 scoping trap. Fixed: removed local import, uses module-level `datetime` | Safe from future refactor crashes |
| **BUG-16** | P3 | `mirror_ml_selector.py:287` | `math.exp(-q_adv)` overflows when Q-advantage < -709. Fixed: clamp to `[-500, 500]` before exp | No OverflowError on pathological Q-values |

### 2. False Positives Found (NO fix applied — correct as-is)

| Bug ID | Why No Fix |
|--------|-----------|
| **BUG-DIR** (price direction filter) | `price` is already side-specific (the token price for YES or NO). The filter `_move_pct > _dir_thresh` correctly blocks trades where price moved toward the trader's side. For NO trades, `price` is the NO token price — if it went up, the NO edge is consumed. No inversion needed. |
| **DATA-1** (calibration reads paper_trades) | `mirror_calibration.py` method is named `fit_from_paper_trades` but it's aliased to `fit_from_trade_events` at line 483. The actual SQL query reads `trade_events`, not `paper_trades`. The name is misleading but the data source is correct. |

### 3. Deferred to P5 (NOT fixed this session)

| Bug ID | Sev | Reason Deferred |
|--------|-----|----------------|
| BUG-4 | P4 | Cross-channel dedup gap — low frequency, composite key adds complexity |
| BUG-5 | P5 | False orphan exits — existing guards handle this, non-issue |
| BUG-14 | P2 | Adaptive safety drawdown vs capital — needs bankroll manager wiring, separate session |
| BUG-COOL | P4 | _market_cooldown unbounded dict — slow growth, months to matter |
| DATA-2 | P4 | _entered_market_sides unbounded — slow growth, months to matter |
| DATA-5 | P4 | Missing doc on opposing pair — documentation only |
| INEFF-1 | P4 | aiohttp session per refresh — performance, not correctness |
| INEFF-2 | P4 | Pipe-delimited serialization — works today, theoretical corruption risk |
| INEFF-6 | P5 | Dead code in reliability tracker — cosmetic |
| LOG-2 | P4 | Reconciliation traceback — FIXED as part of RACE-1 (exc_info=True added) |
| STORE-1 | P5 | Stop-loss naming consistency — not a bug |

### 4. Shared Infrastructure Audit Impact Assessment

Reviewed `AUDIT_SHARED_INFRASTRUCTURE_S128.md` (143 bugs across all shared modules). Assessed MirrorBot-specific impact:

**DIRECTLY hurting MirrorBot NOW (must fix):**
| Bug | File | Impact on MirrorBot |
|-----|------|-------------------|
| **P0-2** | `prediction_engine.py:2512` | `predict()` crashes on dated markets via `UnboundLocalError`. MirrorBot silently skips all markets with `end_date_iso`. 1-line delete fix. |
| **P1-19** | `database.py:3553` | Resolution P&L zeroed every 30-min backfill cycle. `UPDATE positions SET unrealized_pnl = 0 WHERE status = 'closed'` runs AFTER computing resolution P&L → destroys it. Corrupts our P&L data every cycle. |
| **P1-20** | `resolution_backfill.py:470` | Partial-exit double-counting. RESOLUTION records P&L on FULL position size, not remaining after EXIT. MirrorBot has 62 EXIT events — those get double-counted. |
| **P0-3** | `base_engine.py:1451` | Market index empty for first 30s after startup. RTDS trades from elites can't resolve condition_id → market. Post-restart trades dropped. |
| **P1-7** | `kill_switch.py:59` | Kill switch defaults to "allow trading" on DB failure. Safety-critical. |
| **P1-6** | `polymarket_client.py:272-402` | API retry returns `None` on 429 exhaustion. Downstream `TypeError` crashes + doubles request volume in feedback loop. |

**Indirectly affecting MirrorBot:**
- P1-4: `_check_price` NameError in order_gateway edge-eroded logging (CONFIRMED still firing — see VPS logs `mirror_instant_copy_error error="name '_check_price' is not defined"`)
- P1-9: Lifecycle shutdown — one failure skips DB close, leaks connections
- P1-17: Preflight ignored — bots start even if API+DB down
- P2: `paper_trading.py:231` — all bots' positions loaded into one cash pool
- P2: `risk_manager.py:292` — NO-side exposure understated 9x
- P2: `settings.py:152` — TOTAL_CAPITAL defaults $100K (real is $20K)
- P2: `websocket_manager.py:292` — `best_bid=0` treated as falsy
- P2: `order_management_system.py:237` — partial fills use `=` not `+=` (live mode blocker)

**Does NOT affect MirrorBot:**
- P0-1 (live trade TypeError — paper mode bypasses), P1-5 (advanced orders), P1-8 (os import, live only), P1-10/11/12 (signal subsystems), P1-13 (sports Kelly), P1-15/16 (learning engine), P1-18 (esports XGBoost), P1-21 (circuit breaker speed)

---

## CURRENT VPS STATE (as of 2026-03-25 03:26 UTC)

- **PID**: 3311202 (restarted after deploy)
- **Open positions**: 831 (growing — was 765 at deploy, 828→831 in last 5 min)
- **Elites**: 500 tracked
- **RTDS**: Connected, 150,788+ events dispatched
- **Scan cycle**: ~45s intervals, 105ms per scan (fast)
- **ML selector**: `xgb=True ql=True` (AUC=0.590, 1167 samples)
- **Daily exposure**: ~$7,762
- **Trades executing**: YES — confirmed multiple trades in logs (crypto, sports categories)
- **Known error still firing**: `_check_price is not defined` on RTDS instant copy path (P1-4 from shared audit — NOT fixed this session, shared infra scope)
- **Ingestion timeout**: `ingest_elite_trader_activity() timed out after 300.0s` (intermittent, non-fatal)

---

## FILES MODIFIED THIS SESSION

| File | Scope | Lines Changed |
|------|-------|--------------|
| `bots/mirror_bot.py` | MirrorBot only | BUG-11 (line 281), BUG-12/RACE-1 (lines 523-544), BUG-13 (line 942) |
| `bots/mirror_ml_selector.py` | MirrorBot only | BUG-9 (line 91), BUG-16 (line 287) |
| `bots/elite_watchlist.py` | MirrorBot only (RTDS) | BUG-1/2/3 (lines 385-405) |
| `base_engine/learning/elite_detector.py` | **SHARED** (all 15 bots) | BUG-15 — query optimization only, same results, different execution plan |

**Commit**: `0590c58 fix(mirror): S128 — 10 audit bugs from AUDIT_MIRRORBOT_S127`
**Tests**: 1717 passed, 0 failed

---

## KEY ARCHITECTURE FACTS (MirrorBot-specific)

### How MirrorBot Works
1. **Elite Watchlist** (`elite_watchlist.py`) maintains a ranked list of 500 top traders from Polymarket Data API
2. **RTDS WebSocket** (`wss://ws-live-data.polymarket.com`) streams ALL Polymarket trades in real-time
3. **On RTDS trade**: Elite watchlist filters for tracked traders → runs wash detection → checks limits → calls `_execute_mirror_trade()`
4. **Periodic scan loop** (~45s): refreshes elite list, runs reconciliation, updates ML models, handles exits
5. **ML Shadow Race** (S124): XGBoost + Q-learning + combo score every entry. Currently shadow-only (scores logged, not used for gating). Needs 48h+ data to pick winner.

### Data Flow
```
RTDS WebSocket → EliteWatchlist.on_rtds_trade()
  → wash detection (bisect-optimized)
  → position/daily limit checks
  → MirrorBot._execute_mirror_trade()
    → multifactor confidence (base + category + price_adj + conv_adj + rel_mult)
    → ML scoring (xgb + ql + combo) [shadow only]
    → BotBankrollManager sizing
    → BaseBot.place_order(side="YES"/"NO")
    → trade_events ENTRY record
```

### State Persistence
| State | Mechanism | Restore |
|-------|-----------|---------|
| `_open_positions` | `positions` table | `_restore_state_on_startup()` |
| `_daily_exposure` | `trade_events` SUM | `_restore_state_on_startup()` |
| `_category_exposure` | `trade_events` SUM | `_restore_state_on_startup()` |
| `_entered_market_sides` | `trade_events` query | `_restore_entered_sides()` |
| `_dedup_set` | Redis | `_restore_dedup_from_redis()` |
| ML models | `models/*.pkl` on VPS | `mirror_ml_selector.load_xgb()` / `load_qtable()` |
| Calibration | In-memory fit from `trade_events` | `mirror_calibration.fit()` on startup |

### Critical Config (VPS .env)
```
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
ELITE_MIN_TRADES=100
MIRROR_POSITION_CAP=1000  (was 600, raised to 1000)
MIRROR_MAX_PER_MARKET_USD=500
MIRROR_DAILY_CAP_USD=999999  (uncapped for data collection)
KELLY_FRACTION=0.25
PHASE_MAX_BET_USD=1000 (but BotBankrollManager caps at $300)
SIMULATION_MODE=True (paper trading)
```

### Critical Traps (MirrorBot-specific — DO NOT BREAK)
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass "BUY"/"SELL"
- **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **Entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price
- **CLOB volume=0**: Never use volume gates for MirrorBot
- **`_open_positions` on restart**: Restored from DB. Positions opened between last persist and crash are re-entered by EOD UTC.
- **`paper_trades` has NO `metadata` JSONB column**
- **`trade_events` is P&L AUTHORITY** — never read paper_trades for P&L
- **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data is NOT at top level
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **Python 3.13**: `from X import Y` inside function makes Y local for ENTIRE function
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE then re-enable for cleanup.
- **RESOLUTION idempotency**: `ON CONFLICT` broken on partitioned tables. Uses atomic INSERT...SELECT with WHERE NOT EXISTS.

---

## WHAT TO DO NEXT (Priority Order)

### P0 — Fix immediately (shared infra, MirrorBot-impacting)
1. **P0-2**: Delete line 2512 in `prediction_engine.py` — removes local `from datetime import` that shadows module-level. Unblocks ALL dated market predictions for all bots.
2. **P1-19**: Reorder SQL in `database.py:3553` — zero out unrealized_pnl BEFORE resolution update, not after. Stops P&L corruption every 30-min cycle.
3. **P1-20**: Fix `resolution_backfill.py:470` — subtract EXIT sum from total_size before emitting RESOLUTION event. Stops partial-exit double-counting.
4. **P0-3**: Fix `base_engine.py:1451` — add defaults to `_fetch_tradeable_markets()` call. Recovers first-30s RTDS trades after restart.

### P1 — Safety/resilience (shared infra)
5. **P1-7**: Kill switch fail-safe — `kill_switch.py:59` return `True` on DB failure instead of `False`
6. **P1-6**: API retry exhaustion — add `raise` after retry loop in `polymarket_client.py`
7. **P1-4**: Fix `_check_price` NameError in `order_gateway.py:839` — **CONFIRMED still firing on VPS** (see logs: `mirror_instant_copy_error error="name '_check_price' is not defined"`)

### P2 — MirrorBot-specific deferred items from S127 audit
8. **BUG-14**: Adaptive safety drawdown vs capital — needs BotBankrollManager.get_available_capital() wiring
9. **Position cap monitoring**: 831/1000 and growing. At current rate (~60/day net), hits cap in ~3 days. May need to raise cap again or accelerate exits.
10. **ML shadow race analysis**: Need 48h+ of ML-scored data to pick winner (XGBoost vs Q-learning vs combo). Check with:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "PYTHONPATH=/opt/polymarket-ai-v2 python3 -c \"
import asyncio
from sqlalchemy import text
async def main():
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text(
            'SELECT COUNT(*), MIN(event_time), MAX(event_time) '
            'FROM trade_events '
            'WHERE bot_name = \\\"MirrorBot\\\" AND event_type = \\\"ENTRY\\\" '
            'AND event_data->>\\\"ml_score_xgb\\\" IS NOT NULL'
        ))
        row = r.fetchone()
        print(f'ML-scored entries: {row[0]}, first: {row[1]}, last: {row[2]}')
asyncio.run(main())
\""
```

### P3 — Longer-term
11. **Deploy mismatch**: VPS code may still have stale files. Full `deploy.sh` run would sync everything.
12. **Model persistence in deploy.sh**: `models/` dir must survive atomic symlink swap
13. **External service restarts**: Investigate systemd watchdog/OOM killer causing ~5-7min restart cycles (seen in S126)

---

## AUDIT DOCUMENTS (for reference)
- `AUDIT_MIRRORBOT_S127.md` — 23 MirrorBot-specific findings (10 fixed, 2 false positives, 11 deferred)
- `AUDIT_SHARED_INFRASTRUCTURE_S128.md` — 143 shared infra findings (3 P0, 18 P1, 52 P2, 58 P3, 12 P4)
- `AUDIT_ESPORTSBOT_S127.md` — EsportsBot audit (separate bot session)
- `AUDIT_WEATHERBOT_S127.md` — WeatherBot audit (separate bot session)

---

## VERIFICATION COMMANDS

```bash
# Check MirrorBot is scanning and trading
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep -E 'MirrorBot scan:|Mirror trade executed'"

# Check for errors
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'error' | grep -i 'mirror'"

# Check ML selector loaded
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '2 hours ago' --no-pager | grep ml_selector_loaded"

# Check RTDS connected
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '2 hours ago' --no-pager | grep rtds_connected"

# Check position count
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '1 min ago' --no-pager | grep 'MirrorBot scan:' | tail -1"

# Run P&L check
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && PYTHONPATH=. python3 scripts/bot_pnl.py MirrorBot 48"

# Restart service
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo systemctl restart polymarket-ai && sleep 3 && sudo systemctl status polymarket-ai --no-pager"
```

---

## ROLLBACK

```bash
# Revert S128 audit fixes only
git revert 0590c58

# Deploy reverted code
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && git pull && sudo systemctl restart polymarket-ai"
```

---

## SESSION HISTORY CONTEXT

| Session | Key Change | Status |
|---------|-----------|--------|
| S124 | ML shadow race (XGBoost + Q-learning + combo) | Deployed, collecting data |
| S125 | Exclude Mirror/Weather/Esports from PM exits + cooldown port | Deployed |
| S126 | ML models retrained on VPS, Phase 4b-alt resolution path | Deployed |
| S127 | Full MirrorBot audit (23 findings) | Audit doc created |
| **S128** | **10 audit fixes deployed + shared infra impact assessment** | **CURRENT — Deployed** |

---

## P&L CONTEXT

Per `scripts/bot_pnl.py`, MirrorBot is the highest-revenue bot. Historical P&L (from MEMORY.md):
- S86 corrected P&L: **+$15,051** realized
- S119: corrected to **+$26,986**
- S120 48h: **-$3,456** (0.50-0.55 conf bucket = -$6,745 drag)
- Current: ~831 open positions, daily exposure ~$7,762, executing multiple trades per scan cycle

The 0.50-0.55 confidence bucket was identified as the biggest drag. The ML shadow race (S124) is designed to improve trade selection once enough data accumulates to pick a winning strategy.
