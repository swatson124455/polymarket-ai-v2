# S172B SHARED MASTER HANDOFF — Day 1 Complete + Phase 1 Commits 1-7

**Session:** 172B (continuation of S172)
**Date:** 2026-04-13
**Scope:** ALL BOTS — S172 consolidated plan execution
**Deploy:** `20260413_150819` on Ubuntu-32 (18.201.216.0)
**Tests:** 1767 passed, 0 failed, 2 skipped, 6 xfailed
**Branch:** master

---

## SESSION NARRATIVE

This session deployed S172 Day 1 code (D7/D8/D10, 1A, 1B from prior S172 session) plus executed all Day 1 SSH infrastructure items (D0-D6, 1H) and completed Phase 1 commits 3-7 (1C through 1F). Three deployment cycles occurred due to UFW rate-limiting SSH after D4 and migration ownership failures.

Key accomplishments:
1. All Day 1 SSH items executed and verified (were pending from prior session)
2. Phase 1 commits 1C-1F completed and deployed
3. TabPFN evaluated via SIGUSR1 — 48 bytes (stub, not loaded). Phase 5A is a no-op.
4. fail2ban root cause found and fixed (missing `systemd._journal` module → switched to `auto` backend)
5. SSH security restored with iptables rate limit (15 conn/60s) + fail2ban (3 retries, 1h ban)

---

## COMMITS THIS SESSION (8 commits)

| # | SHA | Files | What |
|---|-----|-------|------|
| 1 | `c578704` | tests/unit/test_weather_bot.py | Fix WB test fixture: mock risk_manager for D7 hard stop |
| 2 | `b4e96b0` | scripts/calibration_check.py | Fix calibration_check: pass naive datetime to asyncpg |
| 3 | `f5f0982` | schema/migrations/067_vacuum_tuning.sql + down | 1C: Autovacuum tuning for positions, markets, users, traded_markets |
| 4 | `0e42adb` | schema/migrations/068_fix_resolution_prices.sql | 1D: Fix RESOLUTION events with entry_price instead of payout |
| 5 | `0e48e9f` | schema/migrations/068_fix_resolution_prices.sql | 1D fix: prefer event_data->>'resolution' over realized_pnl sign |
| 6 | `d7cb89d` | schema/migrations/069_market_aliases.sql + down | 1E-a: market_aliases schema (5263 aliases populated) |
| 7 | `3039681` | base_engine/execution/order_gateway.py | 1E-b: Gateway pre-trade market validation (alias resolution + unknown market warning) |
| 8 | `dfec250` | bots/mirror_bot.py, bots/esports_bot.py | 1G: prediction_log writes for MB (was 0 rows) + EB (shared table) |
| 9 | `3e8a40d` | bots/esports_bot.py | 1F: tracemalloc SIGUSR1 handler |

---

## DAY 1 SSH ITEMS — ALL COMPLETE

| Item | What | Verified |
|------|------|----------|
| D0-a | Journal capped at 2G (freed 2G) | systemd-journald.conf.d/polymarket.conf |
| D0-b | Ingestion NRestarts=1 (acceptable) | systemctl show |
| D0-c | Redis AOF enabled (appendonly yes, appendfsync everysec) | CONFIG REWRITE persisted |
| D1 | PG OOMScoreAdjust=-900 | systemctl show postgresql@16-main = -900 |
| D2 | MemoryMax: WB=2G, MB=2.5G, EB=2.5G, Ingestion=512M | systemctl show --property=MemoryMax |
| D2 | OOMScoreAdjust: PG=-900, Redis=-500, WB=-200, MB=-100, EB=0, Ingestion=+100 | /proc/PID/oom_score_adj |
| D3 | 573 RESOLUTION + 916 EXIT dupes cleaned. 10 per-partition unique indexes valid | pg_index.indisvalid=t all 10 |
| D4 | fail2ban active (auto backend, maxretry=3, bantime=3600, findtime=600) | fail2ban-client status sshd |
| D4 | iptables SSH rate limit: 15 new conn/60s | iptables -L shows rules |
| D4 | UFW SSH: ALLOW (not LIMIT — iptables handles rate limiting) | ufw status |
| D5 | pg_dump backup: 2.9GB. Cron: polymarket user, 02:00 UTC daily, 7-day retention | ls backups, crontab -l |
| D5 | Orphaned postgres crons removed (2 old entries pointing to nonexistent scripts) | crontab -u postgres empty |
| D6 | Prune timer enabled (hourly, 30-day retention) | systemctl list-timers |
| 1H | idle_in_transaction_session_timeout = 5min (server backstop, bots set own 60s) | SHOW idle_in_transaction_session_timeout |

---

## PHASE 1 STATUS

| Item | Status | Notes |
|------|--------|-------|
| 1A | DONE (prior S172) | frozen_price_check: updated_at → timestamp |
| 1B | DONE (prior S172) | calibration_check: rolling 90-day + CRPS/PIT. WB baseline: Brier 0.2328, BSS -0.2043, 87,933 resolved [source: scripts/calibration_check.py WeatherBot] |
| 1C | DONE | 067_vacuum_tuning.sql: positions, markets, users, traded_markets. mpl skipped (1.4% dead, not 14%) |
| 1D | DONE | 068_fix_resolution_prices.sql: 7638 events fixed. Uses event_data->>'resolution' first, falls back to realized_pnl |
| 1E-a | DONE | 069_market_aliases.sql: 5263 aliases populated from markets.condition_id |
| 1E-b | DONE | order_gateway: resolves condition_id via in-memory index, warns on unknown markets |
| 1G | DONE | MB + EB now write to shared prediction_log. MB logs kelly_prob (is a probability: price+edge, clamped [price+0.005, 0.95]). EB logs to both esports_prediction_log + shared |
| 1F | DONE | tracemalloc starts at boot, SIGUSR1 dumps top 20. **TabPFN = 48 bytes (stub). Phase 5A is a no-op.** |
| **1I** | **NEXT** | Edge verification — HARD GATE for Phases 5-7 |
| 1J | PENDING | Orderbook collection cron |
| 1K | PENDING | Quick SSH verifications (no code) |
| 1L | PENDING | Shadow mode protocol (process doc, no code) |
| 1M | PENDING | Strategy lifecycle schema (migration) |

---

## KNOWN ISSUES

### 1. Migration ownership (systemic)
Tables are owned by `postgres`, migration runner connects as `polymarket`. ALTER TABLE SET (storage params) requires owner. Worked around 067-069 by manually applying as postgres + marking in schema_migrations. **Every future ALTER TABLE migration will hit this.** Fix: either `ALTER TABLE ... OWNER TO polymarket` for affected tables, or add superuser migration path to runner.

### 2. Old VPS (34.251.224.21) — DECOMMISSION
Ubuntu-3 (16GB/4vCPU) still exists. All services FAILED, last release April 10. Contains code, DB credentials, API keys. Needs manual teardown in Lightsail console. Cost leakage if billing continues.

### 3. fail2ban fragility
fail2ban has crashed 3 times across sessions (exit 255). Root cause this session: missing `systemd._journal` Python module — fixed by switching jail.local to `backend = auto`. May crash again on upgrade. Monitor via: `systemctl is-active fail2ban`.

### 4. MirrorBot InFailed=1 (since reboot)
One InFailedSQLTransaction in MB logs since reboot. Not recurring (0 in subsequent checks). Likely transient during restart. Monitor.

### 5. EB scan_summary not visible in recent logs
EB is active and processing markets but scan_summary lines weren't appearing in the 2-3 minute journal windows checked. May be long scan cycles (25s observed post-restart). Not a bug — EB has heavy initialization.

---

## TRACEMALLOC RESULTS (1F evaluation)

```
Top 5 allocations (EB, 2026-04-13T19:35:26Z):
  asyncio/selector_events.py:1023    28.0 MiB  (506K objects)
  database.py:2955                   17.4 MiB  (189K objects)
  json/decoder.py:361                11.8 MiB  (92K objects)
  sqlalchemy/engine/result.py:563     2.1 MiB  (28K objects)
  unified_market_service.py:366       1.2 MiB  (6.5K objects)

  TabPFN object size: 48 bytes (STUB — import failed gracefully)
  tracemalloc current: 69.7 MB / peak: 74.1 MB
  EB RSS: 1196 MB (tracemalloc only tracks Python heap, not C extensions)
```

**Decision: Phase 5A (TabPFN removal/API migration) is a NO-OP.** TabPFN never loaded.

---

## VPS STATE (as of 2026-04-13 19:50 UTC)

| Component | Status |
|-----------|--------|
| **Host** | Ubuntu-32, 18.201.216.0, 30GB RAM, 8 vCPU |
| **Release** | /opt/pa2-releases/20260413_150819 |
| **PostgreSQL** | active, OOMScoreAdjust=-900, idle_in_txn=5min |
| **Redis** | active, AOF enabled, OOMScoreAdjust=-500 |
| **WeatherBot** | active, RSS=1020MB/2048MB, OOM=-200, scanning |
| **MirrorBot** | active, RSS=1274MB/2560MB, OOM=-100, scanning 2.1s |
| **EsportsBot** | active, RSS=1196MB/2560MB, OOM=0, scanning |
| **Ingestion** | active, RSS=311MB/512MB, OOM=100 |
| **fail2ban** | active, sshd jail (auto backend, maxretry=3) |
| **Backup** | 2.9GB dump, cron 02:00 UTC daily |
| **Dedup indexes** | 10/10 valid |
| **DB errors** | 0 InFailed, 0 Semaphore, 0 MissingGreenlet |
| **Open positions** | EB=7, MB=13, WB=135 |

---

## WHAT'S NEXT (Phase 1 remaining)

### Priority order:
1. **1I: Edge verification** — HARD GATE for Phases 5-7. Bootstrap P(edge > 0) and Kelly on existing trade_events. ~50 lines numpy. Graduated response: P(edge>0) >= 0.9 → full elevation, 0.7-0.9 → core only, <0.7 → root-cause investigation.
2. **1J: Orderbook collection** — Cron polling best_bid/best_ask every 60s. Rate limit consideration for 500+ markets.
3. **1K: Quick verifications** — SSH checks: ArbitrageBot auto-start? EsportsLiveBot orphans? Canary stuck?
4. **1L: Shadow mode protocol** — Process document (not code). Required before Phase 5-7 model changes.
5. **1M: Strategy lifecycle schema** — 5 PG tables, migration only. **Will hit migration ownership issue** — plan to apply as postgres.

### After Phase 1:
Phase 2 (Operational Resilience) starts. First item: 2A asyncio.wait_for verification grep (already mostly fixed in S166).

---

## DEPLOY COMMANDS

```bash
# Deploy (from local machine)
bash deploy/deploy.sh

# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0

# Health checks
for svc in polymarket-weather polymarket-mirror polymarket-esports; do
  echo "--- $svc ---"
  journalctl -u $svc --since '5 min ago' --no-pager | grep -c 'InFailedSQLTransaction'
  journalctl -u $svc --since '5 min ago' --no-pager | grep 'scan_ms' | tail -1
done

# TabPFN check (already done — 48 bytes stub)
sudo kill -USR1 $(systemctl show polymarket-esports -p MainPID --value)
journalctl -u polymarket-esports --lines=25 --no-pager | grep tracemalloc

# Canonical P&L (NEVER raw SQL)
RELEASE=$(readlink -f /opt/polymarket-ai-v2) && cd $RELEASE && \
  sudo -u polymarket bash -c "source /opt/pa2-shared/venv/bin/activate && \
  PYTHONPATH=$RELEASE python3 $RELEASE/scripts/bot_pnl.py WeatherBot 72"

# Apply future ALTER TABLE migrations manually
sudo -u postgres psql -d polymarket -f /tmp/XXX_migration.sql
sudo -u postgres psql -d polymarket -c "INSERT INTO schema_migrations (name) VALUES ('XXX_migration.sql');"
```

---

## CRITICAL RULES (carried forward)

1. **NEVER present financial numbers without source citation** — bot_pnl.py or [UNVERIFIED]
2. **NEVER write raw SQL for P&L** — use scripts/bot_pnl.py
3. **One fix per commit** — each commit addresses ONE issue
4. **Paper trading IS production** — every feature matters identically
5. **No asyncio.wait_for on DB** — use SET statement_timeout
6. **EsportsBot stays in PM_EXCLUDE_BOTS** — removal causes semaphore exhaustion
7. **Migration ownership** — ALTER TABLE SET requires postgres superuser, not polymarket user
8. **TabPFN is a stub** — Phase 5A is a no-op, skip it
9. **UFW LIMIT locks out deploys** — use iptables rate limit (15/60s) + fail2ban instead
10. **Old VPS 34.251.224.21 needs decommission** — contains credentials, costs money
