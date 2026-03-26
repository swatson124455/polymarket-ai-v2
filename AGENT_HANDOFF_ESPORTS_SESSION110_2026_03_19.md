# AGENT HANDOFF — EsportsBot Session 110 (2026-03-19)

## Session Type: EsportsBot-scoped (scan speed optimization)

## What Was Done

### 1. OPT-4: Retrain/Accuracy Parallelization — DEPLOYED

**Problem**: Retrain checks, accuracy monitoring, training data COUNT queries, and collection tasks ran sequentially in Phase B before the main `asyncio.gather()`. This added ~50-200ms of serial I/O per scan.

**Fix**: Wrapped retrain/accuracy block (lines 927-1046) into `async def _step_retrain_and_accuracy()` and added it to the Phase B gather alongside `_step_patch_drift()`, `_refresh_live_matches()`, and `_step_get_markets()`. Changed from 3-branch to 4-branch parallel gather.

**Files modified**: `bots/esports_bot.py` only

---

### 2. Scan Timing Instrumentation — DEPLOYED

**Problem**: No visibility into where scan time was spent. Previous sessions cited "7-10s per scan" but had no breakdown.

**Fix**: Added 4 timing checkpoints (`_t0`, `_t1`, `_t2`, `_t3`) dividing the scan into:
- **Phase A** (pre-scan housekeeping): kill switch, exposure restore, stop-loss checks
- **Phase B** (parallel gather): retrain, patch drift, live matches, market fetch
- **Phase C** (market analysis): parallel `_analyze_one()` for each market

New `timing_ms` dict in `esportsbot_scan_summary` log:
```
timing_ms={'phase_a': 18, 'phase_b': 191, 'phase_c': 10, 'total': 219}
```

**Files modified**: `bots/esports_bot.py` only

---

### 3. Scan Interval 10s → 2s — DEPLOYED

**Problem**: EsportsBot scanned every ~10s during live matches. Actual scan computation takes 150-300ms. The remaining ~9.7s was `asyncio.sleep()` from `SCAN_INTERVAL_ESPORTS_LIVE=10` default in `config/settings.py`.

**Root cause**: The 10s default was set when the scan was assumed to take 7-10s. Timing instrumentation revealed the real scan duration is 150-300ms steady state (occasional spikes to 700-950ms during retrain/PandaScore refresh cycles).

**Fix**: Changed `config/settings.py` default from `"10"` to `"2"`.

**Impact on trade responsiveness**:
- **WS reactive path** (primary): Unchanged at ~60-250ms from price move to order. This handles time-critical trading.
- **Scan path** (secondary): Wall-clock cadence improved from ~10.2s to ~2.2s. Scan's role is cache-warming (`_prediction_cache`, `_market_token_map`), stop-loss monitoring, and new market discovery — not primary trade execution.
- **Net effect**: Stop-loss reactions 5x faster. New market discovery 5x faster. WS token subscriptions refresh 5x faster after new markets appear.

**PandaScore rate impact**: Zero additional calls. `_refresh_live_matches()` has an independent 15s time guard — only fires once per 15s regardless of scan interval. Current usage ~400/hr vs 1000/hr budget.

**Files modified**: `config/settings.py` (1 line)

---

### 4. OPT-5: Series Scan Parallelization — REJECTED (safety)

**Investigated**: Parallelizing `_series_scan()` with `_analyze_one()` calls.

**Found 3 race conditions** (read-check-then-act on shared mutable state in asyncio):
1. `_game_exposure` read at line 1833 (outside `_trade_lock`) vs write at line 3186 (inside `_trade_lock`) — exposure cap could be exceeded
2. `_market_entry_times` / `_recently_exited` contention
3. `order_gateway.has_open_position()` stale reads

**Decision**: Abandoned. Risk of double-entries exceeding exposure caps is unacceptable for a financial system. The 2s scan interval makes this optimization unnecessary.

---

## Performance Data (VPS, post-deploy)

### Scan Timing (10 consecutive scans, 16 markets, 4 live matches)

| Scan | Phase A | Phase B | Phase C | Total | Notes |
|------|---------|---------|---------|-------|-------|
| 1 | 52ms | 170ms | 12ms | 235ms | First scan (cold caches) |
| 2 | 8ms | 186ms | 8ms | 202ms | |
| 3 | 18ms | 191ms | 10ms | 219ms | WS resumed |
| 4 | 472ms | 426ms | 57ms | 955ms | Retrain + PandaScore refresh |
| 5 | 69ms | 217ms | 8ms | 294ms | |
| 6 | 424ms | 292ms | 18ms | 735ms | PandaScore refresh |
| 7 | 11ms | 137ms | 8ms | 156ms | |
| 8 | 17ms | 200ms | 12ms | 228ms | |
| 9 | 21ms | 201ms | 13ms | 235ms | |
| 10 | 23ms | 207ms | 13ms | 243ms | |

**Steady state**: 150-300ms (P50 ~230ms)
**With retrain/refresh**: 700-955ms (occurs every ~15s)
**PandaScore refresh spike**: ~2s (occurs every ~15s, Phase B dominated)
**Cold start (first scan)**: ~3.5s (exposure restore from DB + initial PandaScore fetch)
**Wall-clock cadence**: ~2.0-2.2s steady, ~3-4s during PandaScore refresh cycles

### Spike Analysis (scans >1s, observed over 5 min)

| Time | Phase A | Phase B | Total | Cause |
|------|---------|---------|-------|-------|
| 21:41:35 | 874ms | 2583ms | 3472ms | Cold start — exposure restore from DB + initial PandaScore fetch |
| 21:42:07 | 9ms | 1981ms | 2000ms | PandaScore 15s refresh fired (I/O-bound, non-blocking) |
| 21:43:52 | 838ms | 314ms | 1169ms | Phase A spike — stop-loss / exposure DB queries |

These spikes are I/O-bound `await`s — they do NOT block the event loop. Other bots continue unaffected.

### Observed Throughput (5 min sample, 18 markets, 4 live matches)

- **92 scans in 300s = 18.4 scans/min** (vs ~6/min before)
- Shortfall from theoretical 30/min is due to PandaScore refresh spikes (~2s every ~15s) and occasional Phase A DB spikes
- **3x improvement** in scan throughput, **5x improvement** in best-case cadence

### Before vs After

| Metric | Before (S109) | After (S110) |
|--------|---------------|--------------|
| Scan interval (live) | 10s | 2s |
| Scan duration (P50) | ~200-800ms* | ~230ms |
| Wall-clock cadence (steady) | ~10.2s | ~2.2s |
| Wall-clock cadence (w/ refresh) | ~10.8s | ~3-4s |
| Stop-loss reaction time | ≤10s | ≤2.2s (≤4s worst case) |
| WS token refresh | ≤10s | ≤2.2s |
| Scans/min (observed) | ~6 | **~18** |
| Scans/min (theoretical) | ~6 | ~27 |

*S109 had no timing instrumentation — range estimated from scan_ms in base_bot logs.

---

## Files Modified

| File | Lines | Change |
|------|-------|--------|
| `bots/esports_bot.py` | +134/-118 | OPT-4 (retrain parallelization), timing instrumentation |
| `config/settings.py` | 1 line | `SCAN_INTERVAL_ESPORTS_LIVE` default 10→2 |

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| P2 | RC4: Entry price inflation — positions table stores requested price not actual fill price | Deferred | Separate session — touches shared position_manager |
| P2 | Kelly degradation suspended (CS2 now fitted, but needs ALL 8 games) | Blocked on Dota2/CoD/R6/RL | None — wait |
| P3 | LoL Brier=0.2842 (near 0.30 halt) | Stable, monitoring | Check next session |
| P3 | EsportsSeriesBot silent | No series markets on Polymarket | Expected |
| P3 | WS reconnect stability — drops every ~40s-5min | Working (auto-reconnects + re-subscribes) | Monitor |
| P3 | `no_prediction: 12` per scan — team name matching failures (CS2/Valorant) | Ongoing | Improve team name parser |
| P4 | Dota2 Brier=0.3002 (over threshold, 77.5% WR) | Suspension active | Self-governs when fitted |
| P5 | taker_side dead code / PAPER_BOOK_WALK_ENABLED | No data source | Deferred |

| P4 | Phase B PandaScore refresh (~2s every 15s) | Known, acceptable | Already parallelized in gather. Would require caching or background task to improve — diminishing returns since WS handles trades. |

### Items RESOLVED This Session

| Item | Resolution |
|------|-----------|
| P2: Scan loop speed optimization | OPT-4 parallelized retrain/accuracy into gather. Timing instrumentation added. Real bottleneck was 10s sleep, not computation. |
| Scan interval too slow (10s) | Reduced to 2s. Stop-loss and market discovery now 5x faster. Zero PandaScore rate impact. |
| P0: Exit-failure cooldown gap | Two gaps in stop-loss exit path where `_recently_exited` was never set: (1) SELL failure orphan-close `continue`d past cooldown; (2) `except Exception` caught errors before cooldown. Both now set cooldown. |
| **P0: `_series_scan()` bypassed ALL anti-churn gates** | **ROOT CAUSE of post-S109 churn. `_series_scan()` called `_execute_esports_trade()` directly with ZERO checks — no `_recently_exited`, no `_market_entry_times`, no `has_open_position`. This was the unguarded backdoor that allowed 02:36 re-entry 108s after stop-loss (which the scan path correctly blocked). Fixed: added `_churn_blocked()` helper with both gates before every `_execute_esports_trade()` call in series path, plus `_market_entry_times` recording on success.** |

---

## VPS Config (updated)

```
# Existing
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_EXIT_COOLDOWN_SECONDS=900
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2
ESPORTS_ENTRY_WINDOW_HOURS=12.0

# CHANGED (S110)
SCAN_INTERVAL_ESPORTS_LIVE=2  # was 10
```

---

## Rollback

```bash
# Full rollback (both files)
sudo cp /opt/polymarket-ai-v2/bots/esports_bot.py.bak /opt/polymarket-ai-v2/bots/esports_bot.py
sudo cp /opt/polymarket-ai-v2/config/settings.py.bak /opt/polymarket-ai-v2/config/settings.py
sudo systemctl restart polymarket-ai

# Scan interval only (keep OPT-4 + timing)
export SCAN_INTERVAL_ESPORTS_LIVE=10
sudo systemctl restart polymarket-ai
```

---

## Verification

```bash
# Scan timing (timing_ms in every scan summary)
sudo journalctl -u polymarket-ai --since "2 min ago" --no-pager | grep esportsbot_scan_summary | tail -5

# Scan cadence (timestamps should be ~2s apart during live matches)
sudo journalctl -u polymarket-ai --since "1 min ago" --no-pager | grep "Scan cycle starting.*EsportsBot" | tail -10

# WS status
sudo journalctl -u polymarket-ai --since "5 min ago" --no-pager | grep "esportsbot_ws_subscribed\|ws_trading"

# PandaScore rate usage
sudo journalctl -u polymarket-ai --since "1 hour ago" --no-pager | grep "pandascore_rate" | tail -5
```

---

## P&L Review (as of 2026-03-19 21:45 UTC)

### All-Time EsportsBot: -$1,399.83 realized

| Period | Entries | Exits | Resolutions | Realized P&L |
|--------|---------|-------|-------------|-------------|
| All Time | 163 | 83 | 117 | **-$1,399.83** |
| Mar 6-18 | 125 | 52 | 106 | +$135.50 |
| **Mar 19 pre-S109** | **36** | **30** | **11** | **-$1,535.13** |
| Mar 19 post-S109 | 2 | 1 | 0 | -$0.20 |

### Daily Trend

| Day | Entries | Exits | Res | Day P&L | Cumulative |
|-----|---------|-------|-----|---------|-----------|
| Mar 13 | 11 | 14 | 0 | +$57.62 | +$57.62 |
| Mar 14 | 10 | 3 | 65 | -$79.89 | -$22.27 |
| Mar 15 | 15 | 11 | 5 | -$7.23 | -$29.50 |
| Mar 16 | 17 | 16 | 7 | +$59.13 | +$29.63 |
| Mar 17 | 9 | 3 | 1 | +$15.14 | +$44.78 |
| Mar 18 | 6 | 5 | 28 | +$90.73 | +$135.50 |
| **Mar 19** | **38** | **31** | **11** | **-$1,535.33** | **-$1,399.83** |

### March 19 Forensics

Two markets caused 95% of the Mar 19 loss, **both pre-S109 deploy** (before 13:00 UTC):

1. **0x2ef64c43** (-$1,358.77): 5 buy-exit-rebuy cycles + $1,039 resolution loss on YES side. Bought at 0.42, market crashed to 0.03, kept re-entering. Classic churn + wrong-side resolution.
2. **0x284aaa20** (-$480.56): **14 entries and 14 exits in 40 minutes** (09:13-09:52 UTC). Textbook churn — the exact pattern the S109 anti-churn fix targets.

**Post-S109 deploy**: 0 churn events. Anti-churn gate (2 entries per 12h per market) confirmed working. Only 2 entries in 9+ hours of operation.

### Current State
- **Open positions**: 0
- **WS trading**: Active
- **Scan cadence**: 2s (verified)
- **Errors**: 0

---

## BetaCalibrator Status — 4/8 FITTED (unchanged from S109)

| Game | N | Status |
|------|---|--------|
| Valorant | 1,927 | **FITTED** |
| LoL | 365-367 | **FITTED** |
| CS2 | 229 | **FITTED** |
| SC2 | 52 | **FITTED** |
| Dota2 | ~40 | Not logging (time window issue, self-healing) |
| CoD/R6/RL | 0 | No data |
