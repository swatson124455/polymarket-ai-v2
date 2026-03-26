# AGENT HANDOFF — MirrorBot Session 109 (2026-03-19)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`). Going live is flipping a boolean. Paper trading IS production — every feature must work identically.

**MirrorBot** is the highest-performing bot (+$20,277 all-time realized). It copy-trades elite whale traders in real-time via RTDS WebSocket firehose. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99/S100/S101/S102/S103/S109)
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

### VPS Details
- **Host**: `ubuntu@34.251.224.21`
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **SSH opts**: `-o ConnectTimeout=10 -o StrictHostKeyChecking=no`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Deploy**: `bash deploy/deploy.sh` from local repo root (tar → scp → extract → symlink swap → restart → health check)
- **Real .env**: `/opt/pa2-shared/.env` (loaded by systemd `EnvironmentFile`). NOT `/opt/polymarket-ai-v2/.env`.
- **DB access**: `sudo -u postgres psql -d polymarket` (direct connection). DO NOT use pgbouncer (port 6432) — it hangs.
- **Logs**: `sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager`
- **Symlink**: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/<deploy_timestamp>`

---

## 2. SESSION 109 CHANGES — 5 ROOT-CAUSE P&L BOOKKEEPING FIXES

### Problem Statement
MirrorBot's P&L reporting was unreliable due to 5 compounding bookkeeping bugs accumulated over weeks. The headline +$20K all-time figure was unauditable. S109 fixed all 5 root causes.

### What We Found (Audit Results)
| Bug | Impact | Root Cause |
|-----|--------|-----------|
| 276 resolved markets missing RESOLUTION events | Uncounted P&L | `traded_markets.condition_id` was NULL → backfill couldn't emit events |
| Confidence gate was dead code | Trades at 9% win rate executing | S103 added gate but never committed/deployed |
| 455 markets with duplicate ENTRY events (up to 9x) | Inflated entry count | No same-side dedup — only opposing-side existed |
| 122 closed positions with stale `unrealized_pnl` | $2,716 phantom value | `_update_current_prices()` skips closed positions |
| 565/990 RESOLUTION events with `size=0` | Can't reconstruct positions | Hardcoded `size=0.0` in Phase 4b |

### Fix 1 (P0): Confidence Gate — Commit & Deploy
- **Bug**: S103 added confidence gate in `_execute_mirror_trade()` (lines 1274-1281) but changes were **never committed**. `self.min_confidence` was dead code.
- **Fix**: Committed existing working-tree changes. Gate at 0.45 now enforced.
- **File**: `bots/mirror_bot.py` line 41 (`self.min_confidence = getattr(settings, "MIRROR_MIN_CONFIDENCE", 0.45)`) + lines 1274-1281
- **Verification**: `mirror_low_confidence` log messages firing heavily — blocking trades at 0.38-0.39 confidence

### Fix 2 (P1): condition_id Enrichment + Phase 4b Gate
**Three parts:**

**Part A — Enrich condition_id on trade insert** (prevents future gaps):
- **File**: `base_engine/data/database.py` lines 3056-3076
- **Change**: `INSERT INTO traded_markets` now LEFT JOINs `markets` table to populate `condition_id`
- **SQL**: `LEFT JOIN markets m ON m.condition_id = :market_id OR CAST(m.id AS TEXT) = :market_id`
- On conflict, also updates: `condition_id = COALESCE(traded_markets.condition_id, EXCLUDED.condition_id)`

**Part B — Widen Phase 4b gate** (fixes existing gaps):
- **File**: `base_engine/data/resolution_backfill.py` line 413
- **Before**: `if paper_updated > 0 and hasattr(db, "insert_trade_event"):`
- **After**: `if (paper_updated > 0 or updated > 0) and hasattr(db, "insert_trade_event"):`
- **Why**: RESOLUTION events now emit whenever ANY market resolved in Phase 3, not only when paper_trades updated

**Part C — One-time backfill** (recovered the 276):
- Ran SQL: `UPDATE traded_markets SET condition_id = m.condition_id FROM markets m WHERE ...` → 3,948 enriched
- NULL'd `resolved_at` on 276 orphaned markets to re-queue for backfill processing
- 28 remain NULL (10 WeatherBot numeric IDs + 18 orphan condition_ids without market matches — benign)

### Fix 3 (P2): Same-Side Entry Dedup
- **Bug**: Only opposing-side dedup existed. 455 markets had 2-9x duplicate entries.
- **File**: `bots/mirror_bot.py` lines 1119-1131
- **Fix**: Added same-side dedup check immediately after opposing-side check:
```python
# S109 Same-side dedup: reject BUY if we already hold the SAME side on this market.
if not _is_sell:
    _side_upper = str(side).upper()
    _market_prefix = f"{market_id}:"
    for _pk, _pv in self._open_positions.items():
        if _pk.startswith(_market_prefix) and str(_pv.get("side", "")).upper() == _side_upper:
            logger.debug("mirror_same_side_blocked market=%s side=%s", str(market_id)[:16], side)
            return False
```
- **Blast radius**: MirrorBot only. Runs before `place_order()`, prevents entry entirely.

### Fix 4 (P3): Zero Stale unrealized_pnl on Closed Positions
- **Bug**: 122 closed positions had `unrealized_pnl != 0` ($2,716 total phantom value).
- **File**: `base_engine/data/database.py` lines 3429-3441 (`backfill_positions_resolution`)
- **Fix**: Added cleanup SQL after main update: `UPDATE positions SET unrealized_pnl = 0 WHERE status = 'closed' AND unrealized_pnl != 0`
- **One-time**: Ran SQL on VPS — 1,739 cleaned → 0 remaining
- **Prevention**: Runs every backfill cycle (idempotent)

### Fix 5 (P4): Populate Size on RESOLUTION Events
- **Bug**: 565/990 RESOLUTION events had `size=0.0` (hardcoded).
- **File**: `base_engine/data/resolution_backfill.py` lines 418-421, 442
- **Fix**: Added `SUM(pt.size) AS total_size` to Phase 4b query, changed event emission from `size=0.0` to `size=float(row[5]) if len(row) > 5 and row[5] is not None else 0.0`
- **Historical**: 569 old events still have size=0 (immutability trigger prevents UPDATE). Cosmetic — P&L is correct.

### Post-S109: Position Cap Raised
- **Problem**: MirrorBot was stuck at 200/200 cap — zero new trades entering.
- **Fix**: Changed `MIRROR_MAX_CONCURRENT_POSITIONS=200` → `400` in `/opt/pa2-shared/.env`
- **Result**: Bot immediately started entering new trades, went from 199 → 222 open positions within minutes.

### Commit & Deploy
- **Commit**: `edb0032` — `fix(mirror): S109 — 5 root-cause P&L bookkeeping fixes`
- **Deploy**: `20260319_131015` — successful, all bots scanning
- **Tests**: All 1631+ passed

### Files Modified (S109)
| File | Changes |
|------|---------|
| `bots/mirror_bot.py` | Confidence gate (commit S103 changes), same-side dedup (new) |
| `base_engine/data/database.py` | condition_id enrichment in `insert_paper_trade()`, stale uPnL cleanup in `backfill_positions_resolution()` |
| `base_engine/data/resolution_backfill.py` | Phase 4b gate widened, RESOLUTION size populated |
| `tests/unit/test_mirror_bot_logic.py` | 3 tests updated (removed pre-populated same-side positions), 1 new test (`test_same_side_dedup_blocks_reentry`) |

---

## 3. CURRENT STATE (Post-S109 Deploy)

### P&L Snapshot (live data at deploy time)
| Metric | Value |
|--------|-------|
| All-time ENTRY events | 3,833 |
| All-time EXIT events | 705 (+$6,200 realized) |
| All-time RESOLUTION events | 997 (+$14,076 realized) |
| **All-time realized P&L** | **+$20,277** |
| Open positions | 222 (was 200, cap raised to 400) |
| Today's entries | 55 ($27,809 size) |
| Today's P&L | -$662 (11 trades, 27.3% WR) |

**Important**: The $20K figure includes historical bookkeeping errors from prior sessions. Honest estimate range: $17K-$23K. The S109 fixes make FUTURE P&L tracking accurate. Historical data has ~276 missing RESOLUTION events being recovered via backfill.

### Config (live VPS values — `/opt/pa2-shared/.env`)
```
MIRROR_MIN_CONFIDENCE=0.45          (S103/S109 — TRIAL, enforced)
MIRROR_MAX_CONCURRENT_POSITIONS=400 (S109 — was 200)
MIRROR_ADAPTIVE_SAFETY=false
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_USE_CALIBRATION=true
MIRROR_USE_CONFORMAL=true
WATCHLIST_ENABLED=true

# Other bot defaults (config/settings.py):
MIRROR_MAX_PER_MARKET=400
MIRROR_MAX_POSITIONS=1000           (risk_manager cap, separate from concurrent)
MIRROR_HARD_MIN_PRICE=0.10
MIRROR_HARD_MAX_PRICE=0.95
MIRROR_FAVORITE_DAMPENER=0.40       (>=70c prices)
MIRROR_DEAD_ZONE_DAMPENER=0.50      (30-50c prices)
MIRROR_MIN_TRADE_USD=50.0
kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K
```

### Trade Execution Latency (verified post-deploy)
| Stage | Latency |
|-------|---------|
| Risk check | 0.0ms |
| Trade coordinator | 0.4ms |
| Order execution (paper) | 42.1ms |
| **Total RTDS signal → trade placed** | **83.1ms** |
| Scan cycle | 12-93ms |

### Remaining Data Issues (post-fix)
| Issue | Count | Status |
|-------|-------|--------|
| Null condition_id in traded_markets (Mirror) | 28 | Benign — orphan condition_ids with no market match |
| Historical duplicate ENTRY markets | 455 | Historical only — same-side dedup prevents new ones |
| RESOLUTION events with size=0 | 569 | Historical only — new events have real size |
| Stale unrealized_pnl on closed positions | **0** | ✅ Fixed + prevention active |
| Re-queued markets for backfill (276 orphans) | 276 | Will process in next backfill cycle |

### Validation Pipeline (26 checks — entry path)
1. Wash trader filter
2. Hard price bounds [0.10, 0.95]
3. Circuit breaker
4. Position cap (400 — S109 raised from 200)
5. Daily exposure cap
6. Category cap ($40K)
7. Market blocklist (in-memory)
8. Per-market cooldown (30min)
9. Category blocklist
10. **Opposing-side dedup** (pre-S109)
11. **Same-side dedup** (S109 new)
12. Inactive market filter
13. Near-resolution filter (4h)
14. Current price correction (use market price, not trader fill)
15. Slippage cap (8%)
16. Reliability gate (LR >= 1.0)
17. Domain drift penalty (0.50x if < 10 trades in category)
18. Signal enhancements (skipped by default)
19. FTS calibration (domain + horizon)
20. **Confidence gate (>= 0.45 TRIAL)** (S103/S109)
21. Kelly sizing via BotBankrollManager
22. Single price dampener (30-50c: 0.50x, >=70c: 0.40x)
23. Per-market USD cap ($400)
24. Daily remaining cap
25. Min trade USD ($50)
26. Order execution + paper trade recording

### Confidence Flow (how the number is calculated)
1. **EliteWatchlist** (`elite_watchlist.py:385`): `confidence = min(0.70, 0.55 + efficiency_bonus)` — hardcoded base 0.55 + efficiency (0-0.15). Range: 0.55-0.70.
2. **Domain drift** (`mirror_bot.py:1216`): If trader has < 10 resolved trades in this category → `confidence *= 0.50`. Halves to ~0.275.
3. **Calibration stack** (`mirror_bot.py:1253`): Domain/horizon bias adjustment. Bumps ~0.275 up to ~0.38.
4. **Confidence gate** (`mirror_bot.py:1278`): `if confidence < self.min_confidence: return False`. Set to 0.45.

### Confidence × Side Bracket Data (from S103 diagnostic)
| Confidence | Resolved | Win Rate | P&L | $/position |
|---|---|---|---|---|
| **30-40%** | 11 | **9%** | **-$1,725** | **-$157** |
| **40-50%** | 11 | **18%** | **-$585** | **-$53** |
| **50-55%** | 337 | **36%** | **+$8,696** | **+$26** |
| **55%+** | 587 | **48%** | **+$7,415** | **+$13** |

**Note for next session**: User requested confidence × YES/NO side brackets in the handoff. Not yet collected — run `scripts/diag_confidence.py` to generate.

---

## 4. RESOLUTION BACKFILL PIPELINE (CRITICAL SHARED CODE)

Understanding this is essential — it's the pipeline that converts market outcomes into P&L records.

### Flow
```
Phase 2a: SELECT unresolved markets from traded_markets (WHERE resolved_at IS NULL)
Phase 3:  Check Polymarket API for resolution → UPDATE traded_markets SET resolved_at, resolution
Phase 4:  UPDATE paper_trades SET resolved_at, resolution, realized_pnl WHERE market matches
Phase 4b: Emit RESOLUTION event to trade_events (S109: gate widened to fire on Phase 3 OR Phase 4)
Phase 5:  Backfill positions resolution (unrealized_pnl + status)
```

### S109 Changes to Pipeline
1. **Phase 4b gate**: `if (paper_updated > 0 or updated > 0)` — fires when ANY market resolved, not just when paper_trades updated
2. **Phase 4b query**: Now includes `SUM(pt.size) AS total_size` for real position size
3. **Phase 4b emission**: `size=float(row[5])` instead of `size=0.0`
4. **Phase 5 addition**: `UPDATE positions SET unrealized_pnl = 0 WHERE status = 'closed' AND unrealized_pnl != 0` runs every cycle

### Key Files
- `base_engine/data/resolution_backfill.py` — Main backfill logic (Phases 2a-4b)
- `base_engine/data/database.py` — `backfill_positions_resolution()` (Phase 5), `insert_paper_trade()` (condition_id enrichment)
- `base_engine/data/database.py` — `insert_trade_event()` — atomic INSERT...SELECT with WHERE NOT EXISTS for RESOLUTION events (dedup)

### Backfill Timing
- **Mini backfill**: Every 30 minutes (checks recently-unresolved markets)
- **Full backfill**: Daily (scans all unresolved markets)
- **On restart**: Runs immediately as part of startup

---

## 5. OPEN ITEMS

| Priority | Item | Notes |
|----------|------|-------|
| **P1** | **TRIAL: Review 0.45 confidence gate** | Run `scripts/diag_confidence.py`. Check if <45% trades blocked, volume acceptable, P&L improving. Data shows gate is firing heavily (0.38-0.39 blocked). |
| **P1** | **276 re-queued markets** | NULL'd `resolved_at` to re-queue. Should process in next backfill cycle. Verify: `SELECT COUNT(*) FROM traded_markets WHERE resolved_at IS NULL AND bot_names LIKE '%Mirror%'` — should decrease by ~276. |
| P2 | Confidence × YES/NO side brackets | User requested in S109. Not yet collected. Run diag_confidence.py with side breakdown. |
| P3 | NO vs YES WR asymmetry | 72% vs 39% WR. Confirmed, monitor before config change. |
| P3 | ~570 open positions at 30-40% confidence | Pre-gate entries from before S103. Resolving naturally or hitting stop-loss. Monitor. |
| P3 | WeatherBot 697 duplicate entry markets | Same root cause as MirrorBot's 455 but in different bot. Out of scope for MirrorBot sessions. |
| P4 | Hold-time analysis | <24h positions net losers (-$1,370). Consider minimum hold time. |
| P4 | Historical 569 RESOLUTION events with size=0 | Immutability trigger prevents UPDATE. Cosmetic — P&L correct. |
| P5 | RESOLUTION NULL token_id | RESOLUTION events store `token_id=NULL`. Any P&L-by-entry query MUST join on `(market_id, side)` only. |

---

## 6. CRITICAL TRAPS (DO NOT BREAK)

### MirrorBot-Specific
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- `trade_events` is P&L authority, NOT `paper_trades`.
- RTDS envelope: unwrap `data.get("payload", data)`.
- RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- `whale_trades` requires explicit `await session.commit()` (S101 fix).
- MirrorBot entry price: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
- `_open_positions` on restart: MirrorBot clears in-memory positions; re-enters by EOD UTC.
- RESOLUTION events have `token_id=NULL` — any join to ENTRY events must use `(market_id, side)` only.
- `self.min_confidence` IS NOW ENFORCED (S103/S109) — changing `MIRROR_MIN_CONFIDENCE` in `.env` actually affects trade flow.
- **Same-side dedup is active (S109)** — MirrorBot will NOT enter duplicate positions on same market+side.
- `traded_markets.bot_names` is TEXT column (not array), use `LIKE '%BotName%'` not `= ANY()`.

### System-Wide
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- `asyncpg DATE columns`: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
- `asyncpg timestamp columns`: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`.
- `paper_trades` has NO `metadata` JSONB column.
- Positions table: NO `closed_at`, NO `updated_at` — only `opened_at` + `status`. Use `source_bot` NOT `bot_name`.
- `prediction_log` columns: NO `rejection_reason`. Use `trade_executed` (bool).
- `trade_events` JSONB column is `event_data` NOT `metadata_json`.
- `trade_events` immutability trigger: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must `DISABLE TRIGGER` then re-enable.
- RESOLUTION event idempotency: `ON CONFLICT` is BROKEN on partitioned tables. Uses atomic INSERT...SELECT with WHERE NOT EXISTS.
- Python 3.13 scoping: `from X import Y` inside function makes `Y` local for ENTIRE function.
- BOT_REGISTRY=14 bots — shared module change requires all 14 verified.
- VPS .env is at `/opt/pa2-shared/.env` NOT `/opt/polymarket-ai-v2/.env`. Systemd reads `EnvironmentFile=/opt/pa2-shared/.env`.

---

## 7. KEY FILES

| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Core MirrorBot logic (~1380 lines). Entry validation, RTDS dispatch, position management |
| `bots/elite_watchlist.py` | RTDS trade matching + confidence calculation. 500-whale watchlist. |
| `bots/mirror_calibration.py` | FTS domain/horizon calibration |
| `base_engine/base_engine.py` | Shared engine (market index, order gateway) |
| `base_engine/execution/order_gateway.py` | Order execution + risk checks |
| `base_engine/data/database.py` | DB models, `insert_paper_trade()` (condition_id enrichment), `backfill_positions_resolution()` (stale uPnL cleanup) |
| `base_engine/data/resolution_backfill.py` | Resolution pipeline (Phase 2a→5). Phase 4b gate + size population |
| `config/settings.py` | All config defaults including MIRROR_* |
| `tests/unit/test_mirror_bot_logic.py` | MirrorBot unit tests (same-side dedup test added S109) |
| `scripts/diag_confidence.py` | P&L by confidence bracket diagnostic |
| `scripts/bot_pnl.py` | Canonical P&L script |
| `deploy/deploy.sh` | Atomic deploy to VPS |

---

## 8. SESSION HISTORY (RELEVANT CONTEXT)

| Session | Key Changes |
|---------|-------------|
| S77 | Phantom trade dedup, stale entry pricing fix, resolution backfill SELL overwrite fix |
| S79 | Selectivity tightening (MIN_CONFIDENCE 0.10→0.55, but never enforced!) |
| S81 | RTDS live, paper_trades DB persistence fix |
| S85 | Resolution backfill 3 root causes fixed (Python 3.13 scoping, non-YES/NO outcomes, perf). 544 resolved. |
| S86 | Ingestion sync fix, RESOLUTION event dedup (3238 dupes deleted) |
| S87 | RESOLUTION dedup fix for partitioned tables (atomic INSERT...SELECT) |
| S90 | Scheduler zombie advisory lock fix, master timeout |
| S94 | Latency 2967ms→11.9ms, lock-free DB, RTDS fast-path |
| S100 | Alpha decay, canary persistence, SSH timeouts |
| S101 | Bucket filters, whale trade commit fix |
| S102 | Hard floor 10c, single dampener, dead code purge, $50 min trade |
| S103 | Confidence gate added (0.45 TRIAL), log spam demotion, dead conformal cleanup |
| **S109** | **5 root-cause P&L fixes: condition_id enrichment, Phase 4b gate, same-side dedup, stale uPnL cleanup, RESOLUTION size. Cap 200→400.** |

---

## 9. MANDATORY READING BEFORE ANY CHANGE

1. **`CLAUDE.md`** — Prime directive, rules of engagement, forbidden patterns. Read FIRST.
2. **`memory/MEMORY.md`** — System-wide memory, bot status, outstanding items.
3. **`memory/feedback_pnl_math.md`** — P&L calculation rules. NEVER invert formulas for NO positions.
4. **`memory/feedback_scope_lock.md`** — NEVER add unsolicited features.
5. **`memory/feedback_bot_sessions.md`** — Session boundary rules.
6. **This handoff** — MirrorBot-specific context.

---

## 10. QUICK REFERENCE COMMANDS

```bash
# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 -o StrictHostKeyChecking=no ubuntu@34.251.224.21

# DB access (ALWAYS use this, never pgbouncer)
sudo -u postgres psql -d polymarket

# MirrorBot logs
sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i mirror

# Confidence gate firing
sudo journalctl -u polymarket-ai -f | grep mirror_low_confidence

# Same-side dedup firing
sudo journalctl -u polymarket-ai -f | grep mirror_same_side_blocked

# Service restart (after .env change)
sudo systemctl restart polymarket-ai

# Deploy from local
bash deploy/deploy.sh

# P&L check
sudo -u postgres psql -d polymarket -c "SELECT event_type, COUNT(*), ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) FROM trade_events WHERE bot_name='MirrorBot' GROUP BY event_type ORDER BY event_type;"

# Open positions
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE source_bot='MirrorBot' AND status='open';"

# Stale uPnL check (should be 0)
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE status='closed' AND unrealized_pnl <> 0;"

# Backfill status (276 re-queued)
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM traded_markets WHERE resolved_at IS NULL AND bot_names LIKE '%Mirror%';"

# Run tests locally
pytest tests/ -x -q
```
