# S172 SHARED MASTER HANDOFF — Day 1 + Phase 1 Partial

**Session:** 172
**Date:** 2026-04-13
**Scope:** ALL BOTS — S172 consolidated plan execution
**Commits (this agent):** 763a362, 75e3785, b554c32, 6594c2b, ccae341 (5 commits)
**Tests:** 1890 passed, 0 failed (2 pre-existing flaky WB tests excluded — network-dependent)
**Branch:** master
**NOT YET PUSHED to remote.**

---

## SESSION NARRATIVE

This session established S172 v6.0 as the canonical, immutable 8-month plan. Cold-read review by the user found 10 issues in the plan text — all fixed (commit numbering, 1H definition, D10 data source, Phase 6 ordering, 6Q threshold, Phase 7 gate math, 7K/8H dedup, success criteria, EnsembleBot status). Plan saved to `S172_CONSOLIDATED_PLAN.md` in repo and `memory/project_s172_consolidated_plan.md` + `memory/project_s172_current_state.md`.

Then began Day 1 execution. Completed all code items (D7, D8, D10). Phase 1 started — completed 1A and 1B.

A 7-point audit of the D7/D8 implementation found real issues. All fixed before commit:
1. **Unapproved -20% tier** — removed (was scope creep from old EB logic)
2. **WB hard stop dead code** — added `_check_hard_stop_all_positions()` standalone scan
3. **$2 flat sizing blocked by dust gate** — raised to $30 (clears $25 after 0.88x risk budget)
4. **Signal override** — confirmed correct (passthrough = neutral, plan wording was imprecise)
5. **No tests** — added 14 tests in `test_hard_stop_loss.py`
6. **EB replacement range** — verified correct
7. **D3 SQL** — generated (not yet run on VPS)

---

## WHAT WAS DONE

### Day 1 Code Commits

**D7 — Shared hard stop-loss** (commit 763a362):
- `base_engine/risk/risk_manager.py`: Added `check_hard_stop_loss(bot_name, pnl_pct)`. Single inviolable tier. Per-bot defaults: WB -25%, MB -30%, EB -50%. Absolute -50% floor. Env var configurable.
- `bots/esports_bot.py`: Replaced EB-specific hard stop + S134/S136 edge override (lines 2242-2274) with shared call. One code path.
- `bots/mirror_bot.py`: Added shared hard stop before take-profit in exit scan loop.
- `bots/weather_bot.py`: Added `_check_hard_stop_all_positions()` — standalone scan every cycle, iterates ALL position_details. Also hard stop in `_evaluate_mid_life_exits()`.
- `tests/unit/test_hard_stop_loss.py`: 14 new tests.
- `tests/unit/test_weather_bot.py`: Mock hard stop in mid-life exit helper.

**D8 — MirrorBot flat sizing** (commit 75e3785):
- Flat sizing: `MIRROR_FLAT_POSITION_SIZE_USD=30` (Kelly logged but not used). $30 clears $25 dust gate after typical ~0.88x risk budget deduction.
- Re-entry cooldown: `MIRROR_MARKET_COOLDOWN_SECONDS` default 1800->86400 (30min->24h).
- Signal override: `apply_signal_enhancements()` overridden -> returns confidence unchanged.
- Design debt noted: risk budget deductions apply after flat sizing.

**D10 — WB reentry cooldown** (in commit 763a362):
- `_get_exit_cooldown()` rewritten: TTL = min(time_to_resolution, 6h) floor 1h.
- Data source: `markets.end_date_iso` (Gamma API endDateISO), same pattern as `exit_strategy.py:307-308`.
- Falls back to T1-K reason-specific cooldowns if end_date unavailable.

### Phase 1 Commits

**1A — frozen_price_check** (commit 6594c2b):
- `base_engine/audit/checks/frozen_price_check.py`: `mpl.updated_at` -> `mpl.timestamp` (3 occurrences). Unblocks audit service on VPS.

**1B — calibration_check** (commit ccae341):
- `scripts/calibration_check.py`: Full rewrite.
  - Rolling 90-day window (was hardcoded cutoff)
  - Min 50 resolved + 5 per bin gate
  - Brier Skill Score (BSS) vs climatological baseline
  - WeatherBot CRPS/PIT: PIT histogram + KS test (scipy.stats)
  - Exclude EnsembleBot + NULL bot_name
  - `--days` flag for custom window

### Not Code (SSH commands generated, not yet run on VPS):
- D5: pg_dump backup + cron
- D0-a/b/c: logrotate, ingestion NRestarts, Redis AOF
- D1: PG OOMScoreAdjust=-900
- D2: systemd MemoryMax on all services
- D3: RESOLUTION + EXIT dedup SQL (7-step sequence generated)
- D4: fail2ban + ufw limit ssh
- D6: prune timer
- D9: PipelineGate — no action (2h gate, not bottleneck)

---

## WHAT'S NEXT (Phase 1 remaining)

| Commit | Item | Status |
|--------|------|--------|
| 3 | 1C: Autovacuum tuning (067_vacuum_tuning.sql) | NEXT |
| 4 | 1D: WB post-resolution price override | Pending |
| 5a | 1E-a: market_aliases migration | Pending |
| 5b | 1E-b: order_gateway pre-trade validation | Pending |
| 6 | 1G: prediction_log write fix (MB/EB) | Pending |
| 7 | 1F: EB tracemalloc SIGUSR1 handler | Pending |
| 8 | 1I: Edge verification (HARD GATE) | Pending |
| 9 | 1J: Orderbook collection cron | Pending |
| -- | 1K: Quick verifications (SSH) | Pending |
| -- | 1L: Shadow mode protocol (doc) | Pending |
| 10 | 1M: Strategy lifecycle schema | Pending |

### VPS work pending:
- Run SSH commands for D0-D6
- Run D3 dedup SQL
- 1H: `ALTER SYSTEM SET idle_in_transaction_session_timeout = '300000'`
- pgBackRest setup (60-90min)
- POST-COMMIT-2: Run `calibration_check.py WeatherBot` after deploying 1B

---

## CRITICAL FILES MODIFIED

| File | Lines Changed | Items |
|------|--------------|-------|
| `base_engine/risk/risk_manager.py` | +67 | D7 |
| `bots/esports_bot.py` | +11/-32 | D7 |
| `bots/mirror_bot.py` | +55/-7 | D7, D8 |
| `bots/weather_bot.py` | +160/-7 | D7, D10 |
| `base_engine/audit/checks/frozen_price_check.py` | +3/-3 | 1A |
| `scripts/calibration_check.py` | +144/-33 | 1B |
| `tests/unit/test_hard_stop_loss.py` | +98 (new) | D7 |
| `tests/unit/test_mirror_bot_logic.py` | +41/-12 | D7, D8 |
| `tests/unit/test_weather_bot.py` | +4 | D7 |
| `S172_CONSOLIDATED_PLAN.md` | +378 (new) | Plan |

---

## BLAST RADIUS

**D7 affects all 3 bots:** risk_manager.py is shared. Verified: each bot calls `check_hard_stop_loss()` correctly. EB lost its edge override — positions that would have been held at -20% to -50% with remaining edge >=0.03 will now only exit at the per-bot hard stop (-50% for EB). This is intentional per the plan.

**D8 affects MirrorBot only:** flat sizing, cooldown, signal override. base_bot.py untouched.

**D10 affects WeatherBot only:** reentry cooldown timing. Falls back to existing T1-K cooldowns if end_date_iso unavailable.

**1A affects audit service:** was crashing. Will resume functioning on next deploy.

**1B affects calibration_check.py script only:** no bot runtime impact.

---

## DECISIONS MADE THIS SESSION

1. **$30 flat sizing** (not $25 or $2): $25 gets dust-gated after risk budget. $2 is spread-dominated. $30 x 0.88 = $26.40, clears gate.
2. **No -20% edge-gated tier**: Plan says single inviolable hard stop only. Removed after audit.
3. **WB standalone hard stop scan**: Added because `_evaluate_mid_life_exits` only sees positions with fresh forecasts.
4. **Signal override = confidence passthrough**: Plan said "return 1.0" but the function returns confidence (not a multiplier). Passthrough IS the correct neutral behavior.
5. **D9 = no action**: PipelineGate is 2-hour safety net, not 60s bottleneck.

---

## VERIFICATION

```
git log --oneline -5
# ccae341 S172 1B: calibration_check rolling 90-day + CRPS/PIT + min gates
# 6594c2b S172 1A: fix frozen_price_check — updated_at -> timestamp
# b554c32 docs: update S172 plan — D8 flat sizing $30
# 75e3785 S172 D8: MirrorBot flat sizing $30, 24h re-entry cooldown, signal override
# 763a362 S172 D7: shared inviolable hard stop-loss in risk_manager.py

pytest: 1890 passed, 0 failed
Flaky (pre-existing, network): test_scan_with_weather_market_and_edge, test_heartbeat_counters_reflect_actual_scan
```

---

## ROLLBACK

```bash
# Revert all S172 changes:
git revert ccae341 6594c2b b554c32 75e3785 763a362
sudo systemctl restart polymarket-weather polymarket-mirror polymarket-esports

# After reverting D7: audit all positions exited in last N minutes for false triggers.
```
