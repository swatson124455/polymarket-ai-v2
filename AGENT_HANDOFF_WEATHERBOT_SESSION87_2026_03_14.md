# AGENT HANDOFF — WeatherBot Session 87 (2026-03-14)
## SCOPE: WeatherBot + Resolution Backfill Pipeline + IngestionScheduler

---

## SESSION IDENTITY
- **Bot**: WeatherBot (temperature + precipitation weather markets on Polymarket)
- **Session**: 87 (continues from Session 85/86 data overhaul)
- **Date**: 2026-03-14
- **VPS**: Ubuntu-3, 34.251.224.21, 16GB/4vCPU, eu-west-1
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f`
- **SSH**: `ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21`
- **Deploy**: `cd /c/lockes-picks/polymarket-ai-v2 && KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`

---

## CURRENT STATE (as of 2026-03-14 ~17:15 UTC)

### WeatherBot P&L (ALL TIME)
| Metric | Value |
|--------|-------|
| Total Trades | 727 |
| Resolved | 159 (81W / 78L) — **50.9% win rate** |
| Open | 568 |
| Realized P&L | **+$914.48** |
| Avg Win | +$32.57 |
| Avg Loss | -$22.09 |
| Avg Entry Price | 0.6072 |
| Avg Size | 38.05 tokens |

### P&L by Day
| Date | Trades | Resolved | W/L | P&L | Capital |
|------|--------|----------|-----|-----|---------|
| Mar 6 | 1 | 1 | 0/1 | -$0.11 | $0.10 |
| Mar 7 | 2 | 2 | 0/2 | -$0.11 | $0.11 |
| Mar 8 | 23 | 23 | 10/13 | -$28.60 | $146 |
| Mar 9 | 38 | 38 | 19/19 | +$158.00 | $2,553 |
| Mar 10 | 17 | 17 | 9/8 | +$154.69 | $1,389 |
| Mar 11 | 45 | 31 | 16/15 | +$308.83 | $1,086 |
| Mar 12 | 22 | 22 | 16/6 | +$113.49 | $2,097 |
| Mar 13 | 361 | 25 | 11/14 | +$208.29 | $7,460 |
| Mar 14 | 218 | 0 | 0/0 | $0.00 | $3,139 |

### P&L by Side
| Side | Trades | Resolved | Wins | Total P&L |
|------|--------|----------|------|-----------|
| YES | 202 | 60 | 10 | +$299.15 |
| NO | 525 | 99 | 71 | +$615.33 |

**Key finding**: NO side dominates (72% of trades, 72% of wins). YES side has 16.7% win rate vs NO side 71.7%. WeatherBot's edge is primarily in selling unlikely outcomes (buying NO).

### P&L by City
| City | Trades | Resolved | Wins | P&L | Notes |
|------|--------|----------|------|-----|-------|
| Tokyo | 25 | 5 | 4 | +$407.92 | **Best city, small sample** |
| Toronto | 40 | 9 | 5 | +$290.73 | Strong |
| Seoul | 38 | 12 | 8 | +$259.47 | Consistent |
| Wellington | 22 | 2 | 2 | +$244.20 | Perfect but tiny sample |
| Seattle | 58 | 15 | 9 | +$140.34 | Good volume + edge |
| Chicago | 52 | 11 | 4 | +$44.12 | Mixed |
| London | 28 | 10 | 4 | +$48.62 | Mixed |
| Paris | 44 | 18 | 10 | **-$383.95** | **WORST** |
| NYC | 54 | 4 | 3 | -$30.11 | Too few resolved |
| Ankara | 30 | 7 | 1 | -$27.37 | Poor |
| Other | 275 | 66 | 31 | -$79.50 | Aggregate |

**Paris is hemorrhaging money** — -$383.95 on 18 resolved trades. Potential city-level disabling or confidence adjustment warranted.

### Top 5 Wins
1. Seattle 42-43F Mar 10 (NO): +$342.62
2. Tokyo 10C Mar 13 (NO): +$341.66
3. Wellington 18C Mar 11 (YES): +$238.82
4. Seoul 7C Mar 11 (NO): +$218.88
5. Toronto 1C Mar 12 (YES): +$199.89

### Top 5 Losses
1. Seattle 46-47F Mar 12 (NO): -$413.11
2. Buenos Aires 28C Mar 12 (NO): -$326.68
3. Paris 18C Mar 9 (NO): -$224.21
4. Seoul 11C Mar 13 (NO): -$120.20
5. Paris 11C Mar 11 (YES): -$89.35

### Unresolved Trade Age Distribution
| Bucket | Count | Notes |
|--------|-------|-------|
| > 72h (Mar 11) | 14 | **SHOULD be resolved** — CLOB shows `closed: True` |
| 24-48h (Mar 13) | 197 | Mix of resolved and open on CLOB |
| < 24h (today) | 357 | Today's markets, expected open |

---

## CRITICAL BUG: IngestionScheduler DEAD for 11+ Hours

### What Happened (timeline)
```
04:45:02  Old process still running, ingestion in progress
04:45:59  Service restart (deploy)
04:46:28  NEW IngestionScheduler started (PID 2270112)
04:46:59  First run: "Full ingestion already in progress" (stale sync_log from old process)
04:47:22  Resolution backfill DID run (12 missing markets fetched)
05:03:01  IngestionScheduler stopped (another restart/deploy)
05:03:27  Started again (PID 2271933)
05:04:00  Starting run
05:14:06  ERROR: ingest_everything() timed out after 600s
05:14:07  Cleaned up stale sync_log after timeout ✓
05:14:12  IngestionScheduler stopped (ANOTHER restart)
05:14:39  Started again (PID 2275129) ← CURRENT PROCESS
05:15:11  Starting run
05:25:11  ERROR: ingest_everything() timed out AGAIN after 600s
05:25:11  Cleaned up stale sync_log after timeout ✓
05:26-05:32  Resolution backfill ran: 33 inserted, 6 updated, 4 paper_trades, 19 positions
05:29:35  Phase 2 stats: clob_closed=9, skipped_open=491, skipped_no_res=3, updated=6
06:39:40  Mini backfill: 17 markets_resolved
06:40:24  HEALTH WARNING: 12499 markets past end_date but unresolved
          THEN SILENCE FOR 11+ HOURS — NO MORE SCHEDULER LOGS
```

### Root Cause Analysis
1. **`ingest_everything()` hangs consistently** — takes >600s, times out every run
2. After timeout cleanup, resolution backfill + mini-backfill DO run
3. But after 06:39, the scheduler appears completely dead
4. Zero IngestionScheduler log entries from 06:40 UTC to 17:15 UTC (current time)
5. The asyncio event loop may be blocked or the scheduler task may have crashed silently
6. No error/traceback logged — suggests unhandled hang rather than exception

### Impact
- **No new resolutions since 06:39 UTC** — 11+ hours of closed markets not being resolved
- At least 14 old weather markets (3+ days old) confirmed `closed: True` on CLOB but still unresolved in DB
- Estimated 50-100 Mar 13 markets now closed on CLOB but not resolved in our DB
- Today's (Mar 14) markets will close tonight and also won't be caught

### IMMEDIATE FIX NEEDED (P0)
The IngestionScheduler needs investigation + fix:

**Option A (Quick fix)**: Service restart to get scheduler running again + manual backfill
```bash
# 1. Clear sync_log
sudo -u polymarket psql -d polymarket -c "UPDATE sync_log SET status = 'failed', completed_at = NOW() WHERE status = 'running';"
# 2. Run manual backfill
cd /opt/polymarket-ai-v2 && sudo -u polymarket timeout 300 /opt/pa2-shared/venv/bin/python3 scripts/backfill_market_resolution.py
# 3. Restart service
sudo systemctl restart polymarket-ai
```

**Option B (Root cause)**: Investigate WHY `ingest_everything()` hangs and fix the underlying issue. May need to:
- Add `asyncio.wait_for()` with proper cancellation inside `ingest_everything()`
- Check if it's a DB lock contention issue
- Check if Gamma API is hanging on a specific market batch
- Add per-batch timeouts inside the ingestion loop

**Option C (Skip full ingestion)**: Disable `RESOLUTION_BACKFILL_AFTER_DAILY` and rely solely on the mini-backfill (which runs independently every 30min). The mini-backfill only needs resolution data, not full market ingestion.

---

## FILES INVOLVED

### Core WeatherBot
- **`bots/weather_bot.py`** — Main bot. Scan loop, edge detection, trade signals
  - `_backfill_weather_outcomes()` (line 184-199): Runs every 10 scans (~50 min), calls `db.backfill_prediction_log_resolution()` — only backfills prediction_log, NOT paper_trades

### Resolution Pipeline (CRITICAL — affects ALL bots)
- **`base_engine/data/resolution_backfill.py`** — 7-phase resolution pipeline
  - `_fetch_market_by_condition_id()` (L16-27): CLOB API fallback for 0x condition_ids
  - `_clob_to_market_format()` (L30-118): Converts CLOB response. **S85 fix**: `"closed": closed` in return dict
  - `run_resolution_backfill()` (L138-515): Main function, 7 phases:
    - Phase 1 (L162-223): Fetch missing markets from Gamma → CLOB fallback
    - Phase 2 (L225-356): **THE CRITICAL LOOP** — check closed status, infer resolution, save
      - L283-296: S85 fix — condition_id markets skip Gamma, go straight to CLOB
      - L329-334: Infer resolution from outcome/resolutionPrice
      - L338-343: Save to DB. L342: `except Exception: pass` on mark_market_resolved
      - L347-349: `except Exception` logs warning + traceback per market
      - L352-354: Phase 2 stats: `clob_closed=X skipped_open=Y skipped_no_res=Z updated=W`
    - Phase 3 (L362-368): prediction_log resolution
    - Phase 4 (L373-383): Pseudo-labels
    - Phase 4b (L395-435): RESOLUTION trade_events (S86 fix: deterministic idempotency)
    - Phase 5 (L440-446): Positions unrealized_pnl
    - Phase 6 (L449-492): PerformanceTracker scoring
    - Phase 7 (L496-508): Online learning trigger

### IngestionScheduler (BROKEN — P0)
- **`base_engine/data/ingestion_scheduler.py`**
  - `_loop()` (L106-141): Main event loop
    - L113-125: **S86 fix** — clears ALL orphaned sync_log on startup (`older_than_hours=0.0`)
    - L126-141: Loop: `_run_ingestion()` → sleep 300s → repeat
  - `_run_ingestion()` (L143-408): One ingestion cycle
    - L151-172: Determine run_full/run_weekly → acquire advisory lock
    - L187-205: Resolution backfill (after daily full ingestion)
    - L217-258: **Mini backfill (every 30min)** — runs `run_resolution_backfill()` + downstream
    - L305-408: `_do_ingestion()` — the part that hangs
      - L333-341: `asyncio.wait_for(ingest_everything(), timeout=600s)`
      - L343-359: Timeout handler — logs error, clears sync_log

### Base Engine
- **`base_engine/base_engine.py`**
  - L1168-1190: IngestionScheduler creation (requires `self.data_ingestion is not None`)
  - L1372-1377: IngestionScheduler start with error logging

### WeatherBot Config
```
WeatherBot:  capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000, MAX_POSITIONS=500
SIMULATION_MODE=true (paper trading)
```

---

## BUGS FOUND AND FIXED THIS SESSION

### Bug 1: `_clob_to_market_format()` missing `"closed"` key (FIXED, DEPLOYED)
- **Root cause**: VPS had old version of `resolution_backfill.py` without `"closed": closed,` in the return dict at line 110
- **Impact**: Phase 2 loop checks `m.get("closed")` → always `None` → hits `if not closed: continue` → skipped ALL CLOB-resolved markets → zero new resolutions for 2 days
- **Fix**: Deploy.sh pushed corrected code. Confirmed `"closed": closed,` present on VPS
- **Commit**: Part of Session 85 data overhaul (already in git)

### Bug 2: sync_log stuck "running" after process restart (FIXED Session 86, DEPLOYED)
- **Root cause**: Process restarts leave sync_log with `status='running'` entries. Next scheduler run sees "already in progress" and skips
- **Fix**: `_loop()` now clears ALL orphaned sync_log on startup (L113-125, `older_than_hours=0.0`)
- **Commit**: `4491c89`
- **Status**: Fix IS deployed and working — it clears sync_log on startup. But...

### Bug 3: `ingest_everything()` consistently hangs >600s (ACTIVE, UNRESOLVED — P0)
- **Symptom**: Every call to `ingest_everything()` exceeds 600s timeout. The 600s timeout handler fires, cleans up sync_log, then scheduler continues to resolution backfill and mini-backfill
- **Impact**: Scheduler spends 600s hanging every cycle instead of ingesting new market data. After the 05:32 resolution backfill + 06:39 mini-backfill, scheduler appears completely dead (0 log entries for 11+ hours)
- **Status**: **UNRESOLVED — this is the #1 priority fix needed**

### Bug 4: RESOLUTION event dedup (FIXED Session 86, DEPLOYED)
- **Root cause**: Phase 4b used `event_time=now()` for RESOLUTION events, bypassing `ON CONFLICT (idempotency_key, event_time)` on re-runs. 3238 duplicate RESOLUTION events created
- **Fix**: Aggregate P&L per (market_id, bot_name, side), use deterministic `correlation_id=resolution:{market_id}` + `event_time=resolved_at`
- **Commit**: `4c56349`

---

## OPEN ISSUES (PRIORITY ORDER)

### P0: IngestionScheduler Dead — No New Resolutions for 11+ Hours
- **What**: Scheduler hasn't produced a log entry since 06:39 UTC. `ingest_everything()` hangs consistently. After 2 timeout+recovery cycles, the scheduler appears to have crashed silently
- **Why it matters**: ~200+ weather markets closed since 06:39 that should be resolved. Win/loss/P&L stats are stale
- **Fix options**: See "IMMEDIATE FIX NEEDED" section above
- **Files**: `base_engine/data/ingestion_scheduler.py` (L305-408), `base_engine/data/data_ingestion.py` (ingest_everything)

### P1: 568 Unresolved WeatherBot Trades
- **14 trades > 72h old** — CLOB confirmed `closed: True`, should resolve immediately once backfill runs
- **197 trades from yesterday (Mar 13)** — many should be closed by now (temperature markets resolve within 24h)
- **357 trades from today** — genuinely open, will close tonight
- **Fix**: Once IngestionScheduler is restored, mini-backfill will catch these every 30min

### P2: Paris City Performance is Terrible
- **-$383.95 P&L** on Paris weather markets (worst city by far)
- Could investigate: model accuracy on Paris forecasts, edge calculation, or add city-level confidence floor
- Not urgent — observational only

### P3: YES Side Has Very Low Win Rate (16.7%)
- WeatherBot's YES trades win only 10/60 (16.7%) vs NO trades at 71/99 (71.7%)
- The bot's edge is almost entirely on the NO side
- Could be expected (weather probabilities tend to be distributed, so NO is often correct for specific temperature thresholds), or could indicate model calibration issue on YES side
- Worth investigating but not blocking

### P4: `except Exception: pass` in Phase 2 (L342, L347)
- Silent error swallowing in the resolution loop. When Phase 2 bulk fails but single-market tests work, this pattern hides the real error
- Should add `logger.warning()` at minimum
- **File**: `base_engine/data/resolution_backfill.py` L342, L347

---

## HOW RESOLUTION BACKFILL WORKS (CRITICAL KNOWLEDGE)

### The Pipeline
```
Polymarket CLOB API → resolution_backfill.py (7 phases) → DB updates
```

### Phase 2 Flow (the most important part)
```
1. Query traded_markets for unresolved markets (LIMIT 500, ordered by first_trade_at ASC)
2. For each market:
   a. If 0x condition_id (66-char hex) → skip Gamma, go straight to CLOB API
   b. If numeric ID → try Gamma API first
   c. Check m.get("closed") — if not closed, skip (increment skipped_open)
   d. Infer resolution from outcome/resolutionPrice fields
   e. If resolution not YES/NO → skip (increment skipped_no_res)
   f. Call db.save_market_resolution() → updates markets table + traded_markets
   g. Call db.mark_market_resolved() → marks traded_markets.resolved = TRUE
3. Log stats: clob_closed=X skipped_open=Y skipped_no_res=Z updated=W
```

### Weather Market Types
- **Temperature**: `0x` + 64-hex-chars condition_id (66 chars total). Gamma returns 422. MUST use CLOB fallback
- **Precipitation**: 7-digit numeric ID. Gamma works. End dates are March 31 (monthly)

### Two Backfill Triggers
1. **Full backfill**: After daily `ingest_everything()` (currently broken)
2. **Mini backfill**: Every 30min independent of ingestion. Runs `run_resolution_backfill()` + `backfill_paper_trades_resolution()`. This is the lifeline

### Key S85/S86 Fixes in the Pipeline
1. `"closed": closed` in `_clob_to_market_format()` return dict — without this, CLOB markets never resolve
2. Condition_id markets skip Gamma (always 422) → go straight to CLOB
3. Non-YES/NO outcomes (esports): token index → YES/NO mapping
4. RESOLUTION events use deterministic `correlation_id` + `event_time` for idempotency
5. Orphaned sync_log cleaned on scheduler startup

---

## DATA ARCHITECTURE (P&L AUTHORITY)

### trade_events is the SOLE P&L authority
```sql
-- Realized P&L query
SELECT bot_name, event_type, SUM(realized_pnl)
FROM trade_events WHERE bot_name = 'WeatherBot'
GROUP BY bot_name, event_type;
```

### P&L Formula (UNIFORM — NEVER INVERT FOR NO)
```
cost = entry_price * size         -- ALL sides
uPnL = (current_price - entry_price) * size  -- ALL sides
realized = (exit_price - entry_price) * size  -- ALL sides
```

### Canonical P&L script
```bash
python scripts/bot_pnl.py WeatherBot 720  # last 30 days
```

### Data Tables
| Table | Role |
|-------|------|
| `trade_events` | **P&L AUTHORITY** — ENTRY/EXIT/RESOLUTION events, immutable, partitioned |
| `positions` | Position tracking, 10s price updates, unrealized_pnl mark-to-market |
| `paper_trades` | Legacy compat — still written by 28 callers, NEVER read for P&L |
| `traded_markets` | Resolution backfill tracking (resolved flag, first_trade_at) |
| `markets` | Market metadata (resolution, closed, prices) |
| `daily_counters` | Exposure tracking (write-through for daily exposure) |

---

## WEATHERBOT OPERATIONAL STATE

### Scan Behavior
- Scans every 2.5-5 minutes
- Covers ~950 weather markets across 15+ cities
- Logs raw edges per city per scan (`weatherbot_raw_edges`)
- Temperature + precipitation + snow + wind submodules

### Current Constraints
- 500 max positions (currently ~400-500 open)
- $2,000/day max exposure
- $500 max bet per trade
- Kelly fraction 0.25

### State Persistence
| State | Mechanism |
|-------|-----------|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore |
| `_group/_city_exposure` | `_restore_exposure_from_db()` on startup |
| Exit cooldowns | Redis TTL `_save/_restore_exits_from_redis()` |
| Open positions | `order_gateway.seed_positions_from_db()` |

---

## CRITICAL TRAPS — DO NOT BREAK

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **NEVER invert P&L formula for NO** — prices are token-specific, uniform formula
4. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree != VPS != git HEAD
5. **`paper_trades` has NO `metadata` JSONB column** — never assume it exists
6. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
7. **Python 3.13 scoping**: `from X import Y` inside a function shadows module-level import for ENTIRE function. Any use before that line → `UnboundLocalError`
8. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
9. **asyncpg timestamp**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT
10. **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must `DISABLE TRIGGER` then re-enable for data cleanup
11. **RESOLUTION event idempotency**: Must pass deterministic `correlation_id` + `event_time` (not `now()`) to avoid duplicates on re-runs
12. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
13. **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass
14. **14 bots in BOT_REGISTRY** — shared module changes require all verified
15. **CLOB returns `closed: True` AFTER market resolves** — NOT at market close time. There's a delay

---

## SESSION 85/86 CONTEXT (WHAT WAS DONE BEFORE THIS SESSION)

### Session 85 — Resolution Backfill Fix (DEPLOYED)
- Fixed 3 root causes: Python 3.13 scoping, non-YES/NO outcomes, missing LIMIT
- Result: 544 markets resolved (from 58)
- 5 dead tables purged (migration 052)

### Session 86 — Ingestion Sync Fix + P&L Dedup (DEPLOYED)
- Orphaned sync_log cleanup on scheduler startup (`older_than_hours=0.0`)
- RESOLUTION event dedup: 3238 duplicates deleted

### Session 87 (THIS SESSION) — P&L Diagnostic + Scheduler Investigation
- Full P&L breakdown by day/side/city
- Discovered IngestionScheduler dead for 11+ hours
- Identified `ingest_everything()` consistent 600s timeout as root cause
- Resolution backfill code + `_clob_to_market_format()` confirmed working (manual tests pass)
- Bulk resolution runs when triggered manually — the issue is purely the scheduler dying

---

## WHAT THE NEXT AGENT SHOULD DO

### Immediate (P0)
1. **Fix IngestionScheduler hang**: Investigate why `ingest_everything()` takes >600s
   - Read `base_engine/data/data_ingestion.py` for the actual ingestion logic
   - Check if there's a specific market batch or API call that hangs
   - Consider adding per-batch timeouts, or separating ingestion from backfill scheduling
2. **Run manual resolution backfill** to catch 11+ hours of newly-closed markets:
   ```bash
   sudo -u polymarket psql -d polymarket -c "UPDATE sync_log SET status = 'failed' WHERE status = 'running';"
   cd /opt/polymarket-ai-v2 && sudo -u polymarket timeout 300 /opt/pa2-shared/venv/bin/python3 scripts/backfill_market_resolution.py
   ```
3. **Restart service** after any fixes: `sudo systemctl restart polymarket-ai`

### Short-term (P1-P2)
4. **Add error logging** to `except Exception: pass` blocks in resolution_backfill.py (L342, L347)
5. **Investigate Paris city performance** — consider city-level confidence threshold
6. **Investigate YES side low win rate** — model calibration check

### Verification After Fix
```bash
# Check scheduler is running
sudo journalctl -u polymarket-ai -n 200 --no-pager | grep -i "IngestionScheduler"

# Check resolved count is increasing
sudo -u polymarket psql -d polymarket -c "SELECT COUNT(*) FROM paper_trades WHERE bot_name='WeatherBot' AND resolution IS NOT NULL;"

# Run canonical P&L
cd /opt/polymarket-ai-v2 && sudo -u polymarket /opt/pa2-shared/venv/bin/python3 scripts/bot_pnl.py WeatherBot 720
```

---

## RECENT COMMITS (relevant)
```
4c56349 fix(resolution): prevent duplicate RESOLUTION events in trade_events
4491c89 fix(ingestion): clear orphaned sync_log entries on scheduler startup
d5a1c9f fix(resolution): remove second shadowed datetime import in Phase 7
1280b33 fix(resolution): remove shadowed datetime import causing Python 3.13 scoping error
d34abb0 fix(resolution+equity): add debug stats to backfill + fix equity total_capital upsert
```

---

## DEPLOY CHECKLIST
```bash
# Pre-deploy
cd /c/lockes-picks/polymarket-ai-v2
pytest  # Must pass 1200+ tests

# Deploy
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Verify (5 min post-deploy)
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21
sudo journalctl -u polymarket-ai -n 100 --no-pager | grep -i "WeatherBot.*scan"
sudo journalctl -u polymarket-ai -n 100 --no-pager | grep -i "IngestionScheduler"
sudo -u polymarket psql -d polymarket -c "SELECT status, COUNT(*) FROM sync_log GROUP BY status;"

# Rollback if needed
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
