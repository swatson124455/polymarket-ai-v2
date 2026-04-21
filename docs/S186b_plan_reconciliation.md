# S186b Plan-vs-Reality Reconciliation

**Date:** 2026-04-21
**Scope:** Full per-item verification of every line item in `S172_CONSOLIDATED_PLAN.md` against (a) `git log master` (698 commits), (b) local file existence via `ls`/`Glob`, (c) VPS runtime state via SSH (`systemctl show`, `pg_settings`, `pg_indexes`, `sshd_config`, `crontab`, `.env`, `ss -tlnp`, `logrotate.d`).
**Artifact class:** Reference doc. Session-scoped narrative lives in `AGENT_HANDOFF_S186_CLOSE.md` and the §S186b Corrections Log entry in the plan.
**Rule Zero compliance:** All numeric citations are config values, commit SHAs, migration numbers, line references, or file sizes — not trading performance. No `bot_pnl.py` sourcing required.

---

## Methodology

For each plan item, verification consisted of one or more of:

1. **Plan marker** — explicit `COMPLETE`/`SHIPPED`/`SKIPPED` text in `S172_CONSOLIDATED_PLAN.md`
2. **Commit SHA on master** — via `git log master --oneline` (last 150 commits inspected in detail, full log searched by keyword)
3. **Local file existence** — via `ls`, `Glob`
4. **VPS runtime state** — via SSH: `systemctl cat`/`show`, `sudo -u postgres psql -c`, `sudo grep` on config files, `systemctl list-timers`
5. **Substrate-level queries where hierarchical** — for PG settings: `pg_settings.sourcefile/sourceline/source`; for partitioned tables: per-partition index query; for systemd: template unit inspection

**Legend:** ✅ verified shipped · 🔵 live/in-evaluation · ⏳ shipped but not deployed · 🚫 deferred/removed/skipped · ⚠️ discrepancy vs plan marker · 🔶 partial · ❓ unverified (no direct evidence this pass) · ⭕ not started

---

## Executive summary

| Status | Count |
|---|---|
| ✅ Verified shipped | 57 |
| 🔵 Live (in-evaluation) | 8 |
| ⏳ Pending-deploy | 2 |
| 🚫 Deferred/removed/skipped | 6 |
| ⚠️ Discrepancy vs plan marker | 4 |
| 🔶 Partial | 4 |
| ❓ Unverified | 4 |
| ⭕ Not started | ~55 |
| **Total accounted for** | **~140** |

**Corrections from the first pass of this report:** D1 and D3 were initially classified as discrepancies. Both were verifier errors of the Protocol 4c-shaped class (querying one layer of a hierarchical structure and missing the substrate where the setting actually lives):
- **D1** applied via stock Ubuntu `postgresql@.service` template, not the `postgresql.service` wrapper.
- **D3** applied as per-partition unique indexes, not at parent-table level.

Revised discrepancy count: **4** (not 6). Filed as Protocol candidate "Hierarchical infrastructure verification."

## Four real discrepancies

| Item | Plan says | VPS state | Disposition |
|---|---|---|---|
| **P3-1** | effective_cache_size=12GB (implicit 16GB VPS era) | 24GB via `postgresql.auto.conf:5` (`ALTER SYSTEM` override) | **Plan stale.** VPS upgraded to 32GB (commit `8d7b5e1`) + S152 PG tuning. Current values correct. Plan text updated. |
| **P3-2** | PgBouncer "idle_txn timeout" | `server_idle_timeout=600`; `idle_transaction_timeout` not set | **NOT APPLIED.** Plan phrasing ambiguous — clarified to `idle_transaction_timeout`. Hygiene backlog. |
| **P3-4** | SSH port change to non-standard | Port 22 listening | **NOT APPLIED.** Fail2ban partial mitigation. Security-hardening backlog. |
| **P3-5** | autovacuum_naptime=15 | 60s (`pg_settings.source='default'`) | **NOT APPLIED.** Bundle with next PG deploy. Hygiene backlog. |

Plus one doc-drift reconciliation (not operational): **2F** references a non-existent `health_check.sh`; kill-switch wiring exists at `deploy/dead_man_watchdog.sh` + `deploy/healthcheck_probe.sh`. Plan row updated.

---

## DAY 1 — plan marker "COMPLETE deploy 20260413_172523"

| ID | Item | Status | Evidence |
|---|---|---|---|
| D0-a | Fix logrotate failure | ✅ | `/etc/logrotate.d/polymarket` present on VPS |
| D0-b | Verify ingestion NRestarts | ✅ | `systemctl show polymarket-ingestion`: `Restart=always, NRestarts=0` |
| D0-c | Redis AOF persistence | ✅ | `/etc/redis/redis.conf`: `appendonly yes` |
| D5 | pg_dump backup cron 04:00 UTC | ✅ | postgres crontab: `0 4 * * * /opt/pa2-backups/daily_backup.sh`; `deploy/daily_backup.sh` present; commit `e89d0ae` |
| D1 | PG OOMScoreAdjust=-900 | ✅ | `/lib/systemd/system/postgresql@.service:OOMScoreAdjust=-900` (stock Ubuntu template). Instance units inherit. Backends reset to 0 post-fork by design. |
| D2 | systemd MemoryMax + bot OOM | ✅ | WB 2G/-200, MB 2.5G/-100, EB 2.5G/0, ingestion 0.5G/+100 — all match plan exactly |
| D3 | RESOLUTION+EXIT partial unique indexes | ✅ | Per-partition indexes `idx_trade_events_<YYYY_MM>_exit_dedup` / `_resolution_dedup` across 12 month partitions + default. Origin commit `8f0c69f` (S159 C15+C18). |
| D4 | fail2ban + ufw limit ssh | 🔶 | fail2ban active ✅; ufw has `ALLOW 22/tcp` not `LIMIT` |
| D6 | Start prune timer | ✅ | `polymarket-prune-data.timer` + `polymarket-prune-prices.timer` active |
| D9 | PipelineGate REMOVED | 🚫 | Plan explicit REMOVED |
| D10 | WB reentry_check interim TTL | ✅ | Wired; later elevated by 6A (pending full-fix) |
| D7 | Hard stop-loss shared | ✅ | `763a362` |
| D8 | MB $30 flat + 24h cooldown | ✅ | `75e3785`, `b554c32` |

## PHASE 1 — plan marker "COMPLETE 12/12"

| ID | Item | Status | Evidence |
|---|---|---|---|
| 1A | frozen_price_check fix | ✅ | `6594c2b`; `base_engine/audit/checks/frozen_price_check.py` |
| 1B | calibration_check CRPS/PIT | ✅ | `ccae341`; `scripts/calibration_check.py` |
| 1C | Autovacuum tuning | ✅ | `f5f0982` + `schema/migrations/067_vacuum_tuning.sql` |
| 1D | WB resolution price override | ✅ | `0e42adb` + `0e48e9f` migration fix |
| 1E-a | market_aliases migration | ✅ | `d7cb89d` + `schema/migrations/069_market_aliases.sql` |
| 1E-b | order_gateway pre-trade | ✅ | `3039681` |
| 1G | prediction_log write fix | ✅ | `dfec250` |
| 1F | EB tracemalloc SIGUSR1 | ✅ | `3e8a40d` |
| 1I | Edge verification | ✅ | `10c7232`; `scripts/edge_verification.py` |
| 1J | Orderbook collection | ✅ | `0767d93` + `schema/migrations/070_orderbook_snapshots.sql` + `polymarket-orderbook.timer` active |
| 1K | Quick verifications | 🚫 | Plan line 182: "not a commit" |
| 1L | Shadow mode protocol doc | ✅ | `38b8547`; `docs/SHADOW_MODE_PROTOCOL.md` |
| 1M | Strategy lifecycle schema | ✅ | `d0fe765` + `schema/migrations/071_strategy_lifecycle.sql` |
| 1H | idle_in_transaction_session_timeout=5min | ✅ | `pg_settings.sourcefile=postgresql.auto.conf:21`, value 300000ms |

## PHASE RC — plan marker "COMPLETE"

| ID | Item | Status | Evidence |
|---|---|---|---|
| RC | 3 diagnostic scripts | ✅ | `scripts/rc_diagnostic.py`, `rc_verify.py`, `rc_temporal.py` |

## DAY 2 — plan marker "COMPLETE deploy 20260414_132211"

| ID | Item | Status | Evidence |
|---|---|---|---|
| D2-1 | $200 max position cap | ✅ | `0f1e2a8` |
| D2-2 | WB flat sizing $100 | ✅ | `0f1e2a8` |
| D2-3 | WB blacklist 6 cities | ✅ | `0f1e2a8` |
| D2-4 | MB block crypto | ✅ | `0f1e2a8` |
| D2-5 | MB blacklist 5 wallets | ✅ | `0f1e2a8` |
| D2-6 | MB whale ≥ $100 | ✅ | `0f1e2a8` |
| D2-7 | Kill EB v1 | ✅ | `.env.esports: BOT_ENABLED_ESPORTS=false` |

## PHASE 2 — state "~98%, 2I pending"

| ID | Item | Status | Evidence |
|---|---|---|---|
| 2A | wait_for verification | ✅ | `dc91627` + S166 bundle (`acaef0e`, `1726ce8`, `4f86e30`) |
| 2B | prune_old_data.py | ✅ | `54b28dc`, `0454aeb`; `scripts/prune_old_data.py` |
| 2C | Structlog dedup | ✅ | `a33510b` S177 bundle ("dedup") |
| 2D | logrotate + WatchedFileHandler | ✅ | `a33510b`; `deploy/logrotate.d/polymarket` |
| 2E | RTDS seen_set dedup | ❓ | `grep -n "seen_set\|_seen_trades\|rtds_dedup" bots/` returned empty. Status unclear — may be renamed or unshipped. Needs focused diff-read of a specific S# commit. |
| 2F | Health check kill switch | ✅ | **Filename drift corrected**: `deploy/dead_man_watchdog.sh` (kill-switch writer) + `deploy/healthcheck_probe.sh` (S180 tiered probe). Plan's `health_check.sh` reference updated. |
| 2G | Pool tightening | ✅ | `.env: DB_POOL_SIZE=8` matches plan "MB 10→8" |
| 2I | Illiquidity exit enablement | ⏳ | Code ready per S180 Corrections Log retraction; env flag flip pending |
| 2H-1,2 | Entry liquidity gate | ✅ | `8621979` |
| 2H-3 | Per-bot depth gate | ✅ | `b786316` |
| 2H-b | Shared-token mutex | ✅ | `6823cd7` |
| 2J | Slippage check refactor | ✅ | `scripts/slippage_check.py` present with S169 header |
| 2K | Feast feature store | 🚫 | Plan line 638: "SKIPPED per S179 decision" |

## PHASE 3 — updated this session

| ID | Item | Status | Evidence |
|---|---|---|---|
| P3-1 | effective_cache_size (was 12GB) | ✅ (superseded) | 24GB via `postgresql.auto.conf:5`; origin S152 PG tuning + VPS 32GB upgrade |
| P3-2 | PgBouncer idle_transaction_timeout | ⚠️ | Not in `pgbouncer.ini`; `server_idle_timeout=600` only (different semantics) |
| P3-3 | sshd hardening | 🔶 | PermitRootLogin=no ✅, PasswordAuth=no ✅, MaxAuth/AllowUsers absent |
| P3-4 | SSH port change | ⚠️ | Port 22 default, unchanged |
| P3-5 | autovacuum_naptime=15 | ⚠️ | `pg_settings.source='default'`, running 60s |

## PHASE 4 — hygiene (no phase-level marker)

| ID | Item | Status | Evidence |
|---|---|---|---|
| 4A | Archive handoff documents | ⭕ | `.gitignore:147` keeps them untracked instead of archiving |
| 4B | Archive orphan scripts | ⭕ | ~15 untracked `scripts/*.py` files in working tree |
| 4C | Improve .gitignore | 🔶 | Iteratively updated (S183/S184); no single 4C commit |
| 4D | Commit S170 tests (71 tests) | ❓ | No S170 commit match in master history keyword search |
| 4E | trade_journal nested session fix | 🔶 | File present at `base_engine/analysis/trade_journal.py`; line-level fix not verified this pass |

## PHASE 5v2 — A/B COMPLETE, C/D LIVE

| ID | Item | Status | Evidence |
|---|---|---|---|
| A1 | Kill EB v1 | ✅ | = D2-7 |
| A2 | Migration 072 | ✅ | `schema/migrations/072_esports_v2.sql` |
| A3 | Oracle's Elixir LoL loader | ✅ | `d4543cd` (88 tests in commit) |
| A4 | GRID + HLTV CS2 loader | ✅ | `95b1832` (43 tests) |
| A5 | Elo engine | ✅ | `d4543cd` |
| A6 | Glicko-2 engine | ✅ | `d4543cd` + migration 060 |
| A7 | OpenSkill engine | ✅ | `d4543cd`, `7fbe23a` |
| A8 | Trinity runner | ✅ | `d4543cd`, `cd5eead` |
| B1 | Walk-forward engine | ✅ | `e506072` (52 tests) |
| B2 | XGBoost meta-model | ✅ | `e506072`, `081f48b` |
| B3 | Venn-ABERS calibration | ✅ | `e506072`, `081f48b` |
| B4 | MAPIE conformal filter | ✅ | `e506072`, `081f48b` |
| B5 | CLV tracking (Pinnacle) | ✅ | `e506072`, `081f48b` |
| B6 | Metrics suite | ✅ | `e506072` |
| B7 | Full backtest CS2+LoL | ✅ | `95b1832`, `b954495` |
| C1 | Live data pipeline | 🔵 | `ca9fdae`, `a7d439d` |
| C2 | Market discovery | 🔵 | `ccbc811`, `dc0a16a` |
| C3 | Shadow prediction engine | 🔵 | `4ba0f79` |
| C4 | Live CLV tracking | 🔵 | via C1 |
| D1 | Wire to base_engine | 🔵 | `8cdcffb` BOT_REGISTRY |
| D2 | Sizing quarter-Kelly $100 | 🔵 | `4ba0f79` |
| D3 | prediction_log writes | 🔵 | `451a36c` |
| D4 | BOT_ENABLED=true SIM=true | 🔵 | Running on VPS |
| 5v2-E | Scan-cycle cost reduction | 🚫 | Plan line 886: deferred; `6e06dc4` |

## PHASE 6 — GATED ~2026-05-12

| ID | Item | Status |
|---|---|---|
| 6A reentry_check full fix | ⭕ (P0 pending per WB S167 handoff) |
| 6B, 6-STATION, 6C, 6D, 6E, 6F, 6G, 6H, 6I, 6J, 6K, 6L, 6M, 6N, 6P, 6Q | ⭕ |
| **6O lead-time backtest** | 🚫 **deferred indefinitely** per §S186 Corrections Log |

## PHASE 7 — 4/11 shipped

| ID | Item | Status | Evidence |
|---|---|---|---|
| 7A | Event-driven WS | 🔶 | `83544cd` ships instrumentation only, not full refactor |
| 7B | Wallet selection overhaul | ⭕ | Phase A inventory in `docs/7B_wallet_overhaul_design.md` |
| 7C | Leader-exit signals | ⭕ | gated on 7A |
| 7D | Basket consensus | ⭕ | gated on 7B |
| 7E | Gate_score expectancy | ✅ | `a3052a7`; `scripts/gate_score_expectancy.py` |
| 7F | Slippage-adj paper eval | ⭕ | — |
| 7G | Re-entry cooldown analytical | ✅ | `344f1e2`; `scripts/cooldown_analysis.py` |
| 7H | LLM signal | ⭕ | — |
| 7I | Hedge/MWU aggregation | ⭕ | gated on 7H+7B |
| 7J | ADWIN-U drift | ✅ | `3313874`; `base_engine/learning/prediction_drift.py` |
| 7K | Venn-ABERS MB | ✅ | `11fac16`; `base_engine/learning/venn_abers_intervals.py` |

## PHASE 8 — not started

| ID | Item | Status |
|---|---|---|
| 8A-8B, 8C-8J, 8L-8M, 8P, 8R | ⭕ 13 items |
| 8K remove EnsembleBot | ⭕ (ensemble_bot.py still on VPS) |
| 8N/8O/8Q | 🚫 REMOVED (plan line 489) |

## PHASE 10 / 12 / 13 — not started

| Phase | Items | Status |
|---|---|---|
| 10 | 10D, 10F | ⭕ |
| 12 | 12B-12F | ⭕ (gated on Phase 6) |
| 13 | 13A-13E | ⭕ (ongoing marker, no commits) |

## S186 immediate queue (session-scoped, inherited)

| Step | Item | Status |
|---|---|---|
| 3a | PSM port + contract tests | ⏳ `e19815e` on master, pending deploy |
| 3b | Bulk ACK historical OPEN rows | ⭕ requires auth + post-deploy verify |
| — | One-day audit verify at next 03:00 UTC (first run with guarded query will be **2026-04-22 03:00 UTC** — today's 03:00 fire used the pre-port query) | ⭕ |
| — | DUAL_SIDE_CONCURRENT diagnostic | ⭕ blocked on per-bot semantic decisions |
| — | 7B Phase A implementation | ⭕ parallelizable with above |

---

## Unverified items (4)

Each closable with focused work next session:

| ID | Gap | Next-step |
|---|---|---|
| 2E | RTDS seen_set dedup | 10 min: grep broader terms, check S167 `b9e2ae7` diff |
| P3-2 | PgBouncer idle_txn ambiguous in plan | Already clarified in plan text; status already resolved ⚠️ |
| 4D | S170 71-test commit | `git log --all --grep='S170'` |
| 4E | trade_journal nested session | Read `base_engine/analysis/trade_journal.py` for session handling pattern |

## Meta-findings

### 1. Hierarchical-infrastructure verification cluster (Protocol candidate)

Both initial D1/D3 verifier errors shared the same mechanism: a default query interface surfaces only the less-informative layer of a hierarchical structure. Distinct from Protocol 4c (projection lossiness — component dropping data it had). Filed as Protocol candidate "Hierarchical infrastructure verification" in §Protocol candidates.

### 2. Protocol 5 symmetry

Protocol 5 covers over-optimistic status claims (done → broken). S186b surfaced the symmetric case: over-pessimistic verifier claims (missing → applied). Both directions produce wasted work. Filed for next plan-hygiene round.

### 3. Infra-config-verification gap cluster

All four remaining real discrepancies (P3-2/3/4/5) share one root: config/infra items where "did the commit land" diverges from "is it actually in effect on VPS." No session verified Phase 3 end-to-end on VPS post-deploy. The Day-1-COMPLETE-marker-for-D1/D3 episode was a successful case of this being caught via Protocol 5 discipline; Phase 3 items never got the same end-to-end check.

### 4. Output-ratio pattern (informational)

Third consecutive session (S185, S186, S186b) where highest-value outputs are structural findings and plan corrections rather than code. Pattern-tracking observation; may indicate project maturation or a transient cluster.
