# AGENT HANDOFF — EsportsBot Session 131 (2026-03-25)

> **Scope**: EsportsBot ONLY. No bleed-over to other bots unless manual demand.
> **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
> **Codebase**: `C:\lockes-picks\polymarket-ai-v2\` (local Windows) → `/opt/polymarket-ai-v2/` (VPS)
> **Service**: `sudo systemctl restart polymarket-ai` — restarts ALL 15 bots
> **Previous deploy**: `20260325_030337` (S128 audit bug fixes)
> **Commit**: PENDING — S131 changes NOT yet committed or deployed

---

## WHAT WAS DONE THIS SESSION (S131)

### P0 ROOT FIX: Signal Quality Moved from Confidence to Sizing Multiplier

**Problem**: EsportsBot stopped trading on March 25. Every trade attempt died at the sizing stage because `confidence = side_prob * signal_quality` crushed confidence below market price → negative edge → Kelly=0.

**Root cause**: SQ was modifying the *probability* (what we believe) when it should have modified the *bet size* (how much we wager). A model saying "Valorant team has 57.7% win rate" doesn't become 22.5% just because BetaCalibrator hasn't accumulated 10 samples.

**Fix**: SQ is now a sizing multiplier, not a confidence multiplier.

#### Before (S127, broken):
```
confidence = side_prob * signal_quality    # 0.577 * 0.39 = 0.225
Kelly(confidence=0.225, price=0.425)       # negative edge → returns 0 → KILLED
```

#### After (S131, fixed):
```
confidence = side_prob                     # 0.577 (raw model belief)
Kelly(confidence=0.577, price=0.425)       # positive edge → base size
size *= signal_quality                     # × 0.52 → trade at ~half Kelly
```

### Files Modified

| File | Changes |
|------|---------|
| `bots/esports_bot.py` | 6 edits (details below) |
| `tests/unit/test_esports_bot.py` | 3 test assertion updates |

### Detailed Changes in `esports_bot.py`

**Change 1: Main path confidence assignment (~line 2035)**
- `confidence = side_prob * _sq` → `confidence = side_prob`
- Fallback on error: `confidence = side_prob` (unchanged) + `_sq = 1.0` (was not set)
- `_signal_quality` added to returned opp dict

**Change 2: WS reactive path (~line 762)**
- `confidence = side_prob * _sq` → `confidence = side_prob`
- `_signal_quality` added to opp dict

**Change 3: Series path (~line 5560)**
- `confidence = side_prob * _sq` → `confidence = side_prob`
- `_signal_quality` added to match_opp dict

**Change 4: Series WS reactive path (~line 5896)**
- `confidence = side_prob * _sq` → `confidence = side_prob`
- `_signal_quality` added to opp dict

**Change 5: `_execute_esports_trade()` sizing pipeline (~line 3296)**
- Added `_sq_sizing = float(opp.get("_signal_quality", 1.0))`
- Multiplied into existing sizing chain: `size = size * phi_factor * dd_factor * _game_mult * _decay_mult * _sq_sizing`
- Updated sizing-killed and trade-executed log messages to include `signal_quality`

**Change 6: `_compute_signal_quality()` component defaults (~line 3473)**

| Component | Old Default | New Default | Rationale |
|-----------|------------|-------------|-----------|
| agreement (single model) | 0.50 | 0.70 | One model isn't "uncertain" — it's just one source with no contradicting signal |
| calibration (unfitted) | 0.30 | 0.50 | "Not fitted" ≠ "miscalibrated". 0.50 = unknown |
| brier (no data) | 0.25 → score 0.0 | 0.15 → score 0.40 | "No data" ≠ "worst possible model" |

**Typical SQ with fixes** (unfitted single-model game):
- Old: 0.30×0.5 + 0.25×0.3 + 0.20×0.5 + 0.15×0.33 + 0.10×0.0 = **0.375**
- New: 0.30×0.7 + 0.25×0.5 + 0.20×0.5 + 0.15×0.33 + 0.10×0.4 = **0.525**

As a sizing multiplier: trades execute at ~52% of full Kelly. Conservative but trades actually happen.

**Change 7: Confidence gate repurposed (~line 2093)**
- Old: `confidence < self._min_confidence` (0.20) — dead gate because side_prob always > 0.50
- New: `confidence < max(self._min_confidence, 0.52)` — model must show minimum conviction
- Side_prob of 0.52 means model predicts at least 52% for the chosen side

**Change 8: Brier cache seeded on startup (`start()` ~line 590)**
- Calls `_get_cached_rolling_accuracy(db)` during `start()`
- Seeds `_game_brier_cache` for any game with ≥10 resolved predictions
- Eliminates 10-minute cold start where sq_brier was dead

### Test Changes (`tests/unit/test_esports_bot.py`)

3 assertions updated to match new behavior:
1. `test_returns_trade_dict_when_yes_edge`: confidence now > 0.70 (raw side_prob), not < 0.70
2. `test_returns_trade_dict_when_no_edge`: confidence now > 0.65, not < 0.70
3. `test_returns_none_when_confidence_below_min`: Tests new 0.55 min_confidence gate against raw side_prob

**All 115 esports tests pass (86 bot + 29 series model).**

---

## EXPECTED IMPACT

### Before S131 (Valorant example from S130 logs):
```
side_prob=0.577, SQ=0.39 → confidence=0.225 vs price=0.425
edge = -0.200 → Kelly=0 → KILLED
```

### After S131:
```
side_prob=0.577, confidence=0.577 vs price=0.425
edge = +0.152 → Kelly sizes normally
size *= SQ(0.525) → trade at ~52% Kelly → EXECUTES
```

### Trade volume should restore to ~20-30 entries/day (was 33/day on Mar 24 before collapse).

---

## NOT DONE (REMAINING PRIORITIES)

| Pri | Task | Notes |
|-----|------|-------|
| **P0** | Commit S131 changes | Ready — tests pass |
| **P0** | Deploy S131 to VPS | Bot can't trade until deployed |
| **P0** | Commit S130 uncommitted files | 6 files deployed via SCP but not in git |
| P1 | Disable CoD | -$1,151, no ML model. `BOT_ENABLED_ESPORTS_COD=false` |
| P2 | EXIT P&L hemorrhage | -$4,986 exits vs -$1,959 resolutions. Stop-loss/max-hold is the #1 P&L destroyer |
| P3 | CS2 model retraining | Brier=0.292, graduation failing (0.542 < 0.55 threshold) |
| P4 | WebSocket trading disabled | `ws_trading=False` in logs |
| P5 | Resolution backlog | ~15 stale positions with NULL `end_date_iso` |

---

## VERIFICATION PLAN (POST-DEPLOY)

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# 1. Confirm SQ is in sizing logs, not confidence
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_signal_quality' | tail -5"
# EXPECT: confidence ≈ side_prob (>0.50), signal_quality separate

# 2. Confirm trades executing
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep 'EsportsBot trade executed' | tail -5"
# EXPECT: trades with signal_quality field in log

# 3. Confirm sizing-killed reduced (some still expected for low-edge markets)
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep 'sizing_killed' | wc -l"
# EXPECT: far fewer than before (was every single trade)

# 4. Confirm Brier cache seeded on startup
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep 'brier_cache_seeded'"
# EXPECT: games list with Brier values

# 5. Per-game P&L check (24h window)
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT COALESCE(event_data->>'game','?') as game, event_type, COUNT(*), ROUND(SUM(realized_pnl)::numeric,2)
FROM trade_events WHERE bot_name='EsportsBot' AND event_time > NOW()-INTERVAL '24 hours'
GROUP BY 1,2 ORDER BY game, event_type;\""
```

---

## CRITICAL TRAPS (UPDATED FOR S131)

All traps from S130 still apply, plus:

1. **`confidence` in opp dict is now RAW side_prob** — not SQ-dampened. Any code that reads `opp["confidence"]` expecting a dampened value will see higher numbers. Verified: only `_execute_esports_trade` reads it, and Kelly handles raw probability correctly.
2. **`_signal_quality` is a NEW field in opp dict** — defaults to 1.0 if missing (backward-compatible). All 4 entry paths set it.
3. **Confidence gate is now 0.52 minimum** — `max(self._min_confidence, 0.52)`. The .env `ESPORTS_MIN_CONFIDENCE=0.20` still works but 0.52 is the effective floor.
4. **SQ component defaults changed** — agreement 0.50→0.70, calibration 0.30→0.50, brier default 0.25→0.15. These affect ALL games.
5. **Brier cache now seeded in `start()`** — uses `_get_cached_rolling_accuracy()` with ≥10 sample threshold. Games with <10 resolved predictions still use the 0.15 default.

---

## KEY CONFIG (unchanged from S130)

```
ESPORTS_MIN_CONFIDENCE=0.20          # .env — effective floor is 0.52 (S131)
ESPORTS_MIN_EDGE=0.05
ESPORTS_MAX_BET_USD=300
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_MAX_HOLD_HOURS=96
SIMULATION_MODE=true
```

---

## SESSION HISTORY (updated)
| Session | Date | Key Changes |
|---------|------|-------------|
| S128 | Mar 25 | 10 audit bug fixes (LoL calibration P0, graduation gate, stale detection) |
| S129 | Mar 25 | No-op (context ran out) |
| S130 | Mar 25 | Diagnostic — found bot stopped trading due to SQ blocking |
| **S131** | **Mar 25** | **ROOT FIX: SQ moved from confidence to sizing multiplier. 3 component defaults fixed. Brier cache seeded on startup. Confidence gate repurposed to 0.52 side_prob floor.** |

---

## SHARED ENGINE FIXES (Cross-Bot, S132)

These fixes were applied to shared infrastructure. EsportsBot is affected by all three.

### SE-4: Phase 4b Partial Exit P&L Double-Count (P1)
- **File**: `base_engine/data/resolution_backfill.py`
- **Bug**: Phase 4b used `SUM(pt.realized_pnl)` from paper_trades without subtracting EXIT P&L already captured in trade_events. Partial exits were double-counted.
- **Fix**: Added `exit_pnl_already` subquery (matching Phase 4b-alt pattern). RESOLUTION P&L now = raw_pnl - exit_pnl.
- **EsportsBot impact**: 16 exits. Any partially-exited markets had inflated RESOLUTION P&L.
- **Monitor**: `grep "phase4b_exit_pnl_subtracted" | grep EsportsBot`

### SE-2: Re-Entry Guard Race Condition (P2)
- **File**: `base_engine/execution/paper_trading.py`
- **Bug**: Between position close (`del self.positions[pos_key]`) and DB write, a scan could pass re-entry guards and create a duplicate position. 2-second race window.
- **Fix**: Added `_recently_closed` cooldown dict in PaperTradingEngine. BUY blocked for 2s after position close.
- **EsportsBot impact**: Lower risk (scan interval >30s) but same code path.
- **Monitor**: `grep "paper_reentry_blocked" | grep EsportsBot`

### SE-3: DB Write Error Escalation (P3)
- **File**: `base_engine/execution/paper_trading.py`
- **Bug**: Post-lock DB write failures logged at WARNING, invisible to monitoring. Escalated to ERROR with cumulative counter.
- **Monitor**: `grep "post_lock_db_write_failed"` — should be zero.

### EsportsBot-Specific Bugs Still Open (from audit)
- **EB-3**: No opposing-side guard in main scan `_execute_esports_trade()` — 7 Valorant markets bet both sides
- **EB-4**: `_resolve_esports_from_clob()` should be deleted (races with shared resolution path)
- **EB-5**: `confidence` not stored in event_data (only `signal_quality` and `model_prob`)
- **SE-1/EB-1/EB-2**: paper_trades UPDATE in `_resolve_esports_from_clob()` missing realized_pnl + status
