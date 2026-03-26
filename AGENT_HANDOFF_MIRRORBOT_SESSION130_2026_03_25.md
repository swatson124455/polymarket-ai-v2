# AGENT HANDOFF — MirrorBot Session 130 (2026-03-25)

## Session Scope
MirrorBot only. No bleed to Weather/Esports.

## Deploy
`sudo systemctl restart polymarket-ai` — PID 3356641, 2026-03-26 00:33 UTC

---

## FIX 1: Confidence Formula (S130)

**Bug**: S110 introduced Bayesian replacement that crushed all confidence scores to >=0.80. The base was computed as `P(win|category,price,conviction)` (~0.50-0.53) then capped at 0.75 via `min(base, 0.75)`. Every entry got compressed into a narrow band with most at 0.80+.

**Fix**: Restored upstream efficiency score (0.55-0.70 range) as the floor, with additive adjustments:
- `conf_cat_adj` — category bonus (0.001-0.01)
- `conf_price_adj` — price-based adjustment (0.01-0.04)
- `conf_conv_adj` — conviction adjustment
- Hard cap at 0.99

**Result**: 440 post-S130 entries show spread 0.550 → 0.734, median 0.600. Clean distribution restored.

**Charts**: `/tmp/mirror_conf_scatter.png`, `/tmp/mirror_conf_buckets.png`, `/tmp/mirror_cat_pnl.png`

## FIX 2: Phase 4b-alt Bad Import (P0)

**Bug**: `resolution_backfill.py:532` had `__import__("base_engine.config.settings")` → `ModuleNotFoundError` every 30min backfill cycle since S126.

**Root cause**: Phase 4b-alt resolves positions where `paper_trades.market_id` (condition_id) differs from `positions.market_id` (Gamma ID). Introduced in S126 but import path was wrong — `config/settings.py` is at project root, not inside `base_engine/`.

**Fix**: Changed to `__import__("config.settings", fromlist=["settings"])`.

**Blast**: Shared infrastructure (all bots), but ADDITIVE — fixes a broken path that never worked. No behavioral change to existing code.

**Impact**: Unblocks resolution of ~1,150+ ML-scored entries sitting in positions table with status='closed' but no RESOLUTION trade_event. Resolution rate should jump from 14% (182/1,335) to 40-50% within 24h.

**Verify**: `sudo journalctl -u polymarket-ai --since '30 min ago' | grep '4b-alt'` — should show success instead of `ModuleNotFoundError`.

## FIX 3: Linter — Phase 4b exit_pnl subtraction

Applied during session: Phase 4b now subtracts prior EXIT P&L from RESOLUTION P&L to avoid double-counting when a position had both partial exits and final resolution.

---

## ML Shadow Race Status

- **1,335 entries** with ML scores (XGB, QL, Combo) stored in event_data
- **182 resolved** (14%) — blocked by 4b-alt failure since S126
- Expected resolution rate post-fix: 40-50%
- ML scoring runs in shadow mode — scores entries but does NOT gate trades

## 7-Day P&L Snapshot (pre-S130 data dominates)

| Metric | Value |
|--------|-------|
| Total resolved | 1,777 |
| Overall WR | 39.7% |
| Total P&L | -$92,811 |
| Avg P&L/trade | -$53.16 |

### By Category
| Category | n | WR% | P&L |
|----------|---|-----|-----|
| Crypto | 681 | 38.6% | -$78,292 |
| Sports | 506 | 40.9% | -$14,448 |
| Finance | 12 | 41.7% | -$1,469 |
| Politics | 10 | 50.0% | -$1,465 |
| Unknown | 395 | 41.0% | +$64 |
| Esports | 172 | 37.2% | +$3,241 |

### By Side
| Side | n | WR% | P&L |
|------|---|-----|-----|
| NO | 1,016 | 39.1% | -$81,739 |
| YES | 761 | 40.6% | -$11,072 |

**Key insight**: Crypto is 88% of losses. NO side bleeds 7.4x more than YES. All pre-S130 entries had confidence >=0.80 (broken formula).

---

## Bug Audit (from S129 comprehensive audit)

9 bugs CONFIRMED, 1 FALSE POSITIVE:
- **MB-1**: 4b-alt import → FIXED this session
- **SE-1 through SE-3, EB-1 through EB-5**: Other bot bugs → deferred to respective sessions
- **SE-4**: Phase 4b partial exit double-counting → FALSE POSITIVE (WHERE clause already excludes SELL)

---

## Next Steps (S131+)

1. **Verify 4b-alt success** — Check logs after next backfill cycle (~30min post-restart). Should see success message instead of ModuleNotFoundError.
2. **Monitor resolution rate** — Track 182 → target 600+ within 24h as 4b-alt catches up.
3. **Crypto category investigation** — 88% of losses. Consider: category-specific confidence floor, reduced position sizing, or category exclusion.
4. **NO side asymmetry** — -$81,739 vs -$11,072. Investigate whether NO entries systematically overpay.
5. **ML score evaluation** — Once resolution rate climbs to 40%+, analyze XGB/QL/Combo scores vs realized P&L to determine if ML gating would improve returns.
6. **S130 confidence calibration** — Monitor post-S130 entries (440 so far, spread 0.55-0.73) as they resolve. If WR correlates with confidence buckets, the formula is working.
