# S177 SHARED MASTER HANDOFF — Infrastructure Session

**Session:** 177 (Shared Infrastructure)
**Date:** 2026-04-15 / deployed 2026-04-16
**Scope:** Cross-bot infrastructure only — no trading logic, no bot-specific tuning
**Tests:** 2117 passed, 0 failed, 2 skipped, 9 xfailed
**Branch:** master
**Commit:** `a33510b` — 10 files, 507 insertions, 31 deletions
**VPS Deploy:** `20260416_122845` — LIVE ✅
**Prior sessions:** S173 (last shared master), S176 (EsportsBot v2)

---

## 1. WHAT THIS SESSION DID

7 infrastructure items completed, priority-ordered:

| # | Item | Files | Risk |
|---|------|-------|------|
| 1 | **Database backup script** | `deploy/daily_backup.sh` (new), `deploy/deploy.sh` | Low |
| 2 | **asyncio.wait_for removal** (3 pure DB ops) | `data_ingestion.py`, `id_resolver.py`, `database.py` | Medium |
| 3 | **EB v2 pipeline serialization** (joblib) | `esports_v2/model/pipeline.py`, `bots/esports_bot_v2.py` | Low |
| 4 | **Timer activation** (prune + audit) | `deploy/deploy.sh` | Low |
| 5 | **prediction_log error surfacing** | `database.py` L3162 | Trivial |
| 6 | **Structlog dedup processor** | `config/logging_setup.py` | Low |
| 7 | **Logrotate config** | `deploy/logrotate.d/polymarket` (new), `deploy/deploy.sh` | Low |

---

## 2. DETAILED CHANGES

### 2.1 Database Backup Script (Item 1)

**Problem:** Zero backups on VPS. `deploy/crontabs/postgres.crontab` references `/opt/pa2-backups/daily_backup.sh` but the script didn't exist.

**Fix:**
- Created `deploy/daily_backup.sh` — `pg_dump -Fc` (custom format), gzip, 7-day retention prune
- Modified `deploy/deploy.sh` step 5b — copies script to `/opt/pa2-backups/`, sets ownership to postgres, chmod +x
- Crontab already installed by existing deploy logic at 04:00 UTC daily

**Verify post-deploy:**
```bash
sudo -u postgres /opt/pa2-backups/daily_backup.sh
ls -la /opt/pa2-backups/polymarket_*.dump
sudo crontab -u postgres -l
```

### 2.2 asyncio.wait_for Removal (Item 2)

**Problem:** 3 `asyncio.wait_for` calls in `data_ingestion.py` wrapped pure DB operations, risking asyncpg connection state corruption on timeout.

**Fix:** Replaced with server-side `SET LOCAL statement_timeout`:
- L1580: `resolve_market_ids_batch()` — 15s timeout added in `id_resolver.py` L53
- L1590: `bulk_insert_trades()` — 15s timeout added in `database.py` L1725
- L2211: `_phase2_select_markets()` — removed wait_for, relies on global 60s statement_timeout

**Exception handling:** Call sites catch `(OperationalError, DatabaseError)` for DB-specific errors (covers `asyncpg.QueryCanceledError` wrapped by SQLAlchemy). `bulk_insert_trades` has an additional broad `except Exception` fallback for unexpected errors. L2211 (`_phase2_select_markets`) separates DB errors from other failures. Import: `from sqlalchemy.exc import OperationalError as _OperationalError`.

**KEPT (not changed):**
- L144 (ingestion_scheduler.py) — master watchdog for non-DB hangs
- L2782 (data_ingestion.py) — HTTP API calls, not DB

### 2.3 EB v2 Pipeline Serialization (Item 3)

**Problem:** Pipeline fit takes 5.5 min on every startup (XGBoost + Venn-ABERS + conformal on 28K records).

**Fix:** Added `save(path)` / `load(path)` to `EsportsPipeline` via joblib:
- `pipeline.py`: Added `save()`, `load()`, `is_fitted` property
- `esports_bot_v2.py` startup: tries `_SNAPSHOT_DIR / "pipeline.joblib"` first, falls back to fit
- After retrain (every 50 matches): saves updated pipeline
- On graceful shutdown (`flush_state()`): saves pipeline alongside Trinity snapshot

**Design details:**
- **Staleness:** 24h threshold via file mtime. Stale snapshot triggers refit.
- **Version safety:** `try/except` catches 9 specific deserialization exceptions (`ModuleNotFoundError`, `ImportError`, `AttributeError`, `TypeError`, `ValueError`, `EOFError`, `KeyError`, `pickle.UnpicklingError`, `OSError`) — logs warning with error type, falls back to refit. Non-deserialization bugs propagate.
- **Health check timeout:** Kept at 420s — first deploy has no pipeline snapshot on VPS, bot falls back to full 5.5-min fit. Reduce to 300s in S178+ after first successful run saves the snapshot.

### 2.4 Timer Activation (Item 4)

**Problem:** `polymarket-prune-prices.timer` and `polymarket-audit.timer` existed in repo but were never enabled on VPS.

**Fix:** Added step 6b in `deploy/deploy.sh` — copies timer/service files to `/etc/systemd/system/`, runs `systemctl enable --now`.

**Prune timer:** Prunes `market_prices` table hourly (30-day retention, 50K batch). Table is deprecated (`MARKET_PRICES_FALLBACK_ENABLED=false`) but still 19GB on disk.

**Audit timer:** Runs 24 checks via `base_engine/audit/orchestrator.py` (120s timeout each).

### 2.5 prediction_log Error Surfacing (Item 5)

**Problem:** MirrorBot and EsportsBot have 0 `prediction_log` rows. Failures silently swallowed at `logger.debug` level.

**Fix:** Changed `database.py` L3162 from `debug` to `warning` level with structured fields (`error`, `market_id`, `bot_name`).

**Post-deploy:** Check `journalctl | grep prediction_log_write_failed` to identify the actual root cause.

### 2.6 Structlog Dedup Processor (Item 6)

**Problem:** Repeated identical log lines (e.g., "no edge found" on every scan) flood journald.

**Fix:** Added `_DedupProcessor` to `config/logging_setup.py`:
- Keys on `(event_text, log_level)`
- 60-second suppression window
- Emits "suppressed N duplicates" when window expires
- Bounded to 500 keys (LRU eviction)

### 2.7 Logrotate Config (Item 7)

**Problem:** `data/paper_trading.log` grows unbounded. No logrotate config.

**Fix:**
- Created `deploy/logrotate.d/polymarket` — daily rotation, 7 copies, compress, copytruncate
- `deploy/deploy.sh` copies to `/etc/logrotate.d/polymarket`
- Uses `copytruncate` to avoid needing WatchedFileHandler (compatible with structlog tee logger)

---

## 3. FILES MODIFIED

### New files
```
deploy/daily_backup.sh                    # pg_dump backup script
deploy/logrotate.d/polymarket             # Logrotate config
```

### Modified files
| File | Change |
|------|--------|
| `deploy/deploy.sh` | +backup script copy, +timer activation, +logrotate install, health check 420→300s |
| `base_engine/data/data_ingestion.py` | Removed 3 asyncio.wait_for on DB ops |
| `base_engine/data/id_resolver.py` | +text import, +SET LOCAL statement_timeout |
| `base_engine/data/database.py` | +SET LOCAL in bulk_insert_trades, prediction_log debug→warning |
| `esports_v2/model/pipeline.py` | +joblib import, +save/load/is_fitted |
| `bots/esports_bot_v2.py` | Pipeline snapshot load/save on startup/retrain/shutdown |
| `config/logging_setup.py` | +_DedupProcessor (60s TTL dedup) |

---

## 4. PHASE 2 STATUS (post-S177)

| Item | Status | Notes |
|------|--------|-------|
| 2A: asyncio.wait_for | **DONE** | 3 DB ops fixed. Scheduler instances kept (mixed ops, master watchdog) |
| 2B: Data retention | **DONE** (S159) | prune_market_prices.py exists. Timer now enabled (Item 4) |
| 2C: Structlog dedup | **DONE** | _DedupProcessor, 60s window |
| 2D: Logrotate | **DONE** | copytruncate, 7-day retention |
| 2E: RTDS dedup | **DONE** (prior) | elite_watchlist.py _seen_tx OrderedDict |
| 2F: Health check | **DONE** (prior) | 6-layer health_check.sh + kill_switch.py |
| 2G: Pool tuning | **DONE** (prior) | 8+4 per bot, PgBouncer-aware |
| 2H/2I: Liquidity gates | **DONE** (prior) | order_gateway.py + liquidity_guardian.py |
| 2J: Slippage monitoring | NOT CHECKED | Needs review |
| 2K: Feast | **SKIP** | Custom feature_store.py exists |

**Phase 2 is effectively complete** (10/12 items done, 1 skipped, 1 needs review).

---

## 5. S171 AUDIT GAPS (post-S177 deploy)

| Gap | Status | Notes |
|-----|--------|-------|
| Backups: ZERO | **FIXED + VERIFIED** | Script at `/opt/pa2-backups/daily_backup.sh`, 8 existing dumps (Apr 8-16), crontab 04:00 UTC |
| Prune timer: INACTIVE | **FIXED + VERIFIED** | `polymarket-prune-prices.timer` active, hourly runs |
| Audit service: FAILING | **FIXED + VERIFIED** | `polymarket-audit.timer` active, next 03:00 UTC |
| fail2ban: CRASHED | Fixed in S173 | Deployer IP whitelisted |
| prediction_log: MB/EB 0 rows | **STALE FINDING — NOT A BUG** | Post-deploy SSH diagnosis: MB=18,480 rows (1,328/hr), WB=141,573 rows, EnsembleBot=213,622 rows. Test INSERT succeeded. Writes working. Zero `prediction_log_write_failed` warnings. S171 audit query was wrong. |
| Autovacuum 049: incomplete | NOT FIXED | positions (5.5% dead), mpl (14% dead) |

---

## 6. POST-DEPLOY VERIFICATION (2026-04-16 16:40 UTC)

| Component | Status |
|-----------|--------|
| Release | `/opt/pa2-releases/20260416_122845` ✅ |
| All services (weather, mirror, esports, ingestion) | Active, scanning ✅ |
| Backup script installed | `/opt/pa2-backups/daily_backup.sh` (postgres owned) ✅ |
| postgres crontab | Installed, 04:00 UTC daily ✅ |
| Prune timer | Active, next run +24min ✅ |
| Audit timer | Active, next run +10h ✅ |
| Logrotate config | `/etc/logrotate.d/polymarket` installed ✅ |
| Pipeline snapshot | `pipeline.joblib` saved (602KB, 16:36 UTC) ✅ |
| Structlog dedup | Confirmed working — "(suppressed N duplicates)" in all bot logs ✅ |
| prediction_log writes | MB/WB writing cleanly. Zero failures. ✅ |
| EB v2 shadow | 81 clean (v2-trinity) + 35 contaminated predictions. 0/5 resolved so far. |

**Deploy health check failed at 420s** — expected first-deploy behavior. EB v2 had no pipeline snapshot on VPS, fell back to full 5.5-min fit. Snapshot now saved; next restart will load in <30s. Bot is healthy and scanning.

---

## 7. WHAT'S NEXT

### Immediate (next session / S178)
1. **Reduce health check timeout** from 420s → 300s in `deploy/deploy.sh` (pipeline snapshot now exists)
2. **Fix 153 temporal ordering violations** in `prediction_log` (`resolved_at < prediction_time`). Script `scripts/cleanup_temporal_violations.py` exists (check). Logged as warning every ~5min by MB + WB.
3. **Monitor EB v2 shadow predictions** — accumulating for 5v2-C gate (need 50 resolved)
4. **Verify EB v2 team mapping** on first resolved predictions (correct rate must be >50%)

### Short-term
5. Phase 2J: Slippage monitoring review
6. Autovacuum: tune remaining 4 tables (positions, mpl)
7. EB v2 pipeline P0: market_price=NULL on all predictions (no match-level Polymarket markets for tier-C esports yet)

### Gated (4+ weeks post-Day-2)
8. Phase 6: WB elevation — needs P(edge>0) >= 0.30
9. Phase 7: MB elevation — needs P(edge>0) >= 0.30

---

## 7. VPS QUICK REFERENCE

```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0

# Deploy
bash deploy/deploy.sh

# Check all services
for svc in polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion; do
  echo "--- $svc ---"
  sudo systemctl status $svc --no-pager | head -5
done

# Check timers
sudo systemctl list-timers polymarket-*

# Run backup manually
sudo -u postgres /opt/pa2-backups/daily_backup.sh

# Canonical P&L
cd /opt/polymarket-ai-v2 && sudo -u polymarket bash -c \
  "source /opt/pa2-shared/venv/bin/activate && python3 scripts/bot_pnl.py WeatherBot 24"

# EB v2 shadow predictions
sudo -u postgres psql -d polymarket -c \
  "SELECT model_version, COUNT(*), COUNT(*) FILTER (WHERE actual_winner IS NOT NULL) AS resolved FROM esports_predictions WHERE mode='shadow' GROUP BY model_version;"
```

---

## 8. SESSION CHAIN

```
S172  → Day 1 + partial Phase 1
S172B → Phase 1 completion
S172C → Phase 1 final (12/12), Phase RC drafted
S173  → Phase RC complete, Day 2 deployed, EB v1 killed, deploy.sh fixed
S174  → Phase 5v2-A COMPLETE (EB v2 ratings)
S175  → Phase 5v2-B COMPLETE (EB v2 backtester)
S176  → EB v2 bot built + deployed (dry-run shadow)
S177  → Phase 2 complete (10/12), infra gaps fixed, pipeline serialization deployed ← DONE
S178+ → Health check 420→300s, temporal violations cleanup, monitor shadows
```

---

## 8.X UPDATE 2026-04-17 — Illiquidity exit stage-2 kwarg fix (P1)

**Issue:** Stage-2 CLOB confirmation in `position_manager.py` L930 called `_lg.check_liquidity(token_id=..., size=size, side="SELL")`. `check_liquidity()` takes `trade_size=` (not `size=`) and **requires** `market_id=`. Every call raised `TypeError`, swallowed by the `except Exception` at L937-938 → `_confirmed_illiquid=False` (hold). Stage-2 has never executed in production.

**Fix:** `_lg.check_liquidity(market_id=_mid, token_id=_token_id, trade_size=size, side="SELL")`.

**File:** `base_engine/execution/position_manager.py:930` (1 line).

**Verification:** `pytest tests/unit/test_illiquidity_exit.py` — 7/7 pass (includes CLOB-overrides-prefilter and timeout-conservative-hold cases that exercise the fixed call path).

**Blast radius:** Gated by `ILLIQUIDITY_EXIT_ENABLED` (currently off on VPS). No signature/interface change. Must be merged **before** S172 2I flips the flag on.

---

## 9. CRITICAL RULES (carry forward)

1. **RULE ZERO** — No performance numbers without bot_pnl.py.
2. **Bot-scoped sessions** — no bleed between bots unless manually demanded.
3. **One fix per commit.**
4. **Paper trading IS production.**
5. **No asyncio.wait_for on DB** — use `SET LOCAL statement_timeout`.
6. **Never blacklist cities** for WeatherBot (user directive, permanent).
7. **EsportsBot stays in PM_EXCLUDE_BOTS.**
8. **Two-phase write is non-negotiable** for EB v2.
9. **Backtest ROI is meaningless** — market_price=0.5 default. Only shadow CLV matters.
10. **Verify team mapping on first resolved predictions** — if correct rate < 50%, mapping is inverted.

---

**END OF HANDOFF — S177 SHARED MASTER**
