# EB Splinter Charter — `eb/main` Branch

**Created:** 2026-05-24 off master HEAD `3f015ea`
**Re-architected to autonomy:** 2026-05-24 (drop-in override design)
**Owner:** EB sessions (EsportsBot v1 + EsportsBotV2)
**Status:** Live, long-lived. EB is autonomous minus the VPS.

---

## Why this branch exists

Two prior deploy collisions (2026-05-15 EB→MB revert; 2026-05-18 S222 silent revert) corrupted MB work when an EB session deploy atomic-swapped the shared `/opt/polymarket-ai-v2` symlink and pinned MB+WB+ingestion at EB-released code. CLAUDE.md "SESSION PRIORITY — MIRRORBOT HAS ALL PRIORITIES" (hardcoded 2026-05-20) and RULE ONE-A (hardcoded 2026-05-22) restricted EB sessions from MB-touching actions, but the shared deploy infrastructure was still a collision surface.

Operator directive 2026-05-24: **EB splinters off entirely. EB is autonomous. The only shared resource is the VPS itself.**

This branch is the EB splinter. EB sessions work exclusively here.

---

## Autonomy boundary

EB owns everything in its scope. Master / MB / WB sessions own their own scopes. The VPS machine is the only physical sharing point. Within the VPS, EB carves out its own paths and infra to minimize friction with other bots.

### What EB owns (full autonomy)
- `eb/main` git branch — all code, tests, deploy scripts, configs
- `/opt/pa2-esports-releases/<stamp>/` — release directory on VPS
- `/opt/polymarket-ai-v2-esports` — symlink
- `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf` — drop-in override (decouples from master's main service file)
- `/opt/pa2-shared/.env.esports` — EB-specific env overrides
- `/opt/pa2-esports-shared/venv` — EB-owned Python venv (after the venv-split commit; see "Phase: separate venv" below)
- EB's own `alembic/` on `eb/main` — EB can write and apply migrations to EB-scoped tables (esports_matches, esports_predictions, esports_market_keywords, esports_team_aliases, etc.) via splinter deploy.sh

### What EB shares with other bots (by physical necessity)
- The VPS machine itself (one Lightsail instance)
- Postgres SERVER (one process) — but EB can manage its own tables on it
- PgBouncer connection pool
- `/opt/pa2-shared/data/`, `/opt/pa2-shared/saved_models/`, `/opt/pa2-shared/.env` (shared DB credentials etc.)
- File system

### What EB does NOT do
- Touch master's `deploy.sh`, `polymarket-esports.service` (main file on disk), or any other master file. The drop-in override + separate release path mean EB doesn't need to.
- Touch other bots' files (`bots/weather*`, `bots/mirror*`, etc.) or other bots' env files (`.env.weather`, `.env.mirror`, `.env.ingestion`).
- Touch other bots' shared maintenance (postgres backup crontab, shared systemd timers like polymarket-prune-prices, logrotate). EB has no business with them.
- Apply migrations that affect tables owned by other bots. If EB ever needs a shared-table schema change, escalate to operator (rare).

---

## Architecture

| Surface | Master (MB/WB/ingestion) | `eb/main` Splinter (EB) |
|---------|--------------------------|--------------------------|
| Git branch | `master` | `eb/main` |
| Release path | `/opt/pa2-releases/<stamp>` | `/opt/pa2-esports-releases/<stamp>` |
| Active symlink | `/opt/polymarket-ai-v2` | `/opt/polymarket-ai-v2-esports` |
| Systemd unit (main file) | Owned by master deploy | NOT touched by EB |
| Systemd unit (drop-in override) | N/A | `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf` (EB-owned; survives master clobber) |
| Systemd restart scope | weather + mirror + esports + ingestion | esports only |
| Database server | Shared (one Postgres on VPS) | Same shared Postgres |
| EB-scoped DB tables | — | EB writes migrations to splinter's alembic/ + applies via splinter deploy.sh (scope: EB tables only) |
| PgBouncer | Shared | Same |
| `/opt/pa2-shared/.env` | MB-owned, EB reads | Read-only from EB |
| `/opt/pa2-shared/.env.esports` | — | EB-owned |
| Python venv | `/opt/pa2-shared/venv` (shared) | `/opt/pa2-esports-shared/venv` (after venv-split commit) |
| Backup / logrotate / shared timers | MB-owned | EB doesn't install/touch |

---

## Service-file design (autonomy via drop-in override)

`polymarket-esports.service` (the main file at `/etc/systemd/system/`) is master-owned and gets re-installed on every master deploy. EB doesn't fight that — EB owns a DROP-IN OVERRIDE at `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf`. Standard systemd semantics:

1. systemd loads `polymarket-esports.service` (whatever master installed).
2. systemd loads all files in `polymarket-esports.service.d/` directory ON TOP of the main file.
3. Properties set in drop-in files override the main file.

The splinter override sets:
```ini
[Service]
WorkingDirectory=/opt/polymarket-ai-v2-esports
ExecStart=
ExecStart=/opt/polymarket-ai-v2-esports/venv/bin/python main.py
```

(The empty `ExecStart=` is systemd's required-clear-then-set pattern for list-valued options.)

Effect: EB runs from the splinter release regardless of what master's polymarket-esports.service says. Master's deploys can install/restart the service; on restart, the override applies and EB stays on the splinter.

**~5s of EB downtime per master deploy** (master's `systemctl stop polymarket-esports` then `start`). Acceptable — EB's scan cadence absorbs it.

---

## Cascade policy

Master shared-module updates (`base_engine/`, `paper_trading/`, `position_manager.py`, etc.) do **not** propagate to `eb/main` automatically. The splinter is frozen at clone time (2026-05-24 / master HEAD `3f015ea`) for shared modules. EB and master diverge over time.

EB can pull specific master fixes via operator-authorized cherry-pick when warranted. Or EB can fix bugs independently on its own branch. EB's choice — autonomous.

---

## EB session rules (binding)

1. **Always work on `eb/main`.** Verify with `cat .git/HEAD` before any commit (shared working tree means another session can switch branches under you).
2. **Never commit to `master`.** Master is not EB's.
3. **Never deploy from `master`.** Always from `eb/main`.
4. **Never touch other bots' code, env, handoffs, branches, or telemetry.** RULE ONE-A from CLAUDE.md remains in force as a courtesy to other bot sessions.
5. **Never run `bash deploy/deploy.sh` from a `master` checkout.** Always from `eb/main`.
6. **EB CAN propose and apply migrations** to EB-scoped tables (esports_matches, esports_predictions, etc.). Write to splinter's `alembic/versions/`. Splinter deploy.sh runs them with scope check that rejects non-EB-table references. (Wired when first needed.)
7. **EB CAN add Python dependencies** to its own venv after the venv-split commit. `pip install` on `/opt/pa2-esports-shared/venv` updates `requirements.txt` on `eb/main`. Doesn't affect master.
8. **Modify `.env.esports` freely.** EB-owned. Other `.env.*` files belong to other bots — don't touch.
9. **Module file paths kept identical to master at clone time.** Do NOT rename `base_engine/` → `base_engine_esports/`. The git branch is the isolation.
10. **Splinter rollback only rolls back EB.** Cannot rescue MB/WB/ingestion (they're not splinter's concern).

---

## Deploy semantics

`bash deploy/deploy.sh` on `eb/main` does:

1. Local preflight (syntax check, EB tests, bug-class pattern check).
2. Build tar archive (excludes `pa2-esports-releases/` and other release dirs).
3. Upload + extract to `/opt/pa2-esports-releases/<stamp>/`.
4. Symlink shared resources (`.env`, `data`, `saved_models`, `venv` → EB venv after venv-split) into release. Migrations skipped today; will be wired with scope check when EB first needs a migration.
5. Atomic-swap `/opt/polymarket-ai-v2-esports` → new release.
6. Install splinter drop-in override at `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf`. Restart `polymarket-esports` only. Verify override is effective via `systemctl show -p WorkingDirectory`. Defensive cross-check that MB/WB/ingestion stayed active.
7. EB-only health check via `deploy/healthcheck_probe.sh` (`BOT_SERVICES` / `SCAN_SERVICES` = `polymarket-esports` only). Auto-rollback on failure.
8. Prune old EB splinter releases (keep last 5). Does NOT touch `/opt/pa2-releases/`.

Rollback: `bash deploy/rollback.sh` swaps `/opt/polymarket-ai-v2-esports` back to the 2nd-most-recent splinter release and restarts `polymarket-esports` only.

---

## Things to monitor (not coordinate)

These are operational events to watch for, not things requiring cross-bot coordination:

### Shared venv pip-installs from master deploy (before venv-split)
Until the venv-split commit lands, EB shares `/opt/pa2-shared/venv` with master. If master runs `pip install foo==2.0`, EB also picks up foo==2.0. **Mitigation:** the venv-split commit decouples EB to `/opt/pa2-esports-shared/venv`. After that, master pip-installs don't affect EB.

### DB schema changes from master alembic
If master's deploy applies a migration that drops or alters a table EB reads (e.g., `prediction_log`), EB's frozen code may break. **Mitigation:** monitor `journalctl -u polymarket-esports` for `UndefinedColumnError` / `UndefinedTableError`. If hit, EB session adapts code on `eb/main` to handle the new schema (cherry-pick or write equivalent).

### Per-bot env at `/opt/pa2-shared/.env`
Master may change shared `.env` values (e.g., `DB_EFFECTIVE_POOL_SIZE`) that affect all bots including EB. EB observes and adapts; doesn't try to revert.

### Shared working tree (mitigated by dedicated worktree — see next section)
The main repo dir `C:/lockes-picks/polymarket-ai-v2/` is used concurrently by WB and MB sessions checking out different branches. Before the worktree silo was set up, the `HEAD` pointer in the main dir could change without notice when another session did `git checkout`. **Mitigation: EB session works exclusively in its own dedicated worktree.** See next section.

---

## Worktree silo (autonomy from shared-tree interference)

EB session works EXCLUSIVELY in a dedicated git worktree at:

```
C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/
```

This worktree is locked to `eb/main` (git enforces one-branch-per-worktree). Other sessions checking out branches in the main dir or other worktrees cannot affect this worktree's HEAD pointer. The worktree is gitignored at the `.claude/` parent level (line 47 of `.gitignore`).

### Per-session entry protocol (binding)

```bash
# 1. cd into the EB worktree (NEVER work from the main repo dir)
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main

# 2. Verify HEAD is on eb/main (this should always be true; if not, abort)
cat .git/HEAD   # must print "ref: refs/heads/eb/main"

# 3. Verify worktree integrity
git worktree list | grep "eb-main.*eb/main"   # must find a match

# 4. All subsequent git, file edit, deploy, test commands run here
```

### What this silo prevents

- Another session's `git checkout other-branch` in the main dir does NOT change EB's HEAD (EB's worktree has its own HEAD).
- Another session's `git commit` to a different branch in their own worktree is invisible to EB until EB pulls (which it never auto-does).
- Another session's `git stash pop` or working-tree edits in their dir cannot land in EB's working tree.

### What this silo does NOT prevent

- Shared `.git` objects directory. Branch refs are global. If another session deletes `eb/main` ref (with `--force`), EB loses its branch (commits remain reachable via reflog). Operator-authorized only.
- Shared VPS — the `polymarket-esports` systemd unit and `/opt/polymarket-ai-v2-esports` symlink are shared infrastructure. EB owns them by convention; physical sharing is unavoidable.
- Concurrent EB sessions in the same worktree — only one EB session at a time per worktree. If multiple EB sessions need to work in parallel, each creates a sub-worktree off `eb/main` (e.g., `eb/feature-X` branches) and works there.

### Worktree maintenance

```bash
# List all worktrees (run from anywhere in the repo)
git worktree list

# Recreate the EB worktree if it was accidentally removed
cd C:/lockes-picks/polymarket-ai-v2   # main dir
git worktree add .claude/worktrees/eb-main eb/main

# Remove the EB worktree (only if rescinding the splinter)
git worktree remove .claude/worktrees/eb-main
```

---

## Branching from `eb/main`

Sub-branches OK for in-progress EB work — `eb/<feature-name>`. Merge back to `eb/main` when ready. Do NOT branch off `master` for EB work.

---

## Rescission

This splinter can be retired only by explicit operator directive ("retire `eb/main` splinter" / "merge `eb/main` back to master" / "EB rejoins shared deploy"). On rescission:

1. Operator-authorized merge of `eb/main` → `master` (or operator decides to discard splinter divergence).
2. Operator-authorized restoration of master `polymarket-esports.service` install loop (currently splinter is decoupled via drop-in, but if rescinded, EB might rejoin shared install).
3. Operator-authorized removal of drop-in override at `/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf`.
4. Operator-authorized cleanup of `/opt/pa2-esports-releases/`, `/opt/polymarket-ai-v2-esports`, `/opt/pa2-esports-shared/venv`.
5. Remove this file (`EB-SPLINTER.md`).

---

## Quick-reference for next EB session

```bash
# ALWAYS start in the EB worktree silo — never the main repo dir
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main

# Verify worktree integrity before any work
cat .git/HEAD                                              # must print "ref: refs/heads/eb/main"
git worktree list | grep "eb-main.*eb/main"                # must find a match
git rev-parse --show-toplevel                              # must print the worktree path

# Run EB tests
PYTHONPATH=. python -m pytest tests/unit/test_esports_bot_v2.py \
       tests/unit/test_esports_markets_refresh_v2.py \
       tests/unit/test_pipeline.py \
       tests/unit/test_scanner_contract.py \
       tests/unit/test_esports_team_alias_matcher.py

# Deploy splinter to VPS (from worktree)
bash deploy/deploy.sh

# Rollback splinter
bash deploy/rollback.sh

# Verify splinter state on VPS
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
ssh -i "$KEY" ubuntu@18.201.216.0 "
readlink /opt/polymarket-ai-v2-esports
readlink /opt/pa2-esports-shared/venv 2>&1 | head -1; ls -d /opt/pa2-esports-shared/venv
systemctl show polymarket-esports -p MainPID,ActiveState,WorkingDirectory
systemctl is-active polymarket-weather polymarket-mirror polymarket-ingestion
cat /etc/systemd/system/polymarket-esports.service.d/00-splinter.conf"
```
