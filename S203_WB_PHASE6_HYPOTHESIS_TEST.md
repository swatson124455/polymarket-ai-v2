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
| **Cohort** | "WB closed trades on the CLEAN post-Day-2 INVESTIGATE cohort" — same cohort referenced by S202 close §2.5 verdict (specific counts and bootstrap probability sourced from `scripts/edge_verification.py`, not `scripts/bot_pnl.py`; per Protocol 11 specific magnitudes are not cited inline) | ✓ Same cohort that produced the INVESTIGATE verdict |
| **Bot-focus** | WeatherBot only | ✓ Plan-mandated; WB is the only bot at INVESTIGATE today |
| **Mechanism family** | "Station data is structurally noisy on a few stations" — predicts: few stations dominate loss; loss should correlate with station-instrumentation quality (less-instrumented cities → noisier signal → bigger loss) | UNVERIFIED — proxy is per-CITY breakdown (closest available; station-level breakdown not in `trade_events.event_data` for current schema) |
| **Tooling** | Custom SQL replicating `scripts/bot_pnl.py` block 5 per-CITY logic + the `--clean --since 20260414_132211` filter from `scripts/edge_verification.py` | ✓ Single source of truth for CLEAN contamination CTE |

**Framing verdict:** Acceptable to proceed, but with one open question — city ≠ station. If the falsification or confirmation hinges on station-level granularity that city aggregates obscure, file the gap as a follow-up.

## 3. Verification SQL + result

Cohort: WB CLEAN closed trades since deploy `20260414_132211`, joined to entry city via DISTINCT-ON-latest-ENTRY pattern.

**Sourcing note (Protocol 11 / Rule Zero):** The verification SQL is structurally similar to `scripts/bot_pnl.py`'s block 5 per-side / per-city queries, but bot_pnl.py block 5 does NOT honor `--since` (it's all-time only). The post-Day-2 CLEAN per-side / per-city breakdown is therefore NOT a bot_pnl.py output. Per Protocol 11, specific counts / win-rates / dollar-P&L from this ad-hoc SQL are NOT cited here. The qualitative direction-of-effect findings below stand on shape, not on specific magnitudes; substantive H0 falsification rests on the shape (high-accuracy + negative-edge signature) rather than on any single number.

**The §S203 hygiene backlog includes "extend `scripts/bot_pnl.py` block 5 to honor `--since` and `--clean`"** as the canonical-tooling fix that would let this hypothesis-test be re-run with bot_pnl.py-cited evidence. Until that extension lands, the Track 5 finding is a direction-of-effect signal, not a quantified verdict.

### 3.1 — Per-CITY losers (qualitative)

The top losers in the post-Day-2 CLEAN cohort are well-instrumented major metropolitan areas (Amsterdam, Buenos Aires, Lucknow, San Francisco, Los Angeles, Milan, Miami, Ankara, Madrid, Austin, Jakarta, Munich, Seoul, Paris, Houston). They are NOT obscure low-quality stations. Loss IS concentrated by Pareto-shape distribution; the top handful of cities account for a substantial fraction of the cohort's total negative P&L.

### 3.2 — Per-SIDE breakdown (qualitative)

The post-Day-2 CLEAN cohort exhibits a strong side asymmetry: NO-side trades dominate the closed-trade count, win at a meaningfully high rate, and yet drive the bulk of the negative P&L; YES-side trades are a small minority of the cohort, with roughly even win rate and approximately break-even P&L. **The combination — high accuracy on the dominant side AND negative edge on that same side — is the calibration-failure shape, not the station-noise shape.**

### 3.3 — Top losing cities × SIDE (qualitative)

In the post-Day-2 CLEAN cohort, the top-loss cities are **all NO-side**. YES-side trades appear in aggregate across cities but do not appear concentrated as top-loss buckets — i.e., the side asymmetry is not "NO loses at the same rate everywhere"; it's "the high-volume cities lose specifically on NO."

## 4. Falsification verdict

**H0 (station-noise mechanism):** **FALSIFIED on shape.**

Evidence (qualitative — see §3 sourcing note):
- Top losing cities are major, well-instrumented metropolitan areas — not obscure noisy stations. The "structural data noise" mechanism predicts the opposite distribution.
- Top losers consistently exhibit ABOVE-50% win rates (the qualitative signature; specific WR% values are not bot_pnl.py-citable for the post-Day-2 CLEAN window so are omitted per Protocol 11). Station noise should produce randomly-distributed predictions → win rate near 50%; the observed pattern is higher accuracy + negative edge, the opposite shape.
- The dominant-side-of-volume (NO) shows above-50% win rate AND negative cumulative P&L on the post-Day-2 CLEAN cohort. **High accuracy + negative edge is the calibration-failure signature, not the station-noise signature.**

## 5. Mechanism-family reframe (the actual finding)

The concentration insight from H0 is right; the mechanism family is wrong.

**Reframed H0' (next-session lead):** WB's loss is concentrated on the NO-side specifically. NO-side trades dominate volume, exhibit above-50% win rate, and yet drive cumulative loss; YES-side trades are a small minority of the cohort and approximately break-even. Mechanism family is **side-bias / calibration failure** (the model's NO probability estimates are over-confident relative to realized outcomes). Specific magnitudes will be canonical when the bot_pnl.py block-5 windowing extension (filed as §S203 hygiene) lands.

This maps to S172 Phase 6 candidates:
- **6D-6E: calibration improvements** (NO-side specific)
- **6Q: confidence-scaled sizing trigger** (gated on Brier or CRPS improvement)
- **5N: MAPIE conformal coverage** (potentially side-asymmetric)

It does NOT map to the deferred 6O lead-time backtest or 6-STATION mapping.

## 6. Next-session candidate (NOT executed in S203 per hard close-point)

**Hypothesis to test next session:** "WB's NO-side calibration is over-confident — Brier score on NO-side trades exceeds dynamic threshold."

Cheapest verification: single SQL on `prediction_log` filtered to `WeatherBot AND trade_executed=true AND prediction_time >= 20260414_132211`, grouped by side (derived from trade_side or related), computing Brier separately per side. Expected output: NO-side Brier higher than YES-side Brier.

Alternative if calibration data is unavailable: per-side Pinnacle-anchored CLV check on a sample. If WB enters NO trades at Pinnacle-implied probabilities AND those trades still lose money on the same above-50% win-rate signature observed in §3.2, the issue is structural rather than calibration-numerical.

## 7. Caveats

1. **City ≠ station.** If WB's actual signal source is station-level (the original hypothesis), the city-aggregate may have masked station-level noise. A station-resolution follow-up is filed as: "join `trade_events.event_data->>'station_id'` (if present) with the per-side breakdown." S203 did not extend to that — pre-condition is verifying the schema includes station_id at ENTRY emission time.

2. **Sample asymmetry.** YES-side count is much smaller than NO-side count in the post-Day-2 CLEAN cohort (specific counts deferred until bot_pnl.py block-5 windowing extension lands). YES is in a high-variance band where any single-trade flip materially shifts the verdict. A rebalanced-volume hypothesis ("WB's YES-trade gating is too restrictive") could flip the framing further. Filed as second-tier alternative for the NEXT session, not this one.

3. **Aggregate-statistics bucket-concentration check (S172 Protocol candidates §2 at line 1648).** Per the candidate discipline, a city's headline loss may be driven by a single (entry_date × side × triple) cluster. The top-5 cities × SIDE breakdown showed all-NO concentration — but did NOT verify whether each city's loss is driven by a single date or distributed across dates. Filed as a follow-up.

## 8. Track 5 closure

Per the S203 plan hard close-point: ONE hypothesis-test result, no second hypothesis in S203. H0 falsified, H0' filed for next session. Track 5 closes.
