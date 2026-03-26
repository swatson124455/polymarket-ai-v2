# EsportsBot Session 118 — Volume Gates + Calibration Data Fix (2026-03-22)

## Deploys This Session

### Deploy 1: Volume Gates (20260322_142945)
**Commit**: `33aa5d5`
| Setting | Before | After |
|---------|--------|-------|
| `ESPORTS_EXIT_COOLDOWN_SECONDS` | 900 (15min) | **300 (5min)** |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | 3 | **5** |
| `ESPORTS_REENTRY_MIN_EDGE` | 0.12 | **0.08** |
| `ESPORTS_PER_MARKET_CAP` | 400 (was `MIRROR_MAX_PER_MARKET`) | **600 (own setting)** |

**Why**: Bot was position-saturated (16/25 markets had positions, only 2 opportunities). Daily cap never hit (peak $7K/10K). Decoupled from MirrorBot.
**Result**: 35 markets, 15 passed waterfall, **8 opportunities** (was 2). Tests: 1668 passed.

### Deploy 2: Calibration Data Fix (20260322_200810) — CRITICAL
**Commit**: `667a31c`
**Root cause**: `esports_prediction_log` ON CONFLICT clause overwrote `predicted_prob` every 2s scan. By resolution time, logged prob reflected near-certain outcomes, not entry-time prediction. BetaCalibrator trained on hindsight data → learned identity params → no correction applied.
**Fix**: Removed `predicted_prob = EXCLUDED.predicted_prob` from ON CONFLICT UPDATE in `esports_db.py`. First prediction frozen on INSERT.
**Data cleanup**: Deleted 153 stale resolved rows. Calibrator starts fresh N=0 per game.

## Live State at Session End
| Metric | Value |
|--------|-------|
| Open positions | 18 |
| Exposure | $3,502 (23% of $15K cap) |
| P&L all-time | **+$4,844** |
| Markets scanned | 35 |
| Opportunities | 8 |
| Daily cap | $4,814 / $10,000 (48%) |
| Errors | 0 |
| Data quality | All checks passed |

## P&L
| Day | Net | Notes |
|-----|-----|-------|
| Mar 18 | +$175 | |
| Mar 19 | -$1,709 | |
| Mar 20 | +$1,357 | |
| Mar 21 | +$5,117 | 2 big Valorant wins |
| Mar 22 | -$79 | |

**Side asymmetry**: NO +$2,664 (avg +$35.52), YES -$1,435 (avg -$16.50). Model overestimates underdogs.

## Calibration by Game
| Game | Brier | Accuracy | Status |
|------|-------|----------|--------|
| Valorant | 0.153 | 72% | Best — carrying P&L |
| Dota2 | 0.231 | 62% | Decent |
| CS2 | 0.273 | 42% | Midrange inverted |
| LoL | 0.308 | 31% | Worst — active leak |

## Files Modified
- `esports/data/esports_db.py` — Removed predicted_prob from ON CONFLICT UPDATE
- `config/settings.py` — New ESPORTS_PER_MARKET_CAP, updated cooldown/reentry/entries
- `bots/esports_bot.py` — Read ESPORTS_PER_MARKET_CAP setting

## Next Session Priorities
1. **Monitor BetaCalibrator** — Should produce non-identity params (a≠1, b≠1, c≠0) within 24-48h as clean data resolves. Check `beta_cal` log lines.
2. **Full code audit** — User requested every code path, decision tree, dead code removal, band-aid cleanup. Plan started but NOT executed. Get explicit approval before starting.
3. **LoL** — If calibrator fix doesn't improve accuracy in 48h, investigate Glicko-2 rating quality.
4. **Shadow fills** — 66% negative edge rate. Consider game-specific min_edge widening.
5. **VPS .env cleanup** — `ESPORTS_MAX_EDGE=0.35` (dead), `ESPORTS_MODEL_MAX_BRIER=0.248` (dead).

## Live Config
```
ESPORTS_MIN_EDGE=0.05
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_REENTRY_MIN_EDGE=0.08
ESPORTS_EXIT_COOLDOWN_SECONDS=300
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=5
ESPORTS_PER_MARKET_CAP=600
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=10000
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_USE_CONFORMAL=true
```
