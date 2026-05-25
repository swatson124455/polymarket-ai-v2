# EB Session Handoff — 2026-05-25

**Date:** 2026-05-24 → 2026-05-25 (multi-day EB-scoped session)
**Branch:** `eb/main` (HEAD `96e32f8` + handoff commit)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**VPS active release:** `/opt/pa2-esports-releases/20260524_162245` (splinter)
**Status at close:** Splinter architecture live. Bug A2 deployed. **Bot NOT trading — blocked by VPS infrastructure (45% CPU steal + DB pool exhaustion). Not within EB-session scope to fix.**

---

## §1 — What landed this session (11 commits on `eb/main`)

```
96e32f8 docs(silo): document EB worktree silo at .claude/worktrees/eb-main/
eb9217b chore(silo): revert .eb-worktree/ gitignore — use .claude/worktrees convention
fb81acd chore(silo): gitignore .eb-worktree/ (superseded by eb9217b)
ecc5d0c fix(splinter): EB-owned venv at /opt/pa2-esports-shared/venv
3f05ff6 docs(splinter): rewrite charter for autonomous design + drop superseded files
e0247d1 fix(splinter): systemd drop-in override — autonomy from master clobber
278ffc1 docs(splinter): MB-session coordination request (deleted in 3f05ff6)
41ffdde fix(eb-v2): Bug A2 — side selection by edge direction, not p_model > 0.5
2eb264f fix(test): scope windowing guard to git-tracked files only
ac535db docs(splinter): EB-SPLINTER.md charter (original)
d382e5b feat(splinter): EB deploy/rollback/healthcheck scoped to esports-only
```

### Architecture milestones (all live on VPS)
- **Branch `eb/main`** created off master `3f015ea`
- **Splinter release path** `/opt/pa2-esports-releases/<stamp>` (separate from master's `/opt/pa2-releases/`)
- **Splinter symlink** `/opt/polymarket-ai-v2-esports` (separate from `/opt/polymarket-ai-v2`)
- **systemd drop-in override** at `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf` (survives master's main-service-file clobber)
- **EB-owned venv** at `/opt/pa2-esports-shared/venv` (8.9G, copied from master + shebang-rewritten; decoupled from master pip-installs)
- **EB worktree silo** at `.claude/worktrees/eb-main/` (immune to shared-tree branch hijacking by other sessions)

### Code fix
- **Bug A2** (commit `41ffdde`): EB v2 side selection now uses `p_model > market_price` (edge direction) instead of `p_model > 0.5` (model lean). Pre-fix: 0 `esports_v2_trade_attempt` logs across 5 days because matched markets had wrong-side Kelly = 0 → silent stake=0 → continue gate. Three regression tests added in `tests/unit/test_pipeline.py`.

---

## §2 — Why the bot isn't trading right now

**Root cause: VPS-level infrastructure crisis.** NOT an EB code issue.

### Observed during this session (2026-05-25 13:47–18:11 UTC)
- **CPU steal:** measured at **45.02%** at 14:00, **35.51%** at 18:10. AWS hypervisor noisy-neighbor is depriving the VM of ~40% of allocated CPU cycles.
- **Load avg:** 6.20–6.35 on 8 vCPUs with that steal level → effectively saturated.
- **DB pool exhaustion:** EB local pool went semaphore_available=1 / checked_out=15 / total=17 at 13:57 during cold-start.
- **DB connection errors observed:**
  - `asyncpg.exceptions.ProtocolViolationError: client_login_timeout` (handshake timeouts)
  - `ConnectionDoesNotExistError: connection was closed in the middle of operation`
  - `DB semaphore timeout — all slots occupied for 15s`
- **Polymarket WebSocket latency:** 112,166ms signal_ms on one market (112 seconds). WS handshake timeout traceback at 13:48.
- **HealthScheduler jobs skipped/missed** because event loop too busy.
- **Result:** 0 `esports_v2_scan_funnel`, 0 `esports_v2_trade_attempt`, 0 `esportsbot_trade_attempt` (v1) in 4.5h post-restart.

### Bug A2 effectiveness: cannot verify
The Bug A2 fix code is deployed (verified via `grep p_model > market_price /opt/polymarket-ai-v2-esports/esports_v2/model/pipeline.py`), but EB v2 has not completed a single full scan post-Bug-A2-fix deploy to exercise it. Verification awaits VPS recovery.

---

## §3 — EB-scope mitigations applied this session (last tune at 18:11 UTC)

### Tune 1: `.env.esports` DB timeout tightening (2026-05-25 18:11 UTC)
Edited `/opt/pa2-shared/.env.esports` on VPS:
- `DB_STATEMENT_TIMEOUT_MS=30000` → `15000` (fail queries faster, free pool slots sooner)
- `DB_IDLE_IN_TXN_TIMEOUT_MS=120000` → `30000` (kill stuck idle-in-transaction conns sooner)

Backup at `/opt/pa2-shared/.env.esports.bak.20260525_eb-tune`. Restart pickup PID 79737 at 18:11 UTC.

**Expected effect:** when VPS steal drops, EB will recover faster (less time holding stale connections). Doesn't fix VPS-level cause.

**Rollback:** `sudo cp /opt/pa2-shared/.env.esports.bak.20260525_eb-tune /opt/pa2-shared/.env.esports && sudo systemctl restart polymarket-esports`

---

## §4 — Outside EB scope (escalation if needed)

These are infrastructure-layer issues EB session cannot address:

1. **45% CPU steal** — AWS hypervisor / noisy neighbor. Mitigation paths: wait it out (transient), open AWS support ticket for noisy-neighbor remediation, migrate to a different Lightsail instance, or upsize the plan.
2. **Shared PgBouncer pool** — `default_pool_size=60` per `/etc/pgbouncer/pgbouncer.ini`. Tuning is master/operator scope.
3. **Shared Postgres server load** — all 4 systemd services + ingestion + orderbook_collector compete on one Postgres instance.
4. **Other services' state** (mirror/weather/ingestion) — out of EB scope per autonomous-silo charter.

---

## §5 — Next EB session entry protocol

```bash
# Always start in the worktree silo
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
cat .git                                    # gitdir: .../worktrees/eb-main
git rev-parse --abbrev-ref HEAD             # must print: eb/main
git worktree list | grep "eb-main.*eb/main" # must match

# Verify VPS recovery before any tuning/code work
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "mpstat 1 1 | tail -2"
# If %steal < 10%, VPS is healthy → proceed
# If %steal > 25%, VPS is still degraded → defer trading-related work

# Check whether the bot started producing trade_attempts since the last handoff
ssh -i "$KEY" ubuntu@18.201.216.0 "
SINCE='2026-05-25 18:11:00'  # PID 79737 start time
echo 'cumulative since 18:11 UTC restart:'
printf 'scan_funnel:     %s\n' \$(sudo journalctl --since \"\$SINCE\" -u polymarket-esports --no-pager 2>&1 | grep -c 'esports_v2_scan_funnel')
printf 'trade_attempt:   %s\n' \$(sudo journalctl --since \"\$SINCE\" -u polymarket-esports --no-pager 2>&1 | grep -c 'esports_v2_trade_attempt')
printf 'trade_rejected:  %s\n' \$(sudo journalctl --since \"\$SINCE\" -u polymarket-esports --no-pager 2>&1 | grep -c 'esports_v2_trade_rejected')
"

# Canonical P&L (only after observation window)
ssh -i "$KEY" ubuntu@18.201.216.0 "cd /opt/polymarket-ai-v2-esports && PYTHONPATH=/opt/polymarket-ai-v2-esports ./venv/bin/python scripts/bot_pnl.py EsportsBotV2 24"
```

---

## §6 — Carry-forward decisions

| Item | State | Trigger / next action |
|------|-------|------------------------|
| Bug A2 fix verified live | NOT YET | Need ≥50 scan cycles with at least some matched > 0 to observe `esports_v2_trade_attempt > 0`. Blocked on VPS recovery. |
| `BOT_ENABLED_ESPORTS_LIVE` flag | Still `false` per .env.esports | Operator decision; not actioned this session. |
| 1-week post-trade-flip review for EB v2 | Blocked on trade activity | Currently 0 trades → no data. Re-evaluate once Bug A2 fix produces trades. |
| EB v1 settling | Was healthy pre-restart (17 entries in 120h per bot_pnl.py at session start), stalled with EB v2 today | Re-check post-VPS-recovery. |
| `.env.esports` DB-timeout tune | LIVE since 18:11 UTC | Observe if EB recovers faster on next VPS hiccup; revert if it causes new errors. |
| Cross_game_xgb model 38+ days stale | Carried forward from S223 | Defer until trading resumes; not blocking. |
| EB v1 cold-start retrain (CoD/RL/SC2/R6) | Carried forward from S223 | Was triggered on 2026-05-19 restart per logs; not re-checked this session. |

---

## §7 — Splinter is autonomous now (per operator directive 2026-05-24)

The MB priority rules from CLAUDE.md no longer apply to EB. EB owns its splinter end-to-end. Next session:

- Work exclusively on `eb/main` in the worktree silo
- Do not surface coordination requests to MB session for things EB can do itself
- Schema changes, dep changes, deploy infra changes — all EB-autonomous
- Shared VPS is the only physical constraint

See [EB-SPLINTER.md](EB-SPLINTER.md) for the full charter (revised in `3f05ff6`).
