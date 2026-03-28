# AGENT HANDOFF — MirrorBot Session 137 (2026-03-27)

## Session Scope
MirrorBot-only. No other bots modified. Shared modules touched:
- `base_engine/learning/elite_reliability.py` (C6)
- `base_engine/features/calibration.py` (C14)

Both are fail-safe changes (additive) that do not alter behavior for WeatherBot/EsportsBot.

## Summary
16 commits implementing a comprehensive MirrorBot audit. All 1720 tests pass.

---

## Commits (in order)

### C1 — M1: Python 3.13 scoping fix (`bots/mirror_bot.py`)
**Bug**: `_execute_sell_exit()` used `from sqlalchemy import text as _t` at L909, making `_t` a local for the ENTIRE function. Later block at L953 that also tried to call `_t()` hit `UnboundLocalError`. Changed to `from sqlalchemy import text as _sql` (unique alias) in the second import block.

### C2 — Max daily USD reduction (`base_engine/risk/bankroll_manager.py`)
MirrorBot `max_daily_usd` reduced from $10,000 → $5,000. Data showed $5K/day exposes ≤1.5% of $330K capital.

### C3 — BUG-14: Adaptive safety drawdown denominator (`bots/mirror_adaptive_safety.py`)
**Bug**: Drawdown calculation divided by `max(peak, 1.0)` — the P&L peak of last 50 trades (could be $200). A current loss of -$100 reported 150% drawdown instead of 0.5%.
**Fix**: Divide by `MIRROR_TOTAL_CAPITAL` ($20,000). `self._drawdown_pct = max(0,_high_water - cum) / max(_capital, 1.0)`.

### C4 — Exponential adaptive safety (`bots/mirror_adaptive_safety.py`)
Replaced linear threshold response with exponential decay: `mult = exp(-8 * drawdown_pct)`.
- dd=5% → 0.67x, dd=10% → 0.45x, dd=20% → 0.20x
- Hot streak bonus (WR>65%): up to 1.2x
- `MIRROR_ADAPTIVE_SAFETY` default changed `false` → `true`

### C5 — NO-side dampener + hard block (`bots/mirror_bot.py`, `config/settings.py`)
- `MIRROR_NO_SIDE_DAMPENER` default: 0.5 → 0.3 (NO loses 7x more than YES)
- Added `MIRROR_NO_PRICE_BLOCK=0.75`: hard reject NO trades when NO price > 75%
- Data: NO-side = -$139K (87% of total losses)

### C6 — Beta(6,10) empirical Bayes prior (`base_engine/learning/elite_reliability.py`)
Changed `EliteReliabilityTracker` prior from Beta(1,1) (flat, shrinks toward 50%) to Beta(6,10) (population WR = 37.5%, shrinks toward realistic mean).
- Traders with few resolved trades no longer look artificially like 50% WR
- 76% of tracked traders are unprofitable — prior now reflects this
- Updated `_build_beta_rec()`, added class constants `_PRIOR_ALPHA=6`, `_PRIOR_BETA=10`

### C7 — Market-maker detection gate (`bots/mirror_bot.py`)
Added `_trader_market_sides: Dict[str, float]` to track when traders trade each side per market. If a trader trades YES and NO on the same market within 24h → market-maker, copy gives 0 edge → reject. Dict pruned at 5000 entries (25h TTL).

### C8 — Market volume gate (`bots/mirror_bot.py`, `config/settings.py`)
Added `MIRROR_MIN_MARKET_VOLUME_24H=5000.0` (default $5K). Uses `volume_24h` from market index; falls back to `liquidity` if 24h volume unavailable. Thin markets have poor execution and invite manipulation.

### C9 — Category expertise filter (`bots/mirror_bot.py`, `base_engine/learning/elite_reliability.py`, `config/settings.py`)
New gate: if trader has ≥10 resolved trades in this category AND category WR < 45% → reject. Overall WR can be lucky; category WR reveals systematic failure.
- Added `category_win_rate()` method to `EliteReliabilityTracker`
- New settings: `MIRROR_CAT_MIN_TRADES=10`, `MIRROR_CAT_MIN_WIN_RATE=0.45`
- Defensive `int()`/`float()` conversions for MagicMock test compatibility

### C10 — Reversed stop-loss graduation (`bots/mirror_bot.py`, `config/settings.py`)
**Bug**: Stop-loss was -15% (0-48h), -10% (48-72h), -5% (72h+). BACKWARDS. Young positions need tight stops; mature positions near resolution have less time to recover.
**Fix**: -10% (0-48h), -12% (48-72h), -15% (72h+, loosest near resolution).
Added near-resolution tightener: if TTR < 24h → stop tightens to -5%.
Added settings: `MIRROR_STOP_LOSS_TIGHTEN_48H=-0.12`, `MIRROR_STOP_LOSS_TIGHTEN_72H=-0.15`, `MIRROR_STOP_LOSS_NEAR_RES_HOURS=24.0`, `MIRROR_STOP_LOSS_NEAR_RES_PCT=-0.05`.

### C11 — Resolution-relative max-hold (`bots/mirror_bot.py`, `config/settings.py`)
Replaced fixed 96h force-exit with `held / (held + ttr_remaining) >= 0.80`. A 7-day market exits ~day 5.6; a 30-day market exits ~day 24. Both use 80% of total duration.
Added `MIRROR_MAX_HOLD_FRACTION=0.80`.
TTR computed from `market_index.end_date_iso → hours_until_resolution()` (not `_market_meta_cache` which stores string labels "hours"/"days"/"weeks").

### C12 — TTR component in confidence formula (`bots/mirror_bot.py`)
Added `_ttr_adj` to multi-factor confidence calculation:
- TTR < 12h → -0.05 (dangerous, insider territory)
- 12-48h → +0.02 (optimal copy-trade window)
- > 168h → -0.02 (noisy, overconfident)
- TTR 48-168h: no adjustment

### C13 — XGBoost hyperparameters + walk-forward embargo (`scripts/train_mirror_ml_selector.py`)
- `learning_rate`: 0.1 → 0.02 (prevents overfit on ~585-sample dataset)
- `n_estimators`: 100 → 200 (compensates for lower LR)
- Added `reg_lambda=5`, `reg_alpha=0.5` (L2+L1 regularisation)
- `subsample`: 0.8 → 0.7, `colsample_bytree`: 0.8 → 0.6 (variance reduction)
- Added `_walk_forward_splits(gap=5)`: 5-trade embargo between training end and validation start. Prevents leakage from correlated adjacent trades (same market, same session).

### C14 — Calibration TTR precision + FocalTemperatureCalibrator defaults (`bots/mirror_bot.py`, `base_engine/features/calibration.py`)
- `mirror_bot.py` calibration call: replaced rough bucket mapping `{"hours":0.5,"days":3.0,"weeks":21.0}` with `_ttr_h / 24.0` (exact TTR already computed for C12). Old mapping always landed in 0-7d bucket.
- `FocalTemperatureCalibrator.__init__`: defaults changed T=1.0→1.5, γ=0.0→2.0. Conservative prior for overconfident markets. `calibrate()` still returns identity when `_fitted=False`.

### C15 — RTDS recv_timeout 120s → 25s (`base_engine/data/rtds_websocket.py`, `bots/mirror_bot.py`, `config/settings.py`)
- `RTDSWebSocket` default `recv_timeout` 120 → 25. With 5s manual PINGs, 120s meant silent disconnects went undetected for 2 minutes.
- Added `RTDS_RECV_TIMEOUT=25` setting.
- `mirror_bot.py` passes `recv_timeout` from settings.
- Stale watchdog threshold: 120 → 60s (with 25s timeout, 60s = reconnect loop stuck).

### C16 — Gate chain fail-fast reorder (`bots/mirror_bot.py`)
Moved market-maker detection (C7), opposing-side dedup, and same-side dedup from AFTER the category resolve DB/cache call to BEFORE it.

New tier structure:
- **Tier 0** (if not sell): whale gate, trader blacklist, market blocklist, cooldown
- **Tier 1** (if not sell): market-maker detection, opposing-side dedup, same-side dedup ← **moved up**
- **Tier 2**: category resolve (DB/cache), category blocklist, expertise, position cap

These three gates reject the majority of duplicate RTDS signals in pure memory before incurring any I/O.

### M2 — `elite_watchlist.py` inline position creation
Fixed deleted `_track_open_position()` call replaced with inline `_open_positions` dict creation.

---

## Files Modified
| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | C1, C5, C7, C8, C9, C10, C11, C12, C14, C15, C16 |
| `bots/mirror_adaptive_safety.py` | C3, C4 |
| `bots/elite_watchlist.py` | M2 |
| `base_engine/learning/elite_reliability.py` | C6 |
| `base_engine/features/calibration.py` | C14 |
| `base_engine/risk/bankroll_manager.py` | C2 |
| `base_engine/data/rtds_websocket.py` | C15 |
| `config/settings.py` | C4, C5, C8, C9, C10, C11, C15 |
| `scripts/train_mirror_ml_selector.py` | C13 |
| `tests/unit/test_mirror_bot_logic.py` | C6, C8, C9 test fixes |
| `tests/unit/test_focal_temperature_scaling.py` | C14 test update |

---

## Test Results
**1720 passed, 8 skipped, 7 xfailed** — full suite green after all 16 commits.

---

## New Settings Added
| Setting | Default | Purpose |
|---------|---------|---------|
| `MIRROR_NO_PRICE_BLOCK` | 0.75 | Hard reject NO trades above this price |
| `MIRROR_NO_SIDE_DAMPENER` | 0.3 (was 0.5) | Kelly multiplier for NO-side trades |
| `MIRROR_MIN_MARKET_VOLUME_24H` | 5000.0 | Minimum $24h volume to trade |
| `MIRROR_CAT_MIN_TRADES` | 10 | Min category trades for expertise gate |
| `MIRROR_CAT_MIN_WIN_RATE` | 0.45 | Min category WR to pass expertise gate |
| `MIRROR_STOP_LOSS_TIGHTEN_48H` | -0.12 | Stop loss for 48-72h positions |
| `MIRROR_STOP_LOSS_TIGHTEN_72H` | -0.15 | Stop loss for 72h+ positions (loosest) |
| `MIRROR_STOP_LOSS_NEAR_RES_HOURS` | 24.0 | Hours-to-resolution for near-res tightener |
| `MIRROR_STOP_LOSS_NEAR_RES_PCT` | -0.05 | Stop loss when TTR < near-res threshold |
| `MIRROR_MAX_HOLD_FRACTION` | 0.80 | Exit at 80% of total market duration |
| `MIRROR_ADAPTIVE_SAFETY` | true (was false) | Enable exponential adaptive safety |
| `RTDS_RECV_TIMEOUT` | 25 | WebSocket recv timeout in seconds (was 120) |

---

## Critical Architecture Notes
- `_market_meta_cache[1]` is a TTR **string label** ("hours"/"days"/"weeks"), NOT a float. Never compare to a number. Use `get_market_from_index().get("end_date_iso")` → `hours_until_resolution()` for numeric TTR.
- `_walk_forward_splits(n, n_splits, gap=5)` is the new CV split function. Always uses walk-forward (no future data leakage) with a 5-trade embargo.
- `Beta(6,10)` prior: `_PRIOR_ALPHA=6`, `_PRIOR_BETA=10`. Test assertions must account for these offsets (e.g., `alpha_yes = yes_correct + 6`).
- `FocalTemperatureCalibrator` default T=1.5, γ=2.0. `calibrate()` still returns identity when `is_fitted=False`.

---

## Deploy Checklist
1. All changes on `master` branch (NOT deployed to VPS yet)
2. Deploy via `./deploy.sh` as usual
3. Verify post-deploy:
   ```
   journalctl -u polymarket-ai -f | grep "MirrorBot"
   # Watch for: mirror_adaptive_safety_refresh, mirror_market_maker_blocked, mirror_low_volume_blocked
   # No new errors
   ```
4. Confirm `MIRROR_ADAPTIVE_SAFETY=true` is live (new default)
5. Monitor first 24h for `rtds_recv_timeout` frequency (should be rare now at 25s)

---

## Outstanding (Not in S137)
- ML selector model retraining (C13 fixes the training script; needs 300+ resolved trades to run)
- Calibration FTS fitting (needs 50+ resolved prediction_log entries for MirrorBot)
- S132: rel_mult capped at 1.0 — do NOT raise without new data showing 1.05+ is signal
- S132: `_price_adj` zeroed (contrarian boost was anti-signal) — do NOT re-enable
