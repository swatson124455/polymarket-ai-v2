# S172 Phase RC — Root-Cause Investigation Plan

**Date:** 2026-04-13
**Status:** DRAFT — awaiting approval
**Trigger:** 1I edge verification gate fired. All 3 bots P(edge>0) < 0.07 [UNVERIFIED — from scripts/edge_verification.py, not bot_pnl.py].
**Scope:** All 3 bots. Per-bot investigation with shared diagnostic framework.
**Timeline:** 2-3 weeks analysis per bot, parallel where possible
**Prerequisite:** Phase 1 complete (deployed). Runs parallel to Phase 2 infra work.

---

## Purpose

The S172 plan's 1I gate triggered ROOT-CAUSE INVESTIGATION for all 3 bots. This document defines what that investigation looks like: diagnostic steps, hypotheses, data collection, and decision criteria.

**Goal:** For each bot, determine whether negative edge is caused by a fixable problem (sizing, market selection, exit timing, fees, signal quality) or whether the approach is fundamentally unprofitable. Then either fix the identified cause or kill the bot.

---

## Shared Diagnostic Framework

Every bot gets the same 6-layer decomposition. The layers are ordered from most likely fixable to least:

### Layer 1: Fee & Slippage Decomposition
**Question:** What fraction of gross edge is consumed by transaction costs?
**Method:**
- From trade_events: compute gross_pnl = realized_pnl + fees for each closed trade
- Compare gross edge (before fees) to net edge (after fees)
- If gross edge is positive but net edge is negative → fee/slippage problem, not signal problem
- Use 1J orderbook data (accumulating now) for forward-looking slippage estimates

### Layer 2: Win/Loss Magnitude Asymmetry
**Question:** Are losses disproportionately larger than wins?
**Method:**
- Distribution of |win_pnl| vs |loss_pnl| per trade
- Mean win size vs mean loss size
- If WR > 50% but edge < 0 → losses are outsized (sizing or exit timing problem)
- WB is the prime suspect here: 59.3% WR [UNVERIFIED] but -14.67% edge [UNVERIFIED]

### Layer 3: Entry Edge Analysis
**Question:** Did the signal have edge at entry time, or was the entry already bad?
**Method:**
- For each ENTRY event: compare predicted_probability vs entry_price
- Compute edge_at_entry = predicted_prob - price (for YES) or (1-predicted_prob) - price (for NO)
- Distribution of edge_at_entry: if clustered near zero or negative → signal was never ahead of market
- Compare edge_at_entry to eventual resolution → did the signal predict correctly?

### Layer 4: Exit Timing Analysis
**Question:** Are exits destroying value?
**Method:**
- For EXIT events: compare exit_price to eventual resolution_value
- If positions are exited at worse prices than they would have resolved at → exits are premature
- Hold-time distribution: are profitable trades cut short? Are losing trades held too long?
- Compare realized_pnl of EXIT vs RESOLUTION events separately

### Layer 5: Market/Segment Decomposition
**Question:** Are specific segments profitable while others destroy all value?
**Method:**
- Per-bot segment analysis (details in bot-specific sections below)
- If some segments are profitable and others are deeply negative → market selection filter needed
- Compute edge contribution by segment: which segments contribute most to total loss?

### Layer 6: Temporal Analysis
**Question:** Was there ever positive edge, or has it always been negative?
**Method:**
- Rolling 30-day edge over time
- Cumulative P&L curve — is it monotonically declining or are there profitable periods?
- If edge was once positive then degraded → market adapted, model stale
- If edge was never positive → fundamental approach problem

---

## Bot-Specific Investigation

### RC-WB: WeatherBot

**Primary hypothesis:** High WR + deeply negative edge = asymmetric loss sizing. WB wins many small bets and loses fewer but much larger bets.

**Specific diagnostics:**

| # | Analysis | Data Source | Expected Insight |
|---|----------|-------------|-----------------|
| WB-1 | Win/loss magnitude distribution | trade_events | Quantify the asymmetry |
| WB-2 | P&L by side (YES vs NO) | trade_events joined to entry side | NO-side losses suspected to be outsized |
| WB-3 | P&L by lead time bucket (<24h, 24-48h, 48-72h, 72-120h) | trade_events + market end_date | Historical data suggested 72-120h was profitable |
| WB-4 | P&L by city | trade_events + market text parsing | Some cities may be profitable |
| WB-5 | P&L by confidence bucket | trade_events confidence field | Are high-confidence trades better? |
| WB-6 | Fee impact | trade_events fees field | What % of gross edge goes to fees? |
| WB-7 | YES-side specific: entry_price distribution | trade_events price for YES entries | Buying YES at 0.85+ risks full stake for small upside |
| WB-8 | Resolution timing | trade_events RESOLUTION vs expected end_date | Are markets resolving against us before our window? |

**Kill criteria:** If Layer 3 shows WB signal never had edge at entry (predicted_prob tracks market price), then the forecasting model isn't adding value. Kill unless model replacement is feasible within 4 weeks.

**Fix criteria:** If Layer 2 shows fixable asymmetry (e.g., NO-side losses are 3x win size → add hard stop at -X%), implement the fix and re-run 1I after 500+ new trades.

### RC-MB: MirrorBot

**Primary hypothesis:** Copy-trading signal quality is poor — copying traders who aren't actually profitable. 39.7% WR [UNVERIFIED] on 9,519 trades [UNVERIFIED] suggests the copied signals don't have predictive value.

**Specific diagnostics:**

| # | Analysis | Data Source | Expected Insight |
|---|----------|-------------|-----------------|
| MB-1 | P&L by copy source wallet tier | trade_events + elite_traders | Are T1 copies better than T2/T3? |
| MB-2 | P&L by market category | trade_events + market category | Crypto, politics, sports — which destroys value? |
| MB-3 | P&L by position size bucket | trade_events size field | Are larger bets worse? |
| MB-4 | P&L by hold time | trade_events ENTRY→EXIT/RESOLUTION time | Optimal hold time |
| MB-5 | Wallet win-rate verification | Compare wallet WR claim vs actual MB WR on their picks | Are we copying "profitable" traders who aren't? |
| MB-6 | Entry latency impact | trade_events event_time vs RTDS timestamp | Are we entering too late (price already moved)? |
| MB-7 | Opposing-side analysis | trade_events side | Are NO copies worse than YES? |
| MB-8 | Fee as % of gross | trade_events fees | Copy-trading has tight margins — are fees killing it? |

**Kill criteria:** If MB-5 shows that the copied traders themselves aren't profitable on the trades we copy, then the copy signal is broken. Kill unless wallet selection overhaul (Phase 7B) is viable without the full Phase 7.

**Fix criteria:** If MB-1 shows T1 is profitable but T2/T3 are destroying value → tighten to T1-only and re-evaluate.

### RC-EB: EsportsBot

**Primary hypothesis:** Small sample (541 trades [UNVERIFIED]) + game-specific issues. Some games may have edge while others don't.

**Specific diagnostics:**

| # | Analysis | Data Source | Expected Insight |
|---|----------|-------------|-----------------|
| EB-1 | P&L by game (CS2, LoL, Dota2, Valorant, etc.) | trade_events + game classification | Which games are profitable? |
| EB-2 | P&L by tournament tier | trade_events + event metadata | Major vs minor tournament |
| EB-3 | P&L by edge_at_entry bucket | trade_events confidence/price | Do high-edge entries perform better? |
| EB-4 | P&L by favorite/underdog | trade_events price (>0.50 = favorite) | Favorite-longshot bias? |
| EB-5 | Exit churn analysis | trade_events EXIT events timing | Is edge_gone exit destroying value? |
| EB-6 | Glicko-2 accuracy by game | prediction_log + resolutions | Is the rating model accurate anywhere? |
| EB-7 | XGB stale model impact | trade_events + model freshness | Fresh vs stale model performance |
| EB-8 | LoL YES dampener impact | trade_events LoL YES entries | Did the 40% dampener help? |

**Kill criteria:** If EB-1 shows all games are negative and EB-6 shows Glicko-2 predictions are no better than market prices, the model has no informational advantage. Kill.

**Fix criteria:** If EB-1 shows 1-2 games are profitable and others aren't → restrict to profitable games and re-evaluate. If EB-5 shows exit churn is the primary loss driver → fix exit logic before re-evaluating signal.

---

## Execution Plan

### Week 1: Build diagnostic script + WB analysis

| Day | Task | Output |
|-----|------|--------|
| 1 | Build `scripts/root_cause_analysis.py` — shared 6-layer decomposition | Script |
| 1-2 | Run WB-1 through WB-8 | WB diagnostic report |
| 3 | Analyze WB results, form fix hypothesis | Fix proposal or kill recommendation |
| 4-5 | If fixable: implement fix. If not: WB kill recommendation + pause bot | Code or decision doc |

### Week 2: MB + EB analysis

| Day | Task | Output |
|-----|------|--------|
| 1-2 | Run MB-1 through MB-8 | MB diagnostic report |
| 2-3 | Run EB-1 through EB-8 | EB diagnostic report |
| 3-4 | Cross-bot synthesis: shared patterns? | Synthesis doc |
| 4-5 | Implement fixes for fixable bots, kill recommendations for others | Code or decision docs |

### Week 3: Verification

| Day | Task | Output |
|-----|------|--------|
| 1-5 | Accumulate 200+ new trades per fixed bot | Data |
| 5 | Re-run 1I edge verification on post-fix trades only | Updated P(edge>0) |

---

## Decision Gates

### After each bot's analysis:

| Finding | Action |
|---------|--------|
| Signal never had edge (Layer 3) | Kill bot or replace model entirely |
| Signal had edge but fees consumed it (Layer 1) | Reduce trading frequency, increase edge threshold |
| Signal had edge but exits destroyed it (Layer 4) | Fix exit logic, re-evaluate |
| Signal has edge in some segments only (Layer 5) | Restrict to profitable segments |
| Signal edge is degrading over time (Layer 6) | Model refresh or online learning needed |
| Win/loss asymmetry is the problem (Layer 2) | Position sizing fix or hard stops |

### After all 3 bots analyzed:

| Outcome | Next Phase |
|---------|-----------|
| All 3 bots have fixable root cause identified | Implement fixes → re-run 1I → if passes, resume S172 Phases 5-7 |
| Some bots fixable, some not | Fix what's fixable, kill the rest per 8B criteria |
| No bots have fixable root cause | Kill all 3, reassess entire approach. Consider: different markets, different signal sources, different bot architectures |

### Re-gate after fixes:

Re-run `scripts/edge_verification.py` on post-fix trades only (filter by event_time > fix_deploy_timestamp). Use same graduated response:
- P(edge>0) >= 0.9 → resume full elevation
- 0.7–0.9 → resume core elevation items only
- < 0.7 → back to investigation or kill

---

## What This Phase Does NOT Do

- Does NOT build new models (that's Phase 5-7, gated behind positive edge)
- Does NOT add new data sources or features
- Does NOT change infrastructure (that's Phase 2, running in parallel)
- Does NOT optimize what doesn't work — it diagnoses WHY it doesn't work

---

## Relationship to S172 Plan

Phase RC inserts between Phase 1 (complete) and Phase 2 (starting). It runs parallel to Phase 2 infra work. Phases 3 and 4 continue as scheduled. Phases 5-7 remain gated.

```
Phase 1 (DONE) → Phase RC (NEW — root-cause, 2-3 weeks)
                → Phase 2 (parallel — operational resilience)
                → Phase 3 (VPS config)
                → Phase 4 (hygiene)
                  ↓
                Phase RC outcome determines:
                  → Fix + re-gate → Phases 5-7 resume
                  → Kill → Phase 8B bot disposal
```
