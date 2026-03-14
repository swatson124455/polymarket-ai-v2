# AGENT HANDOFF — Esports Session 89 (2026-03-14)
## Esports Data Fixes + EsportsLiveBot Scaling Features

**Predecessor**: Session 88 (observation mode fix), Session 85 (P&L data overhaul)
**Scope**: Esports subsystem only (3 bots: EsportsBot, EsportsLiveBot, EsportsSeriesBot)
**Status**: All changes uncommitted, ready for deploy after migration 053

---

## WHAT WAS DONE

### Batch 1: EsportsLiveBot Scaling Features (E2-E5)

| ID | Feature | File(s) | Lines |
|----|---------|---------|-------|
| E2 | Dota2 + Valorant patch detection | `esports/models/patch_drift.py` | +58 |
| E3 | Stale match detection (30min no score change) | `esports/live/esports_game_monitor.py`, `bots/esports_live_bot.py` | +30 |
| E4 | Canceled match queue + drain in scan loop | `esports/live/esports_game_monitor.py`, `bots/esports_live_bot.py` | +22 |
| E5 | Adaptive polling (budget-aware PandaScore throttle) | `esports/live/esports_game_monitor.py`, `esports/data/pandascore_client.py` | +37 |

### Batch 2: 3rd Party Audit Fixes (9 issues)

| Fix | Issue | File(s) | Risk |
|-----|-------|---------|------|
| 1 | `_backfill_esports_outcomes()` reads paper_trades | `bots/esports_bot.py` | Zero |
| 2 | `compute_pnl_summary()` reads paper_trades | `esports/data/esports_db.py` | Zero |
| 3 | `HorizonBiasCalibrator` reads paper_trades | `base_engine/features/calibration.py` | Low* |
| 4 | Missing closing_price + tournament_phase columns | `schema/migrations/053_esports_schema_fixes.sql` (NEW) | Zero |
| 5 | PandaScore poll has no timeout | `esports/live/esports_game_monitor.py` | Zero |
| 6 | HorizonBias fit has no timeout | `bots/esports_bot.py` | Zero |
| 7 | Glicko-2 `except Exception: pass` | `esports/data/esports_data_collector.py` | Zero |
| 8 | Runtime DDL hack `_ensure_phase_column()` | `esports/data/esports_db.py` | Zero |
| 9 | No retention cleanup for esports tables | `bots/esports_bot.py`, `config/settings.py` | Zero |

*Fix 3 touches `base_engine/features/calibration.py` — shared by ALL bots. Backward-compat alias `fit_from_paper_trades = fit_from_trade_events` ensures no callers break.

---

## FILE-BY-FILE CHANGES

### `schema/migrations/053_esports_schema_fixes.sql` (NEW)
- Adds `closing_price DOUBLE PRECISION` and `tournament_phase VARCHAR(50)` to `esports_prediction_log`
- **MUST run before code deploy** — Fix 8 removes the runtime DDL fallback

### `esports/data/esports_db.py`
- **Fix 8**: Deleted `_phase_column_ensured` global + `_ensure_phase_column()` function (22 lines removed) + 2 call sites
- **Fix 2**: `compute_pnl_summary()` now reads `trade_events` (event_type IN EXIT, RESOLUTION) instead of `paper_trades`

### `bots/esports_bot.py`
- **Fix 1**: `_backfill_esports_outcomes()` → `trade_events WHERE event_type='RESOLUTION'`
- **Fix 6**: `fit_from_trade_events()` wrapped in `asyncio.wait_for(timeout=10.0)` + TimeoutError handler
- **Fix 9**: New `_cleanup_old_esports_data()` method — once-daily DELETE for training data (365d) and prediction log (180d). Called at end of `_check_monitoring_thresholds()`

### `base_engine/features/calibration.py`
- **Fix 3**: Renamed `fit_from_paper_trades()` → `fit_from_trade_events()`. 2-CTE SQL joining ENTRY events (entry_price, predicted_probability) with RESOLUTION events (realized_pnl). Old name kept as alias.

### `esports/live/esports_game_monitor.py`
- **E3**: Stale match detection — `_last_score_update`, `_prev_scores` dicts, `is_stale()` method
- **E4**: Cancel queue — `_canceled_matches` asyncio.Queue, pushed on status="canceled"
- **E5**: Adaptive polling — checks `PandaScoreClient.get_remaining_budget()`, throttles poll interval (10s→30s→60s) and game list when budget low
- **Fix 5**: `get_live_matches()` wrapped in `asyncio.wait_for(timeout=ESPORTS_LIVE_POLL_TIMEOUT)` + TimeoutError handler
- Cleanup of stale tracking dicts on match finish/cancel

### `bots/esports_live_bot.py`
- **E3**: Stale match skip in `scan_and_trade()` via `self._game_monitor.is_stale()`
- **E4**: Cancel queue drain at top of `scan_and_trade()` with safety guards (hasattr + isinstance)

### `esports/data/pandascore_client.py`
- **E5**: New `get_remaining_budget()` classmethod returning `max(0, 950 - _shared_req_count)`

### `esports/models/patch_drift.py`
- **E2**: `_fetch_dota2_patch_version()` (Steam News API) + `_fetch_valorant_patch_version()` (valorant-api.com)
- Wired into `_check_patch_version()` for dota2 and valorant branches

### `esports/data/esports_data_collector.py`
- **Fix 7**: `except Exception: pass` → `except Exception as e: logger.warning("glicko2_update_failed", error=str(e))`

### `config/settings.py`
- `ESPORTS_LIVE_POLL_TIMEOUT: int = 10` (Fix 5)
- `ESPORTS_TRAINING_RETENTION_DAYS: int = 365` (Fix 9)
- `ESPORTS_PREDICTION_RETENTION_DAYS: int = 180` (Fix 9)
- `ESPORTS_STALE_MATCH_SECONDS: int = 1800` (E3, already added prior session)

---

## DEPLOY STEPS

### 1. Run migration 053 FIRST
```bash
# On VPS:
cd /opt/polymarket-ai-v2
source /opt/pa2-shared/venv/bin/activate
python scripts/run_migrations.py
# Expected: "Applied 053_esports_schema_fixes"
```

### 2. Deploy code
```bash
# From local:
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

### 3. Verify
```bash
# On VPS — check all 3 esports bots healthy:
journalctl -u polymarket-ai -f | grep -E "EsportsBot|EsportsLiveBot|EsportsSeriesBot"

# Fix 7 — Glicko-2 failures now visible:
journalctl -u polymarket-ai --since "10 min ago" | grep glicko2_update_failed

# Fix 9 — Retention cleanup (once daily, first run after deploy):
journalctl -u polymarket-ai --since "1 hour ago" | grep esports_data_cleanup

# Fix 5/6 — Timeout catching:
journalctl -u polymarket-ai --since "1 hour ago" | grep -E "poll timeout|horizon_bias_timeout"

# E3 — Stale match detection:
journalctl -u polymarket-ai --since "1 hour ago" | grep "stale match"

# E4 — Cancel queue:
journalctl -u polymarket-ai --since "1 hour ago" | grep "match canceled"

# E5 — Adaptive polling:
journalctl -u polymarket-ai --since "1 hour ago" | grep "remaining_budget"
```

### Rollback
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
# Migration 053 is safe to leave — IF NOT EXISTS + DDL fallback removed won't error
```

---

## BLAST RADIUS

| Scope | Affected |
|-------|----------|
| Esports-only | Fixes 1, 2, 4-9, E2-E5 |
| Cross-bot (all 14) | Fix 3 (`calibration.py`) — backward-compat alias prevents breakage |
| New config keys | 4 new settings with safe defaults, no .env changes needed |
| Schema | Migration 053 — additive only (ADD COLUMN IF NOT EXISTS) |

---

## CRITICAL ORDERING
1. Migration 053 **MUST** run before code deploy — Fix 8 removes the runtime DDL hack that would have created the `tournament_phase` column on-the-fly. Without migration, `log_prediction()` and `get_phase_accuracy()` will fail on missing column.

---

## TESTS
- 58 esports-related tests: **PASSED**
- 1 pre-existing failure: `test_paper_is_production.py::TestTransactionCostEdgeUnified::test_edge_threshold_identical_both_modes[EsportsBot]` (MagicMock TypeError, not caused by these changes)

---

## WHAT WAS NOT CHANGED
- E1 (TabPFN ensemble) — Deferred. Requires new pip dependency + training data.
- E6 (Map-veto model) — Deferred. Requires HLTV scraper + training pipeline.
- E7 (Conformal prediction intervals) — Deferred. Requires trained model outputs.
- No .env changes required for any of these fixes.
