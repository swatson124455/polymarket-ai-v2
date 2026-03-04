# BASE ENGINE VIABILITY TEST — REWRITTEN FOR ACTUAL ARCHITECTURE

## Context

The original test was written for a generic 10-bot system with Supabase, Binance/Coinbase WebSockets, a `bot_registry` table, `cross_bot_positions` table, per-bot processes, and on-chain nonce management. **None of that matches this system.** This rewrite maps every test to what actually exists in the codebase, flags what's missing, and gives you executable SSH commands — not theory.

### What This System Actually Is

- **Single Python process** (`main.py`) running **15 bots** in one async event loop
- Managed by **systemd** (`Restart=always`, `RestartSec=10`, `MemoryMax=6G`)
- Internal **watchdog** (30s interval, exponential backoff 30→600s, max 10 restart attempts)
- **Local PostgreSQL** on VPS (pool: 15+5 overflow, semaphore-guarded, 30s timeout)
- **Paper trading mode** (`SIMULATION_MODE=true`) — orders go to `PaperTradingEngine`, NOT real CLOB
- **Polymarket WebSocket only** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`)
- **No external alerting configured** (AlertingSystem exists but no Slack/Discord webhook set)
- **No heartbeat DB table** — watchdog checks `bot.running` boolean only

### SSH Prefix (all commands)

```bash
SSH="ssh -i 'C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem' -o StrictHostKeyChecking=no ubuntu@34.251.224.21"
```

### Scoring

Each test: **PASS / FAIL / N/A (capability doesn't exist yet)**

Verdict thresholds at the bottom.

---

## LAYER 1: PROCESS HEALTH (6 tests)

### Test 1.1: systemd Service Active

```bash
$SSH "sudo systemctl is-active polymarket-ai && systemctl show polymarket-ai --property=ActiveState,SubState,NRestarts --no-pager"
```

| | |
|---|---|
| **PASS** | `active`, `SubState=running`, `NRestarts` low (<10 lifetime) |
| **FAIL** | `inactive`, `failed`, or `NRestarts` climbing fast (5+ in last 5 min = `StartLimitBurst` hit) |

### Test 1.2: Exactly One Process (no duplicates)

All 15 bots run in ONE process. Duplicates would fight over DB advisory locks.

```bash
$SSH "pgrep -f 'python.*main.py' | wc -l"
```

| | |
|---|---|
| **PASS** | Output is `1` |
| **FAIL** | `0` (dead) or `>1` (duplicate — advisory lock conflicts, double trades) |

### Test 1.3: Memory Below MemoryMax

systemd kills the process at 6GB (`MemoryMax=6G`). VPS has 16GB.

```bash
$SSH "systemctl show polymarket-ai --property=MemoryCurrent --value"
```

| | |
|---|---|
| **PASS** | Below 4GB (comfortable headroom) |
| **WARN** | 4-5GB (approaching limit) |
| **FAIL** | Above 5GB (OOM kill imminent) |

### Test 1.4: All Expected Bots Logging

6 bots should be actively logging: EnsembleBot, ArbitrageBot, MirrorBot, CrossPlatformArbBot, WeatherBot, LogicalArbBot.

```bash
$SSH "for bot in EnsembleBot ArbitrageBot MirrorBot CrossPlatformArbBot WeatherBot LogicalArbBot; do
  count=\$(journalctl -u polymarket-ai --since '30 minutes ago' --no-pager 2>/dev/null | grep -c \"\$bot\")
  echo \"\$bot: \$count lines\"
done"
```

| | |
|---|---|
| **PASS** | All 6 bots have >0 log lines in the last 30 min |
| **FAIL** | Any bot has 0 lines — either dead (watchdog should catch) or silently stuck |

### Test 1.5: Silent Bot Detection (THE KNOWN GAP)

A bot can be `running=True` but scanning zero opportunities with zero log output. Watchdog only checks the boolean, not scan activity. Session 48 added diagnostic INFO logging to 4 silent bots (P5), but there's no heartbeat table.

```bash
$SSH "for bot in EnsembleBot ArbitrageBot MirrorBot CrossPlatformArbBot WeatherBot LogicalArbBot; do
  scans=\$(journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep \"\$bot\" | grep -ci 'scan\|opportunities\|markets evaluated')
  echo \"\$bot: \$scans scan-related lines in last hour\"
done"
```

| | |
|---|---|
| **PASS** | EnsembleBot has 5+ scan lines (scans every ~5 min). Others have 1+ each. |
| **FAIL** | Any bot has 0 scan-related lines for >1 hour. This is the exact gap from Session 47→48. |
| **KNOWN GAP** | No DB-backed heartbeat. Fix: add `bot_heartbeats` table written at end of each scan cycle. |

### Test 1.6: No Unhandled Exceptions in stderr

```bash
$SSH "journalctl -u polymarket-ai --since '1 hour ago' --no-pager -p err 2>/dev/null | grep -c 'Traceback\|Exception\|Error' "
```

| | |
|---|---|
| **PASS** | 0 (no unhandled exceptions) |
| **WARN** | 1-5 (transient errors, check if retried successfully) |
| **FAIL** | >10 in 1 hour (systematic failure, something is broken) |

---

## LAYER 2: DATABASE HEALTH (7 tests)

### Test 2.1: PostgreSQL Running

```bash
$SSH "sudo systemctl is-active postgresql"
```

| | |
|---|---|
| **PASS** | `active` |
| **FAIL** | Anything else. All bots will crash (no DB = no trades, no positions, no predictions). |

### Test 2.2: DB Connectivity (SELECT 1)

Mirrors `_preflight_check()` in `main.py:192` and `HealthRunner._check_db_connectivity()`.

```bash
$SSH "sudo -u postgres psql -d polymarket -c 'SELECT 1;' -t -A"
```

| | |
|---|---|
| **PASS** | Returns `1` |
| **FAIL** | Connection refused, auth error, or timeout |

### Test 2.3: Connection Pool Utilization

Pool is 15+5 overflow = 20 max connections. At 20, new `get_session()` calls hit the 30s semaphore timeout.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS active,
         (SELECT setting::int FROM pg_settings WHERE name='max_connections') AS max
  FROM pg_stat_activity WHERE datname='polymarket';\" -t -A"
```

| | |
|---|---|
| **PASS** | active < 15 (below pool_size) |
| **WARN** | 15-18 (overflow in use) |
| **FAIL** | ≥19 (pool near exhaustion — trades will timeout at 30s) |

### Test 2.4: Idle-in-Transaction Leaks

Leaked advisory locks from `database_lock.py` crashes. Consumes pool slots permanently.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS leaked,
         MAX(EXTRACT(EPOCH FROM (NOW()-query_start)))::int AS max_age_sec
  FROM pg_stat_activity
  WHERE datname='polymarket'
    AND state='idle in transaction'
    AND query_start < NOW() - INTERVAL '60 seconds';\" -t -A"
```

| | |
|---|---|
| **PASS** | `leaked = 0` |
| **FAIL** | Any leaked sessions (consuming pool slots, will not release until terminated) |

### Test 2.5: Slow Queries (>30s)

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT pid, EXTRACT(EPOCH FROM (NOW()-query_start))::int AS sec, LEFT(query,80) AS query
  FROM pg_stat_activity
  WHERE datname='polymarket' AND state='active'
    AND query_start < NOW() - INTERVAL '30 seconds';\" -t -A"
```

| | |
|---|---|
| **PASS** | No rows (no queries running >30s) |
| **FAIL** | Any query >30s (table lock, missing index, or connection starvation cascade) |

### Test 2.6: Core Tables Exist With Data

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT 'markets' AS t, COUNT(*) FROM markets
  UNION ALL SELECT 'positions', COUNT(*) FROM positions
  UNION ALL SELECT 'paper_trades', COUNT(*) FROM paper_trades
  UNION ALL SELECT 'prediction_log', COUNT(*) FROM prediction_log
  UNION ALL SELECT 'system_config', COUNT(*) FROM system_config
  UNION ALL SELECT 'market_prices', COUNT(*) FROM market_prices
  ORDER BY t;\" -t -A"
```

| | |
|---|---|
| **PASS** | `markets` > 100 rows, all tables exist |
| **FAIL** | Any table missing (migration not run) or `markets` < 100 (ingestion broken) |

### Test 2.7: Disk Space

`decision_events` was 1003MB at Session 49 VACUUM FULL.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"SELECT pg_size_pretty(pg_database_size('polymarket'));\" -t -A && df -h /var/lib/postgresql | tail -1"
```

| | |
|---|---|
| **PASS** | DB < 50GB, disk usage < 80% |
| **FAIL** | DB > 100GB or disk > 90% (PostgreSQL crashes when disk full) |

---

## LAYER 3: DATA PIPELINE (5 tests)

### Test 3.1: Market Data Freshness

Markets should be refreshed by ingestion every ~30 min.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS total,
         COUNT(*) FILTER (WHERE active) AS active,
         ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(updated_at)))/3600,1) AS hours_stale
  FROM markets;\" -t -A"
```

| | |
|---|---|
| **PASS** | `active` > 100, `hours_stale` < 2.0 |
| **FAIL** | `hours_stale` > 2.0 (ingestion stopped) or `active` < 100 |

### Test 3.2: Price Data Freshness

Prices come from ingestion cycles and WebSocket. Position manager uses these for stop-loss/take-profit (10s update loop).

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(timestamp)))/60,1) AS min_stale
  FROM market_prices;\" -t -A"
```

| | |
|---|---|
| **PASS** | < 30 minutes stale |
| **FAIL** | > 30 minutes (both ingestion AND WebSocket are down — position P&L calculations are stale) |

### Test 3.3: Ingestion Success Rate (24h)

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FILTER (WHERE status='success') AS ok,
         COUNT(*) AS total,
         ROUND(100.0 * COUNT(*) FILTER (WHERE status='success') / GREATEST(COUNT(*),1), 1) AS pct
  FROM sync_log
  WHERE started_at > NOW() - INTERVAL '24 hours';\" -t -A"
```

| | |
|---|---|
| **PASS** | Success rate > 80% |
| **WARN** | 50-80% (intermittent failures) |
| **FAIL** | < 50% (CRITICAL per HealthRunner) or 0 runs |

### Test 3.4: Prediction Pipeline Active

At least one prediction per hour means the ML pipeline is alive.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS preds_1h,
         COUNT(DISTINCT bot_name) AS bots
  FROM prediction_log
  WHERE prediction_time > NOW() - INTERVAL '1 hour';\" -t -A"
```

| | |
|---|---|
| **PASS** | `preds_1h` > 0 |
| **FAIL** | 0 predictions in last hour (feature cache not warmed, model not loaded, or all bots idle) |

### Test 3.5: Resolution Backfill Running

Labels predictions as correct/incorrect. Required for model training and Brier score.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(completed_at)))/3600,1) AS hours_ago
  FROM sync_log
  WHERE component='resolution_backfill' AND status='success';\" -t -A"
```

| | |
|---|---|
| **PASS** | < 2.0 hours ago |
| **FAIL** | > 2.0 hours or NULL (backfill not running — model cannot learn from outcomes) |

---

## LAYER 4: RISK & EXECUTION (8 tests)

### Test 4.1: Kill Switch Status

Stored in `system_config` table, checked 3× in execution pipeline (before wallet, before API, in OrderGateway). 30s cache.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COALESCE(value,'not_set') FROM system_config WHERE key='kill_switch';\" -t -A"
```

| | |
|---|---|
| **PASS** | `false` or `not_set` (trading allowed) |
| **FAIL** | `true` (ALL trading halted — was this intentional?) |

### Test 4.2: Paper Trading Mode Confirmed

System must stay in `SIMULATION_MODE=true` until graduation (need 52% win rate + Brier ≤ 0.22, currently 49.6% / 0.2512).

```bash
$SSH "grep '^SIMULATION_MODE' /opt/polymarket-ai-v2/.env"
```

| | |
|---|---|
| **PASS** | `SIMULATION_MODE=true` |
| **CRITICAL FAIL** | `SIMULATION_MODE=false` while model isn't graduated (real money at risk with coin-flip accuracy) |

### Test 4.3: Paper Trades Flowing

Proves the full pipeline works: scan → predict → risk check → coordinator → paper execute.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS last_24h,
         COUNT(DISTINCT bot_name) AS bots,
         ROUND(SUM(realized_pnl)::numeric, 2) AS pnl_24h
  FROM paper_trades
  WHERE created_at > NOW() - INTERVAL '24 hours';\" -t -A"
```

| | |
|---|---|
| **PASS** | `last_24h` > 0 (pipeline is end-to-end functional) |
| **WARN** | 0 trades but all bots running (risk limits too tight, no opportunities, or edge thresholds filtering everything) |
| **FAIL** | 0 trades AND bots are not logging scan activity (broken pipeline) |

### Test 4.4: Position Price Updates Running (Session 44 Fix)

`_update_current_prices()` runs every 10s. Root cause of the 0% sell win rate was stale `current_price = entry_price`.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) AS open,
         COUNT(*) FILTER (WHERE current_price IS NOT NULL AND current_price != entry_price) AS price_updated,
         COUNT(*) FILTER (WHERE current_price IS NULL OR current_price = entry_price) AS stale
  FROM positions WHERE status='open';\" -t -A"
```

| | |
|---|---|
| **PASS** | `stale` = 0 (or ≤1 for brand-new positions) |
| **FAIL** | All positions have `current_price = entry_price` (Session 44 regression — 0% sell win rate returns) |
| **N/A** | 0 open positions (nothing to check) |

### Test 4.5: No Stale Reservations

`TradeCoordinator` uses INSERT ON CONFLICT with `status='reserving'`. Reaper runs every 60s, clears after 8 min.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM positions
  WHERE status='reserving' AND opened_at < NOW() - INTERVAL '8 minutes';\" -t -A"
```

| | |
|---|---|
| **PASS** | 0 stale reservations |
| **FAIL** | >0 (reaper dead — stale reservations block other bots from trading those markets) |

### Test 4.6: Trade Coordinator Conflict Prevention

Two bots cannot hold conflicting sides (YES vs NO) on the same market. Verify no contradictions exist.

```bash
$SSH "sudo -u postgres psql -d polymarket -c \"
  SELECT market_id, COUNT(DISTINCT side) AS sides, COUNT(DISTINCT bot_id) AS bots
  FROM positions WHERE status IN ('open','reserving')
  GROUP BY market_id HAVING COUNT(DISTINCT side) > 1;\" -t -A"
```

| | |
|---|---|
| **PASS** | No rows (no market has both YES and NO positions) |
| **FAIL** | Any row returned (bots fighting each other — coordinator broken) |

### Test 4.7: Risk Price Floor/Ceiling Config

Session 49 fixes: `RISK_MIN_PRICE=0.15` (blocks penny tokens), `RISK_MAX_PRICE=0.90`.

```bash
$SSH "grep -E '^RISK_MIN_PRICE|^RISK_MAX_PRICE|^MODEL_REVERSAL_THRESHOLD|^ENSEMBLE_MAX_RELATIVE_SPREAD' /opt/polymarket-ai-v2/.env"
```

| | |
|---|---|
| **PASS** | `RISK_MIN_PRICE=0.15`, `RISK_MAX_PRICE=0.90`, `MODEL_REVERSAL_THRESHOLD=0.30`, `ENSEMBLE_MAX_RELATIVE_SPREAD=0.20` |
| **FAIL** | Any value missing or reverted to old settings (0.05/0.95/0.45) — penny token churn returns |

### Test 4.8: Drawdown Controller Status

```bash
$SSH "journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep -i 'drawdown\|position_multiplier' | tail -3"
```

| | |
|---|---|
| **PASS** | Status `normal` or `caution` (multiplier > 0.25) |
| **FAIL** | Status `halted` (multiplier = 0, all trading stopped) |
| **N/A** | No log entries (drawdown never triggered — this is fine if P&L is positive) |

---

## LAYER 5: MONITORING & ALERTING (4 tests)

### Test 5.1: HealthRunner Running

13 diagnostic checks run periodically. Logs "Health check complete" on finish.

```bash
$SSH "journalctl -u polymarket-ai --since '2 hours ago' --no-pager 2>/dev/null | grep 'Health check' | tail -3"
```

| | |
|---|---|
| **PASS** | At least 1 health check in last 2 hours |
| **FAIL** | 0 health checks (HealthRunner not being invoked) |

### Test 5.2: Redis Connectivity

Optional but used for price caching, feature vectors, and rate limiting.

```bash
$SSH "redis-cli -a 78psiRhepTgrmWSoy3cgNEIr ping 2>&1"
```

| | |
|---|---|
| **PASS** | `PONG` |
| **WARN** | Connection refused (system runs without Redis but slower, no price caching) |

### Test 5.3: External Alerting Configured

**This is the #1 production blocker.** AlertingSystem exists but no webhook is set.

```bash
$SSH "grep -E '^SLACK_WEBHOOK|^DISCORD_WEBHOOK|^ALERT_WEBHOOK|^SMTP_HOST' /opt/polymarket-ai-v2/.env 2>/dev/null | wc -l"
```

| | |
|---|---|
| **PASS** | ≥1 webhook configured |
| **FAIL (EXPECTED)** | 0 configured. **Kill switch engagement, all-bots-dead, and drawdown halt will produce log entries only. Nobody will see them at 3am.** |
| **PRODUCTION BLOCKER** | Must fix before real money. |

### Test 5.4: Platt Scaling Active

Calibration for model predictions. Enabled in Session 49 (threshold: 200+ resolved predictions).

```bash
$SSH "grep '^PLATT_SCALING_ENABLED' /opt/polymarket-ai-v2/.env && sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) FROM prediction_log WHERE resolution IS NOT NULL;\" -t -A"
```

| | |
|---|---|
| **PASS** | `PLATT_SCALING_ENABLED=true` and resolved count > 200 |
| **WARN** | Enabled but < 200 resolved (Platt scaling won't activate until threshold met) |

---

## LAYER 6: RECOVERY (4 tests)

### Test 6.1: systemd Restart Budget

`StartLimitBurst=5` in 300s. If exhausted, systemd gives up and the process stays dead.

```bash
$SSH "systemctl show polymarket-ai --property=NRestarts,Result --no-pager"
```

| | |
|---|---|
| **PASS** | `Result=success` and NRestarts stable |
| **FAIL** | `Result=exit-code` or NRestarts climbing (crash loop) |

### Test 6.2: Watchdog Restart History (24h)

Internal watchdog restarts individual dead bots (up to 10 attempts with exponential backoff).

```bash
$SSH "journalctl -u polymarket-ai --since '24 hours ago' --no-pager 2>/dev/null | grep -ci 'restart\|watchdog.*dead\|watchdog.*restarting'"
```

| | |
|---|---|
| **PASS** | 0 (all bots stable for 24h) |
| **WARN** | 1-5 (some restarts, check if they succeeded) |
| **FAIL** | >10 or "exhausted restart attempts" message (bot permanently dead) |

### Test 6.3: Position Seeding on Last Startup

`PaperTradingEngine.seed_positions_from_db()` restores positions from DB into memory on restart. Without this, SELL orders fail with "Insufficient position."

```bash
$SSH "journalctl -u polymarket-ai --no-pager 2>/dev/null | grep -i 'seed.*position\|positions seeded\|paper.*restore' | tail -3"
```

| | |
|---|---|
| **PASS** | Log shows positions were seeded (or "0 positions" if none open) |
| **FAIL** | No seeding log (positions lost on restart) |

### Test 6.4: WebSocket Connection Status

Polymarket WebSocket with exponential backoff reconnection. Circuit breaker after 10 failures → 5-min intervals.

```bash
$SSH "journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep -i 'websocket' | tail -5"
```

| | |
|---|---|
| **PASS** | "connected" messages, no reconnect failures |
| **WARN** | Some reconnects that succeeded |
| **FAIL** | "circuit breaker" or >10 consecutive failures (stale price data for 5+ min intervals) |

---

## COMPOSITE HEALTH CHECK (single script)

Run everything at once:

```bash
$SSH "bash -s" << 'EOF'
echo "=== POLYMARKET AI HEALTH CHECK ==="
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

echo "--- LAYER 1: PROCESS ---"
echo "1.1 systemd: $(systemctl is-active polymarket-ai)"
echo "1.2 processes: $(pgrep -f 'python.*main.py' | wc -l)"
echo "1.3 memory: $(systemctl show polymarket-ai --property=MemoryCurrent --value 2>/dev/null || echo N/A)"
echo "1.4 bot_log_lines_30m:"
for b in EnsembleBot ArbitrageBot MirrorBot CrossPlatformArbBot WeatherBot LogicalArbBot; do
  echo "    $b: $(journalctl -u polymarket-ai --since '30 min ago' --no-pager 2>/dev/null | grep -c "$b")"
done
echo "1.6 errors_1h: $(journalctl -u polymarket-ai --since '1 hour ago' -p err --no-pager 2>/dev/null | grep -c 'Traceback\|Exception')"
echo ""

echo "--- LAYER 2: DATABASE ---"
echo "2.1 postgresql: $(systemctl is-active postgresql)"
echo "2.2 connectivity: $(sudo -u postgres psql -d polymarket -c 'SELECT 1' -t -A 2>&1 | head -1)"
echo "2.3 active_conns: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket'" -t -A 2>&1)"
echo "2.4 idle_in_tx_leaked: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket' AND state='idle in transaction' AND query_start < NOW()-INTERVAL '60s'" -t -A 2>&1)"
echo "2.5 slow_queries_30s: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket' AND state='active' AND query_start < NOW()-INTERVAL '30s'" -t -A 2>&1)"
echo "2.7 db_size: $(sudo -u postgres psql -d polymarket -c "SELECT pg_size_pretty(pg_database_size('polymarket'))" -t -A 2>&1)"
echo ""

echo "--- LAYER 3: DATA PIPELINE ---"
echo "3.1 markets: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*)||' total, '||COUNT(*) FILTER (WHERE active)||' active, '||ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(updated_at)))/3600,1)||'h stale' FROM markets" -t -A 2>&1)"
echo "3.2 price_staleness_min: $(sudo -u postgres psql -d polymarket -c "SELECT ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(timestamp)))/60,1) FROM market_prices" -t -A 2>&1)"
echo "3.3 ingestion_24h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FILTER (WHERE status='success')||'/'||COUNT(*) FROM sync_log WHERE started_at > NOW()-INTERVAL '24h'" -t -A 2>&1)"
echo "3.4 predictions_1h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM prediction_log WHERE prediction_time > NOW()-INTERVAL '1 hour'" -t -A 2>&1)"
echo ""

echo "--- LAYER 4: RISK & EXECUTION ---"
echo "4.1 kill_switch: $(sudo -u postgres psql -d polymarket -c "SELECT COALESCE(value,'not_set') FROM system_config WHERE key='kill_switch'" -t -A 2>&1)"
echo "4.2 simulation_mode: $(grep '^SIMULATION_MODE' /opt/polymarket-ai-v2/.env 2>/dev/null || echo 'NOT SET')"
echo "4.3 paper_trades_24h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM paper_trades WHERE created_at > NOW()-INTERVAL '24h'" -t -A 2>&1)"
echo "4.4 open_positions: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE status='open'" -t -A 2>&1)"
echo "4.5 stale_reservations: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE status='reserving' AND opened_at < NOW()-INTERVAL '8 min'" -t -A 2>&1)"
echo "4.6 conflicting_sides: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM (SELECT market_id FROM positions WHERE status IN ('open','reserving') GROUP BY market_id HAVING COUNT(DISTINCT side)>1) x" -t -A 2>&1)"
echo ""

echo "--- LAYER 5: MONITORING ---"
echo "5.1 health_checks_2h: $(journalctl -u polymarket-ai --since '2 hours ago' --no-pager 2>/dev/null | grep -c 'Health check')"
echo "5.2 redis: $(redis-cli -a 78psiRhepTgrmWSoy3cgNEIr ping 2>&1)"
echo "5.3 alert_webhooks: $(grep -cE '^SLACK_WEBHOOK|^DISCORD_WEBHOOK|^ALERT_WEBHOOK|^SMTP_HOST' /opt/polymarket-ai-v2/.env 2>/dev/null) configured"
echo ""

echo "--- LAYER 6: RECOVERY ---"
echo "6.1 systemd_restarts: $(systemctl show polymarket-ai --property=NRestarts --value 2>/dev/null)"
echo "6.2 watchdog_restarts_24h: $(journalctl -u polymarket-ai --since '24h ago' --no-pager 2>/dev/null | grep -ci 'watchdog.*restart')"
echo "6.4 ws_status: $(journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep -i 'websocket' | tail -1)"
echo ""

echo "=== END ==="
EOF
```

---

## VERDICT CRITERIA

### LAYER SCORES

```
LAYER 1 (Process Health):      ___ / 6
LAYER 2 (Database):            ___ / 7
LAYER 3 (Data Pipeline):       ___ / 5
LAYER 4 (Risk & Execution):    ___ / 8
LAYER 5 (Monitoring):          ___ / 4
LAYER 6 (Recovery):            ___ / 4
TOTAL:                         ___ / 34
```

### BASE READY (green light for production prep)

- ALL Layer 4 tests pass (risk & execution — non-negotiable)
- ALL Layer 2 tests pass (database — non-negotiable)
- At least 4/6 in Layer 1, 4/5 in Layer 3, 3/4 in Layers 5 and 6
- 28/34 minimum overall
- Test 5.3 (external alerting) is an EXPECTED FAIL for now but must be fixed before real money

### BASE NOT READY (fix before adding strategies)

- ANY Layer 4 failure (risk broken = money at risk)
- ANY Layer 2 failure (database broken = all bots broken)
- Any layer with less than half passing
- Less than 24/34 overall

### CRITICAL FAILURES (stop everything)

- **Test 4.2 FAIL**: `SIMULATION_MODE=false` with un-graduated model (real money, coin-flip accuracy)
- **Test 4.6 FAIL**: Conflicting positions exist (bots fighting each other)
- **Test 4.4 FAIL**: All position prices stale (Session 44 regression — 0% sell win rate)
- **Test 2.4 FAIL** with count > 5: Pool leak cascade (will exhaust connections, all bots freeze)
- **Test 4.1 FAIL**: Kill switch engaged (all trading halted — was this intentional?)

---

## WHAT'S NOT TESTED (and why)

| Original Test | Why Removed |
|---|---|
| Binance/Coinbase WebSocket | System only connects to Polymarket WS |
| `bot_registry` table heartbeats | No such table. BOT_REGISTRY is a Python dict |
| `cross_bot_positions` table | No such table. Uses `positions` table with trade coordinator |
| Nonce sequencing | Paper trading mode. No on-chain transactions |
| Multi-process bot isolation | All 15 bots in one process |
| Supabase connection limits | Supabase fully removed (Session 44). Local PostgreSQL |
| Order placement latency (CLOB) | Paper trading. Orders never hit real CLOB |
| Cancel/replace cycles | Paper trading. No real order book interaction |
| "Kill 3 of 10 bots" | Single process. Can't kill individual bots without killing all |
| Sustained 1-hour uptime test | Requires real-time monitoring, not a point-in-time check |

---

## PRODUCTION BLOCKERS (fix before real money)

### P0 — Must Fix

1. **No external alerting** (Test 5.3) — Configure Discord/Slack webhook in `.env` + wire to AlertingSystem
2. **No silent bot detection** (Test 1.5) — Add `bot_heartbeats` table with last_scan_at, markets_scanned
3. **Model accuracy too low** — 49.6% win rate (need 52%), Brier 0.2512 (need ≤0.22)

### P1 — Should Fix

4. **Recovery module not wired** — `recovery.py` exists but never called. DB connection loss during runtime has no automated recovery.
5. **DB pool config mismatch** — `settings.py` defaults (15+5) vs `database.py` defaults (50+30). Verify `.env` has explicit `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`.
6. **WebSocket circuit breaker has no alert** — 10 consecutive failures log CRITICAL but don't fire AlertingSystem.

### P2 — Nice to Have

7. **No persistent queue during DB outage** — Trades placed during outage are lost
8. **Position price staleness detection** — No alert for "all prices stale" specifically
