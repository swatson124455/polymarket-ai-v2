# MirrorBot Session 104 — Agent Continuation Prompt

**Bot scope**: MirrorBot ONLY. No bleed to other bots unless manually requested.

---

## WHO YOU ARE

You are continuing development of **MirrorBot**, the highest-performing bot in a 15-bot Polymarket automated trading system. Real capital ($20K). Paper trading mode (`SIMULATION_MODE=true`). Going live = flipping a boolean.

MirrorBot copy-trades elite whale traders via RTDS WebSocket firehose. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing.

---

## WHAT JUST HAPPENED (Session 103)

### Bug Fixed: Confidence gate was dead code
`self.min_confidence` was set at init but NEVER checked in the RTDS entry path. Trades entered at 0.55 confidence from watchlist, domain drift halved to ~0.275, calibration bumped to ~0.38, and they executed with no threshold check.

**Data proof** (corrected SQL join — RESOLUTION events have `token_id=NULL`, must join on `(market_id, side)` only):

| Confidence | Resolved | Win Rate | P&L | $/position |
|---|---|---|---|---|
| 30-40% | 11 | 9% | -$1,725 | -$157 |
| 40-50% | 11 | 18% | -$585 | -$53 |
| 50-55% | 337 | 36% | +$8,696 | +$26 |
| 55%+ | 587 | 48% | +$7,415 | +$13 |

### Changes Made (S103)
1. **Confidence gate at 0.45 (TRIAL)** — Added after domain drift + calibration, before Kelly sizing. `mirror_bot.py:1261`.
2. **`entry_confidence` logged** on executed trades for tracking.
3. **Log spam demoted** — `mirror_price_bounds` and `sell_no_position` → DEBUG (~25k lines/6h removed).
4. **Dead `_conformal_interval`** variable removed.
5. Tests: 1631 passed, 0 failed.

### Confidence Flow
1. **EliteWatchlist** (`elite_watchlist.py:385`): `confidence = min(0.70, 0.55 + efficiency * 0.5)`. Hardcoded base 0.55.
2. **Domain drift** (`mirror_bot.py:1216`): `confidence *= 0.50` if trader < 10 resolved trades in category.
3. **Calibration** (`mirror_bot.py:1253`): FTS domain/horizon adjustment.
4. **Gate** (`mirror_bot.py:1261`): `if confidence < 0.45: return False`.

---

## YOUR FIRST TASK: REVIEW THE 0.45 TRIAL

This is the P1 open item. The 0.45 threshold was set as a trial. You MUST review it.

### How to check
```bash
# SCP the diagnostic script to VPS and run it:
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem scripts/diag_confidence.py ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && ./venv/bin/python /tmp/diag_confidence.py"

# Check gate is firing:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep mirror_low_confidence | wc -l"

# Check trade volume:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "journalctl -u polymarket-ai --since '6 hours ago' --no-pager | grep 'Mirror trade executed' | wc -l"
```

### Decision matrix
| Observation | Action |
|---|---|
| 40-50% bracket still negative | Raise to 0.50 |
| Volume < 50 trades/6h | Lower to 0.40 |
| 50-55% stays best $/pos | Threshold in right range |
| Gate blocking everything | Check calibration stack output, may need adjustment |

---

## SYSTEM ARCHITECTURE

### Entry Path (RTDS → trade execution)
```
RTDS WebSocket (global firehose, all Polymarket trades)
  → EliteWatchlist.on_rtds_trade()
    → O(1) lookup: trader in 500-whale watchlist?
    → YES → resolve side (YES/NO/SELL), log whale_trade
    → Calculate confidence: 0.55 + efficiency bonus (0-0.15)
    → Call MirrorBot._execute_mirror_trade()

_execute_mirror_trade() validation pipeline (24 checks):
  1.  Market blocklist (in-memory)
  2.  Per-market cooldown (30min)
  3.  Category blocklist
  4.  Category resolution (DB lookup, cached 1h)
  5.  Hard price bounds [0.10, 0.95]
  6.  Circuit breaker
  7.  Position cap (200 on VPS, 500 in config — MISMATCH)
  8.  Daily exposure cap ($10K)
  9.  Category cap ($40K)
  10. Opposing-side dedup
  11. SELL: requires existing position + size > 0
  12. Current price correction (market price, not trader fill)
  13. Inactive market filter
  14. Near-resolution filter (4h)
  15. Slippage cap (8%)
  16. Reliability gate (LR ≥ 1.0)
  17. Domain drift penalty (0.50x if < 10 trades in category)
  18. Signal enhancements (skipped by default)
  19. FTS calibration (domain + horizon)
  20. Confidence gate (≥ 0.45 TRIAL) ← S103
  21. Kelly sizing via BotBankrollManager
  22. Single price dampener (30-50c: 0.50x, ≥70c: 0.40x)
  23. Per-market USD cap ($400)
  24. Daily remaining cap + min trade USD ($50)
```

### Exit Path (scan loop, 45s interval)
```
_check_and_execute_exits():
  - Sync prices from DB (position_manager updates every 10s)
  - Take-profit at +25%
  - Force exit at 96h
  - Graduated stop-loss: 15% (0-48h), 10% (48-72h), 5% (72h+)
  - Circuit breaker: pause entries when unrealized < -20% of capital
```

### State Persistence
| State | Mechanism |
|---|---|
| `_daily_exposure` | Restored from `trade_events SUM` on startup |
| `_open_positions` | Restored from `positions` table on startup |
| `mirrored_trades` (dedup) | Redis, flushed every 100 scans |
| `_category_exposure` | Restored from positions on startup |
| `_market_meta_cache` | Pre-populated from DB on startup (S92) |
| `_token_side_cache` | Pre-populated from DB on startup (S92) |

---

## CURRENT STATE

### P&L (as of S103 diagnostic)
- **All-time realized**: +$19,769 (ENTRY=3766, EXIT=694/+$6,153, RESOLUTION=931/+$13,616)
- **24h**: -$212
- **Open positions**: 197, exposure $14,539, unrealized +$138
- **Daily trend**: Mar 17 -$553, Mar 18 -$343

### Config (VPS values)
```
MIRROR_MIN_CONFIDENCE=0.45       # S103 TRIAL (was 0.50, never enforced)
MIRROR_HARD_MIN_PRICE=0.10       # S102 (was 0.05)
MIRROR_HARD_MAX_PRICE=0.95
MIRROR_FAVORITE_DAMPENER=0.40    # ≥70c
MIRROR_DEAD_ZONE_DAMPENER=0.50   # 30-50c
MIRROR_MIN_TRADE_USD=50.0        # S102 (was 10.0)
MIRROR_MAX_CONCURRENT_POSITIONS=200  # VPS .env (config says 500 — MISMATCH)
MIRROR_MAX_PER_MARKET=400
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000  # S100 (was 4000)
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MAX_SLIPPAGE_PCT=0.08
MIRROR_MIN_HOURS_TO_RESOLUTION=4
MIRROR_STOP_LOSS_PCT=0.15
MIRROR_TAKE_PROFIT_PCT=0.25
MIRROR_FORCE_EXIT_HOURS=96
kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K
SIMULATION_MODE=true
```

### Key Insight (S102 data analysis)
The whales' edge is in **picking longshots (10-30c) that hit more often than the market expects**. Asymmetric payoff: lose 15c on loss, gain 85c on win. Even 25-30% WR is hugely profitable. >50c is marginal. <10c is noise.

---

## OPEN ITEMS

| Priority | Item | Notes |
|---|---|---|
| **P1** | **TRIAL: Review 0.45 confidence gate** | Run `diag_confidence.py`. Adjust threshold based on data. |
| P2 | Position cap mismatch | VPS=200, config=500. Bot hitting cap (431 rejections/6h). Decide and align. |
| P2 | ~570 positions at 30-40% confidence | Pre-gate. Resolving naturally. -$157/pos on resolved sample. |
| P3 | NO vs YES WR asymmetry | 72% vs 39%. Monitor. |
| P3 | `mirror_calibration.py` cleanup | FTS active, conformal dead. Could strip conformal code. |
| P4 | Hold-time analysis | <24h positions net losers. Consider min hold time. |
| P5 | Resolution NULL token_id | P&L queries MUST join on `(market_id, side)` only. |

---

## CRITICAL TRAPS (DO NOT BREAK)

1. `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
2. `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
3. `trade_events` is P&L authority, NOT `paper_trades`.
4. RTDS envelope: unwrap `data.get("payload", data)`.
5. RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
6. `whale_trades` requires explicit `await session.commit()`.
7. `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
8. Positions table: NO `closed_at`, NO `updated_at`, NO `bot_name` — use `source_bot` or `bot_id`.
9. MirrorBot entry price: Uses CURRENT market price, NOT trader's fill price.
10. RESOLUTION events have `token_id=NULL` — join on `(market_id, side)` only.
11. `self.min_confidence` is NOW enforced — changing `MIRROR_MIN_CONFIDENCE` in `.env` WILL affect trade flow.
12. `paper_trades` has NO `metadata` JSONB column.
13. `traded_markets.bot_names` is TEXT (not array) — use `LIKE '%BotName%'`.
14. Python 3.13 scoping: `from X import Y` inside function shadows module-level.
15. `PatchDriftDetector._patch_timestamps` must ONLY be set on genuine patch changes.

---

## VPS ACCESS

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS_HOST=ubuntu@34.251.224.21
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS_HOST

# Service
systemctl status polymarket-ai
journalctl -u polymarket-ai -f | grep -i mirror

# Deploy
cd /opt/polymarket-ai-v2 && bash deploy.sh

# DB (via Python on VPS)
cd /opt/polymarket-ai-v2 && ./venv/bin/python scripts/bot_pnl.py MirrorBot 24

# Config
cat /opt/pa2-shared/.env | grep MIRROR
```

---

## KEY FILES

| File | Purpose | Lines |
|------|---------|-------|
| `bots/mirror_bot.py` | Core MirrorBot logic | ~1380 |
| `bots/elite_watchlist.py` | RTDS trade matching + confidence calc | ~420 |
| `bots/mirror_calibration.py` | FTS domain/horizon calibration | ~88 |
| `bots/base_bot.py` | Shared bot base (place_order, sizing) | ~340 |
| `base_engine/base_engine.py` | Shared engine (market index, order gateway) | ~2500 |
| `base_engine/execution/order_gateway.py` | Order execution + risk checks | ~600 |
| `base_engine/data/database.py` | DB models + session management | ~1100 |
| `config/settings.py` | All config defaults | ~400 |
| `scripts/diag_confidence.py` | P&L by confidence bracket (S103) | ~116 |
| `scripts/bot_pnl.py` | Canonical P&L script | ~200 |
| `tests/unit/test_mirror_bot_logic.py` | 64 MirrorBot unit tests | ~1000 |

---

## SESSION HISTORY (MirrorBot, recent)

| Session | Date | Key Changes |
|---------|------|-------------|
| S103 | 2026-03-18 | Confidence gate 0.45 TRIAL, log spam fix, dead conformal |
| S102 | 2026-03-18 | Hard floor 10c, single dampener, dead code purge, $50 min |
| S101 | 2026-03-18 | Bucket filters, whale trade commit fix, copied flag |
| S100 | 2026-03-17 | L2 book walk, whale trade log, category cap $40K |
| S99 | 2026-03-17 | Circuit breaker, stop-loss, take-profit, stale RTDS detection |
| S96 | 2026-03-16 | Consensus scan removed, RTDS sole entry path (-218 lines) |
| S94 | 2026-03-16 | Latency 2967ms→11.9ms, lock-free DB, RTDS fast-path |
| S93 | 2026-03-15 | Conformal disabled, Kelly 0.0625→0.25 |
| S92 | 2026-03-15 | Startup cache, RTDS envelope fix |
| S91 | 2026-03-15 | Tier 0 filters, slippage cap, market cooldown |

---

## RULES (from CLAUDE.md — non-negotiable)

1. **One fix per commit.** No "while I'm in here" refactors.
2. **Preserve every function signature** unless the signature IS the bug.
3. **No silent behavior changes.** State what changes from X to Y.
4. **Never delete code you don't understand.**
5. **Read the entire file** before modifying.
6. **Paper trading IS production.** Every feature matters identically.
7. **Bot-scoped sessions.** MirrorBot only unless manually requested.
8. **Grep for dependents** before changing any shared module.
