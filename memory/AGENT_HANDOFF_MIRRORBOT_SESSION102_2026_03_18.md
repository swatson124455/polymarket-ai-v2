# AGENT HANDOFF — MirrorBot Session 102 (2026-03-18)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`). Going live is flipping a boolean.

**MirrorBot** is the highest-performing bot. It copy-trades elite whale traders in real-time via RTDS WebSocket firehose. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99/S100/S101/S102)
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  -> streams ALL trades on Polymarket (global firehose, no auth)
  -> EliteWatchlist does O(1) lookup: is trader in our 500-whale watchlist?
  -> YES -> log to whale_trades table + _execute_mirror_trade() with validation
  -> NO -> discard

Scan loop (45s interval) handles ONLY:
  - Stop-loss exits (15% default)
  - Housekeeping (position reconciliation, backfill)
```

---

## 2. SESSION 102 CHANGES

### Problem Statement
24h P&L was -$698 driven by 97 resolutions at 35% WR, all in <30c bucket. Retrospective of S85-S101 revealed over-engineering: conformal prediction saga (4 sessions, disabled), stacking dampeners zeroing trades, dead code accumulation, cap whiplash without data backing.

### What We Did

**A. Price bucket analysis (data-driven)**
All-time resolution P&L by 10c entry price bucket:

| Bucket | Trades | Win% | P&L | Action |
|--------|--------|------|-----|--------|
| <10c | 86 | 12.8% | **-$2,845** | BLOCKED (hard floor 5c→10c) |
| 10-15c | 44 | 25.0% | +$3,218 | Sweet spot — full Kelly |
| 15-20c | 39 | 30.8% | +$5,288 | Sweet spot — full Kelly |
| 20-30c | 97 | 35.1% | +$5,729 | Sweet spot — full Kelly |
| 30-40c | 116 | 37.1% | +$58 | Dampened 0.50x |
| 40-50c | 248 | 48.0% | +$1,452 | Dampened 0.50x |
| 50-70c | 225 | 60.0% | +$1,115 | Full Kelly |
| >70c | 34 | 74.3% | -$40 | Dampened 0.40x |

**Sweet spot = 10-30c** (+$14,235 on 180 trades). Asymmetric payoff: lose 15c/share on loss, gain 85c/share on win. Whales pick longshots that hit more than market expects.

**B. Hard price floor raised: 0.05 → 0.10**
`MIRROR_HARD_MIN_PRICE` default changed. Blocks penny longshots (<10c) that were the single biggest P&L drain.

**C. Stacking dampeners collapsed to single dampener**
Before: 3 independent dampeners could multiply (gray zone 0.25x × dead zone 0.50x × favorites 0.40x = 0.05x on edge cases). This caused "size zero after limits" spam — trades dampened to dust.

After: One check, one multiplier, no stacking:
- 30-50c → 0.50x (breakeven zone)
- ≥70c → 0.40x (whale impact eats edge)
- Everything else → 1.0x

**D. Min trade USD: $10 → $50**
$50 floor ensures only meaningful positions. No penny-ante trades wasting volume on trash. Default in both `mirror_bot.py` and `settings.py`.

**E. Dead code deleted (3 files, ~536 lines)**
- `mirror_trade_selector.py` — d3rlpy reinforcement learning, never wired
- `mirror_chronos_filter.py` — Chronos-2 price trajectories, never wired
- `mirror_adaptive_safety.py` — disabled since S94, never re-enabled

**F. Dead code paths removed from mirror_bot.py**
- Adaptive safety init, refresh, max_positions override, daily cap multiplier — all removed
- Conformal fit call removed (was running for "monitoring" despite being disabled since S93)
- Conformal interval code stripped (was hardcoded `None`)

### Files Modified
| File | Change |
|------|--------|
| `bots/mirror_bot.py` | Hard floor 10c, single dampener, min USD $5, adaptive safety removed, conformal removed |
| `config/settings.py` | `MIRROR_MIN_TRADE_USD` default 10→50 |
| `bots/mirror_adaptive_safety.py` | **DELETED** |
| `bots/mirror_trade_selector.py` | **DELETED** |
| `bots/mirror_chronos_filter.py` | **DELETED** |

### Tests
81 passed (64 mirror + 17 book walk), 0 failed.

---

## 3. CURRENT STATE

### Config (live VPS values, update after deploy)
```
MIRROR_HARD_MIN_PRICE=0.10  (was 0.05)
MIRROR_HARD_MAX_PRICE=0.95
MIRROR_FAVORITE_DAMPENER=0.40  (≥70c)
MIRROR_DEAD_ZONE_DAMPENER=0.50  (30-50c)
MIRROR_MIN_TRADE_USD=50.0  (was 10.0)
MIRROR_MAX_CONCURRENT_POSITIONS=500
MIRROR_MAX_PER_MARKET=400 (S101 per-market entry cap=2 still active)
kelly=0.25, capital=$20K, max_bet=$300, max_daily=$20K
```

### P&L Snapshot (as of S102 handoff)
- **All-time**: +$20,312 realized (3323 entries, 683 exits, 889 resolutions)
- **24h**: -$698 (bad Mar 16 batch resolving, 7.7% WR on 13 trades)
- **Open positions**: 79, unrealized +$134, exposure $4,945
- **whale_trades**: 33,648 rows, 97 copied (S101 commit fix working)

### Validation Pipeline (S102 — simplified)
Entry path (~23 checks, down from ~30):
1. Hard price bounds [0.10, 0.95]
2. Circuit breaker
3. Position cap (500)
4. Accepting orders gate
5. Near-resolution filter (4h)
6. Per-market entry cap (2)
7. Category cap ($40K)
8. Opposing-side dedup
9. Confidence (≥0.55) + reliability (≥0.52)
10. FTS calibration (domain + horizon)
11. Kelly sizing via BotBankrollManager
12. Single price dampener (30-50c: 0.50x, ≥70c: 0.40x)
13. Per-market USD cap ($400)
14. Daily exposure cap ($20K)
15. Min trade USD ($50)

No stacking dampeners. No conformal. No adaptive safety.

---

## 4. RETROSPECTIVE — LESSONS LEARNED

### Over-Engineering Pattern (S89-S101)
Build sophistication → discover it doesn't work → disable → add simpler things → accumulate again.

**Conformal prediction (S89-S93)**: 4 sessions, 3 rewrites, then disabled. Wrong technique for binary outcomes.

**Cap whiplash**: Position cap went 200→400→1000→500. Category cap went $2.4K→$4K→$10K→$40K. None data-driven until S102.

**Fill model**: 8 multiplicative penalty layers for paper trading with acknowledged fantasy P&L. Possibly over-engineered but useful for live readiness.

### What Worked
- S94 latency reduction (2967ms→11.9ms)
- S96 architectural teardown (-218 lines)
- S102 data-driven bucket analysis

### Key Insight
The whales' edge is in **picking longshots (10-30c) that hit more often than the market expects**. The asymmetric payoff (5:1 to 10:1) means even 25-30% WR is hugely profitable. Everything above 50c is marginal. <10c is pure noise.

---

## 5. OPEN ITEMS

| Priority | Item | Notes |
|----------|------|-------|
| P2 | Monitor S102 impact | Watch 48h: did "size zero" spam decrease? Did sweet-spot entries increase? |
| P3 | Clean `mirror_calibration.py` | FTS calibrator is still active and useful. But conformal methods inside it are dead code. Could strip conformal, keep FTS. |
| P3 | Stabilize caps | Position/category/daily caps should be set from data analysis, not vibes. Current values are reasonable but arrived via whiplash. |
| P4 | Hold-time analysis | <24h positions are net losers (-$1,370). Consider minimum hold time or entry-time filtering. |
| P5 | 1,987 unresolved markets | Growing naturally. Backfill resolving. Monitor only. |

---

## 6. CRITICAL TRAPS (DO NOT BREAK)

- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- `trade_events` is P&L authority, NOT `paper_trades`.
- RTDS envelope: unwrap `data.get("payload", data)`.
- RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- `whale_trades` requires explicit `await session.commit()` (S101 fix).
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- Positions table: NO `closed_at`, NO `updated_at`, NO `bot_name` — use `source_bot` or `bot_id`.
- MirrorBot entry price: Uses CURRENT market price, NOT trader's fill price.
