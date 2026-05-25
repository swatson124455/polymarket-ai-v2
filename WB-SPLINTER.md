# WB Splinter Charter — `wb/main` Branch

**Created:** 2026-05-24 off master `b18047c` (pre-MB-merge `816e715`)
**Owner:** WB sessions (WeatherBot)
**Status:** Live, long-lived. WB is autonomous minus the VPS.

---

## Why this branch exists

Prior deploy collisions repeatedly corrupted in-flight work when a non-MB session deploy atomic-swapped the shared `/opt/polymarket-ai-v2` symlink and pinned all bots at the new release. EB splintered off first (`eb/main`, 2026-05-24) — WB follows the same pattern.

Today's trigger: WB session merged A.1+A.2 fixes to master at `b18047c`, then an MB session merged `816e715` (V2 wiring, CLOB failure surface, capital/balance guards) on top, introducing failures in `tests/unit/test_startup_hold_wiring.py`. WB's deploy preflight ran the full `tests/unit/` suite against the merged master state and aborted on MB's broken tests. WB's shippable code was blocked by infrastructure WB had nothing to do with.

**Operator directive: WB splinters off entirely. WB is autonomous. The only shared resource is the VPS itself.**

---

## Autonomy boundary

WB owns everything in its scope. Master / MB / EB sessions own their own scopes. The VPS machine is the only physical sharing point. Within the VPS, WB carves out its own paths and infra to minimize friction with other bots.

### What WB owns (full autonomy)
- `wb/main` git branch — all WB-touching code, tests, deploy scripts, configs
- `/opt/pa2-weather-releases/<stamp>/` — release directory on VPS
- `/opt/polymarket-ai-v2-weather` — symlink
- `/etc/systemd/system/polymarket-weather.service.d/00-splinter.conf` — drop-in override (decouples from master's main service file)
- `/opt/pa2-shared/.env.weather` — WB-specific env overrides
- WB's silo'd code at `bots/weather/engine/` (cloned base_engine + config + base_bot, S227)
- `bots/weather_bot.py` and WB-specific tests

### What WB shares with other bots (by physical necessity)
- The VPS machine itself (one Lightsail instance)
- Postgres SERVER (one process) — but WB does not propose schema changes
- PgBouncer connection pool
- `/opt/pa2-shared/data/`, `/opt/pa2-shared/saved_models/`, `/opt/pa2-shared/.env` (shared DB credentials etc.)
- `/opt/pa2-shared/venv` — TODAY shared with master. Future commit can decouple to `/opt/pa2-weather-shared/venv` mirroring EB's pattern if pip-install conflicts arise.
- File system

### What WB does NOT do
- Touch master's `deploy.sh`, `polymarket-weather.service` (main file on disk), or any other master file. The drop-in override + separate release path mean WB doesn't need to.
- Touch other bots' files (`bots/mirror*`, `bots/esports*`, etc.) or other bots' env files (`.env.mirror`, `.env.esports`, `.env.ingestion`).
- Touch other bots' shared maintenance (postgres backup crontab, shared systemd timers like polymarket-prune-prices, logrotate).
- Apply migrations. If WB ever needs a schema change, escalate to operator → MB session.

---

## Architecture

| Surface | Master (MB/ingestion) | `eb/main` Splinter | `wb/main` Splinter |
|---|---|---|---|
| Git branch | `master` | `eb/main` | `wb/main` |
| Release path | `/opt/pa2-releases/<stamp>` | `/opt/pa2-esports-releases/<stamp>` | `/opt/pa2-weather-releases/<stamp>` |
| Active symlink | `/opt/polymarket-ai-v2` | `/opt/polymarket-ai-v2-esports` | `/opt/polymarket-ai-v2-weather` |
| Systemd unit (main file) | Owned by master deploy | Not touched | Not touched |
| Systemd unit (drop-in override) | N/A | `polymarket-esports.service.d/00-splinter.conf` | `polymarket-weather.service.d/00-splinter.conf` |
| Systemd restart scope | mirror + ingestion | esports only | weather only |
| Database server | Shared | Shared | Shared (read-only schema) |
| PgBouncer | Shared | Shared | Shared |
| `.env` | MB-owned | EB reads | WB reads |
| `.env.weather` | — | — | WB-owned |
| Python venv | `/opt/pa2-shared/venv` (shared) | `/opt/pa2-esports-shared/venv` (post-split) | `/opt/pa2-shared/venv` (shared today; can split later) |
| Backup / logrotate / shared timers | MB-owned | Not touched | Not touched |

---

## Service-file design (autonomy via drop-in override)

`polymarket-weather.service` (the main file at `/etc/systemd/system/`) is master-owned and gets re-installed on every master deploy. WB doesn't fight that — WB owns a DROP-IN OVERRIDE at `/etc/systemd/system/polymarket-weather.service.d/00-splinter.conf`. Standard systemd semantics:

1. systemd loads `polymarket-weather.service` (whatever master installed).
2. systemd loads all files in `polymarket-weather.service.d/` directory ON TOP of the main file.
3. Properties set in drop-in files override the main file.

The splinter override sets:
```ini
[Service]
WorkingDirectory=/opt/polymarket-ai-v2-weather
ExecStart=
ExecStart=/opt/polymarket-ai-v2-weather/venv/bin/python main.py
```

(The empty `ExecStart=` is systemd's required-clear-then-set pattern for list-valued options.)

Effect: WB runs from the splinter release regardless of what master's polymarket-weather.service says. Master's deploys can install/restart the service; on restart, the override applies and WB stays on the splinter.

**~5s of WB downtime per master deploy** (master's `systemctl stop polymarket-weather` then `start`). Acceptable — WB's scan cadence absorbs it.

---

## Cascade policy

Master shared-module updates (`base_engine/`, `paper_trading/`, etc.) do **not** propagate to `wb/main` automatically. WB has its own silo at `bots/weather/engine/` (cloned S227 commit `eaa0b7f`) so master-side `base_engine/` updates don't reach WB at runtime anyway. The splinter is frozen at clone time (2026-05-24 / master `b18047c`) for shared modules.

WB can pull specific master fixes via operator-authorized cherry-pick when warranted. Or WB can fix bugs independently on its own silo. WB's choice — autonomous.

---

## WB session rules (binding)

1. **Always work on `wb/main`** in the dedicated worktree at `.claude/worktrees/wb-main/`. Verify with `cat .git/HEAD` before any commit — shared `.git` means another session can't switch this worktree's HEAD, but verify anyway.
2. **Never commit to `master`.** Master is not WB's.
3. **Never deploy from `master`.** Always from `wb/main`.
4. **Never touch other bots' code, env, handoffs, branches, or telemetry.** RULE ONE-A from CLAUDE.md remains in force as a courtesy.
5. **Never run `bash deploy/deploy.sh` from a `master` checkout.** Always from `wb/main`.
6. **Modify `.env.weather` freely.** WB-owned. Other `.env.*` files belong to other bots — don't touch.
7. **Module file paths kept identical to master at clone time.** WB's silo is at `bots/weather/engine/`; do not rename. The git branch + silo are the isolation.
8. **Splinter rollback only rolls back WB.** Cannot rescue MB/EB/ingestion.
9. **WB does NOT propose migrations.** If WB ever needs a schema change, escalate to operator → MB session.

---

## Deploy semantics

`bash deploy/deploy.sh` on `wb/main` does:

1. Local preflight: syntax check + WB-scoped pytest (`test_weather*` + `test_paper_fill_probability_silo`) + bug-class pattern check on WB-relevant paths. **Does NOT run the full `tests/unit/` suite** — MB-side failures should not block WB.
2. Build tar archive (excludes `pa2-weather-releases/` and other release dirs).
3. Upload + extract to `/opt/pa2-weather-releases/<stamp>/`.
4. Symlink shared resources (`.env`, `data`, `saved_models`, `venv` → `/opt/pa2-shared/*`). Migrations skipped.
5. Atomic-swap `/opt/polymarket-ai-v2-weather` → new release.
6. Install splinter drop-in override at `/etc/systemd/system/polymarket-weather.service.d/00-splinter.conf`. Restart `polymarket-weather` only. Verify override is effective via `systemctl show -p WorkingDirectory`. Defensive cross-check that MB/EB/ingestion stayed active.
7. WB-only health check via `deploy/healthcheck_probe.sh` (`BOT_SERVICES` / `SCAN_SERVICES` = `polymarket-weather` only). Auto-rollback on failure.
8. Prune old WB splinter releases (keep last 5). Does NOT touch `/opt/pa2-releases/` or `/opt/pa2-esports-releases/`.

Rollback: `bash deploy/rollback.sh` swaps `/opt/polymarket-ai-v2-weather` back to the 2nd-most-recent splinter release and restarts `polymarket-weather` only.

---

## Worktree silo

WB session works EXCLUSIVELY in a dedicated git worktree at:

```
C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/wb-main/
```

This worktree is locked to `wb/main` (git enforces one-branch-per-worktree). Other sessions checking out branches in the main dir or other worktrees cannot affect this worktree's HEAD pointer. The worktree is gitignored at the `.claude/` parent level.

### Per-session entry protocol (binding)

```bash
# 1. cd into the WB worktree (NEVER work from the main repo dir)
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/wb-main

# 2. Verify HEAD is on wb/main
cat .git/HEAD   # must print "ref: refs/heads/wb/main"

# 3. Verify worktree integrity
git worktree list | grep "wb-main.*wb/main"

# 4. All subsequent git, file edit, deploy, test commands run here
```

---

## Rescission

This splinter can be retired only by explicit operator directive ("retire `wb/main` splinter" / "merge `wb/main` back to master" / "WB rejoins shared deploy"). On rescission:

1. Operator-authorized merge of `wb/main` → `master`.
2. Operator-authorized restoration of master `polymarket-weather.service` install loop.
3. Operator-authorized removal of drop-in override at `/etc/systemd/system/polymarket-weather.service.d/00-splinter.conf`.
4. Operator-authorized cleanup of `/opt/pa2-weather-releases/` and `/opt/polymarket-ai-v2-weather`.
5. Remove this file (`WB-SPLINTER.md`).

---

## Quick-reference for next WB session

```bash
# ALWAYS start in the WB worktree silo
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/wb-main

# Verify worktree integrity
cat .git/HEAD                                              # must print "ref: refs/heads/wb/main"
git worktree list | grep "wb-main.*wb/main"

# Run WB tests
PYTHONPATH=. python -m pytest \
    tests/unit/test_weather_bot.py \
    tests/unit/test_weather_cold_start.py \
    tests/unit/test_paper_fill_probability_silo.py

# Deploy splinter to VPS (from worktree)
bash deploy/deploy.sh

# Rollback splinter
bash deploy/rollback.sh

# Verify splinter state on VPS
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
ssh -i "$KEY" ubuntu@18.201.216.0 "
readlink /opt/polymarket-ai-v2-weather
systemctl show polymarket-weather -p MainPID,ActiveState,WorkingDirectory
systemctl is-active polymarket-mirror polymarket-esports polymarket-ingestion
cat /etc/systemd/system/polymarket-weather.service.d/00-splinter.conf"
```
