# AGENT HANDOFF — MirrorBot Session 134 (2026-03-27)
## CARBON-COPY CONTINUATION PROMPT

> **Scope**: MirrorBot ONLY. No bleed-over to Weather/Esports/Ensemble unless explicitly requested.
> **Deploy**: `20260326_203804` (latest). All S134 changes LIVE on VPS.
> **Git**: Clean working tree on `master`. HEAD = `9061b46`.

---

## 1. WHAT THIS SESSION DID

### 1A. Committed & Deployed (S133 Bug Fixes + S134 Features)

**S133 Bug #2 — Exit decrement price mismatch** (`mirror_bot.py:942`)
- `_daily_exposure` and `_category_exposure` decremented by `exit_size * exit_price` on manual exit, but incremented by `size * entry_price` on entry → exposure drift.
- Fix: Changed to `exit_size * pos.get("entry_price", exit_price)`.

**S133 Bug #7 — New trades never added to `_open_positions`** (`mirror_bot.py:1727-1738`)
- Newly opened positions had NO exit monitoring (stop-loss, trader-exit, max-hold) until next restart.
- `_track_open_position()` was defined but never called.
- Fix: Added inline position creation in the `else` branch at L1731. Deleted dead `_track_open_position()` method.

**S134 Feature 1 — Per-Trader P&L Blacklist** (`mirror_bot.py:1223-1238`)
- Data: 76% of tracked traders unprofitable. Top 3 worst = -$68K (43% of all losses).
- Gate: If trader has >= `MIRROR_TRADER_MIN_RESOLVED` (20) resolved trades AND WR < `MIRROR_TRADER_MIN_WIN_RATE` (0.35), reject.
- New methods in `elite_reliability.py`: `overall_win_rate(address)`, `total_trade_count(address)`.
- Config: `MIRROR_TRADER_MIN_WIN_RATE=0.35`, `MIRROR_TRADER_MIN_RESOLVED=20`.
- Log: `mirror_trader_blacklisted`.

**S134 Feature 2 — Spread Gate** (`mirror_bot.py:1372-1383`)
- Data: 20c+ spread = -$151K in losses.
- Gate: `spread = yes_price + no_price - 1.0`. If `spread > MIRROR_MAX_SPREAD` (0.20), reject.
- Log: `mirror_spread_rejected`.

**S134 Feature 3 — ML Selector Fail-Open** (`mirror_bot.py:1510-1542`)
- Any ML scoring error crashed entire `_execute_mirror_trade()`, silently dropping trade.
- Fix: try/except wrapper. On error, `_ml_scores=None`, trade proceeds without ML gate.
- ML still gated by `MIRROR_USE_ML_SELECTOR=false` (default). Shadow-only scoring active.

### 1B. Verified Shared Infra (Items 4-9 from S128 Audit)
All 6 shared infra bugs previously flagged as MirrorBot-impacting are **ALREADY FIXED**:
- P0-2 (`prediction_engine.py:2512` datetime crash) — Fixed
- P1-19 (`database.py:3553` resolution P&L zeroed) — Fixed
- P1-20 (`resolution_backfill.py:470` partial-exit double-count) — Fixed
- P0-3 (`base_engine.py:1451` market index empty on restart) — Fixed
- P1-7 (`kill_switch.py:59` fail-safe defaults allow) — Fixed
- P1-4 (`_check_price` NameError) — Fixed

---

## 2. COMPLETE TODO LIST (Ordered by Priority)

### P1: Config-Only Activation (No Code Needed)
1. **ML gate activation** — Wiring verified complete. Config-only: `MIRROR_USE_ML_SELECTOR=true`, `MIRROR_ML_STRATEGY=xgb`. Needs 48h shadow data analysis first: `python scripts/ml_shadow_analysis.py --hours 48`. Data: `ml_trade` WR 45.5% vs `ml_skip` 37.3% on 585+ entries.
2. **Verify S134 gates in production** — Check `mirror_trader_blacklisted` and `mirror_spread_rejected` log counts: `journalctl -u polymarket-ai --since "2026-03-27" | grep -c mirror_trader_blacklisted`

### P2: MirrorBot-Specific Code
3. **BUG-14**: Adaptive safety drawdown uses last-50 P&L peak, not bot capital (`mirror_adaptive_safety.py:80-86`). Needs BotBankrollManager wiring. Currently disabled (`MIRROR_ADAPTIVE_SAFETY=false`).
4. **Calibration go-live** — FTS T=2.0 fitted, shadow-only. Compare `conf_cal_shadow` vs raw confidence. Target ~Apr 5.
5. **~1,005 open positions** — Draining as markets resolve. Monitor decline rate.
6. **4b-alt 301 unresolved markets** — Self-healing, not a code problem.

### P3: S127 Audit Bugs (7 Remaining)
7. **BUG-4**: Cross-channel RTDS dedup gap (composite key for channels without `transaction_hash`)
8. **BUG-COOL**: `_market_cooldown` dict unbounded (prune stale entries)
9. **DATA-2**: `_entered_market_sides` unbounded (add 30-day lookback)
10. **INEFF-1**: New aiohttp session per elite refresh (reuse session)
11. **INEFF-2**: Pipe-delimited serialization in reliability tracker (switch to JSON)
12. **INEFF-6**: Dead code in reliability tracker (`a + b >= 2` always true)
13. **DATA-5**: Missing doc on opposing pair cleanup behavior

### P3: Strategic
14. **NO-side asymmetry root cause** — Dampened 0.5x but never investigated WHY NO loses 7x more. Need: per-side entry price distribution, per-side time-to-resolution, per-side market type breakdown.
15. **Watchlist pruning** — Only 16/67 profitable. Auto-prune or shrink to proven winners.
16. **Position reconciliation** — 1,005 open vs actual exposure correctness.

### P5: Backlog
17. Consensus signal (multi-whale agreement)
18. Time-of-day analysis
19. Category-specific confidence floors
20. Model persistence in deploy.sh
21. Calibration re-enable (~Apr 5)
22. CLOB credentials + funded wallet

---

## 3. FULL GATE CHAIN (20 Gates, Top to Bottom)

Position in `_execute_mirror_trade()`, `mirror_bot.py`:

| # | Gate | Line | Config/Value |
|---|------|------|-------------|
| 1 | Category blocklist | L1208 | `MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed` |
| 2 | $50 whale trade minimum | L1215 | `MIRROR_MIN_WHALE_TRADE_USD=50` |
| 3 | **Per-trader blacklist** | L1223 | `MIN_WIN_RATE=0.35, MIN_RESOLVED=20` **NEW S134** |
| 4 | Market blocklist (closed/expired) | L1241 | -- |
| 5 | Per-market cooldown | L1244 | `MIRROR_MARKET_COOLDOWN_SECONDS=1800` |
| 6 | Opposing-side guard | ~L1260 | Prevents YES+NO same market |
| 7 | Same-side dedup | ~L1280 | Prevents doubling |
| 8 | Position cap | ~L1300 | `MIRROR_MAX_POSITIONS=1000` |
| 9 | Daily exposure cap | ~L1320 | `MIRROR_MAX_DAILY_EXPOSURE_PCT=0.15` |
| 10 | Market data fetch | ~L1340 | From index/API |
| 11 | Market inactive check | L1367 | -- |
| 12 | **Spread gate** | L1372 | `MIRROR_MAX_SPREAD=0.20` **NEW S134** |
| 13 | Hours-to-resolution filter | ~L1385 | -- |
| 14 | Price direction filter | ~L1395 | Consumed edge check |
| 15 | Confidence formula | L1405 | Bayesian + conviction, floor 0.60 |
| 16 | Reliability weighting | L1396 | `rel_mult` capped at 1.0 |
| 17 | Confidence floor | ~L1470 | `MIRROR_MIN_CONFIDENCE=0.60` (VPS env) |
| 18 | **ML selector** | L1510 | Shadow-only, fail-open **HARDENED S134** |
| 19 | Sizing | L1560 | BotBankrollManager + NO dampener 0.5x |
| 20 | Risk manager limits | L1600 | Final check |

---

## 4. ALL MIRROR_ CONFIG SETTINGS

```
MIRROR_MIN_CONFIDENCE=0.50 (VPS override: 0.60)
MIRROR_MAX_PER_MARKET=500
MIRROR_MAX_PER_MARKET_PCT=0.10
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000
MIRROR_MAX_TRACKED_TRADES=10000
MIRROR_EXIT_ENABLED=true
MIRROR_MAX_CONCURRENT_POSITIONS=600
MIRROR_MAX_DAILY_EXPOSURE_PCT=0.15
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MIN_ELITE_TRADES=100
MIRROR_USE_CALIBRATION=false
MIRROR_ADAPTIVE_SAFETY=false
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_SKIP_COORDINATOR_BUY=true
MIRROR_RTDS_FAST_PATH=true
MIRROR_USE_ML_SELECTOR=false
MIRROR_ML_STRATEGY=xgb
MIRROR_ML_MIN_SCORE=0.45
MIRROR_ML_MODEL_PATH=models/mirror_ml_selector.pkl
MIRROR_ML_QTABLE_PATH=models/mirror_ml_qtable.pkl
MIRROR_ML_MAX_AGE_DAYS=14
MIRROR_STOP_LOSS_PCT=0.15
MIRROR_MAX_POSITIONS=1000
MIRROR_TOTAL_CAPITAL=20000
MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MIN_TRADE_USD=50.0
MIRROR_MAX_SLIPPAGE_PCT=0.08
MIRROR_MIN_WHALE_TRADE_USD=50.0
MIRROR_NO_SIDE_DAMPENER=0.5
MIRROR_MAX_SPREAD=0.20                    # S134
MIRROR_TRADER_MIN_WIN_RATE=0.35           # S134
MIRROR_TRADER_MIN_RESOLVED=20             # S134
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70
MIRROR_FAVORITE_DAMPENER=1.0
```

---

## 5. P&L GROUND TRUTH

- **All-time resolved**: 5,495 trades, **-$159,442**, 39.3% WR
- **Crypto** (~2,800 trades, ~37% WR, ~-$100K+) — NOW BLOCKED
- **Non-crypto 0.70+ confidence**: 56.9% WR, +$2,243 — only consistently profitable bucket
- **S120 "+$26,986" was FALSE** — orphan RESOLUTION events. Real was -$3,661.
- **76% of tracked traders unprofitable** (51/67)
- **NO-side**: -$139K (38.3% WR) vs YES: -$20K (39.9% WR)
- **20c+ spread**: -$151K
- **Contrarian boost**: 32.9% WR, -$84K (KILLED in S132)
- **rel_mult > 1.05**: 37.1% WR, -$113K (CAPPED in S132)
- **Sub-$50 trades**: 39.9% WR, -$153K (GATED in S132)

**Canonical P&L command**: `python scripts/bot_pnl.py MirrorBot 720`

---

## 6. CRITICAL TRAPS (DO NOT BREAK)

1. `trade_events` is P&L AUTHORITY — never read `paper_trades` for P&L
2. `place_order()` requires `side="YES"/"NO"` — NEVER "BUY"/"SELL"
3. BotBankrollManager handles SIZING; risk_manager handles LIMITS. Both must pass.
4. `risk_manager.calculate_position_size()` DEPRECATED
5. `PSEUDO_LABEL_ENABLED=false` — DO NOT enable
6. asyncpg JSONB: `CAST(:x AS jsonb)` NOT `:x::jsonb`
7. `_market_meta_cache`: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
8. `trade_events` immutability trigger: Must `DISABLE TRIGGER` per-partition before DELETE
9. RESOLUTION idempotency: Uses INSERT...SELECT WHERE NOT EXISTS (ON CONFLICT broken on partitions)
10. Python 3.13 scoping: local imports shadow module-level for ENTIRE function
11. Deploy via `deploy/deploy.sh` — atomic symlink swap. VPS: `ubuntu@34.251.224.21`
12. `rel_mult` CAPPED AT 1.0 — S132. Do NOT raise without new data.
13. `_price_adj` ZEROED — S132. Do NOT re-enable contrarian boost.
14. NO-side 0.5x dampener — S132. `MIRROR_NO_SIDE_DAMPENER=0.5`.
15. `_open_positions` cleared on restart — re-enters from DB by EOD UTC.
16. MirrorBot entry price: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
17. RTDS envelope: Must unwrap `data.get("payload", data)`.
18. RTDS dedup: `on_rtds_trade()` handles own dedup.
19. `paper_trades` has NO `metadata` JSONB column.
20. Resolution backfill excludes SELL trades.
21. asyncpg timestamps: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`.

---

## 7. KEY FILE LOCATIONS

| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Main bot (~1800 lines) |
| `base_engine/learning/elite_reliability.py` | Bayesian win rate tracker |
| `base_engine/learning/elite_watchlist.py` | RTDS watchlist management |
| `base_engine/learning/elite_detector.py` | Elite trader detection |
| `bots/mirror_adaptive_safety.py` | Adaptive drawdown (DISABLED) |
| `bots/mirror_ml_selector.py` | ML trade selector (shadow-only) |
| `bots/mirror_calibration.py` | FTS confidence calibration (shadow-only) |
| `config/settings.py` | All config (L344-397 = MIRROR_*) |
| `scripts/bot_pnl.py` | Canonical P&L script |
| `scripts/ml_shadow_analysis.py` | ML gate evaluation |
| `tests/unit/test_mirror_bot_logic.py` | 68 tests |

---

## 8. MEMORY FILES TO READ

| File | What It Contains |
|------|-----------------|
| `memory/MEMORY.md` | Master index (first 200 lines loaded) |
| `memory/feedback_scope_lock.md` | NEVER add unsolicited features |
| `memory/feedback_pnl_math.md` | P&L formula rules (NEVER invert for NO) |
| `memory/feedback_audit_self_validation.md` | Self-validate before reporting |
| `memory/feedback_bot_sessions.md` | Bot-scoped session rules |

---

## 9. RULES FOR NEXT SESSION

1. **This is a MirrorBot-only session.** No WeatherBot/EsportsBot/Ensemble changes unless explicitly requested.
2. **Read `CLAUDE.md` first** — prime directive: working code is sacred.
3. **Paper trading IS production** — treat every change as if $25K is deployed.
4. **One fix per commit.** No "while I'm in here" refactors.
5. **Preserve function signatures.** Search all callers before changing any.
6. **No structural refactors during bug fixes.**
7. **Run `pytest` before committing** — all 1700+ tests must pass.
8. **Self-validate** per `memory/feedback_audit_self_validation.md` — re-read code, trace paths, check tests, rate confidence, remove false positives before reporting.

---

## 10. QUICK-START COMMANDS

```bash
# Check bot health
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "journalctl -u polymarket-ai --since '5 min ago' | grep MirrorBot | tail -20"

# Check new gates firing
ssh -i ... "journalctl -u polymarket-ai --since '1 hour ago' | grep -c mirror_trader_blacklisted"
ssh -i ... "journalctl -u polymarket-ai --since '1 hour ago' | grep -c mirror_spread_rejected"

# P&L check
ssh -i ... "cd /opt/polymarket-ai-v2/current && python scripts/bot_pnl.py MirrorBot 24"

# Run tests locally
cd C:/lockes-picks/polymarket-ai-v2 && python -m pytest tests/unit/test_mirror_bot_logic.py -v

# Deploy
cd C:/lockes-picks/polymarket-ai-v2 && KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

---

## 11. SESSION LEARNINGS (For Future Agent Behavior)

1. **Don't commit other bots' files** — S134 accidentally committed WeatherBot changes. Violated scope lock.
2. **Spread gate must be paper=live identical** — Initially proposed paper-only relaxation. User correctly flagged: paper IS production.
3. **Crypto blocking != crypto removal from WR** — Blacklist filters by category at trade time. Win rate calculation still includes crypto history for data integrity. Separate concern.
4. **xfail is a band-aid** — `test_pass3_fixes.py` was marked xfail because Pass 3 audit code wasn't applied. Root cause: those tests test code that doesn't exist yet. Should be removed or the code applied.
5. **Verify data exists before building features** — Esports stop-loss floor had no data backing. User flagged: need evidence before changing thresholds.
