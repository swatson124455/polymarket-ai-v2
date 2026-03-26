# WeatherBot Session 100 Handoff — Known Issues Resolved + Alpha Decay + Canary Persistence

**Date**: 2026-03-17
**Branch**: master
**Commits**: `36bfa1b`, `ad2c1b8`, `5d035a7`, `be54680`
**Prior Session**: S99 (cache fixes + 7 optimizations)
**Deploy**: `20260317_144951`

---

## CHANGES MADE

### Commit 1: Deploy SSH Timeouts (`36bfa1b`)
- **Files**: `deploy/deploy.sh`, `deploy/rollback.sh`
- **Bug**: SSH commands had no timeout — hangs became orphaned processes causing restart loops (S99 known issue #1)
- **Fix**: Added `SSH_OPTS` with `ConnectTimeout=10`, `ServerAliveInterval=5`, `ServerAliveCountMax=3` to all 7 ssh/scp invocations across both scripts
- **Blast radius**: Zero (deploy-time only)

### Commit 2: Canary Stage Persistence (`ad2c1b8`)
- **Files**: `schema/migrations/054_system_kv.sql` (NEW), `base_engine/learning/scheduler.py`
- **Bug**: `settings.CANARY_STAGE` was in-memory only, reset to env var default (0) on every restart (S99 known issue #2)
- **Fix**: New `system_kv` table (key TEXT PK, value TEXT, updated_at TIMESTAMPTZ). On stage transition: upsert to DB. On first `_canary_auto_transition()` call: restore from DB. Both wrapped in try/except — falls back to env var on error.
- **Blast radius**: Shared module (scheduler.py) but additive only. No signature changes. All bots unaffected.

### Commit 3: Alpha Decay + Backoff Persistence (`5d035a7`)
- **Files**: `bots/weather_bot.py`, `base_engine/execution/order_gateway.py`

**Alpha decay fix**:
- **Bug**: `order_gateway.py:706` passed `latency_ms=None` to paper_trading. Alpha decay (`paper_trading.py:502`) short-circuited on None. No bot ever passed latency.
- **Fix**: WeatherBot stores `_scan_start_mono = time.monotonic()` at top of `scan_and_trade()`, adds `"scan_start_mono"` to `event_data`. `order_gateway.py` extracts it and computes real `latency_ms`. Other bots unaffected (no `scan_start_mono` = `latency_ms` stays None).
- **Verified**: `paper_alpha_decay` events now firing at decay_factor=0.95-0.98, adding 2-6 bps slippage per trade.

**Backoff persistence**:
- **Bug**: `_consecutive_no_edge` counter reset to 0 on restart, canceling accumulated backoff.
- **Fix**: `_save_backoff_to_redis()` / `_restore_backoff_from_redis()` — Redis key `weatherbot:consecutive_no_edge` with 1h TTL. Same pattern as exit cooldowns.
- **Verified**: `weatherbot_backoff_restored consecutive_no_edge=0` on startup.

### Commit 4: Dead Test Cleanup (`be54680`)
- **File**: `tests/test_web3_compatibility_fixes.py`
- **Bug**: `TestEventLoopHandling` class (2 tests) imported `ui.dashboard` which was deleted in a prior session. Always failed with `ModuleNotFoundError`.
- **Fix**: Removed the dead test class. 13/13 tests now pass in that file.

---

## P&L ANALYSIS (2026-03-17)

### All-Time Summary
```
Realized P&L:   +$2,881.13
Unrealized:     $0.00
Open positions: 0
Entries:        2,002
Closed:         932 (578W / 354L = 62%)
```

### By Side (KEY FINDING — confirmed)
| Side | P&L | Closed | Win Rate |
|------|-----|--------|----------|
| **NO** | **+$1,896** | 647 | **72%** |
| YES | +$985 | 285 | 39% |

NO-side trades are the primary profit driver. Favourite-longshot bias exploitation confirmed working.

### By City
Most P&L is "unknown" (pre-metadata era). Named cities with data:
- Tel Aviv: +$116 (3 closed, 100% WR)
- Singapore: -$7.57 (1 closed, 0% WR)
- Paris: +$0.50 (3 closed, 100% WR) — historical -$384 may have been pre-EMOS era
- City/lead-time metadata too sparse for actionable conclusions — will mature as recent entries resolve.

### Unresolved Markets
- **479 of 1,034** WeatherBot markets unresolved (down from ~600 at S97)
- Resolving naturally via backfill (30min mini + daily full)

---

## POST-DEPLOY METRICS (18:54 UTC)

| Metric | Value |
|--------|-------|
| api_calls | 42-97 |
| groups_with_edge | 1-8 |
| trades/scan | 0-1 |
| weather_markets | 800 |
| alpha_decay firing | YES (2-6 bps) |
| backoff_restored | YES |
| canary_stage | 0 (paper) |
| regime_boost | 1.0-1.2 |

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `deploy/deploy.sh` | SSH_OPTS on all 5 ssh + 1 scp invocations |
| `deploy/rollback.sh` | SSH_OPTS on all 3 ssh invocations |
| `schema/migrations/054_system_kv.sql` | NEW — generic key-value persistence table |
| `base_engine/learning/scheduler.py` | Canary stage DB restore on first call + persist on transition |
| `base_engine/execution/order_gateway.py` | Extract scan_start_mono from event_data, compute real latency_ms |
| `bots/weather_bot.py` | scan_start_mono in event_data, backoff Redis save/restore |
| `tests/test_web3_compatibility_fixes.py` | Removed dead TestEventLoopHandling class |

---

## KNOWN ISSUES (UPDATED)

### Resolved This Session
- ~~Restart loop from orphaned SSH tasks (P3)~~ — **FIXED**: SSH timeouts prevent hangs
- ~~Canary controller resets to stage 0 on restart (P4)~~ — **FIXED**: DB persistence via system_kv
- ~~Alpha decay not firing (P3)~~ — **FIXED**: scan_start_mono passthrough
- ~~Adaptive backoff resets on restart~~ — **FIXED**: Redis persistence
- ~~2 dead tests always failing~~ — **FIXED**: Removed dead ui.dashboard imports

### Still Open
| Priority | Item | Notes |
|----------|------|-------|
| P2 | ~479 markets unresolved | Down from ~600, resolving naturally |
| P3 | City/lead-time P&L data sparse | 905/932 closed are "unknown" (pre-metadata). Will mature. |
| P3 | NO vs YES asymmetry (72% vs 39%) | Confirmed but no config change warranted yet — combined 62% WR is profitable |
| P3 | Paris investigation | Only 3 closed trades — insufficient data |
| P5 | 432 temporal ordering violations | Already filtered by temporal guard, harmless, static |
| P5 | Kalshi cross-platform arbitrage | Deferred — 8-16h effort, separate session |

### Monitoring (next 24h)
- **Alpha decay impact**: Watch paper P&L — expect slight reduction from 2-6 bps additional slippage per trade. This makes P&L more realistic, not worse.
- **Adaptive backoff overnight**: Counter now persists. Should see scan intervals grow to 450-600s after midnight UTC when no edge.
- **Canary stage persistence**: Will persist on next transition. Currently stage 0.

---

## CONFIG (UNCHANGED from S99)

```
WeatherBot:  capital=$20000, kelly=0.25, max_bet=$300, max_daily=$10000
             MAX_POSITIONS=500, MIN_EDGE=0.08 (US), 0.12 (intl w/o local model)
             FILL_FAIL_COOLDOWN_SCANS=2, FILL_FAIL_COOLDOWN_SECS=900
             MIN_FILL_PROB_ESTIMATE=0.25, PSW_SCAN_DIVISOR=2
             ADAPTIVE_BACKOFF_THRESHOLD=6, MAX_SCAN_INTERVAL=600
Paper:       REALISTIC_FILLS=true, KYLE_LAMBDA=true, CROSS_SCAN=true
             ALPHA_DECAY_HALF_LIFE_S=300, RESOLUTION_PROXIMITY=true
```

---

## TESTS

1,593 passed, 0 failed, 8 skipped (full suite including previously-broken web3 tests)

---

## ROLLBACK

```bash
# Revert all S100:
git revert be54680 5d035a7 ad2c1b8 36bfa1b
# Then redeploy

# Disable alpha decay only (no revert needed):
# Remove scan_start_mono from event_data in weather_bot.py

# Disable canary persistence only:
# DELETE FROM system_kv WHERE key = 'canary_stage';
# Code falls back to env var gracefully
```

---

## VERIFICATION COMMANDS

```bash
# Alpha decay firing:
journalctl -u polymarket-ai --since '10 min ago' | grep paper_alpha_decay

# Canary persistence:
sudo -u postgres psql polymarket -c "SELECT * FROM system_kv WHERE key = 'canary_stage'"

# Backoff persistence:
redis-cli GET weatherbot:consecutive_no_edge

# WeatherBot health:
journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_scan_done

# Trade execution:
journalctl -u polymarket-ai --since '30 min ago' | grep weatherbot_trade_filled

# All bot health:
journalctl -u polymarket-ai --since '5 min ago' | grep -E 'scan_done|scan_ms'
```

---

## WHAT THE NEXT SESSION SHOULD DO

1. **Re-run city/lead-time P&L in 3-5 days** — More recent entries (with metadata) will have resolved. Look for city-specific and lead-time-specific patterns.

2. **NO vs YES deep dive** — If YES-side continues at 39% WR after more data, consider raising YES min_edge to 0.10 (Tier 1 config change).

3. **Monitor alpha decay impact** — After 24-48h, compare pre/post P&L per trade to quantify the slippage realism improvement.

4. **P5: Kalshi cross-platform arbitrage** — New module, new API integration. 8-16h effort. Deferred.

**Or**: Follow user instructions. Scope lock applies.
