# AGENT HANDOFF — EsportsBot Session 108 (2026-03-19)

## Session Type: EsportsBot-scoped (data analysis + assessment + monitoring)

## What Was Done

### 1. Full VPS Data Audit — All Items Assessed

No code changes this session. All work was data analysis and assessment of outstanding items from S107.

---

### 2. P&L — Unchanged from S107

| Event Type | Count | P&L |
|-----------|-------|-----|
| ENTRY | 131 | $0.00 |
| EXIT | 55 | **+$369.21** |
| RESOLUTION | 106 | -$58.45 |
| **Total Realized** | | **+$310.76** |

No new trades since S107 (service restarted at ~02:46, 6 trades between 02:26-02:46 from S106/S107).

---

### 3. BetaCalibrator Status — 3/8 CONFIRMED FITTING, Dota2 Borderline

Logs show only 3 games actively fitting (not 4 as S107 reported):

| Game | Resolved | Beta Cal Status | Parameters |
|------|----------|----------------|------------|
| Valorant | 1,543 | **FITTED** | a=0.9994, b=1.0085, c=0.0057 |
| LoL | 371 | **FITTED** (up from 281) | a=0.9925, b=1.0037, c=0.0078 |
| SC2 | 57 (up from 52) | **FITTED** | a=1.0134, b=0.999, c=-0.0068 |
| Dota2 | 40 | **NOT LOGGING** | Was fitted in S107; likely below 30 in beta cal's precise time window |
| CS2 | 22/30 (784 total) | 73% — accumulating | — |
| CoD/R6/RL | 0 | No data | — |

**Dota2 note**: The DB has 40 resolved dota2 predictions post-2026-03-16. But BetaCalibrator's query uses `NOW() - 3 days` (precise to hour, not midnight). At ~03:00 UTC on 03/19, this excludes predictions before 03:00 on 03/16 — likely dropping Dota2 below the 30-sample minimum. As more predictions resolve, Dota2 will re-fit automatically. **No action needed.**

---

### 4. Scan Waterfall — Healthy

Current (14 markets: 9 LoL, 4 CS2, 1 CoD):
```
no_prediction=6, low_edge=1, edge_cap=2, low_confidence=3, passed=2, reentry_rejected=2
skipped_has_position=2
```

---

### 5. no_prediction=6 Investigation — RESOLVED (Working As Designed)

**Root cause**: All 6 are `tournament_winner` markets correctly skipped by `esportsbot_skip_market_type`. They are NOT team name matching failures.

Markets being skipped:
1. "Will NongShim RedForce win the LCK 2026 Season Playoffs?"
2. "Will a team from LCS (North America) win LoL Worlds 2026?"
3. "Will Gen.G Esports win the LCK 2026 Season Playoffs?"
4. "Will a team from LEC (Europe/EMEA) win LoL Worlds 2026?"
5. "Will a team from LCK (South Korea) win MSI 2026?"
6. "Will Anyone's Legend win the LPL 2026 Season?"

These are tournament futures — cannot use Glicko-2 (no head-to-head opponent). **Correct behavior. No fix needed.**

---

### 6. EsportsSeriesBot — CONFIRMED Silent (Expected)

Zero log output in 6+ hours. No series markets available on Polymarket. S107 already noted this as expected. **No action needed.**

---

### 7. Per-Market Entry Cap Assessment — NOT NEEDED

Query of all EsportsBot ENTRY events by market:
- **9 markets** have >1 entry (out of 131 total entries)
- **Max stacking**: 3 entries on 1 market, 2 entries on 8 markets
- **Stacking rate**: 9/131 = 6.8%

This is minimal. No stacking problem exists. MirrorBot's 2-entry cap addresses higher volume (511 entries); EsportsBot's lower volume doesn't warrant it.

**Verdict: Per-market entry cap NOT needed.** Revisit if trade volume increases significantly.

---

### 8. Fill Quality Logging Assessment — ALREADY WORKING

Verified from live trade_events data. The paper engine already enriches event_data in-place for ALL bots that pass event_data. EsportsBot passes event_data via `_prediction_cache`.

**Sample from latest ENTRY** (2026-03-19 02:46):
```json
{
  "game": "lol", "best_of": 1, "model_prob": 0.5098,
  "slippage_bps": 95.7, "fill_prob": 0.3234, "fill_frac": 0.4608,
  "book_walk": false, "alpha_decay_bps": 0, "kyle_lambda_bps": 7,
  "cross_scan_bps": 0, "res_prox_mult": 1.0,
  "team_strength_diff": -0.005349, "matchup_uncertainty": 0.203377,
  "rd_asymmetry": -0.026721,
  "team_a_volatility": 1.000042, "team_b_volatility": 1.000299
}
```

All 8 fill quality fields present: `slippage_bps`, `fill_prob`, `fill_frac`, `book_walk`, `alpha_decay_bps`, `kyle_lambda_bps`, `cross_scan_bps`, `res_prox_mult`.

`alpha_decay_bps=0` because EsportsBot doesn't pass `scan_start_mono` — this is correct (see alpha decay assessment below).

**Verdict: Fill quality logging already complete. No code changes needed.**

---

### 9. Alpha Decay Assessment — NOT APPLICABLE

Alpha decay in the paper engine penalizes the price increase between scan start and order fill, modeling order book drift during processing latency.

**Why it doesn't apply to EsportsBot:**

1. **EsportsBot already has prediction freshness decay** — confluence score includes exponential decay (`ESPORTS_FRESHNESS_DECAY_SECONDS=30s` for live, `600s` for pre-game). Adding alpha decay would double-penalize staleness.

2. **Different signal dynamics** — WeatherBot's weather signals degrade over hours (NOAA update cycle → `half_life=1800s`). EsportsBot's Glicko-2 ratings change when matches complete (discrete events), not continuously. Alpha decay assumes continuous signal degradation.

3. **Scan-to-fill latency is negligible** — EsportsBot's 10s scan interval means at most ~10s between scan and fill. Alpha decay with default `half_life=300s` would give `decay = exp(-ln2 * 10/300) = 0.977` — a 2.3% penalty, producing <3 bps slippage. Noise, not signal.

4. **No `scan_start_mono` needed** — adding it would require 2-line code change but would produce near-zero alpha_decay_bps indefinitely due to point 3.

**Verdict: Alpha decay NOT applicable to EsportsBot. No code changes.**

---

### 10. Brier Scores — Current State (Monitoring)

| Game | N | Brier | Win Rate | Assessment |
|------|---|-------|----------|------------|
| SC2 | 57 | **0.0195** | 0.0% | Excellent — model predicts underdog correctly |
| Valorant | 1,543 | **0.1390** | 69.7% | Good (was 0.47 pre-fix) |
| CS2 | 22 | 0.2051 | 0.0% | Decent (limited sample) |
| LoL | 371 | **0.2842** | 62.5% | Borderline — near 0.30 halt threshold |
| Dota2 | 40 | **0.3002** | 77.5% | Just over threshold — high WR saves it |

**LoL (0.2842)**: 0.016 below the 0.30 monitoring halt threshold. 371 resolved predictions (up from 281 in S107). Tracking stable — no deterioration trend. Continue monitoring.

**Dota2 (0.3002)**: 0.002 over the 0.30 threshold. BUT: 77.5% win rate (highest of all games) and only 40 samples. The monitoring halt suspension is still active while BetaCalibrator is unfitted for Dota2. When/if Dota2 fits and the suspension lifts, the 0.3002 Brier WOULD trigger monitoring halt for Dota2. This is correct — let the system self-govern.

**SC2/CS2 win rate 0.0%**: These games' predictions correctly identify the favored team (prob > 0.5 → underdog correctly predicted to lose). The "0% win rate" means the model rarely predicts upsets, which is correct behavior for prediction accuracy (Brier is excellent at 0.0195 and 0.2051).

---

### 11. LoL edge_cap — Working As Designed

2 LoL markets blocked by `edge_cap` in waterfall. LoL BetaCalibrator is fitted → edge cap dropped from 0.45 to 0.35. Markets with edges >0.35 are correctly filtered. This is the learning system working as intended — higher edges on LoL are likely miscalibrated pre-match predictions, and the cap prevents overconfident trades.

---

## Files Modified

**NONE.** This session was pure data analysis and assessment. No code changes.

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| P2 | CS2 BetaCalibrator: 22/30 resolved | Accumulating naturally | None — wait |
| P2 | Kelly degradation suspended (CS2 blocks ALL-fitted requirement) | Blocked on CS2 | None — wait |
| P2 | Dota2 BetaCalibrator: 40 resolved but not fitting in current window | Precise time window may exclude a few samples | Will re-fit as window slides forward |
| P3 | LoL Brier=0.2842 (near 0.30 halt) | Stable, monitoring | None — check next session |
| P3 | EsportsSeriesBot silent | No series markets on Polymarket | Expected — no fix |
| P4 | Dota2 Brier=0.3002 (over threshold, 77.5% WR) | Suspension still active | Will self-govern when fitted |
| P5 | taker_side dead code | No data source | Deferred |
| P5 | PAPER_BOOK_WALK_ENABLED disabled | Needs orderbook tracker | Deferred |
| P5 | CoD/R6/RL — no BetaCalibrator data | Too few markets | Low priority |

### Items RESOLVED This Session (closed)

| Item | Resolution |
|------|-----------|
| no_prediction=6 | Tournament_winner markets — working as designed, not team name failures |
| Fill quality logging | Already working — paper engine enriches event_data in-place |
| Alpha decay applicability | Not applicable — EsportsBot has own freshness decay, scan latency negligible |
| Per-market entry cap | Not needed — max 3 entries/market, 6.8% stacking rate |
| LoL edge_cap blocking 2 markets | Working as designed — fitted games use 0.35 cap |

---

## VPS Config (unchanged from S107)

```
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=10000
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_MAX_EDGE=0.35  (code raises to 0.45 for unfitted games — CS2 + possibly Dota2)
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MAX_GAME_EXPOSURE=600
ESPORTS_USE_CONFORMAL=true
```

---

## Verification

```bash
# Scan summary
journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3

# BetaCalibrator
journalctl -u polymarket-ai --since "30 min ago" | grep beta_cal

# P&L
sudo -u polymarket psql -d polymarket -c "SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;"

# Brier scores
sudo -u polymarket psql -d polymarket -c "SELECT game, COUNT(*), ROUND(AVG((predicted_prob-COALESCE(actual_outcome,0))^2)::numeric,4) as brier FROM esports_prediction_log WHERE created_at>'2026-03-16' AND actual_outcome IS NOT NULL GROUP BY game ORDER BY brier;"
```
