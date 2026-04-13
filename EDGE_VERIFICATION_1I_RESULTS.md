# 1I Edge Verification Results — S172 Phase 1 HARD GATE

**Date:** 2026-04-13
**Script:** `scripts/edge_verification.py`
**Method:** 10,000 bootstrap resamples on trade_events (RESOLUTION + EXIT with realized_pnl)
**Source:** VPS PostgreSQL via bot_pnl-compatible query on trade_events

---

## Results

| Bot | Closed Trades | RES/EXIT | Win Rate | Total P&L | Total Stake | Raw Edge | P(edge>0) | Kelly (half) | Verdict |
|-----|--------------|----------|----------|-----------|-------------|----------|-----------|-------------|---------|
| WeatherBot | 3,389 | 3099/290 | 59.3% | -$29,919 | $203,919 | -14.67% | 0.0212 | -0.0005 | ROOT-CAUSE |
| MirrorBot | 9,519 | 6365/3154 | 39.7% | -$113,643 | $1,578,710 | -7.20% | 0.0001 | -0.0013 | ROOT-CAUSE |
| EsportsBot | 541 | 254/287 | 36.2% | -$8,622 | $58,493 | -14.74% | 0.0015 | -0.0024 | ROOT-CAUSE |

## 95% Bootstrap Confidence Intervals (Edge)

| Bot | CI Lower | CI Upper | Mean |
|-----|----------|----------|------|
| WeatherBot | -31.83% | -0.44% | -15.12% |
| MirrorBot | -11.47% | -3.14% | -7.23% |
| EsportsBot | -26.68% | -4.46% | -15.02% |

## Graduated Response (per S172 plan)

- P(edge>0) >= 0.9 → FULL ELEVATION
- 0.7 <= P < 0.9 → CORE ONLY
- **P < 0.7 → ROOT-CAUSE INVESTIGATION** ← ALL 3 BOTS

## Interpretation

1. **All 3 bots are definitively negative-edge.** Not a sampling issue — MirrorBot has 9,519 trades with edge CI entirely below zero.
2. **WeatherBot** wins 59.3% of trades but the average loss exceeds the average win by enough to make overall edge -14.67%. Possible causes: asymmetric payoff structure (buying high-confidence NO at 0.85+ and losing full stake), position sizing on losers, resolution timing.
3. **MirrorBot** has the tightest CI (narrowest uncertainty) — the negative edge is the most statistically certain. 39.7% WR with $1.58M staked.
4. **EsportsBot** smallest sample (541) but 95% CI doesn't come close to zero.

## Impact on S172 Plan

Per the plan: "P(edge>0) < 0.7 → root-cause investigation replaces elevation."

**Phases 5 (EsportsBot Elevation), 6 (WeatherBot Elevation), and 7 (MirrorBot Elevation) are GATED.** They cannot proceed until root-cause investigation identifies and fixes the negative-edge sources.

Phases 1 (remaining items), 2 (Operational Resilience), 3 (VPS Config), and 4 (Hygiene) are NOT gated and proceed as planned.

## Next Steps

1. Root-cause investigation per bot (separate sessions):
   - WB: Why does 59.3% WR produce -14.67% edge? Analyze loss magnitude vs win magnitude.
   - MB: 39.7% WR suggests signal quality issue. Analyze by copy source tier, category, hold time.
   - EB: 36.2% WR on 541 trades — analyze by game, edge-at-entry, hold time.
2. Continue Phase 1 remaining (1J-1M), Phase 2, Phase 3, Phase 4 — none are gated.
3. Re-run edge verification after root-cause fixes to check if gate opens.
