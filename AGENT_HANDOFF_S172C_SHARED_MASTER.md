# S172C SHARED MASTER HANDOFF — Phase 1 COMPLETE

**Session:** 172C (continuation of S172B)
**Date:** 2026-04-13
**Scope:** ALL BOTS — S172 Phase 1 completion
**Deploy:** `20260413_172523` on Ubuntu-32 (18.201.216.0) — VERIFIED. Migrations 070+071 applied. Orderbook timer active.
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

**DEPLOYED as `20260413_172523`.** deploy.sh landed the release before health check timed out (SSH locked by fail2ban during check). Verified post-ban: symlink correct, all services active, 0 InFailedSQLTransaction.

**Post-deploy additions:**
- Migrations 070 + 071 applied (6 new tables created)
- Orderbook collector systemd timer active (every 60s, 18/20 tokens on test run)
- fail2ban maxretry raised 3→10 to prevent deploy lockouts
- Phase RC root-cause plan drafted: `S172_PHASE_RC_ROOT_CAUSE_PLAN.md`

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

## PHASE RC — ROOT-CAUSE INVESTIGATION

Full plan: `S172_PHASE_RC_ROOT_CAUSE_PLAN.md`

**Summary:** 6-layer diagnostic per bot (fees → magnitude asymmetry → entry edge → exit timing → segment decomposition → temporal analysis). 2-3 weeks. Runs parallel to Phase 2.

**Priority order:**
1. WB first — 59.3% WR [UNVERIFIED] + negative edge is the most diagnostic puzzle
2. MB second — largest dataset (9,519 trades [UNVERIFIED]), best for segmentation
3. EB third — smallest sample, game-level breakdown

**Decision gates:** Fix what's fixable, kill what's not. Re-run 1I on post-fix data.

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

1. **fail2ban — PARTIALLY FIXED.** maxretry raised 3→10. Still needs deploy IP whitelist if static IP available. ControlMaster in deploy.sh would help.
2. **Migration ownership** — ALTER TABLE requires postgres user. 070-071 were applied manually. Systemic issue persists for future migrations.
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
