# S166 MASTER HANDOFF — Audit System Resurrection + WeatherBot Pause + Session Poison Fix

**Session:** 166
**Date:** 2026-04-08/09
**Scope:** ALL BOTS — shared infrastructure. This is a shared-infrastructure session, no bot-specific bleedover.
**Commits:** e4bd416, 1942df4, a097240, e1456aa, 7d69db5, 9c8af5f, d20df57, 0987ec1 (8 commits)
**Deploys:** 20260408_163101, 20260408_170135, 20260408_172824, 20260408_193541, 20260408_203116, 20260408_204630 (6 deploys)
**Tests:** 1789 passed, 0 failed (every deploy)
**Branch:** master

---

## SESSION NARRATIVE (read this first)

This session started as S164 continuation (deploy SAVEPOINTs + ingestion gap fix), but pivoted when investigation revealed the 21-check audit system was operationally broken: 20/21 checks failing, 370K violations accumulated, nobody reading output. The session became "fix the instrument before trusting measurements."

**Phase 1:** Fixed audit check SQL to match VPS schema (16 check files had wrong column names). This was the bulk of the work — the VPS database has different column names than the local dev schema (e.g., `positions.bot_name` locally vs `positions.source_bot` on VPS, `trade_events.fee` vs `fees`, `bot_health_states.status` vs `state`).

**Phase 2:** Fixed EXIT side transition in 3 audit checks (SizeInvariantCheck, TemporalOrderCheck, DuplicateEntryCheck). Historical EXIT events used `side='SELL'` while ENTRYs used `YES/NO`. Grouping by side created thousands of false violations. Dropped `side` from GROUP BY/JOIN, same fix S163 applied to bot_pnl.py.

**Phase 3:** Deployed S164 infra fixes (SAVEPOINTs, Fallback 2b ingestion gap, silent swallows), ran audit, triaged 370K→2,376 violations. Discovered WeatherBot orphan markets (63 positions on markets not in DB, ongoing). Paused WeatherBot.

**Phase 4:** Found and fixed the upstream session poisoner — Fallback 2's LATERAL JOIN on market_prices was failing and poisoning the shared session, cascading InFailedSQLTransactionError to all downstream fallbacks. Mirror: 34 errors/10min, Esports: 41/10min → both 0 after fix. This recovered price data for 30 positions that had no stop-loss protection.

**Phase 5:** Diagnosed the remaining 38 unpriced positions. 33 WeatherBot (paused, known orphans), 5 Mirror/Esports on zero-liquidity markets (bots entered when liquidity existed, liquidity dried up, price pipeline correctly stopped fetching). Conclusion: need an illiquidity exit trigger (P2 strategy/risk control), not better price fallbacks.

---

## WHAT WAS DONE (complete list)

### Code Changes

**Commit e4bd416 — S164 infra fixes**
- Files: `position_manager.py`, `resolution_backfill.py`, `bot_pnl.py`
- 4 `except:pass` → `session.begin_nested()` SAVEPOINT + `logger.warning()` in seed INSERT paths
- New Fallback 2b: resolves token_id → market_id via markets table JOIN for price lookup
- Fallback 4: added `OR condition_id = ANY(:mids)` to match both market_id formats
- 4 silent swallows → `logger.debug()` (timestamp arithmetic, CLOB parsing, alerting, exit strategy)
- Unpriced positions diagnostic logging
- EXIT side transition documented in bot_pnl.py (cutover 2026-04-08T16:01:40Z)

**Commit 1942df4 — Audit schema batch 1 + systemd timer**
- Files: 6 check files + `orchestrator.py` + 2 new deploy files
- paper_trade_check.py: `amount`→`size`, `profit_loss`→`realized_pnl`
- bot_health_state_check.py: `updated_at`→`recorded_at`
- schema_drift_check.py: `_REQUIRED_COLUMNS` dict updated
- size_invariant_check.py: dropped `side` from GROUP BY
- temporal_order_check.py: dropped `side` from JOIN
- duplicate_entry_check.py: dropped `side` from GROUP BY
- orchestrator.py: `_STATEMENT_TIMEOUT` 30s→120s (safe: audit now out-of-process)
- New: `deploy/polymarket-audit.service` + `deploy/polymarket-audit.timer` (daily 03:00 UTC)

**Commit a097240 — run_audit.py fix**
- `db.initialize()` → `db.init()` (VPS method name)

**Commit e1456aa — Audit schema batch 2 (12 more check files)**
- fee_check.py: `fee`→`fees`
- traded_markets_check.py: `bot_name`→`bot_names`, `first_traded_at`→`first_trade_at`, array ops→simple `=`
- resolution_consistency_check.py: `pt.outcome`→`pt.resolution`
- position_trade_events_check.py: `p.bot_name`→`p.source_bot`
- stale_position_check.py: `p.bot_name`→`p.source_bot`
- shadow_fill_check.py: `sf.size`→`sf.order_size_shares`, `sf.price`→`sf.signal_price`
- fill_analysis_check.py: `fa.bot_name`→`fa.source_bot`, `fa.side`→`fa.fill_side`, `fa.filled_at`→`fa.fill_time`
- signal_execution_check.py: `ts.side`→`ts.signal_direction`, `ts.signal_time`→`ts.created_at`
- prediction_accuracy_check.py: `actual_outcome`→`CASE WHEN resolution='YES' THEN 1.0 ELSE 0.0 END`
- dlq_check.py: `processed`→`status NOT IN ('processed','replayed')`
- equity_snapshot_check.py: `snapshot_time`→`snapshot_date`, `equity_value`→`total_equity`
- bot_health_state_check.py: `status`→`state`
- schema_drift_check.py: full `_REQUIRED_COLUMNS` rewrite for all tables

**Commit 7d69db5 — 3 remaining broken checks**
- traded_markets_check.py: `bot_names` is TEXT not TEXT[], use `=` not `ANY()`
- stale_position_check.py: remove `m.accepting_orders` (doesn't exist on VPS)
- price_integrity_check.py: query `markets.yes_price/no_price` + `market_prices_latest` instead of full `market_prices` scan (120s timeout → 0.7s)

**Commit 9c8af5f — stale_position_check filter**
- Filter `status='open'` (not just `size > 0`): 4,297 closed-but-sized are cosmetic, not bugs
- JOIN uses `(m.id::text = p.market_id OR m.condition_id = p.market_id)`
- Result: 309 → 1 violations

**Commit d20df57 — Fallback 2b SAVEPOINT (first attempt at session poison fix)**
- Wrapped Fallback 2b SELECT in `session.begin_nested()`
- Didn't fully fix: session was already poisoned by Fallback 2 upstream

**Commit 0987ec1 — Fallback 2 SAVEPOINT (actual fix)**
- Wrapped Fallback 2 historical SELECT (LATERAL JOIN on market_prices) in `session.begin_nested()`
- Elevated error log from DEBUG to WARNING
- Result: InFailedSQLTransactionError Mirror 34/10min→0, Esports 41/10min→0
- 30 positions recovered price data (now have stop-loss protection)

### VPS Operations
- Installed systemd audit timer: `polymarket-audit.timer` (daily 03:00 UTC, verified active)
- **Paused WeatherBot:** `sudo systemctl stop polymarket-weather` (strategic decision)
- Multiple `scp`/`sudo tee` for ad-hoc scripts to VPS

---

## AUDIT BASELINE (Run #54 — the authoritative numbers)

| Check | Count | Notes |
|---|---|---|
| pnl_math | 0 | **PASS** |
| fill_analysis_inconsistency | 0 | **PASS** |
| dlq_spike | 0 | **PASS** |
| schema_drift | 0 | **PASS** (was 10) |
| bot_health_state_anomaly | 0 | **PASS** |
| stale_open_position | 1 | Was 309. Filter on status='open' eliminated cosmetic |
| prediction_accuracy_anomaly | 3 | Edge case |
| equity_snapshot_gap | 5 | Edge case |
| price_integrity | 26 | Was timeout. Now runs in 0.7s |
| temporal_order | 64 | Historical |
| orphan_resolution | 68 | Historical |
| fee_anomaly | 100 | LIMIT-capped |
| paper_trade_mismatch | 100 | LIMIT-capped |
| duplicate_entry | 193 | |
| signal_trade_mismatch | 200 | LIMIT-capped, true count 1,480 |
| traded_markets_drift | 200 | LIMIT-capped, true count 1,435 |
| resolution_consistency | 218 | |
| shadow_fill_mismatch | 228 | |
| size_invariant | 299 | Was 10,490. 97% drop from side fix |
| position_size_mismatch | 300 | LIMIT-capped, true count 303 |
| fk_integrity | 371 | 678 orphan markets, ALL WeatherBot |
| **TOTAL** | **2,376** | Was 370K |

---

## CRITICAL FINDINGS

### P1 — WeatherBot orphan markets (MITIGATED by pause)
- 63 positions on markets that don't exist in `markets` table at all
- 678 trade_events referencing non-existent markets
- Root cause: WeatherBot discovers markets via Gamma API (bypasses standard ingestion), uses hex condition_id as market_id. Standard ingestion misses these (pagination limit, event_id > 249K). No FK validation at any layer.
- **Mitigated:** WeatherBot paused. No new orphans.
- **To fix:** WeatherBot must upsert full market rows before trading. insert_trade_event should validate market existence. See `bots/weather_bot.py:3567-3716` (_fetch_weather_events_by_tag), line 3694 sets `"id": str(m.get("conditionId"))`.

### P2 — Illiquidity exit trigger (strategy/risk control)
- 5 Mirror/Esports positions on zero-liquidity markets. Bots entered when liquidity existed, it dried up, positions are stranded.
- Markets exist in DB, have correct token IDs, but `liquidity = 0.0` and `market_prices` has 0 rows.
- The price pipeline is working correctly — don't fetch prices for untradeable markets.
- **Fix needed:** Bots should exit when liquidity drops below threshold, before orderbook is empty. Strategy issue, not infrastructure.

### P3 — Zombie positions (COSMETIC, no action needed)
- 4,297 positions: `status='closed'`, `size > 0` on resolved markets
- **0 positions with `status='open'` on resolved markets** — verified by query
- Phase 4b-alt correctly sets status='closed' but doesn't zero size. No operational impact.

### P3 — Refactor `_update_current_prices` to isolated sessions
- Currently has 4+ SAVEPOINT wrappers in one function. Structurally fragile — each new fallback risks another unwrapped failure point.
- Right design: one session per fallback (like S163's Fix 4 isolated persist session).

---

## ARCHITECTURAL KNOWLEDGE (carry forward)

### VPS Schema (canonical, differs from local)
```
positions: id, bot_id, source_bot, market_id, token_id, side, size, entry_price, current_price, unrealized_pnl, opened_at, status, is_paper, entry_cost, breakeven_price, trader_addresses
trade_events: sequence_num, event_type, execution_mode, event_time, knowledge_time, recorded_at, bot_name, market_id, token_id, correlation_id, order_id, side, size, price, fees, realized_pnl, confidence, predicted_probability, model_version, model_name, idempotency_key, event_data
paper_trades: id, order_id, market_id, token_id, bot_name, side, size, price, confidence, created_at, resolution, resolved_at, realized_pnl, correlation_id, latency_ms, status, submitted_at, filled_at
bot_health_states: id, bot_name, state, failure_count, sizing_multiplier, state_entered_at, recorded_at, details
dead_letter_queue: id, event_type, payload, error_message, error_type, retry_count, max_retries, status, created_at, next_retry_at, replayed_at, source_bot, market_id
equity_snapshots: id, snapshot_date, bot_name, total_capital, ..., total_equity, ..., execution_mode
shadow_fills: id, created_at, bot_name, market_id, token_id, side, order_size_shares, ..., signal_price, ..., trade_executed, ..., shadow_pnl
fill_analysis: id, market_id, source_bot, fill_price, fill_side, fill_time, price_30s, ..., adverse_move_30s, ...
trade_signals: id, trade_id, market_id, bot_name, signal_direction, signal_confidence, signal_source, ..., created_at
prediction_log: ..., resolution, was_correct, ..., bot_name, model_version
traded_markets: market_id, condition_id, bot_names (TEXT not array!), first_trade_at, ..., status, resolution_status
```

### Key Column Name Mappings (local → VPS)
- `positions.bot_name` → `source_bot`
- `trade_events.fee` → `fees`
- `bot_health_states.status` → `state`, `updated_at` → `recorded_at`
- `paper_trades.amount` → `size`, `profit_loss` → `realized_pnl`, no `outcome` column (use `resolution`)
- `equity_snapshots.snapshot_time` → `snapshot_date`, `equity_value` → `total_equity`
- `shadow_fills.size` → `order_size_shares`, `price` → `signal_price`
- `fill_analysis.bot_name` → `source_bot`, `side` → `fill_side`, `filled_at` → `fill_time`
- `trade_signals.side` → `signal_direction`, `signal_time` → `created_at`
- `prediction_log.actual_outcome` → doesn't exist, use `resolution` + `was_correct`
- `dead_letter_queue.processed` → `status`
- `traded_markets.bot_name` → `bot_names` (TEXT, not array)
- `markets.accepting_orders` → doesn't exist on VPS
- `Database.initialize()` → `Database.init()` on VPS

### EXIT Side Transition (S163)
- Before 2026-04-08T16:01:40Z: EXIT events have `side='SELL'`
- After: EXIT events have `side='YES'` or `side='NO'`
- Audit checks must NOT group by side for EXIT events. Use event_type as discriminator.

### market_id Format Inconsistency (root cause of 5+ bugs)
- Standard ingestion uses `markets.id` (numeric/Gamma format)
- WeatherBot uses `condition_id` (hex `0x...` format)
- Every downstream consumer needs `(m.id = X OR m.condition_id = X)` — and many don't
- Affected: frozen uPnL (S163), ingestion gap (S164), zombie positions, orphan trade_events, audit check joins
- Long-term fix: normalize to one canonical format + translate at boundaries. High-risk migration.

### Audit System Architecture
- 21 checks in `base_engine/audit/checks/`, registered in `base_engine/audit/factory.py`
- Orchestrator: `base_engine/audit/orchestrator.py` — READ COMMITTED isolation, per-check sessions, 120s timeout
- CLI: `scripts/run_audit.py` (use `db.init()` not `db.initialize()`)
- Violations persist to `reconciliation_breaks` table (columns: `break_id, recon_date, recon_type, bot_name, market_id, internal_value, external_value, difference, severity, status, details, detected_at, resolved_at, resolution_note, audit_run_id, violation_hash`)
- Systemd timer: `polymarket-audit.timer` fires daily 03:00 UTC
- Alerting: `_maybe_alert()` fires for CRITICAL violations via Discord (if webhook configured)
- **41 audit runs** in DB. Last daily run: Apr 4. Post-resolution runs: 39 total.

### Position Manager Price Fallback Chain
```
_update_current_prices(session, positions):
  1. market_prices_latest (tiny table, O(1))          ← simple SELECT
  2. market_prices historical (7-day LATERAL JOIN)     ← SAVEPOINT-wrapped (S166)
  2b. markets table JOIN → market_prices by market_id  ← SAVEPOINT-wrapped (S166)
  3. CLOB orderbook API (spread < 0.5 gate)            ← try/except per token
  4. markets.yes_price/no_price                        ← condition_id match (S164)
  → seed market_prices_latest from fallback results    ← each INSERT in SAVEPOINT
  → log unpriced_positions warning
```

### Bot Services (VPS)
- `polymarket-weather` — **STOPPED** (strategic pause, S166)
- `polymarket-mirror` — active
- `polymarket-esports` — active
- `polymarket-ingestion` — active
- Audit: `polymarket-audit.timer` — daily 03:00 UTC (systemd, not APScheduler)
- Prune: `polymarket-prune-prices.timer` — hourly

### SSH Access
```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21
RELEASES=/opt/pa2-releases
SHARED=/opt/pa2-shared
VENV=/opt/pa2-shared/venv/bin/activate
CURRENT=/opt/polymarket-ai-v2  # symlink to latest release
```

---

## WHAT WAS NOT DONE (next session backlog)

### Immediate (verify)
1. **Discord alert verification** — Audit timer fires at 03:00 UTC. Confirm Discord message arrives. If not, check `_maybe_alert()` severity filtering — daily audit may only produce WARNINGs (not CRITICALs), and alert threshold filters those. Confirm what severity triggers Discord.
2. **Remove daily_audit from health_scheduler.py** — Only after verifying systemd timer fires reliably. The APScheduler entry is still there as fallback.

### P1
3. **WeatherBot market upsert** — Required before re-enabling WeatherBot. Must upsert full market rows (id, condition_id, yes_token_id, no_token_id, end_date_iso, resolved) from Gamma API before trading. See `weather_bot.py:3567-3716`.
4. **WeatherBot strategy re-evaluation** — YES-side 37.8% WR (UNVERIFIED), negative P&L all-time. Decide whether to re-enable at all.

### P2
5. **Illiquidity exit trigger** — Bots should exit when market liquidity drops below threshold. Strategy/risk control issue. 5 Mirror/Esports positions stranded on zero-liquidity markets.
6. **Bulk-ack historical violations** — Safety-filtered (only closed positions + known-fixed classes). SQL template in plan file.
7. **FrozenPriceCheck + PricesCoverageCheck** — 2 new audit checks. Files don't exist yet.
8. **RESOLUTION dedup in insert_trade_event** — Remove `side` from NOT EXISTS guard. One RESOLUTION per (bot_name, market_id). Needs dedicated tests.
9. **EXIT over-size guard in insert_trade_event** — Reject EXIT if total EXIT size > total ENTRY size. Needs dedicated tests. Edge cases corrupt trade_events permanently.
10. **Fix `scripts/backfill_entry_metadata.py:78-79`** — Last `except Exception: pass` in codebase.

### P3
11. **Refactor `_update_current_prices` to isolated sessions per fallback** — SAVEPOINTs work but are structurally fragile.
12. **Zero size on closed positions** — One-time: `UPDATE positions SET size = 0 WHERE status = 'closed'`. Cosmetic cleanup.
13. **market_id format normalization** — The root of 5+ bugs. Pick canonical format, translate at boundaries. High-risk migration.
14. **Local vs VPS schema drift** — Run VPS migrations locally or document comprehensively.

---

## LEARNINGS (feedback for next agent)

1. **RULE ZERO** — Never present financial figures without citing bot_pnl.py as source or labeling UNVERIFIED. This was enforced by a stop hook.

2. **Fix the instrument before trusting measurements** — The audit system existed but was broken. Fixing it first revealed the true state of the system (4,272 zombies are cosmetic, WeatherBot is the real problem).

3. **Always run against VPS before committing audit changes** — The initial investigation found 3 column mismatches. Running on VPS revealed 12 more. Should have run `scripts/run_audit.py` on VPS first, not after deploying.

4. **Trace backward from the symptom to find the first failure** — The InFailedSQLTransactionError showed up at Fallback 4, but the root cause was Fallback 2. First commit wrapped Fallback 2b (wrong), second commit wrapped Fallback 2 (right). Should have traced 4→3→2b→2→1 before writing code.

5. **SAVEPOINTs are band-aids on shared sessions** — The right design is isolated sessions per fallback. SAVEPOINTs work but each new code change risks introducing another unwrapped failure.

6. **Zero-liquidity positions need strategy exits, not better price fallbacks** — Don't build infrastructure to price untradeable markets. The bots should exit before liquidity hits zero.

7. **`traded_markets.bot_names` is TEXT, not TEXT[]** — Despite the plural name. Use `=` not `ANY()` or `@>`.

8. **Paper trading IS production** — Per CLAUDE.md. Every feature matters identically in paper and live modes.

---

## FILES MODIFIED THIS SESSION

| File | Lines | Change |
|---|---|---|
| `base_engine/execution/position_manager.py` | +130/-37 | SAVEPOINTs on Fallback 2+2b+seeds, Fallback 2b new, condition_id match, diagnostics, silent swallows |
| `base_engine/data/resolution_backfill.py` | +17/-17 | 2 SAVEPOINT fixes on seed INSERT paths |
| `scripts/bot_pnl.py` | +5 | EXIT side transition docs |
| `scripts/run_audit.py` | +3/-3 | db.initialize()→db.init() |
| `base_engine/audit/orchestrator.py` | +5/-1 | statement_timeout 30s→120s |
| `base_engine/audit/checks/paper_trade_check.py` | schema fix | amount→size, profit_loss→realized_pnl |
| `base_engine/audit/checks/bot_health_state_check.py` | schema fix | updated_at→recorded_at, status→state |
| `base_engine/audit/checks/schema_drift_check.py` | schema fix | Full _REQUIRED_COLUMNS rewrite |
| `base_engine/audit/checks/size_invariant_check.py` | schema fix | Dropped side from GROUP BY |
| `base_engine/audit/checks/temporal_order_check.py` | schema fix | Dropped side from JOIN |
| `base_engine/audit/checks/duplicate_entry_check.py` | schema fix | Dropped side from GROUP BY |
| `base_engine/audit/checks/fee_check.py` | schema fix | fee→fees |
| `base_engine/audit/checks/traded_markets_check.py` | schema fix | bot_name→bot_names, array→text |
| `base_engine/audit/checks/resolution_consistency_check.py` | schema fix | outcome→resolution |
| `base_engine/audit/checks/position_trade_events_check.py` | schema fix | bot_name→source_bot |
| `base_engine/audit/checks/stale_position_check.py` | schema+logic fix | bot_name→source_bot, status='open' filter, condition_id JOIN |
| `base_engine/audit/checks/shadow_fill_check.py` | schema fix | size→order_size_shares |
| `base_engine/audit/checks/fill_analysis_check.py` | schema fix | bot_name→source_bot, side→fill_side |
| `base_engine/audit/checks/signal_execution_check.py` | schema fix | side→signal_direction |
| `base_engine/audit/checks/prediction_accuracy_check.py` | schema fix | actual_outcome→CASE resolution |
| `base_engine/audit/checks/dlq_check.py` | schema fix | processed→status |
| `base_engine/audit/checks/equity_snapshot_check.py` | schema fix | snapshot_time→snapshot_date |
| `base_engine/audit/checks/price_integrity_check.py` | perf fix | market_prices scan→markets.yes_price |
| `deploy/polymarket-audit.service` | new | systemd oneshot for audit |
| `deploy/polymarket-audit.timer` | new | daily 03:00 UTC |
| `AGENT_HANDOFF_S166_MASTER.md` | new | this file |
| `AGENT_HANDOFF_S164_MASTER.md` | new | earlier session handoff |
| `memory/project_s163_uncovered_issues.md` | new | backlog tracking |
| `scripts/audit_triage.py` | new (ad-hoc) | One-time triage queries |
| `scripts/audit_triage_p0.py` | new (ad-hoc) | P0 zombie/orphan investigation |
| `scripts/check_unpriced_5.py` | new (ad-hoc) | Illiquidity diagnosis |

---

## ROLLBACK

```bash
# Revert all S166 commits (newest first)
git revert 0987ec1  # Fallback 2 SAVEPOINT
git revert d20df57  # Fallback 2b SAVEPOINT
git revert 9c8af5f  # stale_position_check
git revert 7d69db5  # 3 broken checks
git revert e1456aa  # audit batch 2
git revert a097240  # run_audit.py
git revert 1942df4  # audit batch 1
git revert e4bd416  # S164 infra fixes

# Re-enable WeatherBot if needed
sudo systemctl start polymarket-weather

# Remove audit timer
sudo systemctl stop polymarket-audit.timer
sudo systemctl disable polymarket-audit.timer
```

---

## HOW TO CONTINUE

The next agent reads MEMORY.md first, sees S166 is current, reads this handoff doc. The immediate task is:

1. Check if the 03:00 UTC audit timer fired and Discord alert arrived
2. If yes: proceed to bulk-ack historical violations, then WeatherBot decision
3. If no: debug `_maybe_alert()` severity filtering and Discord webhook

All context, all feedback rules (RULE ZERO, CLAUDE.md), all architectural knowledge carries over through the memory system. The CLAUDE.md development directive is strict — read it before modifying any file.
