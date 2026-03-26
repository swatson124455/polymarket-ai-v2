# AGENT HANDOFF — WeatherBot Session 117 (2026-03-22)

## STATUS: BOT BROKEN — 0 TRADES POST-DEPLOY, NEEDS SURGICAL ROLLBACK

---

## What Happened This Session (S116)

Session started by picking up S115 backlog items 1-4 in parallel:
1. **Climatology backfill** — DONE. 106/106 stations, 38,796 DOYs. No action needed.
2. **Shadow fill check** — DONE. 7 WeatherBot fills, 0.72% avg slippage. Healthy but low volume.
3. **Hardcoded values audit** — DONE. Found 20 remaining magic numbers (report below).
4. **Cold-start/SAMOS tests** — DONE. 42 tests in `tests/unit/test_weather_cold_start.py`, all passing.

Then 5 code changes were deployed that **broke the bot**:

### Changes Made (in order of suspicion)

| # | Change | File | Lines | Suspect? |
|---|--------|------|-------|----------|
| A | YES confidence gate: skip YES trades with confidence < 0.55 | `bots/weather_bot.py` ~L1167 | Added `_yes_min_confidence` check | **YES — may be filtering ALL temp opps** |
| B | Edge sign flip: `"edge": edge if side == "YES" else -edge` | `bots/weather_bot.py` L1177 | Changed opportunity dict | **YES — may break downstream edge comparisons** |
| C | Combined boost cap wired to `self._combined_boost_cap` (2.5) | `bots/weather_bot.py` ~L2379 | Changed from hardcoded 2.0 | LOW — only affects sizing, not filtering |
| D | `WEATHER_MIN_TRADE_USD=15` in .env | VPS .env only | No code change | MEDIUM — blocks sub-$15 trades |
| E | `WEATHER_BUHLMANN_KAPPA=20` in .env | VPS .env only | No code change | **POSSIBLY NOT READING** — kappa is loaded via `getattr(settings, ...)` but NOT defined in Settings class. May still be 30. |

### Critical Bug: ENV VARS NOT WIRED TO SETTINGS CLASS

`WEATHER_YES_MIN_CONFIDENCE`, `WEATHER_COMBINED_BOOST_CAP`, and `WEATHER_BUHLMANN_KAPPA` were added to the VPS `.env` file BUT were loaded in `weather_bot.py` via:
```python
self._yes_min_confidence = float(getattr(settings, "WEATHER_YES_MIN_CONFIDENCE", 0.55))
```

The `Settings` class in `config/settings.py` does NOT have these attributes. So `getattr` always returns the hardcoded default. The .env values are **being ignored**. Only `WEATHER_MIN_TRADE_USD` (which IS in Settings class) is actually being read from .env.

This means:
- `_yes_min_confidence` = 0.55 (from default, not env)
- `_combined_boost_cap` = 2.5 (from default, not env)
- `_buhlmann_kappa` = 30.0 (from default, not env — the 20 in .env is ignored)

### Evidence of Breakage

**Pre-deploy (13:00-15:38 UTC):**
- `groups_with_edge` = 4-9 per scan
- `best_edge` = 0.07-0.46 (mixed positive/negative in old format)
- Trades firing normally

**Post-deploy (15:38+ UTC):**
- `groups_with_edge` = 0 on EVERY scan
- `best_edge` = 0.0 on EVERY scan
- **ZERO trades in 3+ hours**
- 146 groups scanned, 1500 markets, 35 cities — all healthy inputs, zero output

### Root Cause Analysis (INCOMPLETE — needs next session)

The YES confidence gate (Change A) only blocks YES trades. Pre-deploy, 65% of trades were NO-side. Those should still pass. Yet `groups_with_edge=0` means NO opportunities are being returned from `_analyze_single_bucket()` AT ALL.

Possible explanations:
1. **Edge sign flip (Change B) broke the opportunity filtering downstream** — if something downstream compares `edge > 0` to validate, flipping NO edges to positive could confuse a filter
2. **The confidence calculation change** introduced a subtle bug — the old code used `model_prob` directly as confidence; the new code uses `min(0.95, 1.0 - model_prob)` for NO side. Need to verify this matches how downstream code expects confidence
3. **Restart cleared EMOS calibration** — probability engine starts cold, calibration reload is on 6-hour timer (`WEATHER_CALIBRATION_RELOAD_SECS=21600`). Without EMOS params, raw ensemble → possibly no edges found. BUT this would be a pre-existing restart issue, not caused by code changes.

---

## PLAN FOR NEXT SESSION

### Phase 1: Debug (Option C — understand before reverting)

Add temporary diagnostic logging to `_analyze_single_bucket()` to trace exactly where opportunities die:
```
journalctl -u polymarket-ai | grep 'weatherbot_bucket_debug'
```

Check specifically:
- Is `_prob_engine._get_emos_params()` returning identity (no EMOS loaded)?
- What is `model_prob` for a known market? Is it reasonable?
- Does `abs(edge)` pass the `min_edge` filter?
- Does the YES gate block it? (it shouldn't for NO trades)
- Does anything downstream filter it out?

### Phase 2: Surgical Rollback (Option B — revert only broken changes)

**REVERT these changes:**
1. **Change A (YES gate)** — Remove the `_yes_min_confidence` gate entirely. Re-implement later with proper data validation.
2. **Change B (edge sign flip)** — Revert to `"edge": edge`. The logging was cosmetic; breaking trades is not worth it.

**KEEP these changes:**
3. **Change C (boost cap)** — Harmless, only affects sizing magnitude
4. **Change D (min trade $15)** — This is a valid .env-only change, keep it
5. **Change E (kappa .env)** — It's being ignored anyway (not in Settings class), harmless

**Also fix:**
6. Wire `WEATHER_YES_MIN_CONFIDENCE`, `WEATHER_COMBINED_BOOST_CAP`, and `WEATHER_BUHLMANN_KAPPA` into `config/settings.py` as proper class attributes so .env values actually get read

### Phase 3: Validate

1. Deploy rollback
2. Verify `groups_with_edge > 0` within 2 scan cycles
3. Verify trades resume
4. Monitor 24h before re-attempting any YES-side filtering

---

## PERFORMANCE DATA (from pre-deploy SQL queries)

### Weekly P&L (RESOLUTION events)
| Week | Side | N | P&L | WR |
|------|------|---|-----|-----|
| Mar 16 | NO | 473 | +$454.15 | 85.8% |
| Mar 16 | YES | 221 | +$145.28 | 15.8% |
| Mar 9 | NO | 162 | +$1,300.65 | 74.7% |
| Mar 9 | YES | 90 | +$468.13 | 15.6% |

**Key insight**: YES WR is 15-16% but still profitable because winning YES trades pay large (buying cheap tokens). However, the 85% loss rate on YES is a drag. A confidence gate IS the right idea — just needs to be implemented without breaking NO trades.

### Trade Size Distribution
- Last 20 trades: all NO-side, ranging $3.72-$494.49
- Median around $80-$100 for US cities, $15-$40 for international
- Miami outlier at $494 shows the sizing CAN work when boosts align

### Shadow Fills (post-S115 deploy)
| Bot | Fills | Avg Slippage |
|-----|-------|-------------|
| WeatherBot | 7 | 0.72% |
| EsportsBot | 5 | 24.25% |

WeatherBot slippage healthy. Low sample size (7 fills in ~24h).

### Current Open Positions
- 1 stale position auto-closed in recent scan
- Daily P&L restored: +$81.28

---

## HARDCODED VALUES AUDIT (3A — completed, not yet extracted)

Found 20 actionable hardcoded values across 3 files. Full report from research agent:

### Highest Priority (Tier 2 — trade-universe gating)
1. **Edge cap schedule** (5 lead-time tiers): `<6h: 0.70, <12h: 0.50, <24h: 0.40, <48h: 0.30, else: 0.25`
2. **Penny-bet price filter**: `price <= 0.04 or price >= 0.97`
3. **Drawdown halt/warn**: `20% halt, 10% warn`

### Most Useful to Tune (Tier 1)
4. NBM boost (1.3x)
5. Combined boost cap (2.0 → now 2.5 via code, .env ignored)
6. Kelly graduation schedule (n≥200 + MSE<4 → 0.50 Kelly)
7. Station MSE tiers (4/9/16 thresholds → 1.2/1.0/0.8/0.5 multipliers)
8. Boundary risk factor (0.5x penalty)
9. Regime boost (3+ cities → 1.2x)
10. 429 cooldown (3600s)
11. API timeout (15s)
12. Tail discount default (0.90)
13. Alpha decay half-life (1800s)
14. Drawdown schedule [(8, 0.25), (5, 0.50), (3, 0.75)]
15. Monitoring interval (600s)
16. Stale position age (20h)
17. Drift error threshold (3.0°F)
18. Ensemble std floor (0.5°)
19. NBM sigma schedule (1.5/2.5/3.5/5.0 by lead time)
20. Climate blend schedule (72h start, 168h end, 40% max weight)

**DO NOT extract all 20 at once.** Do 3-5 per session, test each, deploy incrementally.

---

## TEST COVERAGE ADDED

`tests/unit/test_weather_cold_start.py` — 42 tests, all passing:
- Bühlmann credibility (9 tests)
- Spread confidence gate (11 tests)
- EMOS/SAMOS fallback chain (6 tests)
- SAMOS fitting (6 tests)
- Severe weather halt (10 tests)

Full suite: 1668 passed, 0 failed, 8 skipped.

---

## FILES MODIFIED THIS SESSION

| File | Status | Safe? |
|------|--------|-------|
| `bots/weather_bot.py` | Modified (YES gate, edge flip, boost cap, confidence calc) | **NEEDS PARTIAL REVERT** |
| `config/settings.py` | Modified (added WEATHER_YES_MIN_CONFIDENCE, WEATHER_COMBINED_BOOST_CAP) | Keep but verify |
| `tests/unit/test_weather_cold_start.py` | NEW | Safe — tests only |
| VPS `.env` | Modified (added 4 env vars) | Keep WEATHER_MIN_TRADE_USD=15, remove others until wired |

---

## VPS .ENV VARS ADDED THIS SESSION
```
WEATHER_MIN_TRADE_USD=15          # WORKING (in Settings class)
WEATHER_YES_MIN_CONFIDENCE=0.55   # NOT WORKING (not in Settings class, ignored)
WEATHER_COMBINED_BOOST_CAP=2.5    # NOT WORKING (not in Settings class, ignored)
WEATHER_BUHLMANN_KAPPA=20         # NOT WORKING (not in Settings class, ignored)
```

---

## CRITICAL WARNINGS FOR NEXT SESSION

1. **DO NOT add more features until trades resume.** Fix first, improve later.
2. **The edge sign flip may have downstream effects** — grep for every use of `opp["edge"]` and `best_edge` before deciding to keep it.
3. **EMOS calibration may be cold** — check if `_maybe_reload_calibration()` has fired since restart. If not, the probability engine is running on raw ensemble (no EMOS correction), which would produce different probabilities than pre-deploy.
4. **The .env wiring bug affects ALL future env var additions** — any `getattr(settings, "NEW_VAR", default)` pattern MUST also add the attribute to the Settings class, or the .env value is silently ignored.
5. **Test the rollback in isolation** — revert A and B, deploy, confirm `groups_with_edge > 0` within 2 scans, THEN proceed with any new work.
