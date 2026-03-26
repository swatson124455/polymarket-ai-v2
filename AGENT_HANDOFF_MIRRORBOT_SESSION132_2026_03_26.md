# AGENT HANDOFF — MirrorBot Session 132 (2026-03-26)

## SCOPE: MirrorBot ONLY — No bleed to other bots

---

## 1. SYSTEM OVERVIEW

### What Is This?
A 15-bot automated trading system on Polymarket (prediction markets). MirrorBot is one bot — it copies trades from "elite" whale traders detected via RTDS (Real-Time Data Stream) on the Polygon blockchain.

### Architecture (Key Files)
| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | MirrorBot core — RTDS listener, confidence formula, trade execution |
| `bots/mirror_calibration.py` | Calibration stack (FTS + Horizon bias) — currently shadow-only |
| `bots/mirror_ml_selector.py` | ML shadow race (QL, XGB, Combo) — scoring but not gating |
| `bots/mirror_adaptive_safety.py` | Adaptive daily cap (disabled) |
| `bots/elite_watchlist.py` | 500-trader watchlist management |
| `bots/base_bot.py` | Shared bot base class — place_order(), scan loop, bankroll |
| `config/settings.py` | All config — MIRROR_* settings (lines 340-390) |
| `base_engine/data/database.py` | DB layer — asyncpg, insert_trade_event() |
| `base_engine/data/resolution_backfill.py` | Resolves markets, backfills P&L |
| `base_engine/execution/paper_trading.py` | Paper trade engine |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing (BotBankrollManager) |
| `base_engine/risk/risk_manager.py` | Risk limits (deprecated for sizing) |
| `base_engine/learning/elite_reliability.py` | Per-whale reliability tracker |
| `base_engine/features/calibration.py` | FTS + Le Horizon calibration math |
| `main.py` | Bot registry, startup, scheduler |

### VPS
- **Ubuntu-3**: 34.251.224.21 (16GB/4vCPU)
- **SSH**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`, user `ubuntu`
- **Service**: `systemd polymarket-ai` → `/opt/polymarket-ai-v2/venv/bin/python main.py`
- **Shared env**: `/opt/pa2-shared/.env` (production DB creds, overrides settings.py defaults)
- **Deploy**: `bash deploy/deploy.sh` (atomic symlink swap, auto-migrations, health check)
- **Current deploy**: `20260326_111112`
- **Logs**: `sudo journalctl -u polymarket-ai -f | grep Mirror`

### DB Tables (MirrorBot-relevant)
| Table | Purpose | Key columns |
|-------|---------|-------------|
| `trade_events` | **P&L AUTHORITY** — partitioned by event_time | bot_name, market_id, event_type (ENTRY/EXIT/RESOLUTION), event_data (JSONB) |
| `paper_trades` | Legacy — still has `realized_pnl` for resolved trades | market_id (Gamma ID, NOT condition_id), resolved_at, realized_pnl |
| `positions` | Open/closed positions | source_bot (not bot_name), status, unrealized_pnl, side (YES/NO only) |
| `traded_markets` | Market metadata cache | condition_id, resolution, bot_names (TEXT, LIKE match) |
| `prediction_log` | Per-scan predictions | No rejection_reason column |
| `system_kv` | Generic key-value | Used for canary stage |

---

## 2. CURRENT STATE (Post-S132 Fixes)

### What Was Done This Session

#### Data Cleanup (Pre-Fixes)
1. **Deleted 639 orphan RESOLUTION events** — trade_events with no matching ENTRY (Phase 4b market_id mismatch created phantom -$28,275 P&L)
2. **Deleted 7 ghost positions** — zero trade_events, created during S116 restart flood
3. **Deleted 144 SELL-side positions** — invalid (positions track YES/NO holdings, SELL is an event type not a position side)
4. **Fixed 34 position status mismatches** — 12 open→closed (purged/exited/resolved markets), 22 closed→open (live unresolved markets)
5. **P&L gap identified**: 215 resolved paper_trades have ZERO trade_events (-$7,655). 15 have ENTRY but no RESOLUTION (+$1,564). Total untracked ~$5K. Accepted as immaterial (3.5% of total).

#### Config Changes (ENV VARS on VPS — no code deploy needed)
```
MIRROR_MIN_CONFIDENCE=0.60  (was 0.45)
MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed  (was only "15-minute,speed")
```

#### Code Changes (Deploy 20260326_111112)
All 4 changes are DATA-DRIVEN — backed by 5,495 resolved paper_trades:

1. **Killed contrarian price_adj** (`mirror_bot.py:1429-1441`)
   - Was: contrarian bets got +0.075 confidence boost
   - Now: `_price_adj = 0.0` always
   - Data: contrarian = 32.9% WR, -$84K. Neutral = 46.6% WR.

2. **$50 minimum whale trade gate** (`mirror_bot.py:1219-1225`)
   - New Tier 0 filter: `whale_trade_usd < 50 → reject`
   - Data: <$50 = 39.9% WR, -$153K. $50+ = 47.1% WR, +$1,428.
   - Config: `MIRROR_MIN_WHALE_TRADE_USD=50.0`

3. **Capped rel_mult at 1.0** (`mirror_bot.py:1396`)
   - Was: `min(lr, 2.0)` — amplified sizing for "strong" whales
   - Now: `min(lr, 1.0)` — only penalizes, never amplifies
   - Data: rel_mult 1.05+ = 37.1% WR, -$113K (anti-correlated)

4. **NO-side 0.5x sizing dampener** (`mirror_bot.py:1577-1581`)
   - New: NO-side positions get half the size
   - Data: NO = -$139K (38.3% WR) vs YES = -$20K (39.9% WR), 7x worse
   - Config: `MIRROR_NO_SIDE_DAMPENER=0.5`

New config settings added to `settings.py` lines 385-388:
```python
MIRROR_MIN_WHALE_TRADE_USD: float = float(os.getenv("MIRROR_MIN_WHALE_TRADE_USD", "50.0"))
MIRROR_NO_SIDE_DAMPENER: float = float(os.getenv("MIRROR_NO_SIDE_DAMPENER", "0.5"))
```

---

## 3. P&L REALITY (Honest Numbers)

### All-Time MirrorBot (paper_trades, resolved)
- **Total resolved**: 5,495 trades
- **Realized P&L**: **-$159,442**
- **Win rate**: 39.3% (2,143W / 3,352L)

### By Category (All-Time)
| Category | Trades | WR | P&L |
|----------|--------|-----|------|
| Crypto | ~2,800 | ~37% | ~-$100K+ |
| Sports | ~900 | ~38% | ~-$30K |
| Everything else | ~1,800 | ~42% | ~-$29K |

### Weekly Trend
| Week | Trades | WR | P&L |
|------|--------|-----|------|
| Mar 9 | 708 | 45.1% | +$15,866 |
| Mar 16 | 2,506 | 36.5% | -$19,527 |
| Mar 23 | 2,281 | 39.9% | -$155,781 |

### The S120 "+$26,986" Was False
- That number came from trade_events RESOLUTION P&L which included orphan events
- paper_trades before S120: **-$3,661** (the real number)
- The orphan RESOLUTION events we deleted were inflating/deflating P&L unpredictably

### Non-Crypto Confidence Buckets (THE REAL SIGNAL)
| Bucket | Trades | WR | P&L |
|--------|--------|-----|------|
| 0.50-0.54 | 2,257 | 38.4% | **+$6,490** |
| 0.55-0.59 | 564 | 43.1% | -$6,990 |
| 0.60-0.64 | 184 | 45.7% | **+$941** |
| 0.65-0.69 | 70 | 38.6% | -$5,681 |
| **0.70+** | **269** | **56.9%** | **+$2,243** |

**0.70+ non-crypto is the only consistently profitable bucket.**

---

## 4. WHALE ANALYSIS (Critical Finding)

### The "Top 500" Question
MirrorBot copies from a **500-trader watchlist** via RTDS. But only **197 unique traders** have been copied. Of those with 5+ resolved trades:

- **67 traders** with 5+ resolved trades
- **16 profitable** (+$25,996 total)
- **51 unprofitable** (-$182,138 total)
- **76% of tracked traders lose money when we copy them**

### Entry Source
- `rtds`: 3,049 entries (whale trades via RTDS feed)
- `unknown`: 2,963 entries (likely consensus/older source)

### Whale Trade Size Distribution
| Size | Entries |
|------|---------|
| <$10 | 1,917 (63%) |
| $10-50 | 723 (24%) |
| $50-100 | 142 (5%) |
| $100-500 | 201 (7%) |
| $500+ | 65 (2%) |

**87% of copied trades are <$50 whale trades — pure noise.** The $50 gate we just deployed will cut ~87% of RTDS volume.

### Top Whale Performers (Non-Crypto, 5+ Trades)
| Trader | Trades | WR | P&L |
|--------|--------|-----|------|
| 0x2005D16a | 70 | 40.0% | +$7,507 |
| 0x3471a897 | 11 | 72.7% | +$5,424 |
| 0x7eA571C4 | 40 | 60.0% | +$3,720 |

### Worst Whale Performers
| Trader | Trades | WR | P&L |
|--------|--------|-----|------|
| 0x818F214c | 116 | 32.8% | -$26,465 |
| 0xD84c2b6d | 178 | 41.0% | -$24,883 |
| 0x732F1891 | 82 | 40.2% | -$16,922 |

**Key insight**: The top 3 worst traders account for -$68K (43% of all losses). A per-trader P&L blacklist would be high-impact.

---

## 5. CONFIDENCE FORMULA (Current State)

### Multi-Factor Confidence (`mirror_bot.py:1405-1461`)
```
confidence = max(0.35, min(0.75, _base + _price_adj + _conv_adj))
```

**Factor 1: Bayesian base** (`_base`) — per-whale per-category WR with shrinkage toward 0.50
- `_shrinkage = cat_n / (cat_n + 20)` (pseudocount=20)
- `_base = 0.50 + shrinkage * (cat_wr - 0.50)`
- Capped at 0.52 for categories with <10 trades
- Data says: `conf_base 0.52+` has 43% WR vs `0.50-0.51` has 34.4% WR — **signal exists but weak**

**Factor 2: Price adjustment** (`_price_adj`) — **ZEROED in S132**
- Was boosting contrarian bets (anti-signal)
- Now: `_price_adj = 0.0` always

**Factor 3: Conviction** (`_conv_adj`) — whale bet size vs their average
- `>2x average → +0.04`, `<0.3x average → -0.03`, else `0.0`
- Not enough data to validate yet (only 228 trades with non-zero)

### Calibration Stack (`mirror_calibration.py`)
- **FTS (Focal Temperature Scaling)**: T=2.0, gamma=5.0 — fitted but **shadow-only**
- **Le Horizon Bias**: Not fitted (horizon=False)
- **MIRROR_USE_CALIBRATION=false** — calibration does NOT affect live trades
- `conf_cal_shadow` logged to event_data for analysis

### ML Shadow Race (`mirror_ml_selector.py`)
- 3 models: QL (Q-Learning), XGB (XGBoost), Combo
- All scoring in shadow mode — decisions logged but not gating
- Data: ml_trade = 45.5% WR vs ml_skip = 37.3% WR — **ML has signal, not yet activated**
- 585 resolved ML-scored entries (approaching threshold for activation analysis)

### Other Signals in event_data
| Signal | Finding |
|--------|---------|
| `spread` | 20c+ spread = -$151K. Tight spreads barely exist in data. |
| `consensus` | Always 1 whale (no multi-whale consensus seen yet) |
| `whale_trade_usd` | $50+ = profitable. <$50 = noise. **Gate deployed.** |
| `best_ask` | Not analyzed yet |
| `depth_at_best_usd` | Not analyzed yet — could indicate liquidity quality |

---

## 6. WHAT'S BROKEN / OPEN ITEMS

### P0 (Deployed but Monitor)
- **Crypto blocklist**: Confirmed active in logs (`mirror_category_blocked category=crypto`)
- **$50 whale gate**: Confirmed via code deploy. Need to verify in logs once RTDS trades come in.
- **Confidence floor 0.60**: Set via env var.

### P1 (Next Session)
- **Per-trader blacklist**: Top 3 worst traders = -$68K. Add automatic P&L-based trader blocking.
- **ML shadow → live gate**: 585 resolved entries, ml_trade WR 45.5% vs ml_skip 37.3%. Evaluate if threshold (500+) is met for activation.
- **Spread gate**: 20c+ spread = -$151K. Consider adding `MIRROR_MAX_SPREAD` config.

### P2 (Soon)
- **Calibration activation**: FTS T=2.0 fitted. Should it go live? Need to compare `conf_cal_shadow` vs raw confidence against outcomes.
- **1,005 open positions**: Way too many. Most are pre-fix crypto positions that will resolve over time. Monitor decline rate.
- **4b-alt still has 301 unresolved markets**: Will self-heal as Polymarket resolves them. Not a code problem.

### P3 (Backlog)
- **NO side asymmetry root cause**: We dampened sizing but didn't investigate WHY NO loses 7x more. Is it the market structure? Whale behavior? Price ranges?
- **Position reconciliation**: 1,005 open positions vs actual exposure — are they mark-to-market correct?
- **Phase 4b market_id mismatch**: Gamma IDs in paper_trades vs condition_ids in trade_events. Root cause of orphan data. Long-term: normalize to one ID scheme.

### P5 (Ideas)
- **Watchlist pruning**: Only 16/67 tracked traders are profitable. Auto-prune traders with <40% WR after 20+ trades.
- **Consensus signal**: Currently always 1. Multi-whale agreement on same side/market could be high signal.
- **Time-of-day analysis**: Not done yet — crypto markets are 24/7, sports have schedules.
- **Category-specific confidence floors**: Sports may need different threshold than politics.

---

## 7. CRITICAL TRAPS (DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L in dashboards/reports
2. **paper_trades.realized_pnl IS valid for resolved trades** — use for per-trade P&L analysis (it's populated correctly by the paper engine)
3. **paper_trades.market_id ≠ trade_events.market_id** — Gamma ID vs condition_id mismatch for many older entries
4. **YES/NO only**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
5. **positions uses `source_bot`** not `bot_name`. NO `closed_at`/`updated_at` columns.
6. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
7. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
8. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
9. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
10. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
11. **trade_events immutability trigger**: Must `DISABLE TRIGGER` per-partition before DELETE, then re-enable.
12. **RESOLUTION event idempotency**: Uses atomic INSERT...SELECT with WHERE NOT EXISTS (ON CONFLICT broken on partitioned tables).
13. **Python 3.13 scoping**: `from X import Y` inside a function shadows module-level. NEVER use local imports that shadow top-level names.
14. **Deploy via `deploy/deploy.sh`**: Atomic symlink swap. Working tree ≠ VPS ≠ git HEAD.
15. **Shared env at `/opt/pa2-shared/.env`**: Overrides settings.py defaults. Check here first for live config.
16. **rel_mult NOW CAPPED AT 1.0** — S132 fix. Do not raise back to 2.0 without new data.
17. **_price_adj NOW ZEROED** — S132 fix. Do not re-enable contrarian boost without data proving it works.

---

## 8. CONFIG (Live VPS Values)

```env
# /opt/pa2-shared/.env (overrides settings.py defaults)
MIRROR_MIN_CONFIDENCE=0.60
MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed

# settings.py defaults (used when not in env)
MIRROR_MIN_WHALE_TRADE_USD=50.0
MIRROR_NO_SIDE_DAMPENER=0.5
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=1000
MIRROR_TOTAL_CAPITAL=20000
MIRROR_STOP_LOSS_PCT=0.15
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MIN_TRADE_USD=50.0
MIRROR_MAX_SLIPPAGE_PCT=0.08
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70
MIRROR_FAVORITE_DAMPENER=1.0
MIRROR_EXTREME_PRICE_DAMPENER=1.0
MIRROR_MAX_PER_MARKET_PCT=0.05
MIRROR_MAX_PER_MARKET=400
MIRROR_USE_CALIBRATION=false
```

---

## 9. RUNNING QUERIES ON VPS

### Pattern for DB Queries
```python
# Write script locally, SCP to VPS, run with venv python
# MUST read DSN from /opt/pa2-shared/.env (not /opt/polymarket-ai/current/.env)
import asyncio, os, asyncpg

async def main():
    dsn = None
    with open("/opt/pa2-shared/.env") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    conn = await asyncpg.connect(dsn)
    # ... queries ...
    await conn.close()

asyncio.run(main())
```

```bash
# SCP + SSH execution pattern
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem script.py ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "/opt/polymarket-ai-v2/venv/bin/python /tmp/script.py"
```

**GOTCHA**: Multi-line Python with single quotes breaks SSH shell quoting. Always SCP the script file, don't inline it.

---

## 10. SESSION HISTORY (Key Decisions)

| Session | Key Change | Impact |
|---------|-----------|--------|
| S110 | Multi-factor confidence (replaced flat 0.55) | Added _base, _price_adj, _conv_adj |
| S117 | Bot revived from S116 restart flood | Calibration disabled, dampeners neutralized |
| S119 | 6 root cause fixes, dampeners set to 1.0 for data collection | NO price trap fix, position stacking fix |
| S120 | Production readiness (fee, balance, fill confirmation) | Reported +$26,986 P&L (later proven false) |
| S131 | 6 WeatherBot fixes + MirrorBot session prep | Identified crypto as 88% of losses |
| **S132** | **4 data-driven fixes + crypto block + conf floor 0.60** | **Firehose turned off. Volume ~700/day → ~30-50/day expected** |

---

## 11. STRATEGIC DIRECTION

### What Works
- **Non-crypto 0.70+ confidence**: 56.9% WR, +$2,243 — real but small edge
- **$50+ whale trades**: 47.1% WR, +$1,428 — size signals quality
- **ML shadow says "trade"**: 45.5% WR vs 37.3% for "skip" — ready for activation
- **Top 16 traders**: +$25,996 combined

### What Doesn't Work
- **Crypto markets**: -$100K+. Blocked.
- **Small whale trades (<$50)**: -$153K. Gated.
- **Contrarian bets**: -$84K. Zeroed.
- **High rel_mult whales**: -$113K. Capped.
- **NO side**: -$139K. Dampened 0.5x.
- **51 of 67 traders are unprofitable**: Need pruning.

### Next Priorities (In Order)
1. **Per-trader P&L blacklist** — auto-block traders with <35% WR after 20+ resolved trades
2. **ML gate activation** — evaluate 585+ resolved ML-scored entries, activate if ml_trade WR holds
3. **Spread gate** — reject 20c+ spread entries (data says -$151K)
4. **Calibration go-live evaluation** — compare conf_cal_shadow vs raw confidence outcomes
5. **Watchlist pruning** — reduce from 500 to profitable traders only
6. **Position cleanup** — 1,005 open positions will naturally reduce as markets resolve

### The Fundamental Question
The bot copies 500 "elite" traders, but only 16/67 are profitable when we follow them. The system needs to shift from "copy all whales" to "copy only proven profitable whales" or "use whale trades as one signal among many" (which is what the ML models are trying to do).

---

## 12. TEST COMMANDS

```bash
# Run all tests
cd C:/lockes-picks/polymarket-ai-v2 && python -m pytest tests/ -x -q --timeout=30

# Check service health
ssh ubuntu@34.251.224.21 "sudo systemctl is-active polymarket-ai"

# Watch MirrorBot logs
ssh ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai -f | grep -i mirror"

# Check gate rejections
ssh ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '5 min ago' | grep -E 'mirror_category_blocked|mirror_small_whale|mirror_low_confidence|mirror_no_side'"

# Quick P&L check (last 24h)
# SCP scripts/mirror_conf_history.py to VPS and run with venv python
```

Tests: **1,717 passed** as of S132 commit `09e7079`.
