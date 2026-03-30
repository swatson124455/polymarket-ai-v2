# Agent Handoff — EsportsBot Session 138
**Date:** 2026-03-29
**Branch:** master
**Deploy tag:** (inline — commits pushed to master, deployed via `deploy.sh` → `sudo systemctl restart polymarket-esports`)
**All-tests baseline:** 1720 passed (S137 baseline, S138 changes are additive/fix-only)

---

## SESSION CONTRACT — REVIEW BEFORE ANY WORK

> **These rules govern every interaction. They are non-negotiable. You have
> zero latitude to deviate from them. No exceptions, no judgment calls,
> no "I thought it would be better." If a rule below does not explicitly
> grant you permission to do something, you do not have permission.**

1. **You touch ONLY what I tell you to touch.** I will describe a specific task. You may only modify or create files directly required to complete that task. Nothing else. If you believe a file outside that scope needs attention, tell me. Do not open it, do not edit it, do not "quickly improve" it.
2. **You do not delete anything I did not ask you to delete.** No function, class, method, variable, import, config value, comment, or file gets removed unless I explicitly said to remove it. If you think something should go, stop and ask. Renaming counts as deleting the original — ask first.
3. **No bandaids. Root cause or nothing.** No try/except blocks that swallow errors silently. No bare `except:` or broad `except Exception`. No `pass` used to skip over a problem. No `# TODO`, `# FIXME`, `# HACK`, `# TEMPORARY`, or `# WORKAROUND`. No `return None` as a way to dodge an error path. If you cannot fix the root cause within scope, stop and report it. Do not ship a workaround and move on.
4. **No silent behavior changes.** If your change alters how any existing function, method, or class behaves — return type, side effects, error handling, default values, call signature, logging output — you must explain what changes and why BEFORE writing the code. I approve or reject. You do not decide.
5. **No new dependencies without approval.** Do not add any pip package, npm package, system library, or external tool. If the task requires one, state the package name, what it does, and why there is no alternative already in the project. Wait for approval.
6. **No weakening of existing safeguards.** Do not replace specific exceptions with broader ones. Do not remove or reduce logging. Do not suppress warnings. Do not relax input validation. Do not remove type hints. Do not shorten or gut docstrings.
7. **Locked files — never touch under any circumstance.** `.env` and any `.env.*` variants. Any file with `production`, `prod`, or `secrets` in its name. `docker-compose.prod.yml`. Wallet files, key files, or anything containing API keys. If you are unsure whether a file is locked, ask.
8. **Before writing any code, state your plan.** Every response that includes code changes must begin with: the exact files you will modify and/or create, a plain-language explanation of what you are changing and why, the root cause of any bug you are fixing (not just the symptom), any behavior that will differ from current behavior. If anything about the task is unclear, ask before you start.
9. **After writing code, confirm compliance.** End every code response with: Files modified (list). Files created (list). Files deleted (must be none unless I authorized it). Functions/classes removed (must be none unless I authorized it). New dependencies added (must be none unless I approved it). Behavior changes (list any, or state "none").
10. **When you hit a wall, stop.** If you encounter a problem you cannot solve within the scope I gave you, do not improvise. Report: what the problem is, what file and line it is in, what you believe the root cause is, what solving it would require. Then wait. I decide the next step. You do not.

> **Summary:** You are a tool under my direction. You execute what I ask, within the boundaries I set, and nothing more. If something is not covered here, the answer is "ask first." Silence is not consent. Ambiguity is not permission.

---

## Summary of Work

Four commits landed in S138. In priority order:

1. **`7def77c`** — Churn loop fix (P0 from S137)
2. **`01538d0`** — Retrain spam fix
3. **`ebb647b`** — Backfill import path fix
4. **`03f733f`** — Calibration elevation (6 root causes)

All deployed and service confirmed healthy.

---

## Commit 1 — Churn Loop Fix (`7def77c`)

### Problem
CS2 market `-$290/day` single market. Bot exited on `edge_gone` within seconds of entry (price moved against it), then re-entered same market next scan (5-min cooldown too short), then exited again. 8 entries in 7 hours on one market. Per-market entry cap of 5 was in-memory only — reset to zero on every restart, so restarts bypassed it entirely.

### Root Cause (3 missing safeguards)
1. No minimum hold time: `edge_gone` could fire 30 seconds post-entry
2. Edge-gone cooldown same as generic exits: 300s vs 300s (should be longer)
3. Entry cap in-memory only: restart = cap resets to 0

### Fix
**Files:** `bots/esports_bot.py`, `config/settings.py`

| Setting | Before | After |
|---------|--------|-------|
| `ESPORTS_MIN_HOLD_MINUTES` | (did not exist) | `10.0` |
| `ESPORTS_EDGE_GONE_COOLDOWN_SECONDS` | (did not exist) | `1800.0` (30 min) |
| `ESPORTS_EXIT_COOLDOWN_SECONDS` | `300.0` | `300.0` (unchanged) |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | `5` | `2` |

**Code changes:**
- `esports_bot.py:1744-1773`: Before firing `edge_gone`, check `opened_at`. If hold time < `ESPORTS_MIN_HOLD_MINUTES`, emit `esportsbot_edge_exit_hold_gate` debug log and skip exit.
- `esports_bot.py:820-828` (WS) and scan entry gate: Read `self._exit_reasons[market_id]`. If reason == `edge_gone`, use 1800s cooldown; else use 300s.
- `self._exit_reasons: Dict[str, str]` added at `__init__`. All 3 exit paths set this.
- `_save_exit_cooldown_to_redis()` / `_restore_exit_cooldowns_from_redis()`: persist both TTL and reason to Redis so restart survives.

**Rollback:** `git revert 7def77c` OR `export ESPORTS_MIN_HOLD_MINUTES=0 ESPORTS_EDGE_GONE_COOLDOWN_SECONDS=300`

---

## Commit 2 — Retrain Spam Fix (`01538d0`)

### Problem
364 `esportsbot_accuracy_below_threshold` warnings per 30-minute window. Log noise masked real issues.

### Root Cause
`bots/esports_bot.py:~1156` (pre-fix): When accuracy < `ESPORTS_MIN_ACCURACY_TO_TRADE`, handler called `self._trainer._last_train_time.pop(game, None)` to "force a retrain." But `IncrementalLearner` has its own 2h retrain cooldown that blocks the actual retrain. The `pop()` destroyed the only state that could suppress the warning on the next scan cycle. Net effect: retrain never happens, warning fires every 120s.

### Fix
Removed the two-line `pop()` block. `IncrementalLearner` retrain fires naturally on schedule; warning now fires only after cooldown expires (once per 2h per game max).

**Files:** `bots/esports_bot.py` (2 lines removed)

---

## Commit 3 — Backfill Import Path Fix (`ebb647b`)

### Problem
Phase 4b-alt (position-based RESOLUTION event creation for markets where condition_id or Gamma ID mismatch prevents normal backfill) was silently dead.

### Root Cause
`base_engine/data/resolution_backfill.py:546` (pre-fix):
```python
_settings_mod = __import__("base_engine.config.settings", fromlist=["settings"])
```
`base_engine.config.settings` does not exist — settings lives at `config.settings`. The `ModuleNotFoundError` was caught by a bare `except Exception: continue`, silently disabling the entire 4b-alt path on every backfill run.

### Fix
```python
_settings_mod = __import__("config.settings", fromlist=["settings"])
```

**Files:** `base_engine/data/resolution_backfill.py`

---

## Commit 4 — Calibration Elevation (`03f733f`)

### Context
CS2 Brier score: **0.309** (81 resolved predictions, 14-day window). Severe anti-calibration:
- 0.7-1.0 bucket: predicted 78.6% → actual 37.5% (massive overconfidence)
- 0.0-0.3 bucket: predicted 21.8% → actual 58.3% (underconfidence on underdogs)

Full per-game table at `memory/project_esports_calibration_review.md`.

### 6 Root Causes Found and Fixed

| RC | Problem | Fix |
|----|---------|-----|
| RC1 | Double EGM d=1.5 on CS2 (ML+Glicko2 blend at line 2820, then XGB blend at line 2556) = effective d²=2.25 extremization | `ESPORTS_EGM_D` default 1.5→1.2; Brier-adjusted d now 1.0 (not 1.2) |
| RC2 | Dynamic EGM adjuster too weak: `max(1.0, 1.5-0.3)=1.2`, applied twice = 1.44 effective | Changed to `1.0` (no extremization at all for Brier>0.25 games) |
| RC3 | `ESPORTS_RFLB_STRENGTH=0.0` (disabled) | Enabled at `0.03` (A/B logged: 0.03/0.05/0.08) |
| RC4 | Calibrators overfit at small samples: BetaCal min_samples=10, Platt=30, VennABERS=5 | All raised to 50. Per-game resolved counts at S138 start: CS2=81 (stays fitted), all others <50 (identity passthrough) |
| RC5 | Unfitted calibration SQ = 0.50 (gave too much credit for no data) | Lowered to 0.25 |
| RC6 | Calibrators trained on post-calibration output (soft feedback loop) | Added `raw_model_prob` column to `esports_prediction_log`; calibrators now train on `COALESCE(raw_model_prob, predicted_prob)` |

### Files Modified

**`config/settings.py`:**
- Line 1096: `ESPORTS_EGM_D` default `"1.5"` → `"1.2"`
- Line 1258: `ESPORTS_RFLB_STRENGTH` default `"0.0"` → `"0.03"`

**`bots/esports_bot.py`:**
- Line 319: `BetaCalibrator(min_samples=10)` → `50`
- Line 162: `OnlinePlattCalibrator(min_samples=30)` → `50`
- Line 348: Global BetaCal `min_samples=15` → `50`
- Line 4925: VennABERS guard `>= 5` → `>= 50`
- Line 4930: `VennAbersCalibrator(min_samples=5)` → `50`
- Line 4090: `_calibration = 0.50` → `0.25`
- Line 5655: `max(1.0, self._egm_d - 0.3)` → `1.0`
- Lines 2249, 2402, 6444: All 3 `log_prediction()` call sites pass `raw_model_prob=_raw_prob`
- BetaCal fit query (line ~95): `SELECT predicted_prob` → `SELECT COALESCE(raw_model_prob, predicted_prob)`
- Global BetaCal query (~line 4855): Same COALESCE
- VennABERS fitting query (~line 4917): Same COALESCE

**`esports/data/esports_db.py`:**
- `log_prediction()`: Added optional `raw_model_prob: float = None` parameter
- INSERT column list and values include `raw_model_prob`
- ON CONFLICT UPDATE: `raw_model_prob = COALESCE(EXCLUDED.raw_model_prob, esports_prediction_log.raw_model_prob)`

**`schema/migrations/061_add_raw_model_prob.sql`** (NEW):
```sql
ALTER TABLE esports_prediction_log ADD COLUMN IF NOT EXISTS raw_model_prob DOUBLE PRECISION;
```
Migration run on VPS postgres — confirmed succeeded.

---

## Source File Inventory (3rd-Party Reference)

| File | Role | S138 changes |
|------|------|-------------|
| `bots/esports_bot.py` | Main bot: scan loop, entry/exit logic, calibration ensemble, EGM blending, WebSocket reactive | YES — all 4 commits |
| `config/settings.py` | All env-var config defaults | YES — commits 1 + 4 |
| `esports/data/esports_db.py` | DB layer: `log_prediction()`, `load_glicko2_ratings()`, `save_glicko2_ratings()` | YES — commit 4 |
| `schema/migrations/061_add_raw_model_prob.sql` | Schema migration | YES — commit 4 (new file) |
| `base_engine/data/resolution_backfill.py` | Auto-resolves positions from on-chain outcomes | YES — commit 3 |
| `base_engine/features/aggregation.py` | `extremized_geometric_mean()` — EGM blending engine used by all bots | NO — read-only audit; no changes |
| `esports/models/glicko2.py` | Per-game Glicko-2 rating tracker (8 games, ~1054 teams) | NO |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing, BotBankrollManager | NO |
| `base_engine/execution/order_gateway.py` | Order submission to CLOB | NO |
| `base_engine/data/daily_counter.py` | Write-through exposure persistence for `_game_exposure` | NO |

---

## Key Architecture Facts (EsportsBot-Specific)

### Prediction Pipeline
```
PandaScore API → team name → Glicko-2 rating lookup
                           ↘
                             EGM blend (d=1.2 baseline)   ← ML model (XGBoost per game)
                           ↗
                         → calibration ensemble (BetaCal + Platt + VennABERS)
                         → RFLB correction (0.03 strength)
                         → edge = model_prob - market_price
                         → Signal Quality (5 components) → Kelly sizing
```

### EGM Double-Application (Critical for CS2)
CS2 is in `_CROSS_GAME_IDS`. Prediction flows through EGM twice:
1. `esports_bot.py:2820`: `EGM([cs2_model_prob, glicko2_prob], d=_d)` — game-specific d from `_game_egm_d` dict
2. `esports_bot.py:2556`: `EGM([prob, xgb_prob], weights=[0.6, 0.4], d=_d)` — same d applied again

After S138: If CS2 Brier > 0.25, `_game_egm_d["cs2"] = 1.0`. Applied twice = 1.0² = 1.0. No extremization.

### Exit Hierarchy (post-S138)
1. `edge_gone`: `remaining_edge ≤ 0.01` → requires hold ≥ 10 min, then 30-min re-entry block
2. `trailing_edge`: peak_edge dropped 50% AND remaining < 0.02
3. `stop_loss`: unrealized P&L ≤ -20% AND edge < 0.03
4. `max_hold`: configurable max hold time
5. `resolution`: market resolved on-chain

### Calibration Ensemble (post-S138)
| Calibrator | min_samples | Per-game status at S138 |
|-----------|------------|------------------------|
| VennABERS (50-tree GBT) | 50 | CS2=81 ✅ active; all others ≤30 → identity |
| BetaCalibrator (per-game) | 50 | CS2 only active |
| OnlinePlattCalibrator | 50 | CS2 only active |
| Global BetaCalibrator | 50 | CS2 only active |

All calibrators receive `raw_model_prob` (pre-calibration) to train on, not post-calibration output.

### Per-Game Brier Scores (2026-03-29, 14-day window)
| Game | n | Brier | Status |
|------|---|-------|--------|
| CS2 | 81 | 0.309 | Elevated — anti-calibrated at extremes |
| Dota2 | 30 | 0.237 | Acceptable |
| Valorant | 11 | 0.248 | Borderline |
| LoL | 12 | 0.189 | Good |
| CoD | 1 | 0.064 | Insufficient data |
| R6/SC2/RL | 0 | — | No resolutions |

---

## VPS State at S138 Close

**Service:** `polymarket-esports` — running, healthy scan loop
**pgbouncer:** `default_pool_size=25` (was 50 before S137 ops, reduced to 15 during S138 ops, final 25)
**PG max_connections:** 50
**DB pool per app:** 8 (reduced from 12 in S137 ops to prevent pool exhaustion with 3 services)

**Key observation:** 3 services × ~8 connections = ~24 needed minimum. pgbouncer at 25 leaves 1 spare.
**Monitor:** `journalctl -u polymarket-esports | grep -E "pool|timeout|DB"` if service restart fails to verify DB connections.

---

## Open Items

### P1 — Baker-McHale Shadow Sizing (NOT deployed yet)
Shadow mode: `size = min(old_kelly, baker_mchale)` — ratios 1.7-3.5x observed.
**Hold condition:** Churn loop fix deployed 2026-03-29 ~16:43 UTC. Cutover requires 48h of stable, non-churning operation.
**Target:** 2026-03-31. If churn recurs, investigate before cutting over.
**Baker-McHale** uses `n=samples_count` for uncertainty-aware sizing — better than flat Kelly at small n.

### P1 — CS2 Calibration Monitoring
HLTV features wired in S137. Calibration elevation in S138. Need 100+ post-HLTV predictions to assess.
**If CS2 Brier still >0.28 after 100+ predictions:**
- Option A: Disable CS2 pregame ML model, fall back to pure Glicko-2 (conservative, lower variance)
- Option B: Tighten `ESPORTS_MIN_EDGE` for CS2 only (reduce position sizing on uncertain signals)
- **Do NOT disable CS2 entirely** — it has the highest data volume and feeds calibrator training

### P2 — Phase 3 Calibration (DEFERRED — 48-72h of Phase 2 data needed)
**Commit F (not deployed):** Replace VennABERS 50-tree GBT with temperature scaling (`p_cal = sigmoid(logit(p)/T)`) — 1 free parameter vs hundreds, far more robust at n<100.
**Commit G (not deployed):** Single-pass EGM across all 3 sources ([ML, Glicko2, XGB]) rather than two sequential EGM applications. Eliminates compound extremization structurally.

### P3 — Player Glicko-2 Activation
`players_loaded=0` all games at S137/S138 close. Awaiting first roster change detection event.
Monitor: `journalctl -u polymarket-esports | grep esportsbot_roster_change`
Player Glicko-2 data loads via `_check_roster_stability()` on roster change only (was no-op before S137 fix).

### P3 — MirrorBot P1 (from S140, not an EsportsBot issue)
`current_price` not updating on open MirrorBot positions. VPS postgres pool exhaustion: 135 > 50 connections. See `AGENT_HANDOFF_MIRRORBOT_SESSION140_2026_03_29.md`.

---

## Verification Queries

```sql
-- Brier by game post-S138 (run after 48h)
SELECT game, COUNT(*) as n,
  ROUND(AVG(POWER(predicted_prob - actual_outcome, 2))::numeric, 4) as brier
FROM esports_prediction_log
WHERE actual_outcome IS NOT NULL AND created_at > NOW() - INTERVAL '48 hours'
GROUP BY game ORDER BY n DESC;

-- Calibration buckets (target: CS2 0.7-1.0 bucket actual > 55%)
SELECT game,
  CASE WHEN predicted_prob < 0.3 THEN '0.0-0.3'
       WHEN predicted_prob < 0.5 THEN '0.3-0.5'
       WHEN predicted_prob < 0.7 THEN '0.5-0.7'
       ELSE '0.7-1.0' END as bucket,
  COUNT(*) as n,
  ROUND(AVG(predicted_prob)::numeric, 3) as avg_pred,
  ROUND(AVG(actual_outcome)::numeric, 3) as avg_actual
FROM esports_prediction_log
WHERE actual_outcome IS NOT NULL AND created_at > NOW() - INTERVAL '7 days'
GROUP BY game, bucket ORDER BY game, bucket;

-- raw_model_prob column populated (should be non-null for all rows after 03f733f deploy)
SELECT COUNT(*) as total, COUNT(raw_model_prob) as has_raw
FROM esports_prediction_log
WHERE created_at > NOW() - INTERVAL '24 hours';

-- Churn check: edge_gone exits with short holds (should be 0 after 7def77c)
SELECT COUNT(*) FROM esports_prediction_log ep
JOIN trade_events te ON te.market_id = ep.market_id
WHERE te.event_type = 'EXIT'
  AND te.metadata->>'exit_reason' = 'edge_gone'
  AND (te.event_time - ep.created_at) < INTERVAL '10 minutes'
  AND te.event_time > NOW() - INTERVAL '24 hours';
```

```bash
# RFLB adjustments firing (should see entries if extreme prices present)
journalctl -u polymarket-esports --since '1 hour ago' | grep rflb

# Hold gate blocking premature edge_gone exits (debug level)
journalctl -u polymarket-esports --since '1 hour ago' | grep esportsbot_edge_exit_hold_gate

# EGM d per game (should show cs2 d=1.0 if Brier>0.25)
journalctl -u polymarket-esports --since '1 hour ago' | grep egm_d

# Calibrator ensemble (expect "n_calibrators=0" for Dota2/Val/LoL until 50 samples)
journalctl -u polymarket-esports --since '1 hour ago' | grep calibrator_ensemble

# Retrain warnings (should be ≤ 1 per 2h per game, not 364 per 30min)
journalctl -u polymarket-esports --since '1 hour ago' | grep accuracy_below_threshold | wc -l
```

---

## Rollback

| Commit | Impact if reverted | Rollback command |
|--------|-------------------|-----------------|
| `7def77c` | Churn loop returns | `git revert 7def77c` OR `export ESPORTS_MIN_HOLD_MINUTES=0 ESPORTS_EDGE_GONE_COOLDOWN_SECONDS=300` |
| `01538d0` | 364 retrain warnings/30min return | `git revert 01538d0` |
| `ebb647b` | Phase 4b-alt RESOLUTION backfill dead again | `git revert ebb647b` |
| `03f733f` | Calibration reverts to overconfident defaults | `git revert 03f733f` (also need to export: `ESPORTS_EGM_D=1.5 ESPORTS_RFLB_STRENGTH=0.0`) |

All 4 commits are independently revertible. Config changes in 03f733f can be overridden via VPS `.env` without code changes.

---

## Change Log

```
## CHANGE: 2026-03-29
**Commit 1 (7def77c):** Churn loop: 3 missing safeguards
**Root cause:** No min hold, edge_gone cooldown=generic, in-memory entry cap reset on restart
**Files:** bots/esports_bot.py (+79/-18), config/settings.py (+4)
**Blast radius:** EsportsBot only. exit_reasons dict new state. Redis TTL keys extended for edge_gone.
**Verification:** Deployed; monitoring esportsbot_edge_exit_hold_gate logs

## CHANGE: 2026-03-29
**Commit 2 (01538d0):** Retrain spam
**Root cause:** pop() destroyed cooldown key, log fired every scan cycle
**Files:** bots/esports_bot.py (-2 lines)
**Blast radius:** EsportsBot IncrementalLearner only
**Verification:** 0 retrain warnings after 1h of scanning post-deploy

## CHANGE: 2026-03-29
**Commit 3 (ebb647b):** Backfill Phase 4b-alt dead
**Root cause:** Wrong import path base_engine.config.settings → config.settings
**Files:** base_engine/data/resolution_backfill.py (+26/-14)
**Blast radius:** Resolution backfill only (all bots use it, read-path fix)
**Verification:** ModuleNotFoundError no longer appears in backfill logs

## CHANGE: 2026-03-29
**Commit 4 (03f733f):** Calibration overconfidence (6 root causes)
**Root cause:** Double EGM d²=2.25 + RFLB disabled + overfit calibrators + SQ over-credits uncalibrated games
**Files:** bots/esports_bot.py (+25/-17), config/settings.py (+4/-3), esports/data/esports_db.py (+10/-3), schema/migrations/061_add_raw_model_prob.sql (new)
**Blast radius:** EsportsBot only (calibrators, EGM, log_prediction). No other bots affected.
**Verification:** Migration applied; service running; calibrator_ensemble logs show CS2 active, others identity
```

---

## UNIFIED AUDIT SYSTEM (deployed 2026-03-29, commits fd1afd5 + ba8e5b3)

A system-wide data integrity audit covering all 36+ DB tables is now live.

### What changed in shared infrastructure
| File | Change | Impact on EsportsBot |
|------|--------|----------------------|
| `base_engine/data/database.py` | Added `AuditRun` ORM; `audit_run_id` + `violation_hash` on `ReconciliationBreak` | Additive only — no existing columns changed |
| `base_engine/monitoring/health_scheduler.py` | Added `daily_audit` job at 86400s | Runs once/day. READ COMMITTED + 30s timeout. Does not share EsportsBot DB pool slots. |
| `base_engine/data/trade_event_audit.py` | After existing checks, runs 3-check mini-audit (SizeInvariant, Orphan, TemporalOrder) | Fires every 30min backfill. Non-fatal. 3 SQL queries only. |
| `schema/migrations/062_audit_runs.sql` | `audit_runs` table + `violation_hash` on `reconciliation_breaks` | Already applied. |

### P2 (432-row impossible timestamps) is now monitored automatically

**S139 P2 (432 `prediction_log` rows with `prediction_time > resolved_at`) is now caught by the audit system.** `prediction_accuracy_check.py` includes a sub-check for `prediction_time > resolved_at`. It will surface per-bot counts in every daily audit run as `PREDICTION_ACCURACY_ANOMALY / prediction_time_after_market_resolution`.

This was previously only a handoff note — now it's automated detection. The root cause (clock skew vs INSERT ordering bug in how `prediction_time` is set) still needs investigation. Query to diagnose:
```sql
SELECT bot_name, COUNT(*), MIN(prediction_time - resolved_at) AS min_delta
FROM prediction_log pl JOIN markets m ON m.id = pl.market_id
WHERE pl.prediction_time > m.resolved_at
GROUP BY bot_name;
```

### CLI for EsportsBot sessions
```bash
python scripts/run_audit.py --bot EsportsBot --json   # EsportsBot-specific violations
python scripts/run_audit.py --list-open               # all open violations system-wide
python scripts/run_audit.py --ack <id> --reason "..."
```

### Most relevant checks for EsportsBot
- `prediction_accuracy_anomaly` — Brier drift + the 432-row impossible timestamp check (see P2 above)
- `signal_trade_mismatch` — signal→entry alignment (WARNING only until `SIGNAL_REQUIRED_BOTS` env var is set)
- `fk_integrity` — EsportsBot writes to `prediction_log`, `trade_signals`, `trade_events` — all checked against `markets.id`
- `temporal_order` — EXIT/RESOLUTION `event_time` before first ENTRY (trade_events audit)
- `duplicate_entry_check` — multiple ENTRY events while lifecycle still open (churn-loop guard)

### Note on SIGNAL_REQUIRED_BOTS
EsportsBot does **not** call `store_pending_trade_signals()` — confirmed by code search. Do not add EsportsBot to `SIGNAL_REQUIRED_BOTS`. The `signal_trade_mismatch` check will fire WARNING (not CRITICAL) for EsportsBot — this is expected and correct behaviour.

### P2 — 432-row root cause: action needed this session
The 432 rows are silently excluded from calibration labeling on every retrain. The audit system now detects them automatically, but the root cause is still unknown. Run this diagnostic to find out which fix applies:

```sql
SELECT
  pl.bot_name,
  COUNT(*) AS bad_rows,
  MIN(EXTRACT(EPOCH FROM (pl.prediction_time - m.resolved_at))) AS min_delta_s,
  MAX(EXTRACT(EPOCH FROM (pl.prediction_time - m.resolved_at))) AS max_delta_s,
  AVG(EXTRACT(EPOCH FROM (pl.prediction_time - m.resolved_at))) AS avg_delta_s
FROM prediction_log pl
JOIN markets m ON m.id = pl.market_id
WHERE pl.prediction_time > m.resolved_at
  AND m.resolved_at IS NOT NULL
GROUP BY pl.bot_name;
```

**Interpretation:**
- `avg_delta_s < 5` → VPS clock jitter. Fix: add 5-second grace to the `AND m.resolved_at >= pl.prediction_time` guard in `database.py:3140`.
- `avg_delta_s` in minutes/hours → backfill wrote a backdated `resolved_at` from the Polymarket API. Fix: in `resolution_backfill.py`, prevent `resolved_at` from being set earlier than the earliest `prediction_time` for that market.
- Both present → apply both fixes.

Do NOT remove or widen the guard without understanding the distribution — it prevents ML data leakage.

### Full audit docs
`AGENT_HANDOFF_AUDIT_SYSTEM_2026_03_29.md`
