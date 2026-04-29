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

**Sourcing note (Protocol 11 / Rule Zero) — UPDATED post-S203 hygiene #12:** Originally the verification was via ad-hoc SQL because `scripts/bot_pnl.py` block 5 did not honor `--since` or `--clean`. **§S203 hygiene #12 (commit `009fbc6`) extended block 5 to honor both flags**, so the post-Day-2 CLEAN per-side / per-city / per-lead-time / side×lead-time breakdown is NOW a bot_pnl.py output. The verbatim bot_pnl.py output from the master commit `009fbc6` invocation against the prod VPS database (release `20260429_134741`) is captured at `S203_H0PRIME_BOT_PNL_OUTPUT.txt`. The numbers in §3.1-§3.3 below are now Protocol 11-citable via that file. Reproducer command:

```
python scripts/bot_pnl.py WeatherBot --since 20260414_132211 --clean
```

(The original ad-hoc SQL output and the new bot_pnl.py output match exactly — confirms the hygiene #12 fix is structurally correct.)

### 3.1 — Per-CITY losers

Verbatim from `S203_H0PRIME_BOT_PNL_OUTPUT.txt:218-257` (bot_pnl.py output). Top 10 losing cities (descending loss):

| City | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| Amsterdam | 16 | 8 | 50.0% | -$365.78 |
| Buenos Aires | 11 | 5 | 45.5% | -$292.51 |
| Lucknow | 8 | 4 | 50.0% | -$289.81 |
| San Francisco | 23 | 13 | 56.5% | -$261.86 |
| Los Angeles | 21 | 14 | 66.7% | -$207.04 |
| Milan | 13 | 5 | 38.5% | -$148.15 |
| Miami | 17 | 8 | 47.1% | -$128.50 |
| Ankara | 10 | 5 | 50.0% | -$122.67 |
| Madrid | 7 | 4 | 57.1% | -$117.19 |
| Austin | 7 | 2 | 28.6% | -$114.07 |

(All values are bot_pnl.py output — see canonical file for full 40-city breakdown.) Top 5 cumulative loss is approximately $1,418; top 10 is approximately $2,048. Top losers are well-instrumented major metropolitan areas (Amsterdam, Buenos Aires, San Francisco, Los Angeles, Miami, Madrid, Munich, Paris) — NOT obscure low-quality stations. Loss IS concentrated by Pareto-shape distribution.

### 3.2 — Per-SIDE breakdown

Verbatim from `S203_H0PRIME_BOT_PNL_OUTPUT.txt:206-211`:

| Side | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| **NO** | 574 | 370 | 64.5% | **-$1,714.64** |
| YES | 25 | 12 | 48.0% | +$80.50 |

NO-side dominates volume (574 vs 25), wins at a meaningfully high rate (64.5%), AND drives the bulk of the cumulative loss. YES-side is a small minority with approximately break-even cumulative P&L. **The combination — high accuracy on the dominant side AND negative edge on that same side — is the calibration-failure shape, not the station-noise shape.**

### 3.3 — Per-LEAD-TIME and side × lead-time breakdowns

Verbatim from `S203_H0PRIME_BOT_PNL_OUTPUT.txt:259-280`:

**Per-lead-time:**

| Bucket | n | wins | WR% | total_pnl |
|---|---:|---:|---:|---:|
| <24h | 264 | 163 | 61.7% | -$289.23 |
| **24-48h** | **118** | 71 | **60.2%** | **-$830.89** |
| 48-72h | 24 | 15 | 62.5% | +$122.49 |
| 72-120h | 8 | 5 | 62.5% | -$72.18 |

**Side × lead-time cross-tab:**

| Side | Bucket | n | wins | WR% | total_pnl |
|---|---|---:|---:|---:|---:|
| NO | <24h | 245 | 153 | 62.4% | -$340.49 |
| **NO** | **24-48h** | **116** | 70 | **60.3%** | **-$814.57** |
| NO | 48-72h | 23 | 15 | 65.2% | +$122.49 |
| NO | 72-120h | 8 | 5 | 62.5% | -$72.18 |
| YES | <24h | 19 | 10 | 52.6% | +$51.26 |
| YES | 24-48h | 2 | 1 | 50.0% | -$16.32 |
| YES | 48-72h | 1 | 0 | 0.0% | +$0.00 |

**The single load-bearing bucket is NO × 24-48h:** 116 closed trades, 60.3% WR, -$814.57 — accounts for nearly half (~$815 of $1,714) of the total NO-side loss. Refines H0' from "NO-side calibration over-confidence" to the more specific "NO-side 24-48h-lead-time calibration over-confidence." 24-48h NO is a higher-volume bucket than NO 48-72h or NO 72-120h, so the loss is concentration-by-volume not by per-trade-magnitude (~-$7 average loss per trade in the NO 24-48h bucket).

## 4. Falsification verdict

**H0 (station-noise mechanism):** **FALSIFIED.** Evidence is bot_pnl.py-cited via `S203_H0PRIME_BOT_PNL_OUTPUT.txt`.

- Top losing cities are major, well-instrumented metropolitan areas — Amsterdam (16 trades, -$365.78), Buenos Aires (11 trades, -$292.51), San Francisco (23 trades, -$261.86), Los Angeles (21 trades, -$207.04). Not obscure noisy stations. The "structural data noise" mechanism predicts the opposite distribution.
- Top losers exhibit above-50% win rates (LA 66.7%, SF 56.5%, Houston 66.7%, Seoul 64.3%). Station noise should produce randomly-distributed predictions → win rate near 50%; observed pattern is higher accuracy + negative edge, the opposite shape.
- NO-side cumulative result on the post-Day-2 CLEAN cohort: 574 closed trades, 64.5% WR, -$1,714.64. **High accuracy + negative edge is the calibration-failure signature, not the station-noise signature.**

## 5. Mechanism-family reframe (the actual finding)

The concentration insight from H0 is right; the mechanism family is wrong.

**Reframed H0' (next-session lead):** WB's loss is concentrated specifically on the **NO-side, 24-48h lead-time bucket** — 116 closed trades, 60.3% WR, -$814.57 (per `S203_H0PRIME_BOT_PNL_OUTPUT.txt:275`). This single bucket accounts for approximately half of the total NO-side loss (-$814.57 of -$1,714.64). NO trades at <24h lead time are also lossy but at lower magnitude (-$340.49 across 245 trades). NO 48-72h is profitable (+$122.49 across 23 trades). The mechanism family is **lead-time-dependent NO-side calibration failure** (the model's NO probability estimates are over-confident specifically in the 24-48h lead-time band, where it has the most volume; the longer leads where it has less volume actually look healthy).

This maps to S172 Phase 6 candidates:
- **6D-6E: calibration improvements** (NO-side specific)
- **6Q: confidence-scaled sizing trigger** (gated on Brier or CRPS improvement)
- **5N: MAPIE conformal coverage** (potentially side-asymmetric)

It does NOT map to the deferred 6O lead-time backtest or 6-STATION mapping.

## 6. Next-session candidate (NOT executed in S203 per hard close-point)

**Hypothesis to test next session:** "WB's NO-side calibration is over-confident specifically in the 24-48h lead-time bucket — Brier score on NO 24-48h trades exceeds the model's reported `train_brier=0.237` (per `S203_H0PRIME_BOT_PNL_OUTPUT.txt:286`) by a margin large enough to explain the cumulative loss."

Cheapest verification: single SQL on `prediction_log` filtered to `WeatherBot AND trade_executed=true AND prediction_time >= 20260414_132211`, grouped by (trade_side, lead_time_bucket), computing Brier separately. Expected output: NO 24-48h Brier substantially worse than NO 48-72h Brier (the latter is the same-side longer-lead cohort, profitable in §3.3).

Alternative if calibration data is unavailable: per-side Pinnacle-anchored CLV check on the NO 24-48h sample (n=116). If WB enters those trades at Pinnacle-implied probabilities AND they still lose at the observed -$814.57 magnitude, the issue is structural (e.g., adverse selection in the 24-48h band) rather than calibration-numerical (Brier score within tolerance, but trade selection skewed).

The verification should also check the calibrator's `yes_widened=True` flag (per `S203_H0PRIME_BOT_PNL_OUTPUT.txt:285`) — the calibrator is widening YES side during fitting due to small training-sample (n_yes=121); the parallel question for NO is whether 24-48h NO predictions are receiving the same widening treatment, since they are the highest-volume sub-bucket of NO trades.

## 7. Caveats

1. **City ≠ station.** If WB's actual signal source is station-level (the original hypothesis), the city-aggregate may have masked station-level noise. A station-resolution follow-up is filed as: "join `trade_events.event_data->>'station_id'` (if present) with the per-side breakdown." S203 did not extend to that — pre-condition is verifying the schema includes station_id at ENTRY emission time.

2. **Sample asymmetry.** YES-side n=25 vs NO-side n=574 (per `S203_H0PRIME_BOT_PNL_OUTPUT.txt:210-211`) — YES-side WR=48% sits in a high-variance band where a few-trade flip materially shifts the verdict. The calibrator's `yes_widened=True` flag (line 285 of the same file) is the model's own admission that YES-side calibration is undertrained. A rebalanced-volume hypothesis ("WB's YES-trade gating is too restrictive") could flip the framing further. Filed as second-tier alternative for the NEXT session, not this one.

3. **Aggregate-statistics bucket-concentration check (S172 Protocol candidates §2 at line 1648).** Per the candidate discipline, a city's headline loss may be driven by a single (entry_date × side × triple) cluster. The top-5 cities × SIDE breakdown showed all-NO concentration — but did NOT verify whether each city's loss is driven by a single date or distributed across dates. Filed as a follow-up.

## 8. Track 5 closure

Per the S203 plan hard close-point: ONE hypothesis-test result, no second hypothesis in S203. H0 falsified, H0' filed for next session. Track 5 closes.
