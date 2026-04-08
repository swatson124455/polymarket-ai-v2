# WeatherBot Master Agent Handoff — Session 162
**Date**: 2026-04-08
**Bot**: WeatherBot (polymarket-weather.service)
**Purpose**: Carbon-copy handoff — zero context loss, pick up seamlessly
**Deploy**: `20260407_210515` — ALL SERVICES LIVE (Weather, Mirror, Esports, Ingestion)
**VPS**: ubuntu@34.251.224.21 | SSH: ~/.ssh/LightsailDefaultKey-eu-west-1.pem
**Service**: polymarket-weather.service | Active since deploy `20260407_210515`
**Commit**: `9f3a421`
**Tests**: 1789 passed (0 failed, 2 skipped, 9 xfailed) — same as S161
**Prior handoffs (READ FOR FULL CONTEXT)**:
  - `AGENT_HANDOFF_WEATHERBOT_SESSION161_MASTER.md` — S161: T1-A position_details rebuild, YES dampener logging, METAR dead code removed
  - `AGENT_HANDOFF_WEATHERBOT_SESSION160_MASTER.md` — S160: 7-agent deep audit, 7 WB fixes, 4 new tests
  - `AGENT_HANDOFF_WEATHERBOT_SESSION159_MASTER.md` — S159: OOS Brier gate, identity conf dampener, EMOS shape

---

## 0. SESSION SCOPE — SINGLE BOT ONLY

WeatherBot-only session. No shared modules touched. All changes are WeatherBot-specific
config/sizing tuning + observability improvements.

---

## 0.1. ABSOLUTE RULES — NEVER VIOLATE

### NEVER DISABLE SIDES OR MARKETS
See S152 handoff section 0.1 for full rationale. Fix calibrator/sizing/gates, never disable.

### BOOSTS ARE NOW DAMPENERS (S153)
Shadow data (30K+ resolved) proved boosts were anti-signal. Do NOT revert without new data.

### CANONICAL P&L RULE (S155 — HARD ENFORCEMENT)
**`bot_pnl.py` is the ONLY authority for P&L numbers.** CLAUDE.md Forbidden Pattern #7.
- Run `scripts/bot_pnl.py <BotName> <hours>` FIRST for any P&L question
- S162 added per-side × per-lead-time cross-tabulation (new section in output)
- ALL financial figures must be labeled `[per bot_pnl.py <Bot> <hours>]` or `[UNVERIFIED]`

### CALIBRATOR DIVERGENCE WATCH (S154)
The isotonic NO calibrator collapses to 2-3 output bins (0.7356, 0.8380). Track positive
divergence at price < $0.70 specifically.

### S162 CRITICAL FINDING: YES SIDE IS THE ROOT CAUSE
The side × lead-time cross-tab proved that what appeared to be two problems (YES losses +
short lead-time losses) is primarily ONE problem: YES-side miscalibration manifesting at
every lead-time bucket. **DO NOT re-propose lead-time tightening as a fix for YES-side
losses in future sessions.** The cross-tab data is definitive:
- <24h NO: -$9,464.63 [per bot_pnl.py WeatherBot 9999] at 60.5% WR [per bot_pnl.py WeatherBot 9999] — genuine lead-time problem
- <24h YES: -$5,752.63 [per bot_pnl.py WeatherBot 9999] at 49.8% WR [per bot_pnl.py WeatherBot 9999] — YES calibration problem
- 72-120h NO: +$773.71 [per bot_pnl.py WeatherBot 9999] at 88.8% WR [per bot_pnl.py WeatherBot 9999] — PROFITABLE, do not touch
- 72-120h YES: -$5,744.21 [per bot_pnl.py WeatherBot 9999] at 20.2% WR [per bot_pnl.py WeatherBot 9999] — YES is anti-predictive at long horizons

---

## 1. WHAT S162 DID

### S162 Changes (4 files, all WeatherBot-scope)

| File | Change | Type |
|------|--------|------|
| `scripts/bot_pnl.py` | Added side × lead-time cross-tab query + WB-16 comment | Observability |
| `bots/weather_bot.py` | <24h lead-time multiplier 0.85→0.60 | Tier 1 config |
| `config/settings.py` | YES_SIZE_MULTIPLIER 0.75→0.50 | Tier 1 config |
| `base_engine/weather/probability_engine.py` | WB-10: MLE scale out-of-bounds debug log | Observability |

### Change 1 — Side × Lead-Time Cross-Tab (bot_pnl.py)
- **What**: New SQL query combining per-side and per-lead-time breakdowns into a 2×5 matrix
- **Why**: Required to determine whether lead-time losses are independent or a symptom of YES miscalibration
- **Finding**: YES is broken at ALL lead-times (20.2% WR at 72-120h). <24h NO also independently negative.
- **Lines**: Added after per-lead-time section, before calibrator status

### Change 2 — <24h Lead-Time Multiplier Tightened (weather_bot.py:3058-3062)
- **What**: `WEATHER_LEAD_TIME_MULT_0_24` default 0.85→0.60
- **Why**: Cross-tab showed <24h is negative for BOTH sides. NO <24h: -$9,464.63 [per bot_pnl.py WeatherBot 9999] at 60.5% WR [per bot_pnl.py WeatherBot 9999]. Short-horizon markets have less forecast skill and more efficient pricing.
- **Override**: `export WEATHER_LEAD_TIME_MULT_0_24=0.85` to revert via env var
- **Blast radius**: WeatherBot only. Reduces <24h trade sizes by ~29% (0.60/0.85).

### Change 3 — YES_SIZE_MULTIPLIER Reduced (config/settings.py:795-796)
- **What**: Default 0.75→0.50
- **Why**: YES all-time: 1,221 trades [per bot_pnl.py WeatherBot 9999], 37.8% WR [per bot_pnl.py WeatherBot 9999], -$24,424.90 [per bot_pnl.py WeatherBot 9999] = 77% of total losses [calculated from bot_pnl.py WeatherBot 9999]
- **Calibrator impact**: NONE — n_yes counts resolved ENTRY→RESOLUTION pairs from trade_events, not trade sizes. Reducing size changes dollar risk, not sample count.
- **Combined YES dampening**: confidence × 0.85 (identity dampener) + size × 0.50 (this). Entry gate unaffected.
- **Override**: `export WEATHER_YES_SIZE_MULTIPLIER=0.75` to revert
- **Blast radius**: WeatherBot only.

### Change 4 — WB-10 MLE Scale Out-of-Bounds Log (probability_engine.py:151-156)
- **What**: Added `logger.debug("weather_mle_scale_out_of_bounds")` when MLE scale falls outside [0.1, 30.0]
- **Why**: Previously silently fell through to normal distribution with no observability
- **Blast radius**: WeatherBot only. Debug-level log, no behavioral change.

---

## 2. CURRENT BOT STATE

**Service**: polymarket-weather.service — running S162 deploy `20260407_210515`

### Canonical P&L [per bot_pnl.py WeatherBot 9999, run 2026-04-08 00:25 UTC]
- Open positions: 30 [per bot_pnl.py WeatherBot 9999]
- Cost basis: $1,630.92 [per bot_pnl.py WeatherBot 9999]
- All-time realized: -$31,788.02 [per bot_pnl.py WeatherBot 9999]
- Realized (exits): +$684.46 [per bot_pnl.py WeatherBot 9999]
- Realized (resolutions): -$32,472.48 [per bot_pnl.py WeatherBot 9999]
- NO side: 2,107 trades [per bot_pnl.py WeatherBot 9999], 71.5% WR [per bot_pnl.py WeatherBot 9999], -$7,543.89 [per bot_pnl.py WeatherBot 9999]
- YES side: 1,221 trades [per bot_pnl.py WeatherBot 9999], 37.8% WR [per bot_pnl.py WeatherBot 9999], -$24,424.90 [per bot_pnl.py WeatherBot 9999]

### Calibrator [per bot_pnl.py WeatherBot 9999]
- NO model: fitted (n_no=640 [per bot_pnl.py WeatherBot 9999])
- YES model: IDENTITY PASSTHROUGH (n_yes=62 [per bot_pnl.py WeatherBot 9999], needs >=100)
- OOS Brier: 0.2479 [per bot_pnl.py WeatherBot 9999] vs raw 0.2601 [per bot_pnl.py WeatherBot 9999] (delta -0.0122 [per bot_pnl.py WeatherBot 9999])
- Graduation estimated ~Apr 20-26 [UNVERIFIED — estimated from entry rate]

---

## 3. S161 HANDOFF CORRECTIONS

Two errors found in S161 handoff:
1. **"YES price dampener never shipped"** — WRONG. It IS shipped at weather_bot.py:3017-3032 (S155B). Zero fires in current log window because no YES entries exceeded the $0.50 soft cap — correct behavior.
2. **WB-1 "T1-A bypasses self.place_order()"** — FALSE POSITIVE. Line 3434 shows `await self.place_order(...)`. T1-A correctly uses the wrapper.

---

## 4. AUDIT ITEMS STATUS (S160 audit + S162 updates)

### STRUCK (false positive or already fixed)
| ID | Reason |
|----|--------|
| WB-1 | FALSE POSITIVE — T1-A uses `self.place_order()` at line 3434 |
| WB-2 | ALREADY FIXED — `logger.warning("integrate_bucket_unknown_type")` at probability_engine.py:232 |
| WB-3 | ALREADY FIXED — cooldown prune in `_handle_daily_boundary()` at weather_bot.py:3992-4000 |
| WB-6 | ALREADY FIXED — bare `except: pass` replaced with logger |
| WB-7 | ALREADY FIXED — bare `except: pass` replaced with logger |
| WB-13 | CLASS NOT FOUND — `StationHealthMonitor` doesn't exist in current codebase |
| WB-14 | NON-ISSUE — `.get("score", 0.0)` handles missing field correctly |
| WB-15 | ALREADY FIXED — METAR dead code removed in S161 commit `08bcce0` |
| WB-17 | ALREADY FIXED — S160 changed default bot to WeatherBot |

### FIXED IN S162
| ID | Fix |
|----|-----|
| WB-10 | Added `logger.debug("weather_mle_scale_out_of_bounds")` in probability_engine.py |
| WB-16 | Documented DISTINCT ON limitation with comment in bot_pnl.py (rare for WeatherBot) |

### REMAINING OPEN (all P3, low priority)
| ID | Description |
|----|-------------|
| WB-4 | f-string SQL for EMOS interval (int-cast, not exploitable) |
| WB-5 | 6 redundant `import aiohttp` inside methods (no runtime cost) |
| WB-8 | Naive datetime in `_restore_daily_pnl_from_db` (works because UTC) |
| WB-9 | Redundant `getattr` in probability_engine variance inflation |
| WB-11 | Unreachable `else 18` in model_run_monitor init hour |
| WB-12 | `import re` inside function body in station_registry |
| WB-18 | `::float` cast on `lead_time_hours` could fail on malformed data |

---

## 5. POST-DEPLOY MONITORING

### M0: negative_ev is the primary gate (S162 finding)
Post-deploy funnel: 174 negative_ev blocks vs 49 exposure_cap blocks [journalctl].
The NO calibrator (n_no=640 [per bot_pnl.py WeatherBot 9999], fitted) is correctly deflating
overconfident model probabilities below market price — rejecting ~78% of raw-edge signals.
This is correct behavior. The long-term fix is improving the underlying probability model
so more signals survive calibration with genuine edge, NOT loosening gates.

### M1: YES entry rate
- After 0.50x multiplier deploys, verify YES entries are still occurring (identity dampener at confidence level is the real gate, not size)
- `grep -c 'weather_entry.*YES' /tmp/wb.log`

### M2: <24h trade volume
- 0.60x multiplier reduces size but doesn't block. Verify trades still flowing.
- `grep 'lead_time_mult' /tmp/wb.log | head -10`

### M3: YES calibrator graduation
- n_yes=62 [per bot_pnl.py WeatherBot 9999], needs >=100. Est ~Apr 20-26 [UNVERIFIED].
- When graduated, YES identity dampener auto-removes.

### M4: MLE scale out-of-bounds frequency
- New debug log. Check: `grep -c 'mle_scale_out_of_bounds' /tmp/wb.log`
- If frequent, investigate station data quality.

### M5-M9: Carried from S161/S160
- M5: EMOS shape zeroed frequency
- M6: OOS Brier gate activity
- M7: Drawdown compression
- M8: DB statement timeout 60s
- M9: International METAR station activity

---

## 6. INVARIANTS — NEVER BREAK THESE

All S154-S161 invariants (36-68) remain active.

**S162 additions:**
69. **YES is the root cause, not lead-time**: Cross-tab proved YES side is anti-predictive at
    all lead-times (20.2% WR at 72-120h). Do NOT re-propose lead-time fixes for YES losses.
70. **NO 48-120h is profitable**: +$418.36 [per bot_pnl.py WeatherBot 9999] (48-72h) and
    +$773.71 [per bot_pnl.py WeatherBot 9999] (72-120h). Do NOT tighten these buckets.
71. **YES_SIZE_MULTIPLIER doesn't affect calibrator graduation**: n_yes counts resolved
    entries, not trade sizes. Reducing multiplier is safe for calibrator timeline.
72. **negative_ev is the primary trading gate**: The NO calibrator rejects ~78% of raw-edge
    signals. This is correct — do NOT loosen gates. Fix the underlying probability model.
73. **Exposure caps are correctly sized**: City deployment is well below $10K cap. The
    exposure_cap shadow entries are legitimate capacity constraints, not bugs.
74. **Always split logs by deploy timestamp**: Pre-deploy noise in unsplit log windows
    caused a false diagnosis in S162. Filter `--since <deploy_time>` first.

---

## 7. VPS OPERATIONS

```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# FIRST ACTION — Canonical P&L
cd /opt/polymarket-ai-v2 && sudo -u polymarket PYTHONPATH=/opt/polymarket-ai-v2 \
  DB_STATEMENT_TIMEOUT_MS=120000 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 9999
# NOTE: 9999h window requires DB_STATEMENT_TIMEOUT_MS=120000 (30s default times out)

# S162-specific log checks
journalctl -u polymarket-weather -n 10000 --no-pager --output=cat 2>/dev/null > /tmp/wb.log
grep -c "mid_life_exit" /tmp/wb.log           # T1-A exits (should be > 0)
grep -c "yes_identity_dampener_applied" /tmp/wb.log  # YES dampener (should be > 0)
grep -c "mle_scale_out_of_bounds" /tmp/wb.log  # WB-10 new log (expect low)
grep -c "scan_ms" /tmp/wb.log                  # Scan completions
rm /tmp/wb.log

# Deploy from LOCAL
bash deploy/deploy.sh
# POST-DEPLOY: .env.weather has all settings — NO manual sed needed
# If env var override desired: add to /opt/pa2-shared/.env.weather
```

---

## 8. FIRST ACTIONS FOR NEXT SESSION

1. **Run canonical P&L**: `bot_pnl.py WeatherBot 9999` (DB_STATEMENT_TIMEOUT_MS=120000)
2. **Check cross-tab**: Verify SIDE x LEAD-TIME section appears in output
3. **Compare P&L trend**: Has YES-side loss rate decreased with 0.50x multiplier?
4. **Check calibrator graduation**: Has n_yes crossed 100? (was 62 at S162 start)
5. **Monitor negative_ev ratio**: 174/223 signals blocked post-deploy. Is ratio stable or improving?
6. **3-4 day check**: Run `bot_pnl.py WeatherBot 48` — if P&L per trade improved, system is working
7. **Long-term**: Improve underlying probability model so fewer signals get rejected by calibrator

---

## 9. SESSION CHAIN

| Session | Key Change | Deploy |
|---------|-----------|--------|
| S154 | NO price dampener, lead-time mult, variance inflation | 20260402_165503 |
| S155/B | 10-bug fix + YES price dampener, T1-A exits enabled | 20260405_230442 |
| S159 | OOS Brier gate, identity conf dampener, 17 cross-bot fixes | 20260406_154734 |
| S160 | Deep audit (7 agents). 7 WB fixes + 11 shared + 10 Esports | 20260406_203453 |
| S161 | T1-A alive (position_details rebuild), elite batch decoupled | 20260407_172053 |
| **S162** | **Cross-tab query, <24h mult 0.85→0.60, YES size 0.75→0.50, WB-10 log** | **20260407_210515** |

---

## 10. POST-DEPLOY VALIDATION (done in-session)

### Deployment
- Commit `9f3a421`, deploy `20260407_210515` — health OK at 60s, all 4 services active

### Canonical P&L (post-deploy)
- 84 entries in 48h [per bot_pnl.py WeatherBot 48]
- 40 open positions [per bot_pnl.py WeatherBot 9999], all opened within last 12h [VPS psql]
- Zero legacy/stale positions — book is clean
- 42 active cities [journalctl], 2 unmatched (Busan, Panama City — NOT weather markets)

### Trading Funnel (post-deploy, since 01:07 UTC) [all journalctl]
- 174 negative_ev blocks (primary gate — calibrator correctly rejecting overconfident signals)
- 49 exposure_cap blocks (legitimate — cities with open positions at capacity)
- 6 zero_kelly blocks
- Slippage rejections on 2-3 illiquid markets (Houston YES, Jakarta NO) — correct behavior
- Multiple successful fills in first scan, steady-state ~2-5 trades/hour

### Key Lesson (for future sessions)
- Always split log data by deploy timestamp BEFORE analyzing. Pre-deploy noise (128 of 177
  exposure_cap blocks) led to a misdiagnosis of in-memory counter drift that didn't exist.
- The real finding: negative_ev is the primary gate, not exposure caps. The NO calibrator
  is doing its job — the underlying model is systematically overestimating edge.

### Items Verified Not Bugs
- Exposure caps ($10K city, $20K group) are NOT too tight — actual city deployment is well
  below caps. The caps correctly constrain per-city concentration.
- In-memory exposure counters are NOT drifting — rollback on failed orders works correctly
  (weather_bot.py:3246-3259).
- Busan/Panama City "unmatched cities" are political/sports markets, not weather — no
  onboarding needed.
- YES price dampener (S155B) IS shipped at weather_bot.py:3017-3032 — S161 handoff was wrong.

---

## 11. CHANGE LOG

```
## CHANGE: 2026-04-08 (S162 — WeatherBot session, 4 files)
**Issue:** YES side accounts for 77% of losses; <24h losing on both sides; no cross-tab to diagnose
**Root cause:** (1) YES uncalibrated at all lead-times (20.2% WR at 72-120h);
  (2) <24h markets have less forecast skill for both sides;
  (3) no observability on side × lead-time interaction
**Files modified:**
  scripts/bot_pnl.py (+33: cross-tab query + WB-16 comment)
  bots/weather_bot.py (+3/-1: <24h multiplier 0.85→0.60 + comment)
  config/settings.py (+2/-1: YES_SIZE_MULTIPLIER 0.75→0.50)
  base_engine/weather/probability_engine.py (+5: WB-10 MLE scale debug log)
**Blast radius:** All 4 files WeatherBot-only. No shared module changes.
  bot_pnl.py is a script (no runtime impact). Config changes overridable via env vars.
**Verification:** 1789 tests pass (0 fail). Cross-tab query verified on VPS.
**Rollback:** git revert <sha>; or override via .env.weather:
  export WEATHER_LEAD_TIME_MULT_0_24=0.85
  export WEATHER_YES_SIZE_MULTIPLIER=0.75
```
