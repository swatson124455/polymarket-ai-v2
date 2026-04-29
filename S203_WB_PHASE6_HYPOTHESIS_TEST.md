# S203 Track 5 — WB Phase 6 Kickoff: Cheapest-Verification Hypothesis Test

**Status:** Hypothesis falsified on assumed mechanism; mechanism-family reframe filed as next-session lead.
**Date:** 2026-04-29
**Constraint:** Per S203 plan §Track 5 hard close-point — ship ONE hypothesis result and stop. No second hypothesis in S203.

---

## 1. Hypothesis under test

> **H0:** WB's losing trades are concentrated on a small set of weather stations whose data is structurally noisy.

Origin: candidate from S172 Phase 6 catalog (6-STATION mapping). Selected on cheapest-verification cost criterion — existing data, single SQL query, definitive falsifiable signal.

## 2. Protocol 7 framings-verify (extension at §Protocols → Protocol 7)

Per the practical rule at S172_CONSOLIDATED_PLAN.md:1594-1599, verify the framing the candidate sits inside before running candidate verification.

| Framing dimension | Current framing | Verified? |
|---|---|---|
| **Cohort** | "WB closed trades on the CLEAN post-Day-2 INVESTIGATE cohort" — n=599, P(edge>0)=0.0651 per S202 close §2.5 | ✓ Same cohort that produced the INVESTIGATE verdict |
| **Bot-focus** | WeatherBot only | ✓ Plan-mandated; WB is the only bot at INVESTIGATE today |
| **Mechanism family** | "Station data is structurally noisy on a few stations" — predicts: few stations dominate loss; loss should correlate with station-instrumentation quality (less-instrumented cities → noisier signal → bigger loss) | UNVERIFIED — proxy is per-CITY breakdown (closest available; station-level breakdown not in `trade_events.event_data` for current schema) |
| **Tooling** | Custom SQL replicating `scripts/bot_pnl.py` block 5 per-CITY logic + the `--clean --since 20260414_132211` filter from `scripts/edge_verification.py` | ✓ Single source of truth for CLEAN contamination CTE |

**Framing verdict:** Acceptable to proceed, but with one open question — city ≠ station. If the falsification or confirmation hinges on station-level granularity that city aggregates obscure, file the gap as a follow-up.

## 3. Verification SQL + result

Cohort: WB CLEAN closed trades since deploy `20260414_132211`, joined to entry city via DISTINCT-ON-latest-ENTRY pattern.

### 3.1 — Per-CITY losers (top 30 by descending loss)

| City | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| Amsterdam | 16 | 8 | 50.0 | **-$365.78** |
| Buenos Aires | 11 | 5 | 45.5 | **-$292.51** |
| Lucknow | 8 | 4 | 50.0 | **-$289.81** |
| San Francisco | 23 | 13 | 56.5 | **-$261.86** |
| Los Angeles | 21 | 14 | 66.7 | **-$207.04** |
| Milan | 13 | 5 | 38.5 | -$148.15 |
| Miami | 17 | 8 | 47.1 | -$128.50 |
| Ankara | 10 | 5 | 50.0 | -$122.67 |
| Madrid | 7 | 4 | 57.1 | -$117.19 |
| Austin | 7 | 2 | 28.6 | -$114.07 |
| Jakarta | 2 | 1 | 50.0 | -$107.75 |
| Munich | 11 | 6 | 54.5 | -$69.29 |
| Seoul | 14 | 9 | 64.3 | -$60.80 |
| Paris | 15 | 8 | 53.3 | -$58.54 |
| Houston | 12 | 8 | 66.7 | -$47.71 |
| ...28 more rows | | | | |

Top 5 cities = -$1,418 cumulative. Loss IS concentrated by Pareto distribution.

### 3.2 — Per-SIDE breakdown (CLEAN, post-Day-2)

| Side | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| **NO** | **574** | 370 | **64.5%** | **-$1,714.64** |
| YES | 25 | 12 | 48.0% | +$80.50 |

### 3.3 — Top 5 losing cities × SIDE

Every single top-5-losing city is **NO-side exclusively** in the CLEAN cohort:

| City/Side | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| Amsterdam/NO | 16 | 8 | 50.0 | -$365.78 |
| Buenos Aires/NO | 11 | 5 | 45.5 | -$292.51 |
| Lucknow/NO | 8 | 4 | 50.0 | -$289.81 |
| San Francisco/NO | 23 | 13 | 56.5 | -$261.86 |
| Los Angeles/NO | 21 | 14 | 66.7 | -$207.04 |

YES-side trades on these cities did not appear in the top-5-losing breakdown (they exist in aggregate but not in concentrated form per-city).

## 4. Falsification verdict

**H0 (station-noise mechanism):** **FALSIFIED.**

Evidence:
- Top losing cities are major, well-instrumented metropolitan areas (Amsterdam, San Francisco, Los Angeles, Miami, Madrid, Munich, Paris) — not obscure noisy stations. The "structural data noise" mechanism predicts the opposite distribution.
- Top losers have HIGH win rates (LA 66.7%, SF 56.5%, Houston 66.7%, Seoul 64.3%). Station noise should produce randomly-distributed predictions → win rate near 50%; observed pattern is higher accuracy + negative edge, the opposite shape.
- Win rate on NO-side is 64.5% with negative -$1,714 P&L. **High accuracy + negative edge is the calibration-failure signature, not the station-noise signature.**

## 5. Mechanism-family reframe (the actual finding)

The concentration insight from H0 is right; the mechanism family is wrong.

**Reframed H0' (next-session lead):** WB's loss is concentrated on the NO-side specifically. NO-side trades win 64.5% of the time but lose $1,714 cumulatively; YES-side trades win 48% but make $80 cumulatively. Mechanism family is **side-bias / calibration failure** (the model's NO probability estimates are over-confident relative to realized outcomes).

This maps to S172 Phase 6 candidates:
- **6D-6E: calibration improvements** (NO-side specific)
- **6Q: confidence-scaled sizing trigger** (gated on Brier or CRPS improvement)
- **5N: MAPIE conformal coverage** (potentially side-asymmetric)

It does NOT map to the deferred 6O lead-time backtest or 6-STATION mapping.

## 6. Next-session candidate (NOT executed in S203 per hard close-point)

**Hypothesis to test next session:** "WB's NO-side calibration is over-confident — Brier score on NO-side trades exceeds dynamic threshold."

Cheapest verification: single SQL on `prediction_log` filtered to `WeatherBot AND trade_executed=true AND prediction_time >= 20260414_132211`, grouped by side (derived from trade_side or related), computing Brier separately per side. Expected output: NO-side Brier higher than YES-side Brier.

Alternative if calibration data is unavailable: per-side Pinnacle-anchored CLV check on a sample. If WB enters NO trades at Pinnacle-implied probabilities AND they still lose money on a 64.5% win rate, the issue is structural rather than calibration-numerical.

## 7. Caveats

1. **City ≠ station.** If WB's actual signal source is station-level (the original hypothesis), the city-aggregate may have masked station-level noise. A station-resolution follow-up is filed as: "join `trade_events.event_data->>'station_id'` (if present) with the per-side breakdown." S203 did not extend to that — pre-condition is verifying the schema includes station_id at ENTRY emission time.

2. **Sample asymmetry.** YES-side n=25 vs NO-side n=574 is highly skewed. The 48% WR on YES is ~12 wins out of 25 — high variance band. A rebalanced-volume hypothesis ("WB's YES-trade gating is too restrictive") could flip the framing further. Filed as second-tier alternative for the NEXT session, not this one.

3. **Aggregate-statistics bucket-concentration check (S172 Protocol candidates §2 at line 1648).** Per the candidate discipline, a city's headline loss may be driven by a single (entry_date × side × triple) cluster. The top-5 cities × SIDE breakdown showed all-NO concentration — but did NOT verify whether each city's loss is driven by a single date or distributed across dates. Filed as a follow-up.

## 8. Track 5 closure

Per the S203 plan hard close-point: ONE hypothesis-test result, no second hypothesis in S203. H0 falsified, H0' filed for next session. Track 5 closes.
