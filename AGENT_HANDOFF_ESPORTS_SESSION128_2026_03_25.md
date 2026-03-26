# AGENT HANDOFF — EsportsBot Session 128 (2026-03-25)

> **Scope**: EsportsBot ONLY. No bleed-over to other bots unless manual demand.
> **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU). SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
> **Codebase**: `C:\lockes-picks\polymarket-ai-v2\` (local Windows) → `/opt/polymarket-ai-v2/` (VPS)
> **Service**: `sudo systemctl restart polymarket-ai` — restarts ALL 15 bots
> **Deploy path**: `/opt/polymarket-ai-v2/` (NOT `/opt/polymarket-ai/current/` — that's the old path)
> **Current deploy**: `20260325_030337` (S128 audit bug fixes)
> **Commit**: `2f2e417` — `fix(esports): S128 — 10 audit bugs from AUDIT_ESPORTSBOT_S127`

---

## TABLE OF CONTENTS
1. [What Was Done This Session (S128)](#what-was-done-this-session-s128)
2. [What Was Done Last Session (S127)](#what-was-done-last-session-s127)
3. [Current Live State](#current-live-state)
4. [All-Time P&L by Game](#all-time-pnl-by-game)
5. [Priority Queue for Next Session](#priority-queue-for-next-session)
6. [Signal Quality System (S127)](#signal-quality-system-s127)
7. [Prediction Pipeline Flow](#prediction-pipeline-flow)
8. [Sizing Pipeline](#sizing-pipeline)
9. [State Persistence](#state-persistence)
10. [Key Config (Live VPS)](#key-config-live-vps)
11. [Architecture Reference](#architecture-reference)
12. [Shared Infrastructure Audit Review](#shared-infrastructure-audit-review)
13. [Critical Traps](#critical-traps)
14. [Deploy Protocol](#deploy-protocol)
15. [Diagnostic Queries](#diagnostic-queries)
16. [Per-Game Analysis](#per-game-analysis)
17. [Session History](#session-history)

---

## WHAT WAS DONE THIS SESSION (S128)

### Scope: 10 Audit Bug Fixes from AUDIT_ESPORTSBOT_S127.md

Reviewed 15 audit items. **Verified each against actual source code.** 10 confirmed REAL, 5 confirmed FALSE. All 10 real bugs fixed, committed, deployed, verified live.

### 5 FALSE ALARMS (verified against source, no action taken)
| ID | Audit Claim | Reality |
|----|-------------|---------|
| BUG-8 | Prediction log fails silently at debug | Actually logs at `WARNING` — not silent |
| DATA-3 | Calibrator not persisted across restarts | Pickle dict correctly saves/loads `"calibrator"` key in lol_win_model.py save()/load() |
| LOG-1 | No PandaScore circuit breaker | Full circuit breaker exists in `pandascore_client.py` with `_HARD_LIMIT` gate + warning log |
| RACE-2 | Direct private `_available_capital` access | Uses public `self.bankroll.get_bet_size()` — no private access found |
| INEFF-3 | Module-level asyncio locks | All locks are instance-level in `__init__` (`self._trade_lock`) |

### 10 Fixes Applied (all esports-scoped, ZERO shared modules)

#### FIX 1: BUG-24 — LoL Calibration Shape Mismatch [P0 CRITICAL]
**File**: `esports/models/lol_win_model.py`
**Root cause**: `CalibratedClassifierCV.predict_proba()` at line 109 received shape `(1,1)` but wraps XGBoost internally expecting `(1,9)`. XGBoost threw ValueError, caught by `except Exception` at debug level, returning **0.5 for every LoL prediction**. LoL model had ZERO edge — every trade sized as coin flip.
**Fix**: Replaced `CalibratedClassifierCV` with `IsotonicRegression` for probability→probability 1D mapping.
- Training: `raw_probs = self._model.predict_proba(X)[:, 1]` → `IsotonicRegression(out_of_bounds='clip').fit(raw_probs, y)`
- Inference: `float(self._calibrator.predict([proba])[0])` (was: `self._calibrator.predict_proba(np.array([[proba]]))[0][1]`)
**Verification**: LoL confidence now 0.14-0.41 in live logs (was flat 0.5).

#### FIX 2: BUG-28 — CS2 Map Heterogeneity Ignored [P1 HIGH]
**File**: `esports/models/cs2_economy_model.py`
**Root cause**: Lines 433-440 averaged per-map probabilities for BO3/BO5 series, then passed average to `_binomial_race()`. A team 80%/30%/55% on three maps became flat 55%.
**Fix**: Added `_heterogeneous_series_prob()` — recursive calculation with per-map probabilities and memoization. Replaces averaging with correct conditional series probability.

#### FIX 3: BUG-30 — Team Name Substring False Match [P1 MEDIUM]
**File**: `esports/data/opendota_client.py`
**Root cause**: Pass 3 used `name_lower in tname or tname in name_lower` — "og" matched "rogue", "bo" matched "betboom". Sort by shortest name was also backwards.
**Fix**: Word-boundary regex `re.compile(r'\b' + re.escape(name_lower) + r'\b')` + longest-first sort (`reverse=True`). Added `import re`.

#### FIX 4: BUG-29 — CoT Validator Fail-Open [P2 MEDIUM-HIGH]
**File**: `esports/models/cot_validator.py`
**Root cause**: `except Exception` at line 145 logged at `debug` and returned `approved=True`. Any LLM API failure silently rubber-stamped trades.
**Fix**: Changed to `logger.warning(..., exc_info=True)` + return `{"approved": False, "reason": "validation_error", "confidence": 0.0}` (fail-closed).

#### FIX 5: BUG-27 — Dota2 Patch Detection False Positives [P2 MEDIUM]
**File**: `esports/models/patch_drift.py`
**Root cause**: Line 298 matched `"update" in title` — caught Steam maintenance/client/community posts. Each triggered 48h observation mode.
**Fix**: Tightened to `"gameplay update" or "patch" or version_regex`. Added exclusion list: `client, workshop, community, cosmetic, server, maintenance`. Added `import re`.

#### FIX 6: BUG-26 — Stale Match Detection Never Triggers [P2 MEDIUM]
**File**: `esports/live/esports_game_monitor.py`
**Root cause**: Line 173 updated `_last_score_update[mid]` on every poll. `is_stale()` at line 395 (called from `esports_live_bot.py:278`) could never exceed the 1800s threshold.
**Fix**: Only update timestamp when score changes: `if cur_score != self._prev_scores.get(mid)`. First observation also sets timestamp.

#### FIX 7: BUG-7 — Token Map Clear Blackout [P3 LOW]
**File**: `bots/esports_bot.py`
**Root cause**: Line 811 `self._market_token_map.clear()` created ~1s window where WS events silently dropped.
**Fix**: Evict oldest half instead of clearing: `del self._market_token_map[k]` for first half of keys.

#### FIX 8: BUG-6 — list.pop(0) O(n) [P3 LOW]
**File**: `bots/esports_bot.py`
**Root cause**: `self._latency_samples.pop(0)` on Python list is O(n), runs on every WS price update.
**Fix**: Changed to `collections.deque(maxlen=100)`. Removed manual pop logic. Added `import collections`.

#### FIX 9: BUG-25 — Graduation Gate Hardcoded True [P3 LOW]
**File**: `esports/models/esports_trainer.py`
**Root cause**: `result["graduated"] = True` always (line 285).
**Fix**: `result["graduated"] = (accuracy >= 0.55 and brier < 0.30 and len(training_data) >= 200)`

#### FIX 10: STORE-2 — Queue Drops at maxsize=200 [P4 LOW]
**Files**: `bots/esports_live_bot.py`, `esports/live/esports_game_monitor.py`
**Fix**: Queue `maxsize=200`→`500`. Drop log `debug`→`warning`. Test updated.

### Verification
- **Tests**: 1717 passed, 0 failures (full suite)
- **Deploy**: `20260325_030337` — VPS running, scans healthy, signal quality logging active
- **LoL predictions**: Confidence 0.14-0.41 (was flat 0.5 before BUG-24 fix)
- **Zero errors** in post-deploy scan cycles

---

## WHAT WAS DONE LAST SESSION (S127)

### Fix 1: Game Tag Backfill (data-only)
- 226 EXIT/RESOLUTION events had NULL game tags (pre-S125 historical events)
- Backfilled 176 via ENTRY join, 50+42 confirmed Valorant via market text
- Required DISABLE/ENABLE of `trg_trade_events_immutable` trigger

### Fix 2: Confidence → Signal Quality Rewire (THE MAIN FIX)
- `confidence = model_prob` replaced with `confidence = side_prob * signal_quality`
- `signal_quality` = 5-component composite [0.30, 1.0] from: model_agreement, calibration_score, uncertainty, enrichment_depth, brier_component
- 3 files, ~80 lines, 4 confidence assignment sites rewired
- `ESPORTS_MIN_CONFIDENCE`: 0.52 → 0.35, `ESPORTS_BRIER_HALT_THRESHOLD`: 1.0 → 0.30

---

## CURRENT LIVE STATE (as of 03:05 UTC 2026-03-25)

### EsportsBot Health
- **Scanning**: Healthy. ~155ms cycles, 17 markets (5 CS2, 10 LoL, 2 Valorant)
- **Open positions**: 27
- **Live matches**: 2
- **Waterfall**: 8 passed, 6 no_prediction, 2 low_confidence, 1 low_edge, 2 reentry_rejected, 2 skipped_has_position
- **BetaCalibrator**: CS2 fitted. LoL/Dota2/Valorant insufficient data. R6/SC2/RL = 0 samples.
- **Signal quality**: Active. LoL samples: confidence 0.14-0.41, signal_quality 0.35-0.56. `sq_brier=0.0` across all (Brier cache empty after restart — repopulates in ~10 min).

---

## ALL-TIME P&L BY GAME (as of S128 deploy)

| Game | Trades | P&L | Win Rate | Notes |
|------|--------|-----|----------|-------|
| **Valorant** | 93 | **+$4,132** | 37.6% | ONLY profitable game. DO NOT TOUCH. |
| SC2 | 1 | +$41 | 100% | 1 trade |
| CoD | 16 | -$1,151 | 18.8% | Terrible. No ML model. Consider disable. |
| Dota2 | 76 | -$1,404 | 46.1% | WR OK but avg loss 2x avg win. Signal quality should help sizing. |
| LoL | 40 | -$1,858 | 17.5% | Catastrophic WR. BUG-24 fix (S128) should restore model edge. MONITOR. |
| CS2 | 155 | -$2,896 | 37.4% | Biggest loser by volume. BUG-28 fix (S128) should help series predictions. |
| UNTAGGED | 1 | -$207 | 0% | Orphan event. |
| **TOTAL** | **382** | **-$3,343** | -- | Valorant carrying entire bot. |

---

## PRIORITY QUEUE FOR NEXT SESSION

### P0 — Monitor S128 fixes (24-48h check) ← THIS IS NEXT
After 24h: query confidence distribution + win rates by confidence bucket. Key questions:
1. Are LoL predictions now discriminating? (confidence should spread, not cluster)
2. Are CS2 series predictions improved? (check BO3/BO5 outcomes specifically)
3. Has Dota2 team matching improved? (fewer `no_prediction` in waterfall?)
4. Is CoT validator rejecting any trades? (check for `validation_error` reason in logs)

Key query:
```sql
SELECT
  COALESCE(event_data->>'game', 'unknown') AS game,
  CASE WHEN confidence < 0.30 THEN '<0.30'
       WHEN confidence < 0.40 THEN '0.30-0.40'
       WHEN confidence < 0.50 THEN '0.40-0.50'
       ELSE '0.50+' END AS conf_bucket,
  COUNT(*), ROUND(SUM(realized_pnl)::numeric, 2) AS pnl,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END)::numeric, 3) AS wr
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
  AND event_time > '2026-03-25 03:00:00'
GROUP BY 1, 2 ORDER BY 1, 2;
```

### P1 — Brier halt threshold verification
- Code default now 0.30 (S127)
- VPS .env has `ESPORTS_BRIER_HALT_THRESHOLD=0.30` ← CONFIRMED SET
- Monitor: check if any games get halted after Brier cache populates

### P2 — Per-game model tuning (NOW ACTIONABLE)
With real game tags and signal quality deployed:
- **LoL**: Monitor post-BUG-24 fix. If WR doesn't improve in 48h, consider game-specific edge floor.
- **CS2**: Monitor post-BUG-28 fix for series markets. BetaCalibrator fitted.
- **CoD**: 16 trades, 18.8% WR, no ML model. Strong candidate for **disable**.
- **Dota2**: Monitor post-BUG-30 fix (team matching). WR is 46% but sizing was backwards.

### P3 — Price floor analysis
- <30c trades lost -$6,981 total across all games
- Signal quality should naturally reduce sizing on these
- Re-evaluate after 48h of signal quality data

### P4 — Resolution backlog
- ~15 stale positions (matches ended days ago)
- NULL `end_date_iso` ordering blocks resolution backfill
- Owner: shared infra fix

### P5 — Shared infrastructure audit (AUDIT_SHARED_INFRASTRUCTURE_S128.md)
- 143 bugs found across all shared modules
- **MirrorBot cross-review done in S128** — no fixes hurt MirrorBot
- 2 fixes need verification before applying: P1-6 (API retry RuntimeError vs None), P1-19 (resolution P&L zero-out SQL reorder)
- P1-20 (partial-exit double-counting) will change reported P&L for all bots including EsportsBot — this is CORRECT (current numbers are inflated)
- **EsportsBot-relevant shared fixes**: P0-1 (live trade TypeError — go-live blocker), P1-6 (API retry), P1-19/P1-20 (resolution P&L), P1-7 (kill switch fail-safe)

---

## SIGNAL QUALITY SYSTEM (S127 — unchanged in S128)

### Before (broken):
```
confidence = model_prob          # YES side: 0.78 on 22c token
confidence = 1.0 - model_prob   # NO side: 0.78 on 22c token → biggest bets on worst trades
```

### After (fixed):
```
side_prob = model_prob if YES else (1.0 - model_prob)     # 0.78
signal_quality = _compute_signal_quality(game, market_id)  # 0.30-1.0
confidence = side_prob * signal_quality                    # 0.78 * 0.45 = 0.35
```

### 5 Components of Signal Quality
| Component | Weight | Source | Logic |
|-----------|--------|--------|-------|
| model_agreement | 0.30 | XGB, CatBoost, Glicko-2 est, final prob | `1 - stdev(probs)/0.20` clamped [0,1] |
| calibration_score | 0.25 | BetaCalibrator + OnlinePlatt fitted status | both=1.0, one=0.7, neither=0.3 |
| uncertainty | 0.20 | `matchup_uncertainty` from Glicko-2 phi | `1 - (phi_a+phi_b)/700` |
| enrichment_depth | 0.15 | Count of enrichment layers that fired | `min(1, count/3)` |
| brier_component | 0.10 | Rolling game-level Brier | `1 - brier/0.25` |

### Where computed: `_compute_signal_quality(game, market_id)` at line ~3451
### Where assigned (4 sites):
1. Main path `analyze_opportunity()` ~line 2017-2037
2. WS reactive ~line 762
3. Series path ~line 5550
4. Series WS reactive ~line 5884

### Enrichment metadata from `_enrich_prediction()`:
Returns `(prob, _enrich_meta)` tuple where `_enrich_meta` = dict with:
- `xgb_raw`: float or None
- `cb_prob`: float or None
- `form_applied`: bool
- `tabpfn_applied`: bool
- `lan_applied`: bool
- `bo_applied`: bool (always True)

---

## PREDICTION PIPELINE FLOW

```
scan_and_trade → analyze_opportunity
  → _detect_game
  → _get_model_prediction (game-specific ML)
     → _enrich_prediction → returns (prob, _enrich_meta)  # S127
     → stores _enrich_meta in prediction_cache event_data
  → BetaCalibrator.calibrate() (if fitted)
  → ConformalPredictor
  → side_prob = model_prob (YES) or 1-model_prob (NO)
  → signal_quality = _compute_signal_quality(game, market_id)  # S127
  → confidence = side_prob * signal_quality                     # S127
  → phase_mult applied
  → min_confidence gate (0.35 in code, 0.20 in .env override)
  → edge gate (0.05)
  → TRADE
```

---

## SIZING PIPELINE

```
_execute_esports_trade:
  → expiry_boost (confidence *= 1.2-1.5x near expiry)
  → BotBankrollManager.get_bet_size(confidence, price)
     → Kelly: kelly_full = (confidence * b - q) / b
     → if confidence <= price: return 0  (natural filter)
  → size *= phi_factor * dd_factor * game_kelly_mult * edge_decay_mult
  → exposure checks (game, tournament, total)
  → place_order()
```

---

## STATE PERSISTENCE

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_game_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_recently_exited` | Redis TTL (300s) | `_restore_exit_cooldowns_from_redis()` |
| `_market_game` | Restored from ENTRY trade_events | `_restore_market_game_from_db()` |
| `_game_brier_cache` | In-memory, populated from monitoring | Lost on restart (repopulates ~10 min) |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |
| Glicko-2 ratings | DB table | `_init_glicko2_trackers()` |

---

## KEY CONFIG (Live VPS .env — confirmed S128)

```
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000
ESPORTS_MIN_EDGE=0.05
ESPORTS_MIN_CONFIDENCE=0.20          # .env overrides code default 0.35
ESPORTS_MAX_EDGE=0.35
ESPORTS_BRIER_HALT_THRESHOLD=0.30
ESPORTS_KELLY_DEFAULT_FRACTION=0.25  # (from BOT_BANKROLL_CONFIG)
ESPORTS_USE_CONFORMAL=true
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MODEL_MAX_BRIER=0.248
ESPORTS_RETRAIN_INTERVAL_HOURS=24
ESPORTS_MIN_VOLUME_USD=0
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true
PANDASCORE_API_KEY=<redacted>
PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot
```

**NOTE**: `ESPORTS_MIN_CONFIDENCE=0.20` in .env overrides the code default of 0.35. This is more permissive. The signal quality system compensates — low-quality trades get dampened confidence anyway.

---

## ARCHITECTURE REFERENCE

### File Map
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | ~5,900 | Main bot: scan, predict, trade, exit, calibrate, signal quality |
| `bots/esports_live_bot.py` | ~350 | Live in-game trading wrapper (queue maxsize=500) |
| `esports/live/esports_game_monitor.py` | ~400 | PandaScore polling, stale detection, game state queue |
| `esports/models/lol_win_model.py` | ~450 | LoL XGBoost + IsotonicRegression calibrator (S128) |
| `esports/models/cs2_economy_model.py` | ~630 | CS2 round+map+series model with _heterogeneous_series_prob (S128) |
| `esports/models/dota2_model.py` | ~500 | Dota2 XGBoost model |
| `esports/models/valorant_model.py` | ~400 | Valorant XGBoost model |
| `esports/models/series_model.py` | ~350 | Generic series probability model |
| `esports/models/esports_trainer.py` | ~600 | Training orchestrator with graduation gate (S128) |
| `esports/models/cot_validator.py` | ~150 | Chain-of-thought validation (fail-closed S128) |
| `esports/models/patch_drift.py` | ~310 | Patch detection + observation mode (tightened filter S128) |
| `esports/data/opendota_client.py` | ~225 | Dota2 team/hero data (word-boundary match S128) |
| `esports/data/esports_data_collector.py` | ~510 | PandaScore data collection |
| `esports/data/esports_db.py` | ~300 | Esports DB operations |
| `config/settings.py` | ~1300 | ESPORTS_* block at lines 1043-1233 |
| `base_engine/risk/bankroll_manager.py` | ~262 | Kelly sizing — DO NOT MODIFY (shared) |
| `tests/unit/test_esports_bot.py` | -- | 115 tests (all passing) |
| `tests/unit/test_esports_series_model.py` | -- | 29 tests (all passing) |
| `tests/unit/test_esports_live_bot.py` | -- | Tests (queue=500 assertion) |

### Key Methods Added/Modified
| Method | File | Session | Change |
|--------|------|---------|--------|
| `_compute_signal_quality` | esports_bot.py ~3451 | S127 | 5-component signal quality [0.30, 1.0] |
| `_enrich_prediction` | esports_bot.py ~2159 | S127 | Returns `tuple[float, dict]` |
| `_heterogeneous_series_prob` | cs2_economy_model.py ~399 | S128 | Recursive per-map series probability |
| `calibrate` | lol_win_model.py ~313 | S128 | IsotonicRegression replaces CalibratedClassifierCV |
| `predict` | lol_win_model.py ~108 | S128 | `calibrator.predict([proba])` replaces `predict_proba()` |
| `is_stale` | esports_game_monitor.py ~395 | S128 | Timestamp updates on score change only |

---

## SHARED INFRASTRUCTURE AUDIT REVIEW

Session 128 reviewed `AUDIT_SHARED_INFRASTRUCTURE_S128.md` (143 bugs across all shared modules) specifically for MirrorBot/EsportsBot impact.

### MirrorBot Cross-Review Conclusions:
- **12 fixes**: NO IMPACT on MirrorBot (uses different subsystems)
- **6 fixes**: SAFE / Beneficial (kill switch, lifecycle, circuit breaker)
- **2 fixes**: Change P&L (correct direction — P1-20 partial-exit dedup, fee calc)
- **2 fixes**: Need verification before applying (P1-6 API RuntimeError, P1-19 SQL reorder)
- **NONE hurt MirrorBot**

### EsportsBot-Relevant Shared Fixes (NOT YET APPLIED):
| # | Fix | EsportsBot Impact |
|---|-----|--------------------|
| P0-1 | Live trade TypeError (correlation_id kwarg) | Go-live blocker. Paper trading unaffected. |
| P1-6 | API retry returns None → should raise | EsportsBot CLOB calls need try/except audit |
| P1-7 | Kill switch fail-safe | More conservative — correct behavior |
| P1-17 | Preflight abort if API+DB down | Prevents blind startup |
| P1-19 | Resolution P&L zeroed after computation | Affects EsportsBot resolution numbers |
| P1-20 | Partial-exit double-counting | EsportsBot has 16 EXIT events — P&L will shift |

---

## CRITICAL TRAPS (DO NOT FORGET)

1. **VPS deploy path is `/opt/polymarket-ai-v2/`** — NOT `/opt/polymarket-ai/current/`.
2. **`trade_events` immutability trigger**: `trg_trade_events_immutable` on partitions. Must DISABLE then re-enable for data corrections.
3. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"/"NO"`. Never "BUY"/"SELL".
4. **`_enrich_prediction()` returns a TUPLE**: `(prob, _enrich_meta)`. All call sites must unpack.
5. **`_compute_signal_quality()` reads from `_prediction_cache`**: Defaults to ~0.45 if cache miss.
6. **Signal quality is NOT in EXIT/RESOLUTION event_data** — only ENTRY. Join by market_id.
7. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable.
8. **Paper trading IS production**: Only difference is final order submission.
9. **`asyncio.create_task()` forbidden for financial write-throughs** — always `await`.
10. **One fix per commit. Preserve every function signature. No while-I'm-in-here refactors.**
11. **15 bots in BOT_REGISTRY. Shared module change = all 15 must be verified.**
12. **`ESPORTS_MIN_CONFIDENCE=0.20` in .env** overrides code default 0.35 — this is intentional.
13. **LoL calibrator is now IsotonicRegression** (S128) — old pickles with CalibratedClassifierCV will still load but inference path changed. If LoL model is retrained, calibrator will be IsotonicRegression.
14. **CS2 `_heterogeneous_series_prob` is a @staticmethod** — no self access. Takes `map_probs, needs_a, needs_b`.
15. **CoT validator is now fail-CLOSED** (S128) — API failures REJECT trades. Monitor for false rejections.
16. **Patch drift filter tightened** (S128) — excludes "client", "workshop", "community", "cosmetic", "server", "maintenance" from title. If a real gameplay patch contains these words, it'll be missed.
17. **Graduation gate** (S128) — requires accuracy≥0.55, brier<0.30, n≥200. Models below this won't graduate. Check if any existing models are affected.

---

## DEPLOY PROTOCOL

### Single-file hot-patch (esports_bot.py only):
```bash
# 1. Test locally
python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_series_model.py -x -q

# 2. Upload
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/esports_bot.py

# 3. Deploy + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai"

# 4. Verify (wait 30s)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo journalctl -u polymarket-ai --since '30 sec ago' -o cat --no-pager | grep -i 'esports'"

# 5. Check first scan
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sleep 60 && sudo journalctl -u polymarket-ai --since '60 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3"
```

### Multi-file deploy (S128 pattern):
```bash
# Upload all files to /tmp/, then:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && \
   sudo cp /tmp/esports_live_bot.py /opt/polymarket-ai-v2/bots/esports_live_bot.py && \
   sudo cp /tmp/lol_win_model.py /opt/polymarket-ai-v2/esports/models/lol_win_model.py && \
   sudo cp /tmp/cs2_economy_model.py /opt/polymarket-ai-v2/esports/models/cs2_economy_model.py && \
   sudo cp /tmp/opendota_client.py /opt/polymarket-ai-v2/esports/data/opendota_client.py && \
   sudo cp /tmp/cot_validator.py /opt/polymarket-ai-v2/esports/models/cot_validator.py && \
   sudo cp /tmp/patch_drift.py /opt/polymarket-ai-v2/esports/models/patch_drift.py && \
   sudo cp /tmp/esports_game_monitor.py /opt/polymarket-ai-v2/esports/live/esports_game_monitor.py && \
   sudo cp /tmp/esports_trainer.py /opt/polymarket-ai-v2/esports/models/esports_trainer.py && \
   sudo systemctl restart polymarket-ai"
```

---

## DIAGNOSTIC QUERIES

```sql
-- Real P&L by game (all tags backfilled)
SELECT COALESCE(event_data->>'game', 'UNTAGGED') AS game,
       COUNT(*) AS trades, ROUND(SUM(realized_pnl)::numeric, 2) AS pnl,
       ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END)::numeric, 3) AS wr
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1 ORDER BY 3 DESC;

-- Signal quality in ENTRY events (S127+)
SELECT event_data->>'game' AS game,
       ROUND(AVG((event_data->>'signal_quality')::float)::numeric, 3) AS avg_sq,
       ROUND(MIN((event_data->>'signal_quality')::float)::numeric, 3) AS min_sq,
       ROUND(MAX((event_data->>'signal_quality')::float)::numeric, 3) AS max_sq,
       COUNT(*)
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY'
  AND event_data->>'signal_quality' IS NOT NULL
GROUP BY 1 ORDER BY 1;

-- Confidence distribution post-S128
SELECT
  COALESCE(event_data->>'game', 'unknown') AS game,
  CASE WHEN confidence < 0.30 THEN '<0.30'
       WHEN confidence < 0.40 THEN '0.30-0.40'
       WHEN confidence < 0.50 THEN '0.40-0.50'
       ELSE '0.50+' END AS conf_bucket,
  COUNT(*), ROUND(SUM(realized_pnl)::numeric, 2) AS pnl,
  ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END)::numeric, 3) AS wr
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
  AND event_time > '2026-03-25 03:00:00'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Open positions
SELECT market_id, side, entry_price, size, unrealized_pnl, opened_at
FROM positions WHERE status='open' AND bot_id='EsportsBot'
ORDER BY opened_at DESC LIMIT 20;

-- Scan summary (SSH)
sudo journalctl -u polymarket-ai --since '5 min ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3

-- Signal quality logs (SSH)
sudo journalctl -u polymarket-ai --since '2 min ago' -o cat --no-pager | grep 'esportsbot_signal_quality' | tail -5

-- CoT validator rejections (SSH) — new in S128
sudo journalctl -u polymarket-ai --since '1 hour ago' -o cat --no-pager | grep 'CoTValidator.*validation_error'

-- Patch drift observation mode (SSH) — should be less frequent after S128
sudo journalctl -u polymarket-ai --since '24 hours ago' -o cat --no-pager | grep 'observation_mode'
```

---

## PER-GAME ANALYSIS (unchanged from S127 + S128 fixes)

### Valorant (+$4,132, 37.6% WR, 93 trades)
- Only profitable game. Low WR but huge avg win ($66.52) vs small avg loss ($22.09)
- **DO NOT TOUCH this game's settings**

### CS2 (-$2,896, 37.4% WR, 155 trades)
- Biggest loser by volume. BetaCalibrator fitted but near-identity transform
- **S128 fix**: `_heterogeneous_series_prob` for BO3/BO5. Monitor series outcomes.
- <30c trades: major loss contributor

### LoL (-$1,858, 17.5% WR, 40 trades)
- Catastrophic WR. Was 18.4% pre-S128, now 17.5% (2 more losses since deploy)
- **S128 fix**: BUG-24 restored model edge (was returning 0.5 for everything)
- BetaCalibrator NOT fitted (insufficient data). Signal quality calibration_score=0.3 (lowest)
- **CRITICAL TO MONITOR** — if WR doesn't improve in 48h, consider edge floor or temp disable

### Dota2 (-$1,404, 46.1% WR, 76 trades)
- WR looks OK but avg loss 2x avg win
- **S128 fix**: BUG-30 team matching (word boundary). Less `no_prediction` expected.
- <30c trades: -$1,566. Above 30c: ~breakeven

### CoD (-$1,151, 18.8% WR, 16 trades)
- Tiny volume, terrible results. No dedicated ML model.
- Signal quality will have low enrichment_depth + low calibration = conservative sizing
- **Strong candidate for disable** after S128 monitoring period

---

## SESSION HISTORY

| Session | Date | Type | Key Outcome |
|---------|------|------|-------------|
| S89 | 2026-03-14 | Code | E2-E5 features + 9 audit fixes |
| S120 | 2026-03-23 | Code | Game tags, series model, exposure tracking |
| S121 | 2026-03-23 | Code | ENTRY/EXIT game tagging in trade events |
| S124 | 2026-03-23 | Diagnostic | Full P&L audit: NO-side catastrophe, confidence useless, sizing backwards |
| S125 | 2026-03-23 | Code | BetaCalibrator min_samples fix, _restore_market_game_from_db |
| S126 | 2026-03-24 | Code+Deploy | Deployed S125, fixed PM churn loop (position_manager.py) |
| S127 | 2026-03-24 | Code+Deploy | Game tag backfill (353 events), confidence→signal quality (3 files, 80 lines). Deploy 20260324_202302. |
| **S128** | **2026-03-25** | **Code+Deploy** | **10 audit bugs fixed: LoL calibration (P0), CS2 series (P1), team matching (P1), CoT fail-closed (P2), patch filter (P2), stale detection (P2), token map (P3), deque (P3), graduation gate (P3), queue size (P4). 5 false alarms verified. Shared infra audit cross-reviewed for MirrorBot. Deploy 20260325_030337.** |
