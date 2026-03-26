# AGENT HANDOFF — EsportsBot Session 124 (2026-03-23)
## FULL SYSTEM HANDOFF — Carbon Copy for New Agent

---

## SESSION SUMMARY

This session was a **diagnostic/data-mining session** for EsportsBot. No code was written. Two previously-implemented fixes (F+G game tags) were verified as already deployed. The session focused on:

1. Deep NO-side win rate analysis (all-time + 48h)
2. Full calibration pipeline audit
3. Full liquidity/execution pipeline audit
4. Reviewing proposed fixes and narrowing to data-backed decisions
5. Building this handoff for seamless continuation

---

## CRITICAL DATA FINDINGS

### P&L Reality (All-Time, RESOLUTION only)

| Side | Trades | Wins | WR | P&L | Avg P&L |
|------|--------|------|----|-----|---------|
| YES | 93 | 37 | 39.8% | -$786 | -$10.48 |
| NO | 85 | 24 | 28.2% | -$2,214 | -$36.90 |
| **Total** | **178** | **61** | **34.3%** | **-$3,000** | **-$16.85** |

**Both sides are losing.** Total esports P&L is approximately -$3,000.

### NO Side by Token Price (All-Time)

| Price Bucket | Trades | WR | P&L |
|-------------|--------|----|-----|
| 70-85c | 4 | 75% | +$108 |
| 50-70c | 18 | 44% | +$111 |
| 30-50c | 39 | 23% | -$845 |
| <30c | 24 | 17% | -$1,588 |

**Pattern**: NO tokens above 50c are profitable (55% WR, +$219). Below 30c is a death trap.

### YES Side by Token Price (All-Time)

| Price Bucket | Trades | WR | P&L |
|-------------|--------|----|-----|
| 50-70c | 26 | 62% | +$251 |
| 30-50c | 45 | 31% | -$1,222 |
| <30c | 20 | 30% | +$163 |
| 70c+ | 2 | 50% | +$22 |

**Pattern**: YES 50-70c is the sweet spot (62% WR, +$251). Below 50c is mixed.

### 48h Snapshot (Most Recent)

| Side | Resolved | Wins | WR | P&L |
|------|----------|------|----|-----|
| YES | 9 | 6 | 46% | +$625 |
| NO | 7 | 2 | 18% | -$1,468 |

48h NO losses averaged -$337 each. Every NO loss was the model saying a market favorite was dramatically overpriced — and being wrong every time.

### Sizing Problem
NO losers average 423 shares, NO winners average 131 shares. Cheaper tokens = more shares per dollar = bigger losses. The sizing pipeline gives MORE capital to worse bets.

### Confidence Is Useless
Every single resolved trade (YES and NO) was in the 75%+ confidence bucket. Zero discrimination.

---

## CALIBRATION PIPELINE AUDIT (FULL)

### Current State: Effectively Uncalibrated

**BetaCalibrator** (8 instances, 1 per game):
- min_samples=15, data window starts 2026-03-16 (7 days of clean data)
- Almost certainly UNFITTED for most/all games due to low trade volume
- When unfitted: raw predictions pass through unchanged AND many guardrails are suspended (Kelly degradation, phase penalty, phi floor)
- Refits every 10 min from `esports_prediction_log`
- Location: `esports_bot.py` lines 47-148, applied at lines 1892-1896

**OnlinePlattCalibrator** (8 instances, 1 per game):
- min_samples=30, streaming via River LogisticRegression
- Almost certainly UNFITTED (needs 30 resolved predictions per game in 7 days)
- When fitted, OVERRIDES BetaCalibrator (doesn't blend — one or the other)
- Location: `esports_bot.py` lines 151-191, applied at lines 1898-1901

**PatchDriftDetector Brier checks: DEAD**
- Nobody calls `record_prediction()` to feed resolved outcomes
- Patch version detection works, but the rolling Brier / calibration halt features never fire
- Location: `esports/models/patch_drift.py`

**Model Graduation Gate: BYPASSED**
- `esports_trainer.py` line 284-285: `result["graduated"] = True` always
- The accuracy/Brier thresholds (`_MIN_ACCURACY=0.55`, `_MAX_BRIER=0.24`) are computed but never enforced
- All trained models auto-graduate

**Brier Halt Threshold: DISABLED**
- `ESPORTS_BRIER_HALT_THRESHOLD=1.0` — Brier score maxes at 1.0, so this can never trigger

**CoT Config Mismatch**:
- `ESPORTS_COT_EDGE_THRESHOLD=0.15` in settings.py is DEAD CODE
- `cot_validator.py` hardcodes `EDGE_THRESHOLD=0.20` at line 31

### Full Prediction Flow

1. **Raw model** → game-specific XGBoost or pure Glicko-2 fallback
2. **Enrichment** → form adj, TabPFN blend, cross-game XGB, LAN adj, blue side bonus, BO format adj
3. **Beta calibration** → pass-through if unfitted (current state)
4. **Online Platt** → pass-through if unfitted (current state)
5. **RFLB** → nudges favorites toward 0.50 when price>0.70 and model_prob>0.60
6. **Edge computation** → `model_prob - price`, side selection
7. **Uncertainty filter** → skip if matchup uncertainty >= 0.70 AND BO1 AND edge < 0.10
8. **Tournament phase multiplier** → SUSPENDED while calibrators unfitted
9. **Confidence gate** → must pass 0.52
10. **CoT validation** → LLM check for edge >= 0.20

**Net result**: The bot is running on raw Glicko-2 with enrichment tweaks, no calibration, suspended guardrails, and a confidence calculation that puts everything at 75%+.

### Game-Specific Models

| Game | Model | Calibrator | Effective Features | Known Issues |
|------|-------|------------|-------------------|--------------|
| LoL | XGBoost | Internal Isotonic | team_strength_diff only | +1.9% blind blue bonus, 31% accuracy worst game |
| CS2 | XGBoost 3-tier | Internal Isotonic (round) | team_strength_diff dominant | LAN asymmetry -2%/+1% |
| Dota2 | XGBoost | None | 6 Glicko-2 features | Heuristic fallback |
| Valorant | XGBoost | None | 6 Glicko-2 features | Heuristic fallback |
| CoD/R6/SC2/RL | TabPFN sparse | None | Limited data | Low volume |

### Learning Loops

| Loop | Status | Notes |
|------|--------|-------|
| BetaCalibrator batch | UNFITTED | Needs 15+ resolved per game since Mar 16 |
| OnlinePlatt streaming | UNFITTED | Needs 30+ resolved per game |
| ADWIN drift detection | ACTIVE | Logs warnings, doesn't block trades |
| Model retraining | ACTIVE (24h) | Auto-graduates everything |
| Adaptive EGM d | ACTIVE | Per-game, Brier-based |
| Adaptive Kelly | ACTIVE | Brier-based graduation |
| PatchDrift Brier | DEAD | No one feeds resolved predictions |
| Edge decay sizing | ACTIVE | CLV-based multiplier |

---

## LIQUIDITY / EXECUTION PIPELINE AUDIT (FULL)

### Execution Chain

1. `analyze_opportunity()` → price validation, model prediction, edge calc, confidence gate
2. `_execute_esports_trade()` → sizing (BotBankrollManager + Kelly + dampeners), calls `place_order()`
3. `BaseBot.place_order()` → thin wrapper to `OrderGateway.place_order()`
4. `OrderGateway.place_order()` → kill switch, canary, drawdown, adverse selection, RL timing, risk limits, **book walk**, paper/live execution

### Book Walk (VWAP)
- Snapshots live L2 orderbook (top 20 levels)
- Walks asks (BUY) or bids (SELL) to compute VWAP at target size
- Returns: vwap_price, fill_fraction, slippage
- Thin book → higher VWAP → edge-at-VWAP gate may reject

### Edge-at-VWAP Gate
- `confidence - shadow_vwap <= 0` → trade rejected
- Spread guard: spread > 80c → force reject (dead market)
- WeatherBot exempt, EsportsBot is NOT exempt
- **Bug**: Line 839 references undefined `_check_price` variable — would cause NameError in log statement (rejection still works, just log fails)

### Liquidity Check: SKIPPED for Esports
- `order_gateway.py` lines 603-607 explicitly returns None for EsportsBot/EsportsLiveBot/EsportsSeriesBot
- Comment: "esports orderbooks are chronically thin"
- This means no cascade check, no liquidity guardian

### Fill Probability Model: DOES NOT EXIST
- The `volume` parameter is passed through the entire pipeline but **never consumed**
- `PaperTradingEngine.place_order()` accepts `volume` as parameter but never references it
- There is no fill probability calculation — just VWAP book walk + partial fill fraction

### Liquidity-Based Sizing: NONE
- Size is purely confidence/Kelly-driven with multiple dampeners (phi, drawdown, game mult, decay, upset risk)
- None of these dampeners incorporate orderbook depth or volume
- When book is thin: VWAP degrades, edge-at-VWAP may reject, partial fill applied — but size is never reduced proactively

### Volume Passthrough (GAP-1)
- Wired correctly: `_market_index` → `_clob_volume` → `event_data["volume_24h"]` → paper engine
- But paper engine ignores it (dead parameter)

---

## PROPOSED FIXES (REVIEWED, NOT YET IMPLEMENTED)

### Approved for Next Session
- **Fix F + G (game tags)**: ALREADY DONE from prior session. EXIT line 1490, RESOLUTION line 1710. Need to verify tags are populating in prod (SSH timed out during session).

### Reviewed, User Deferred Pending More Data
All of these were analyzed with full pro/con/guard breakdown:

| Fix | What | Status | User Decision |
|-----|------|--------|---------------|
| A: Underdog RFLB | Dampen underdog predictions toward market | DROPPED | "Wouldn't that hurt what's working?" — correct, dampening wrong predictions doesn't fix them |
| B: NO edge multiplier 1.5x | Require NO trades to prove more edge | DEFERRED | Wait for per-game data from game tags |
| C: EGM d=1.5→1.0 | Reduce blending extremization | DEFERRED | Affects YES side too, monitor first |
| D: Kill blue side bonus | Remove +1.9% LoL bias | DEFERRED | Wait for per-game data |
| E: Symmetrize LAN adj | -2%/+1% → ±1.5% | DEFERRED | Wait for per-game data |
| H: Shared backfill game tags | Game tag on resolution_backfill.py | DEFERRED | Crosses esports scope lock |
| 35c price floor | Reject tokens < 35c | DEFERRED | Wait for per-game data |
| EGM to config var | Make d=1.5 tuneable without redeploy | DEFERRED | Do alongside other fixes |

### User's Key Insight
"If we have 18% win rate, can we not reverse engineer what to do from what not to do?" — The losing pattern IS the signal. Don't try to fix the model's prediction. Study WHERE it fails (cheap tokens, specific games, extreme disagreement with market) and either filter those trades or fade the signal.

---

## ITEMS TO REVIEW AND RESOLVE (NEXT SESSION)

### P0 — Calibration Is Broken
1. **BetaCalibrator status check**: Query `esports_prediction_log` to see actual sample counts per game since Mar 16. Are ANY games fitted?
2. **Suspended guardrails**: While calibrators are unfitted, phase penalty, Kelly degradation, and phi floor are all disabled. The bot is running with no safety net. Either: (a) fast-track calibrator fitting by lowering min_samples further, or (b) re-enable guardrails independent of calibrator status.
3. **PatchDriftDetector is dead**: Wire `record_prediction()` calls or delete the Brier/calibration features. Currently dead code pretending to provide safety.
4. **Graduation gate bypassed**: Models auto-graduate. Either enforce the Brier/accuracy gate or remove the dead code.

### P1 — Confidence Calculation Is Useless
5. **Everything reads 75%+**: The confidence number is not discriminating good trades from bad. Need to trace exactly how confidence is computed and why it's so compressed. If confidence can't separate winners from losers, any confidence-based sizing or gating is theater.

### P2 — Sizing Is Backwards
6. **Losers get 3x the size of winners**: Cheap tokens = more shares per dollar. The Kelly sizer doesn't account for the fact that cheap-token bets have lower base rates. Need to either: (a) add price-awareness to sizing, or (b) cap shares (not just dollars) on cheap tokens.

### P3 — No Liquidity Awareness in Sizing
7. **Volume parameter is dead**: Passed through entire pipeline, never consumed. Either build a fill probability model that uses it, or stop pretending.
8. **No liquidity sizing**: When book is thin, size should decrease. Currently relies entirely on VWAP rejection gate, which is binary (trade or don't). Need a continuous sizing reduction.

### P4 — Dead Code / Config Mismatches
9. **CoT edge threshold**: Config says 0.15, code says 0.20. Pick one.
10. **Brier halt threshold**: Set to 1.0 (impossible to trigger). Either set a real value or remove.
11. **`_check_price` bug**: Line 839 of order_gateway.py references undefined variable. Causes NameError in log (not in trade logic, but messy).

### P5 — Data Collection for Decisions
12. **Verify game tags in prod**: SSH timed out. Confirm EXIT and RESOLUTION events have game tags.
13. **Wait 48h after verification**: Then pull per-game P&L to make data-backed decisions on fixes B/C/D/E/price floor.

---

## KEY ARCHITECTURE (ESPORTSBOT-SPECIFIC)

### Files
- `bots/esports_bot.py` — main bot (~3300 lines)
- `esports/models/esports_trainer.py` — model training orchestrator
- `esports/models/cot_validator.py` — Claude Haiku sanity check
- `esports/models/patch_drift.py` — patch monitoring
- `esports/models/dota2_model.py`, `cs2_economy_model.py`, `lol_win_model.py`, `valorant_model.py`, `series_model.py` — per-game models
- `esports/data/esports_data_collector.py`, `esports_db.py`, `opendota_client.py` — data layer
- `base_engine/execution/order_gateway.py` — execution pipeline (shared)
- `base_engine/execution/paper_trading.py` — paper fills (shared)
- `base_engine/risk/risk_manager.py` — risk limits (shared)
- `config/settings.py` — all config (shared)

### Config (Live VPS)
```
ESPORTS_MIN_EDGE=0.05
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MAX_BET_USD=$300
ESPORTS_MAX_TOTAL_EXPOSURE_USD=$15,000
ESPORTS_KELLY_FRACTION=0.25
ESPORTS_BETA_CAL_MIN_SAMPLES=15
ESPORTS_BRIER_HALT_THRESHOLD=1.0 (effectively disabled)
ESPORTS_COT_EDGE_THRESHOLD=0.15 (dead — class uses 0.20)
ESPORTS_USE_CONFORMAL=false
ESPORTS_CATBOOST_ENABLED=false
ESPORTS_CLV_SCALING_ENABLED=false
```

### Sizing Chain
1. BotBankrollManager (Kelly fraction * confidence * bankroll)
2. phi_factor (Glicko-2 uncertainty dampener)
3. dd_factor (drawdown Kelly reduction)
4. _game_mult (per-game Kelly multiplier)
5. _decay_mult (edge decay scaling)
6. Upset risk scaling
7. CLV-gated max override
8. ESPORTS_MAX_BET_USD cap ($300)
9. ESPORTS_MIN_TRADE_USD floor ($10)
10. 0.10 shares minimum

### Traps
- Liquidity check is SKIPPED for esports (order_gateway lines 603-607)
- Fill probability model DOES NOT EXIST (volume param is dead)
- Confidence always reads 75%+ (no discrimination)
- BetaCalibrator unfitted → guardrails suspended
- PatchDriftDetector Brier checks are dead
- Model graduation gate is bypassed
- CoT edge threshold config is dead code
- EGM d=1.5 hardcoded in aggregation.py, esports passes it from 4 call sites

---

## VISION / STRATEGY

### What's Working
- YES 50-70c trades: 62% WR, +$251 all-time
- NO 50c+ trades: 55% WR, +$219 all-time
- Game tag infrastructure (F+G) is in place
- Glicko-2 team ratings provide real signal
- Book walk / VWAP gate catches illiquid trades

### What's Not Working
- Everything below 50c on both sides
- NO trades overall (-$2,214)
- Confidence calculation (zero discrimination)
- Calibration (unfitted, guardrails suspended)
- Sizing (inversely correlated with quality)

### Strategy
1. Get per-game data flowing (verify game tags) — **immediate**
2. Use per-game data to make surgical decisions — **48h from now**
3. Fix calibration pipeline (fit calibrators or re-enable guardrails) — **next session**
4. Fix confidence calculation — **next session**
5. Consider price floor / NO edge multiplier once we have game-level data — **session after**

### User Preferences
- Prefers data-driven decisions, not guesses
- "What can you almost guarantee will work?" — only pure additive/diagnostic changes
- Skeptical of anything that cuts off trades without understanding why they fail
- Wants to use losing data as signal ("18% WR is as informative as 82% WR")
- Bot-scoped sessions (this is esports only)

---

## DEPLOY STATUS
No code changes this session. F+G were pre-existing. Current VPS deploy is from prior session. Game tags should already be live if the prior deploy included them — need to verify by checking recent events for populated game fields.

## VERIFICATION NEEDED
```bash
# SSH to VPS and check game tags
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo -u postgres psql -d polymarket -c "
  SELECT event_type, event_data->>'game' AS game, COUNT(*)
  FROM trade_events
  WHERE bot_name='EsportsBot' AND event_time > now()-interval '24h'
  GROUP BY 1,2 ORDER BY 1,2;
"

# If game tags are NULL/empty, check which deploy is running
ls -la /opt/polymarket-ai/current
cat /opt/polymarket-ai/current/bots/esports_bot.py | grep -n "_res_game\|_exit_game"
```
