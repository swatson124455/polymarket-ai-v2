# MirrorBot Session 91 Handoff — CVaR Cache + Conformal Fix + Tier 0 Pre-Trade Filters

**Date**: 2026-03-14 (deploy 20260314_230936)
**Scope**: MirrorBot-exclusive
**Commits**: `56c1d70` (CVaR cache + conformal + RTDS cleanup, bundled in batch), `fbbff93` (Tier 0 filters + config + test fix)
**Tests**: 1552 passed, 0 failures

---

## What Was Done

### Fix 1: CVaR Base Cache (`base_engine/risk/risk_manager.py`)
**Problem**: `risk_ms` was 3-13s per trade. `compute_cvar()` runs 10k Monte Carlo sims with Gaussian copula on ~200 positions. Was called 3x per trade: `compute_marginal_cvar()` calls it 2x (before/after), then `check_risk_limits()` calls it 1x more.

**Fix**: Added `_cvar_base_cache` with 30s TTL (position count as cheap hash proxy). Now base CVaR is computed once and reused for subsequent trades within the TTL window. Also reuses `_existing_positions` snapshot between CVaR and PCA factor checks (was fetching twice).

**Result**: risk_ms dropped from 3-13s to **0.9-2.0s** on VPS. First call in window ~2s, cached calls ~900ms.

### Fix 2: Conformal Width-Based Dampening (`base_engine/risk/bankroll_manager.py`)
**Problem**: Conformal prediction interval `p_low=0.052` (from logit-space residuals ~3.0) was substituted into Kelly as `kelly_confidence = p_low`. This made Kelly return 0 for any trade at market price > $0.05, blocking ALL trades.

**Fix**: Removed p_low → kelly_confidence substitution. New approach computes interval width (`p_high - p_low`) and applies as fraction dampener: `max(0.25, 1.0 - width)`. Point estimate (confidence) always used for Kelly edge calculation.

| Width | Dampener | Effect |
|-------|----------|--------|
| 0.0 | 1.0x | Full Kelly |
| 0.5 | 0.50x | Half Kelly |
| 0.9+ | 0.25x | Floor (quarter Kelly) |

**Result**: MirrorBot now executes trades with conformal active. Size dampened by interval uncertainty, not killed.

### Fix 3: RTDS Debug Logging Cleanup (`base_engine/data/rtds_websocket.py`)
Removed dead `_MAX_DEBUG_SAMPLES = 0` constant, `_debug_samples` attribute, and never-executed `rtds_raw_sample` logging block.

### Fix 4: Tier 0 Pre-Trade Filters (`bots/mirror_bot.py`)
5 in-memory filters at top of `_execute_mirror_trade()`, before any DB/cache/API hit (<0.01ms):

| Filter | Setting | Default | Behavior |
|--------|---------|---------|----------|
| Market blocklist | — | — | In-memory set, populated by category blocklist hits |
| Category blocklist | `MIRROR_CATEGORY_BLOCKLIST` | `"15-minute,speed"` | Substring match on category |
| Per-market cooldown | `MIRROR_MARKET_COOLDOWN_SECONDS` | 1800 (30 min) | Prevents re-entry on same signal |
| Min trade USD | `MIRROR_MIN_TRADE_USD` | 10.0 | Skip dust trades |
| Slippage cap | `MIRROR_MAX_SLIPPAGE_PCT` | 0.08 (8%) | Reject when market moved >8% from whale's fill |

**VPS verification**: dust filter blocking $3-9 trades, category/slippage filters ready (no matches yet in first scan).

### Fix 5: Test Fix (`tests/unit/test_mirror_bot_logic.py`)
`test_entry_trade_success` was failing because `bot.bankroll = MagicMock()` without setting `.capital` caused `float(MagicMock())` to return 1.0, making per-market cap $0.10, triggering dust filter. Fixed by setting `bot.bankroll.capital = 3000.0`.

---

## Config Settings Added (`config/settings.py`)
```
MIRROR_CATEGORY_BLOCKLIST=15-minute,speed
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MIN_TRADE_USD=10.0
MIRROR_MAX_SLIPPAGE_PCT=0.08
```

---

## VPS Verification (post-deploy)
```
risk_ms: 911ms, 1591ms, 2001ms  (was 3-13s)  ← CVaR cache working
mirror_conformal_applied: trades flowing through (p_low=0.052 no longer kills)
mirror_dust_skipped: trade_usd=$3-9 blocked (min_usd=10.0) ← dust filter working
Order latency breakdown: trades executing  ← conformal no longer blocks
```

---

## Files Modified
| File | Changes |
|------|---------|
| `base_engine/risk/risk_manager.py` | CVaR base cache (3 attrs + cached check) |
| `base_engine/risk/bankroll_manager.py` | Width-based conformal dampening |
| `base_engine/data/rtds_websocket.py` | Remove dead debug logging |
| `bots/mirror_bot.py` | 5 Tier 0 filters + `import time as _time` + 2 new attrs |
| `config/settings.py` | 4 new MIRROR_* settings |
| `tests/unit/test_mirror_bot_logic.py` | bankroll.capital=3000.0 in test |

---

## Outstanding Items
- **P3**: Reduce RTDS copy latency (currently 2-16s, target <1s)
- **P3**: `no_prediction: 12` per scan — team name matching failures (CS2/Valorant)
- **P5**: Consider lowering MIRROR_MIN_TRADE_USD from $10 to $5 after monitoring
- **P5**: Monitor slippage filter hit rate — 8% threshold may need tuning
- **P5**: CVaR cache TTL (30s) may be adjustable based on position change frequency

---

## Critical Traps (inherited from S90 + new)
All traps from Sessions 77-90 remain valid. New S91 additions:
- **`_market_blocklist` and `_market_cooldown`**: In-memory only, reset on restart. This is intentional — loss is 10-second re-sync, not financial risk.
- **`float(MagicMock())`**: Returns 1.0 in Python 3.13. Any test using `bot.bankroll = MagicMock()` MUST set `.capital` to a real number, or per-market cap becomes $0.05-$1.00.
- **CVaR cache invalidation**: Uses position count as hash proxy. If a position is swapped (close one, open another), count stays same → stale cache for up to 30s. Acceptable for risk limits but not for P&L.

---

## P&L State (unchanged from S90)
| Bot | Realized | Unrealized | Open Positions |
|-----|----------|------------|---------------|
| MirrorBot | +$15,051 | +$631 | 103 |

---

## Key Config (live VPS)
```
MirrorBot: capital=$20000, kelly=0.30, max_bet=$300, max_daily=$10000
MIRROR_MIN_CONFIDENCE=0.55, MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200, MIRROR_MAX_CONCURRENT_POSITIONS=200
MIRROR_CATEGORY_BLOCKLIST=15-minute,speed
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MIN_TRADE_USD=10.0
MIRROR_MAX_SLIPPAGE_PCT=0.08
```
