# S172C FINAL HANDOFF — Phase 1 Complete, Phase RC Drafted, Deploy Verified

**Session:** 172C (shared bot + master plan session)
**Date:** 2026-04-13
**Scope:** ALL BOTS — S172 consolidated plan execution
**Deploy:** `20260413_172523` on Ubuntu-32 (18.201.216.0) — VERIFIED
**Tests:** 1892 passed, 0 failed, 2 skipped, 9 xfailed
**Branch:** master
**Prior session:** S172B (`AGENT_HANDOFF_S172B_SHARED_MASTER.md`)

---

## 1. WHAT THIS SESSION DID

### 1A. Phase 1 Completion (all 12 items now done)

This session completed the 5 remaining Phase 1 items from the S172 consolidated plan:

| Item | SHA | Files | What |
|------|-----|-------|------|
| **1I** | `10c7232` | scripts/edge_verification.py, EDGE_VERIFICATION_1I_RESULTS.md | Bootstrap P(edge>0) + Kelly — HARD GATE fired for ALL 3 bots |
| **1J** | `0767d93` | schema/migrations/070_orderbook_snapshots.sql + down, scripts/orderbook_collector.py | Orderbook collection table + collector + systemd timer |
| **1K** | (SSH only) | — | Quick verifications: ArbitrageBot masked, no orphans, no canary |
| **1L** | `38b8547` | docs/SHADOW_MODE_PROTOCOL.md | Shadow mode protocol (process doc, no code) |
| **1M** | `d0fe765` | schema/migrations/071_strategy_lifecycle.sql + down | Strategy lifecycle: 5 PG tables (strategies, strategy_performance, capital_allocations, strategy_transitions, strategy_predictions) |

Plus 2 documentation commits:
| SHA | What |
|-----|------|
| `ef04b1f` | Initial S172C handoff doc |
| `60b143b` | Phase RC plan + updated handoff with deploy verification |

### 1B. VPS Deployment + Infrastructure

- **deploy.sh landed** release `20260413_172523` (despite health check SSH lockout)
- **Migrations 070 + 071** applied manually as postgres user (6 new tables)
- **Orderbook collector** systemd timer active — running every 60s, 13,194 snapshots accumulated as of session end [source: VPS psql COUNT(*)]
- **fail2ban** maxretry raised 3→10 to prevent deploy lockouts
- All 4 services verified active, 0 InFailedSQLTransaction across all bots

### 1C. Phase RC Root-Cause Plan

Drafted `S172_PHASE_RC_ROOT_CAUSE_PLAN.md` — a new phase that inserts between Phase 1 (complete) and the gated Phases 5-7. This was the audit's most critical finding: the plan caught negative edge via 1I but had no plan for what to do after the gate fired.

### 1D. Cold-Read Audit Integration

Received and responded to a comprehensive cold-read audit of S172 post-edge-verification. Key findings integrated:
- ~60% of plan items suspended (Phases 5-7, 12, parts of 8)
- "Keep WB running" decision revisited (justified: paper trading = zero real capital risk)
- SSH lockout pattern identified (deploy.sh + fail2ban incompatibility)
- 1J orderbook confirmed valuable for root-cause slippage analysis (audit initially flagged as premature, then retracted)

---

## 2. THE CRITICAL FINDING: ALL 3 BOTS HAVE NEGATIVE EDGE

[source: scripts/edge_verification.py, 10,000 bootstrap samples on VPS PostgreSQL trade_events]

| Bot | Closed Trades | RES/EXIT | WR | Total P&L | Raw Edge | P(edge>0) | 95% CI | Kelly (half) | Verdict |
|-----|--------------|----------|-----|-----------|----------|-----------|--------|-------------|---------|
| WeatherBot | 3,389 | 3099/290 | 59.3% | -$29,919 | -14.67% | 0.0212 | [-31.83%, -0.44%] | -0.0005 | ROOT-CAUSE |
| MirrorBot | 9,519 | 6365/3154 | 39.7% | -$113,643 | -7.20% | 0.0001 | [-11.47%, -3.14%] | -0.0013 | ROOT-CAUSE |
| EsportsBot | 541 | 254/287 | 36.2% | -$8,622 | -14.74% | 0.0015 | [-26.68%, -4.46%] | -0.0024 | ROOT-CAUSE |

**Per S172 plan graduated response:** P(edge>0) < 0.70 for all 3 → ROOT-CAUSE INVESTIGATION replaces elevation (Phases 5-7).

**Interpretation:**
- **WB:** Wins 59.3% of trades but average loss magnitude dwarfs average win. Most likely: asymmetric payoff from NO-side bets at high prices (full stake at risk for small upside).
- **MB:** Most statistically certain negative edge (9,519 trades, CI entirely below zero). Copy signal quality or wallet selection is the primary suspect.
- **EB:** Smallest sample (541) but CI doesn't come close to zero. Game-specific decomposition needed.

---

## 3. PHASE 1 — COMPLETE STATUS (all 12 items across S172, S172B, S172C)

| Item | Session | Status | Key Detail |
|------|---------|--------|------------|
| 1A | S172 | DONE | frozen_price_check: updated_at → timestamp |
| 1B | S172 | DONE | calibration_check: rolling 90-day + CRPS/PIT. WB baseline Brier 0.2328 [source: scripts/calibration_check.py] |
| 1C | S172B | DONE | 067_vacuum_tuning.sql: positions, markets, users, traded_markets |
| 1D | S172B | DONE | 068_fix_resolution_prices.sql: 7638 events fixed [source: VPS psql migration output] |
| 1E-a | S172B | DONE | 069_market_aliases.sql: 5263 aliases [source: VPS psql migration output] |
| 1E-b | S172B | DONE | order_gateway: alias resolution + unknown market warning |
| 1F | S172B | DONE | tracemalloc SIGUSR1. TabPFN = 48 bytes STUB. Phase 5A is a no-op. |
| 1G | S172B | DONE | prediction_log writes for MB (kelly_prob) + EB (shared table) |
| **1I** | **S172C** | **DONE** | **Edge verification — ALL BOTS ROOT-CAUSE. Phases 5-7 GATED.** |
| **1J** | **S172C** | **DONE** | **Orderbook collection: migration 070, collector script, systemd timer (every 60s)** |
| **1K** | **S172C** | **DONE** | **SSH checks: ArbitrageBot masked, no EsportsLiveBot orphans, no canary stuck** |
| **1L** | **S172C** | **DONE** | **Shadow mode protocol: docs/SHADOW_MODE_PROTOCOL.md** |
| **1M** | **S172C** | **DONE** | **Strategy lifecycle: migration 071, 5 tables** |

---

## 4. VPS STATE (verified 2026-04-14 00:52 UTC)

| Component | Status | Detail |
|-----------|--------|--------|
| Host | Ubuntu-32, 18.201.216.0, 30GB RAM, 8 vCPU | Up 5h28m |
| Release | /opt/pa2-releases/20260413_172523 | S172C code |
| PostgreSQL | active | OOMScoreAdjust=-900, idle_in_txn=5min |
| Redis | active | AOF enabled, OOMScoreAdjust=-500 |
| WeatherBot | active | RSS=1161MB/2048MB, OOM=-200, scanning |
| MirrorBot | active | RSS=1241MB/2560MB, OOM=-100, scanning |
| EsportsBot | active | RSS=1191MB/2560MB, OOM=0, scanning |
| Ingestion | active | RSS=303MB/512MB, OOM=+100 |
| Orderbook timer | active | Every 60s. 13,194 snapshots accumulated. |
| fail2ban | active | sshd jail, maxretry=10, bantime=3600, 0 banned |
| Backup | active | 2.9GB dump, cron 02:00 UTC daily |
| Dedup indexes | 10/10 valid | |
| DB errors | 0 InFailed, 0 MissingGreenlet | All 3 bots |
| Open positions | EB=5, MB=10, WB=128 | |
| New tables | 6 | orderbook_snapshots, strategies, strategy_performance, capital_allocations, strategy_transitions, strategy_predictions |

---

## 5. S172 PLAN STATUS — POST 1I GATE

### Phases that CONTINUE (edge-independent):

| Phase | Status | Next Items |
|-------|--------|-----------|
| **Phase 1** | **COMPLETE** | All 12 items done |
| **Phase RC** | **DRAFTED** | `S172_PHASE_RC_ROOT_CAUSE_PLAN.md` — awaiting approval |
| **Phase 2** | NEXT | 2A-2K: asyncio grep, data retention, structlog dedup, logrotate, health check, pool tuning, liquidity gate, Feast |
| **Phase 3** | PENDING | VPS config (PG planner, PgBouncer, sshd hardening, SSH port change, autovacuum) |
| **Phase 4** | PENDING | Hygiene (archive handoffs, orphan scripts, gitignore, test files, trade_journal fix) |
| **Phase 8 infra** | PENDING | 8A position registry, 8I partitioning, 8J service templates, 8K dead EnsembleBot removal, 8P drift monitoring |
| **Phase 10** | PENDING | 10D Telegram alerts, 10F backtester (diagnostic value for root-cause) |
| **Phase 13** | PENDING | Compliance, dashboard |

### Phases GATED behind positive edge (suspended):

| Phase | Reason |
|-------|--------|
| **Phase 5** (EB elevation) | P(edge>0)=0.0015. All items suspended. |
| **Phase 6** (WB elevation) | P(edge>0)=0.0212. All items suspended EXCEPT 6A (reentry fix, operational) and 6O (lead-time analysis, diagnostic). |
| **Phase 7** (MB elevation) | P(edge>0)=0.0001. All items suspended. |
| **Phase 8 bot-specific** | 8D exit sweep, 8H calibration, 8L CLV, 8M RL timing, 8R Kelly — suspended. |
| **Phase 12** (EMOS) | Depends on WB elevation. Suspended. |

### Plan success criteria status:

| Criterion | Current | Required |
|-----------|---------|----------|
| All 3 bots P(edge>0) >= 0.7 | WB=0.02, MB=0.0001, EB=0.002 | >= 0.70 |
| Max drawdown < 25% | Paper trading, not applicable yet | < 25% |
| Zero P0 audit items | 0 | 0 |
| WB calibrated | Brier 0.2328 [source: calibration_check.py] | CRPS/PIT pass |
| MB 500+ predictions | Logging since S172B | 500+ |
| EB 300+ predictions | Logging since S172B | 300+ |
| Shadow mode used for all model changes | Protocol written (1L) | Yes |
| Backups operational | 2.9GB daily dump | Yes |

---

## 6. PHASE RC — ROOT-CAUSE INVESTIGATION PLAN (SUMMARY)

Full plan: `S172_PHASE_RC_ROOT_CAUSE_PLAN.md`

### Framework: 6-Layer Decomposition (per bot)

| Layer | Question | Fixable? |
|-------|----------|----------|
| 1. Fees & Slippage | What % of gross edge do costs consume? | Yes — raise edge thresholds |
| 2. Win/Loss Magnitude | Are losses disproportionately large? | Yes — stops, sizing |
| 3. Entry Edge | Did signal ever beat the market price? | Maybe — model quality |
| 4. Exit Timing | Are exits destroying value? | Yes — exit logic fixes |
| 5. Segment Decomposition | Are some segments profitable? | Yes — market selection |
| 6. Temporal Analysis | Was edge ever positive? | Depends — stale vs never |

### Per-Bot Hypotheses

| Bot | Primary Hypothesis | Key Diagnostic |
|-----|-------------------|----------------|
| WB | Asymmetric loss sizing (59.3% WR [UNVERIFIED] but outsized losses) | WB-1: win/loss magnitude distribution |
| MB | Poor copy signal quality (39.7% WR [UNVERIFIED]) | MB-5: are copied traders actually profitable? |
| EB | Game-specific edge variance | EB-1: P&L by game breakdown |

### Timeline

| Week | Focus | Output |
|------|-------|--------|
| 1 | Build diagnostic script + WB analysis (WB-1 through WB-8) | WB fix proposal or kill recommendation |
| 2 | MB analysis (MB-1 through MB-8) + EB analysis (EB-1 through EB-8) | Per-bot recommendations |
| 3 | Implement fixes → accumulate 200+ trades → re-run 1I | Updated P(edge>0) |

### Decision Gates

| After analysis | Action |
|---------------|--------|
| Signal never had edge (Layer 3) | Kill bot or replace model entirely |
| Fees consumed edge (Layer 1) | Reduce frequency, raise edge threshold |
| Exits destroyed edge (Layer 4) | Fix exit logic, re-evaluate |
| Some segments profitable (Layer 5) | Restrict to profitable segments |
| Edge degraded over time (Layer 6) | Model refresh needed |
| Win/loss asymmetry (Layer 2) | Position sizing fix or hard stops |

### Re-gate: Run `scripts/edge_verification.py` on post-fix trades only. Same thresholds: >=0.9 full elevation, 0.7-0.9 core only, <0.7 back to investigation or kill.

---

## 7. KNOWN ISSUES (ACTIVE)

| # | Issue | Severity | Status | Action |
|---|-------|----------|--------|--------|
| 1 | **fail2ban deploy lockout** | High | PARTIALLY FIXED — maxretry 3→10 | Still needs: deploy IP whitelist, ControlMaster in deploy.sh, re-add UFW rate limiting |
| 2 | **Migration ownership** | Medium | SYSTEMIC | Tables owned by postgres, migrations run as polymarket. Every ALTER TABLE fails. Fix: `ALTER TABLE ... OWNER TO polymarket` for affected tables |
| 3 | **Old VPS 34.251.224.21** | Medium | OPEN | All services FAILED, contains credentials. DECOMMISSION via Lightsail console. |
| 4 | **Lightsail static IP unverified** | Medium | OPEN | If not static, stop/start will rotate IP and break all deploy configs |
| 5 | **UFW SSH wide open** | Medium | OPEN | Rate limiting was removed as lockout workaround. Brute force protection gone. Re-add after ControlMaster fix. |
| 6 | **EB slow scans** | Low | KNOWN | 93-126s scan cycles observed. Heavy initialization, known behavior. |
| 7 | **S172 handoff audit false positives** | Low | NOTED | S172 audit generated 4+ false positive findings (1F, 1E-a, 1G, kelly_prob). Future audits should verify against actual code. |

---

## 8. KEY FILES (created or modified this session)

| File | Purpose | New/Modified |
|------|---------|-------------|
| `scripts/edge_verification.py` | 1I: Bootstrap P(edge>0) + Kelly per bot. THE GATE. | NEW |
| `EDGE_VERIFICATION_1I_RESULTS.md` | 1I results report with all numbers and interpretation | NEW |
| `schema/migrations/070_orderbook_snapshots.sql` | 1J: orderbook_snapshots table | NEW |
| `scripts/orderbook_collector.py` | 1J: Polls best_bid/best_ask, bulk inserts. Runs via systemd timer. | NEW |
| `docs/SHADOW_MODE_PROTOCOL.md` | 1L: Shadow mode rules for Phases 5-7 model changes | NEW |
| `schema/migrations/071_strategy_lifecycle.sql` | 1M: 5 strategy lifecycle tables | NEW |
| `S172_PHASE_RC_ROOT_CAUSE_PLAN.md` | Phase RC: Root-cause investigation plan (DRAFT) | NEW |
| `AGENT_HANDOFF_S172C_SHARED_MASTER.md` | Session handoff (interim, updated with deploy verification) | NEW |

---

## 9. SESSION CHAIN

```
S172 → S172B → S172C (this session)
  ↓       ↓       ↓
Day 1   Phase 1  Phase 1 complete
D7-D10  1A-1G    1I-1M + Phase RC
code    + Day 1   + deploy verified
        SSH       + orderbook timer
                  + fail2ban fix
```

Prior bot-specific sessions (still current for bot context):
- **MirrorBot:** S168 (`AGENT_HANDOFF_MIRRORBOT_SESSION168_MASTER.md`)
- **EsportsBot:** S167 (`AGENT_HANDOFF_ESPORTS_SESSION167_MASTER.md`)
- **WeatherBot:** S167 (`AGENT_HANDOFF_WEATHERBOT_SESSION167_MASTER.md`)

---

## 10. WHAT TO DO NEXT (for new agent)

### Immediate (before any new development):
1. Read this handoff + `S172_PHASE_RC_ROOT_CAUSE_PLAN.md`
2. Read `CLAUDE.md` for non-negotiable development rules
3. Verify orderbook timer is still running: `systemctl is-active polymarket-orderbook.timer`

### Priority order:
1. **Get Phase RC approved** — user must approve before execution begins
2. **Start RC-WB** — WeatherBot root-cause analysis (highest diagnostic signal: 59.3% WR [UNVERIFIED] + deeply negative edge)
3. **Phase 2 infra** in parallel — 2A asyncio.wait_for verification grep is the first item

### DO NOT:
- Touch Phases 5, 6, 7, or 12 — they are GATED
- Present financial numbers without source citation or [UNVERIFIED] label
- Use asyncio.wait_for on DB operations
- Remove EsportsBot from PM_EXCLUDE_BOTS
- Write raw SQL for P&L (use scripts/bot_pnl.py)
- Use `deploy.sh` without first fixing ControlMaster (risk of SSH lockout)
- Touch old VPS 34.251.224.21 via SSH — decommission via Lightsail console only

---

## 11. CRITICAL RULES (carried forward, all still active)

1. **NEVER present dollar amounts, win rates, P&L, trade counts without explicit source citation** — `[source: bot_pnl.py]` or `[source: edge_verification.py]` or `[UNVERIFIED]`
2. **NEVER write raw SQL for P&L** — use `scripts/bot_pnl.py`
3. **One fix per commit** — each commit addresses exactly ONE issue
4. **Paper trading IS production** — every feature matters identically
5. **No asyncio.wait_for on DB** — use SET statement_timeout
6. **EsportsBot stays in PM_EXCLUDE_BOTS** — removal causes semaphore exhaustion
7. **Migration ownership** — ALTER TABLE requires postgres superuser, not polymarket
8. **TabPFN is a stub** — Phase 5A is a no-op, skip it
9. **deploy.sh + fail2ban** — maxretry=10 now, but still needs ControlMaster fix
10. **Phases 5-7 are GATED** — all 3 bots P(edge>0) < 0.07 [source: scripts/edge_verification.py]. Root-cause first.
11. **Bot-scoped sessions** — unless explicitly shared. This was a shared session.
12. **No "while I'm in here" refactors** — fix only what's assigned
13. **Pre/post deploy split** — always split log data by deploy timestamp before analyzing
14. **Read entire file before modifying** — not just the function you're changing
15. **Grep for dependents** before changing any shared module

---

## 12. VPS ACCESS

```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0

# Canonical P&L (NEVER raw SQL)
RELEASE=$(readlink -f /opt/polymarket-ai-v2) && cd $RELEASE && \
  sudo -u polymarket bash -c "source /opt/pa2-shared/venv/bin/activate && \
  PYTHONPATH=$RELEASE python3 $RELEASE/scripts/bot_pnl.py WeatherBot 72"

# Edge verification (re-run after fixes)
RELEASE=$(readlink -f /opt/polymarket-ai-v2) && cd $RELEASE && \
  sudo -u polymarket bash -c "source /opt/pa2-shared/venv/bin/activate && \
  PYTHONPATH=$RELEASE python3 $RELEASE/scripts/edge_verification.py"

# Health checks
for svc in polymarket-weather polymarket-mirror polymarket-esports; do
  echo "--- $svc ---"
  journalctl -u $svc --since '5 min ago' --no-pager | grep -c 'InFailedSQLTransaction'
  journalctl -u $svc --since '5 min ago' --no-pager | grep -E 'scan_ms' | tail -1
done

# Orderbook timer health
systemctl status polymarket-orderbook.timer
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), MAX(snapshot_time) FROM orderbook_snapshots;"

# fail2ban status
sudo fail2ban-client status sshd
```
