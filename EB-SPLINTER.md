# EB Splinter Charter — `eb/main` Branch

**Created:** 2026-05-24 off master HEAD `3f015ea`
**Owner:** EB sessions (EsportsBot v1 + EsportsBotV2)
**Progenitor:** MirrorBot (MB) — master remains MB's canonical domain
**Status:** Live, long-lived. No expiration; no auto-merge to master.

---

## Why this branch exists

Two prior deploy collisions (2026-05-15 EB→MB revert; 2026-05-18 S222 silent revert) corrupted MB work when an EB session deploy atomic-swapped the shared `/opt/polymarket-ai-v2` symlink and pinned MB+WB+ingestion at EB-released code. CLAUDE.md "SESSION PRIORITY — MIRRORBOT HAS ALL PRIORITIES" (hardcoded 2026-05-20) and RULE ONE-A (hardcoded 2026-05-22) restricted EB sessions from MB-touching actions, but the shared deploy infrastructure was still a collision surface.

Operator directive 2026-05-24: **EB splinters off entirely. Clone all shared engines. Never touch another bot's work again. MB is the progenitor.**

This branch is the EB splinter. EB sessions work exclusively here. MB sessions work exclusively on master.

---

## Architecture

| Surface | Master (MB/WB/ingestion) | `eb/main` Splinter (EB) |
|---------|--------------------------|--------------------------|
| Git branch | `master` | `eb/main` |
| Release path | `/opt/pa2-releases/<stamp>` | `/opt/pa2-esports-releases/<stamp>` |
| Active symlink | `/opt/polymarket-ai-v2` | `/opt/polymarket-ai-v2-esports` |
| Systemd units installed | weather, mirror, esports*, ingestion | esports |
| Restart loop | weather + mirror + esports + ingestion | esports only |
| Migrations | Applied | Skipped (MB is schema-canonical) |
| Shared timers (prune/audit/backup) | Installed | Skipped (MB-owned) |
| Logrotate | Installed | Skipped (MB-owned) |
| Database | Shared (one Postgres, disambiguated by `bot_name`) | Same shared DB |
| PgBouncer | Shared | Same |
| `/opt/pa2-shared/.env` | MB-owned | Read-only from EB perspective |
| `/opt/pa2-shared/.env.esports` | — | EB-owned |
| Module file paths (`base_engine/`, `risk_manager.py`, etc.) | MB updates here | Frozen at clone time; identical paths to master at clone time |

\* `polymarket-esports.service`: master's version points at `/opt/polymarket-ai-v2`. Splinter's version (this branch) points at `/opt/polymarket-ai-v2-esports`. See "Cross-bot coordination concerns" below.

---

## Cascade policy: NEVER

Master shared-module updates (`base_engine/`, `paper_trading/`, `position_manager.py`, `database.py`, `BotBankrollManager`, `risk_manager`, `base_bot.py`, `prediction_engine.py`) **do not propagate to `eb/main`**. The splinter is frozen at clone time (2026-05-24 / master HEAD `3f015ea`) for shared modules. EB diverges as it sees fit; master diverges as MB sees fit.

**Implication:** If MB fixes a bug in `base_engine/data/database.py` next week, EB's splinter still runs the 2026-05-24 version of that file. EB sessions running into the same bug must fix it independently on `eb/main` (or surface to operator for explicit cherry-pick authorization).

**No automatic sync mechanism exists.** No cron job, no merge hook, no scheduled rebase. Drift is the steady state.

---

## EB session rules (binding)

1. **Always work on `eb/main`.** Verify with `git branch --show-current` before any commit.
2. **Never commit to `master`.** Master is MB's domain.
3. **Never deploy from `master`.** Master deploys are MB session's responsibility.
4. **Never touch MB code, env, handoffs, branches, or telemetry.** RULE ONE-A from CLAUDE.md remains in force.
5. **Never run `bash deploy/deploy.sh` from a `master` checkout.** Always from `eb/main`.
6. **Never propose database schema migrations.** If EB needs a schema change, surface to MB session for canonical migration on master. The splinter's `alembic/` is frozen reference.
7. **Never add a Python dependency.** Splinter uses the shared `/opt/pa2-shared/venv`. New deps require MB-session-applied `pip install` on the shared venv. Surface to MB.
8. **Modify `.env.esports` freely.** It's EB-owned per-bot env override. `.env` (shared) and other `.env.*` are off-limits.
9. **Module file paths kept identical to master at clone time.** Do NOT rename `base_engine/` → `base_engine_esports/` or similar. The git branch is the isolation.
10. **Splinter rollback only rolls back EB.** Cannot rescue MB/WB/ingestion.

---

## Deploy semantics

`bash deploy/deploy.sh` on `eb/main` does:

1. Local preflight (syntax check, EB tests, bug-class pattern check).
2. Build tar archive (excludes `pa2-esports-releases/` along with other release dirs).
3. Upload + extract to `/opt/pa2-esports-releases/<stamp>/`.
4. Symlink shared resources (`.env`, `data`, `saved_models`, `venv`) into release. **Migrations skipped.**
5. Atomic-swap `/opt/polymarket-ai-v2-esports` → new release.
6. Install splinter `polymarket-esports.service` to `/etc/systemd/system/` (overwriting master's version on disk). Restart `polymarket-esports` only. Defensive cross-check that MB/WB/ingestion stayed active.
7. EB-only health check via `deploy/healthcheck_probe.sh` (splinter version probes `polymarket-esports` only). Auto-rollback on failure.
8. Prune old EB splinter releases (keep last 5). Does NOT touch `/opt/pa2-releases/`.

Rollback: `bash deploy/rollback.sh` swaps `/opt/polymarket-ai-v2-esports` back to the 2nd-most-recent splinter release and restarts `polymarket-esports` only.

---

## Cross-bot coordination concerns

### Concern 1: MB session master-deploy clobbers splinter service file

Master's `deploy/deploy.sh` (on `master` branch) still installs `polymarket-esports.service` from master's copy (which points at `/opt/polymarket-ai-v2`). Every MB session deploy will:

- Copy master's `polymarket-esports.service` to `/etc/systemd/system/` — **overwriting** splinter's version.
- Restart `polymarket-esports` — which now picks up master's service file → points at `/opt/polymarket-ai-v2` → **runs MB-canonical EB code, not splinter EB code**.

Splinter remains intact at `/opt/pa2-esports-releases/<latest>/`, but the live `polymarket-esports.service` now bypasses it.

**Mitigation (operator-coordinated):**
- Each MB session deploy that disrupts splinter triggers a follow-up EB session deploy to restore splinter (manual re-run of `deploy.sh` on `eb/main`).
- OR an MB session surgically removes `polymarket-esports.service` from master `deploy.sh`'s install loop. That's a one-time master-side change requiring MB-session ownership. **EB cannot perform this fix** (would touch master). Flag as MB-session follow-up.

### Concern 2: Shared venv pip-install during MB deploy

MB session may run `pip install -r requirements.txt` on the shared `/opt/pa2-shared/venv` during a master deploy if `requirements.txt` changed. That mutates the shared venv that splinter EB also uses. If splinter EB's frozen code is incompatible with the new deps, splinter EB breaks silently.

**Mitigation:** Splinter EB session runs `pip check` post-MB-deploy to detect incompatibilities. Surface failures to operator.

### Concern 3: Database schema drift

MB session applies new migrations on master deploys. The shared DB schema moves forward. Splinter EB's frozen code may reference removed/renamed columns.

**Mitigation:** EB session monitors for `UndefinedColumnError` / `UndefinedTableError` in `journalctl -u polymarket-esports`. Surface to operator + cherry-pick the schema-handling code from master if needed (operator-authorized).

### Concern 4: Per-bot env at `/opt/pa2-shared/.env`

The shared `.env` is MB-owned. MB session may change values (e.g., `DB_EFFECTIVE_POOL_SIZE`) that affect all bots including EB. Splinter EB cannot prevent this; just monitors and surfaces issues.

---

## Phase 2: One-time VPS infra (operator-authorized, not yet executed)

**Pending operator authorization to perform.** Required before first splinter deploy can succeed:

1. **Bootstrap the splinter symlink:** First splinter deploy creates `/opt/polymarket-ai-v2-esports` via atomic-swap. No pre-create needed. (`mv -T` creates if absent.)
2. **Splinter service file is installed by splinter deploy itself.** No manual systemd unit edit needed if splinter deploy runs successfully.

Practically: the first `bash deploy/deploy.sh` on `eb/main` self-bootstraps. The only operator action needed is **authorize the first splinter deploy** (per RULE ONE-A, EB sessions need explicit operator OK for any deploy).

---

## Branching from `eb/main`

Sub-branches OK for in-progress EB work — `eb/<feature-name>`. Merge back to `eb/main` when ready. Do NOT branch off `master` for EB work; always off `eb/main`.

---

## Rescission

This splinter can be retired only by explicit operator directive ("retire eb/main splinter" / "merge eb/main back to master" / "EB rejoins shared deploy"). On rescission:

1. Operator-authorized merge of `eb/main` → `master` (or operator decides to discard splinter divergence).
2. Operator-authorized restoration of master `polymarket-esports.service` to point at `/opt/polymarket-ai-v2`.
3. Operator-authorized cleanup of `/opt/pa2-esports-releases/` + `/opt/polymarket-ai-v2-esports` symlink.
4. Remove this file (`EB-SPLINTER.md`).
5. Log rescission date in MB session memory.

---

## Quick-reference for next EB session

```bash
# Verify you're on eb/main before any work
git branch --show-current   # must be "eb/main"

# Pull latest splinter state (if multiple EB sessions exist)
git pull origin eb/main 2>/dev/null || echo "no remote configured"

# Run EB tests
pytest tests/unit/test_esports_bot_v2.py \
       tests/unit/test_esports_markets_refresh_v2.py \
       tests/unit/test_pipeline.py \
       tests/unit/test_scanner_contract.py \
       tests/unit/test_esports_team_alias_matcher.py

# Deploy splinter to VPS (operator-authorized)
bash deploy/deploy.sh

# Rollback splinter
bash deploy/rollback.sh

# Verify splinter state on VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 \
    "readlink /opt/polymarket-ai-v2-esports; \
     systemctl show polymarket-esports -p MainPID,ActiveState,FragmentPath; \
     systemctl is-active polymarket-weather polymarket-mirror polymarket-ingestion"
```
