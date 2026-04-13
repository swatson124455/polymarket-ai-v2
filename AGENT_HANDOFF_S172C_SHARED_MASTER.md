# S172C SHARED MASTER HANDOFF — Phase 1 COMPLETE

**Session:** 172C (continuation of S172B)
**Date:** 2026-04-13
**Scope:** ALL BOTS — S172 Phase 1 completion
**Deploy:** PENDING — fail2ban locked out SSH (~1h ban). Code committed locally.
**Tests:** 1892 passed, 0 failed, 2 skipped, 9 xfailed
**Branch:** master

---

## SESSION NARRATIVE

Completed all remaining Phase 1 items (1I, 1J, 1K, 1L, 1M). The critical finding: **all 3 bots have definitively negative edge**, gating Phases 5-7 elevation and triggering root-cause investigation per the S172 graduated response.

---

## COMMITS THIS SESSION (4 commits)

| # | SHA | Files | What |
|---|-----|-------|------|
| 1 | `10c7232` | scripts/edge_verification.py, EDGE_VERIFICATION_1I_RESULTS.md | 1I: Edge verification — all 3 bots P(edge>0) < 0.07 |
| 2 | `0767d93` | schema/migrations/070_orderbook_snapshots.sql + down, scripts/orderbook_collector.py | 1J: Orderbook collection table + collector script |
| 3 | `38b8547` | docs/SHADOW_MODE_PROTOCOL.md | 1L: Shadow mode protocol document |
| 4 | `d0fe765` | schema/migrations/071_strategy_lifecycle.sql + down | 1M: Strategy lifecycle schema (5 tables) |

**NOT YET DEPLOYED.** SSH locked out by fail2ban (deploy.sh triggered iptables rate limit). Need to wait ~1h for unban or use Lightsail console.

---

## 1I EDGE VERIFICATION RESULTS (CRITICAL)

| Bot | Trades | WR | P&L | Edge | P(edge>0) | Verdict |
|-----|--------|-----|-----|------|-----------|---------|
| WeatherBot | 3,389 | 59.3% | -$29,919 | -14.67% | 0.021 | ROOT-CAUSE |
| MirrorBot | 9,519 | 39.7% | -$113,643 | -7.20% | 0.0001 | ROOT-CAUSE |
| EsportsBot | 541 | 36.2% | -$8,622 | -14.74% | 0.002 | ROOT-CAUSE |

[source: scripts/edge_verification.py run on VPS PostgreSQL, 10,000 bootstrap samples]

**Impact:** Phases 5-7 (bot elevation) are GATED. Root-cause investigation required for each bot. Phases 1-4 continue as planned.

Key observations:
- WB: 59.3% win rate but losses outweigh wins by ~2.5x — asymmetric payoff structure
- MB: Tightest CI (9,519 trades), definitively negative — 95% CI entirely below zero
- EB: Smallest sample but 95% CI [-26.68%, -4.46%] — not close to zero

---

## 1K SSH VERIFICATION RESULTS

- **ArbitrageBot:** polymarket-ai service MASKED + all 4 arb BOT_ENABLED=false. No auto-start risk.
- **EsportsLiveBot:** No orphan processes. No service units.
- **Canary:** No stuck positions.
- **All services:** polymarket-weather/mirror/esports/ingestion all active + enabled.

---

## PHASE 1 STATUS — ALL COMPLETE

| Item | Status | Notes |
|------|--------|-------|
| 1A | DONE (S172) | frozen_price_check timestamp fix |
| 1B | DONE (S172) | calibration_check CRPS/PIT |
| 1C | DONE (S172B) | autovacuum tuning |
| 1D | DONE (S172B) | resolution price fix |
| 1E-a | DONE (S172B) | market_aliases schema |
| 1E-b | DONE (S172B) | order_gateway validation |
| 1F | DONE (S172B) | tracemalloc (TabPFN=48 bytes stub) |
| 1G | DONE (S172B) | prediction_log MB+EB writes |
| **1I** | **DONE (S172C)** | Edge verification — ALL BOTS ROOT-CAUSE |
| **1J** | **DONE (S172C)** | Orderbook collection (migration 070 + collector). Deploy pending. |
| **1K** | **DONE (S172C)** | SSH checks — all clean |
| **1L** | **DONE (S172C)** | Shadow mode protocol at docs/SHADOW_MODE_PROTOCOL.md |
| **1M** | **DONE (S172C)** | Strategy lifecycle schema (migration 071, 5 tables). Deploy pending. |

---

## DEPLOY CHECKLIST (for next session)

When SSH unblocks:

```bash
# 1. Manual deploy
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
tar czf /tmp/pa2-$TIMESTAMP.tar.gz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='./data' --exclude='./saved_models' --exclude='./venv' --exclude='./.venv' -C /c/lockes-picks/polymarket-ai-v2 .
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem /tmp/pa2-$TIMESTAMP.tar.gz ubuntu@18.201.216.0:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "
  sudo mkdir -p /opt/pa2-releases/$TIMESTAMP
  sudo tar xzf /tmp/pa2-$TIMESTAMP.tar.gz -C /opt/pa2-releases/$TIMESTAMP
  sudo chown -R ubuntu:ubuntu /opt/pa2-releases/$TIMESTAMP
  ln -sfn /opt/pa2-shared/.env /opt/pa2-releases/$TIMESTAMP/.env
  for f in /opt/pa2-shared/.env.d/.env.*; do ln -sfn \$f /opt/pa2-releases/$TIMESTAMP/\$(basename \$f); done
  ln -sfn /opt/pa2-shared/venv /opt/pa2-releases/$TIMESTAMP/venv
  ln -sfn /opt/pa2-shared/data /opt/pa2-releases/$TIMESTAMP/data
  [ -d /opt/pa2-shared/saved_models ] && ln -sfn /opt/pa2-shared/saved_models /opt/pa2-releases/$TIMESTAMP/saved_models
  sudo ln -sfn /opt/pa2-releases/$TIMESTAMP /opt/polymarket-ai-v2
  sudo systemctl restart polymarket-esports polymarket-mirror polymarket-weather polymarket-ingestion
"

# 2. Apply migrations as postgres (ownership issue)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "
  RELEASE=\$(readlink -f /opt/polymarket-ai-v2)
  sudo -u postgres psql -d polymarket -f \$RELEASE/schema/migrations/070_orderbook_snapshots.sql
  sudo -u postgres psql -d polymarket -c \"INSERT INTO schema_migrations (name) VALUES ('070_orderbook_snapshots.sql');\"
  sudo -u postgres psql -d polymarket -f \$RELEASE/schema/migrations/071_strategy_lifecycle.sql
  sudo -u postgres psql -d polymarket -c \"INSERT INTO schema_migrations (name) VALUES ('071_strategy_lifecycle.sql');\"
"

# 3. Set up orderbook collector cron (every 60s via systemd timer)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "
  cat << 'TIMER' | sudo tee /etc/systemd/system/polymarket-orderbook.service
[Unit]
Description=Polymarket Orderbook Collector
After=postgresql@16-main.service

[Service]
Type=oneshot
User=polymarket
Group=polymarket
WorkingDirectory=/opt/polymarket-ai-v2
ExecStart=/opt/pa2-shared/venv/bin/python scripts/orderbook_collector.py --once --limit 200
Environment=PYTHONPATH=/opt/polymarket-ai-v2
EnvironmentFile=/opt/pa2-shared/.env
TimeoutStartSec=90
MemoryMax=256M
TIMER

  cat << 'TIMER' | sudo tee /etc/systemd/system/polymarket-orderbook.timer
[Unit]
Description=Polymarket Orderbook Collector Timer

[Timer]
OnCalendar=*:*:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER

  sudo systemctl daemon-reload
  sudo systemctl enable --now polymarket-orderbook.timer
"

# 4. Health check
for svc in polymarket-weather polymarket-mirror polymarket-esports; do
  echo "--- \$svc ---"
  journalctl -u \$svc --since '5 min ago' --no-pager | grep -c 'InFailedSQLTransaction'
done
```

---

## WHAT'S NEXT — Phase 2 (Operational Resilience)

Phase 1 is COMPLETE. Phase 2 starts:

| # | Item | Priority |
|---|------|----------|
| 11 | 2A: asyncio.wait_for verification grep | Verify S166 cleanup complete |
| 12 | 2B: Data retention (trades CREATE-AS-SELECT + recon_breaks) | |
| 13 | 2C: Structlog dedup (30s TTL) | |
| 14 | 2D: WatchedFileHandler + logrotate | |
| 15 | 2E: RTDS seen_set dedup | |
| 16 | 2F: Health check kill switch wiring | |
| 17 | 2G: Pool tightening (investigate first) | |
| 18 | 2I: Illiquidity exit validation + enable | Before 2H |
| 19 | 2H: Entry-time liquidity gate | |
| 20 | 2H-b: Shared-token mutual exclusion | |
| 21 | 2J: Slippage monitoring refactor | |
| 22 | 2K: Feast feature store | |

**AND: Root-cause investigation for all 3 bots' negative edge** — this is now the highest priority insight from 1I. Without fixing the edge, elevation is pointless.

---

## KNOWN ISSUES (carried forward + new)

1. **fail2ban locking out SSH** — deploy.sh opens many connections, trips iptables 15/60s rate limit + fail2ban 3-retry ban. Solution: deploy.sh needs connection reuse (ControlMaster), or increase rate limit. This is the 4th SSH lockout across sessions.
2. **Migration ownership** — ALTER TABLE requires postgres user. Migrations 070-071 need manual apply.
3. **Old VPS 34.251.224.21** — still exists, needs decommission.

---

## CRITICAL RULES (carried forward)

1. NEVER present financial numbers without source citation
2. NEVER write raw SQL for P&L — use scripts/bot_pnl.py
3. One fix per commit
4. Paper trading IS production
5. No asyncio.wait_for on DB
6. EsportsBot stays in PM_EXCLUDE_BOTS
7. Migration ownership requires postgres superuser
8. TabPFN is a stub — Phase 5A is a no-op
9. UFW LIMIT / deploy.sh locks out SSH — use iptables + fail2ban instead
10. **Phases 5-7 are GATED** — all 3 bots P(edge>0) < 0.07. Root-cause first.
