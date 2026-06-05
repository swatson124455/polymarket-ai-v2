# Deploy topology — Polymarket AI V2

Canonical description of how the 4 production services map to release directories on
the VPS. Written S235 (2026-05-30) after WORK_PROGRAM WI-2 found the prior premise
("splinter symlinks absent") was wrong. **Update this file like code when the topology
changes.**

## Services and their release paths

| Service | WorkingDir symlink | Release pool | Deploy channel |
|---|---|---|---|
| `polymarket-mirror` | `/opt/polymarket-ai-v2` | `/opt/pa2-releases/` | master (`deploy/deploy.sh`) |
| `polymarket-ingestion` | `/opt/polymarket-ai-v2` | `/opt/pa2-releases/` | master (`deploy/deploy.sh`) |
| `polymarket-weather` | `/opt/polymarket-ai-v2-weather` | `/opt/pa2-weather-releases/` | WB splinter (`wb/main`, `deploy/deploy.sh` from the wb worktree) |
| `polymarket-esports` | `/opt/polymarket-ai-v2-esports` | `/opt/pa2-esports-releases/` | EB splinter (`eb/main`) |

MirrorBot and the ingestion service share master's consolidated release symlink.
WeatherBot and EsportsBot each run their **own** splinter release, deployed
independently from their `wb/main` / `eb/main` branches. This implements
MEMORY.md RULE ONE-A (WB/EB own their splinters; splinter deploys do not touch MB).

## How WB/EB get redirected to the splinter paths

The base unit files in this repo (`deploy/polymarket-weather.service`,
`deploy/polymarket-esports.service`) **intentionally still point at the consolidated
path** `/opt/polymarket-ai-v2`. The redirect to the splinter path is done by a
**systemd drop-in override** installed on the VPS at:

```
/etc/systemd/system/polymarket-weather.service.d/00-splinter.conf
/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf
```

These drop-ins are version-controlled in this repo under
`deploy/polymarket-weather.service.d/` and `deploy/polymarket-esports.service.d/`
(committed S235 — previously they lived only on the VPS, which was the WI-2
silent-fallback risk: if a drop-in were lost via `systemctl revert` / manual
cleanup / host migration, WB or EB would silently fall back to running master's
code with no alert).

Drop-in semantics: systemd loads `*.service.d/*.conf` **on top of** the main unit
file, regardless of the main file's content. The empty `ExecStart=` line clears
the inherited value before re-setting it (required for list-valued options). So a
master `deploy.sh` run that reinstalls `polymarket-weather.service` (pointing at
the consolidated path) does **not** dislodge the splinter — the drop-in still wins
after `daemon-reload`.

### Why drop-ins instead of baking splinter paths into the base units

The drop-in layer makes the override **explicit and self-documenting**: a base unit
that says "consolidated path" plus a drop-in that says "…except this is overridden
to the splinter path, here's why" carries more signal than a base unit silently
edited to a per-bot path. The latter risks a future session "normalizing" the
asymmetry back to consolidated and silently regressing WB/EB onto master code. The
drop-in's header comment is the guardrail against that. (WORK_PROGRAM WI-2 option A1.)

## deploy.sh interaction (important)

`deploy/deploy.sh` step 6 installs only the base `*.service` files
(`sudo cp .../${SVC}.service /etc/systemd/system/`). It does **not** install or
touch `*.service.d/` drop-ins. Committing the drop-ins to this repo is therefore
**documentation + recovery + reproducibility only — it does not change deploy
behavior.** If a drop-in is ever lost, restore it manually:

```bash
sudo cp deploy/polymarket-weather.service.d/00-splinter.conf \
    /etc/systemd/system/polymarket-weather.service.d/00-splinter.conf
sudo systemctl daemon-reload
sudo systemctl restart polymarket-weather   # WB session's call — not MB's
```

## Resource-limits drop-ins (WI-16, S235 2026-05-31)

Each service has a `*.service.d/limits.conf` drop-in that overrides `MemoryMax`
and sets `OOMScoreAdjust`. These are now tracked in this repo (same A1 pattern
as the splinter drop-ins). `deploy.sh` does NOT install them automatically —
they are for documentation + host-rebuild recovery only.

| Service | Base unit MemoryMax | Drop-in MemoryMax | Actual (drop-in wins) | OOMScoreAdjust |
|---|---|---|---|---|
| mirror | 3G | **2560M** | 2560M | -100 (protected) |
| weather | 2G | 2G | 2G | -200 (most protected) |
| esports | 2G | **2560M** | 2560M | 0 (neutral) |
| ingestion | 1G | **512M** | 512M | +100 (most killable) |

Base-unit `MemoryMax` values for mirror, esports, and ingestion are stale
(the drop-in wins). The base units were not updated to avoid a deploy.sh
behavior-change; the drop-ins are the source of truth for actual limits.

To restore on a rebuilt host:
```bash
for svc in mirror weather esports ingestion; do
  sudo mkdir -p /etc/systemd/system/polymarket-${svc}.service.d/
  sudo cp deploy/polymarket-${svc}.service.d/limits.conf \
       /etc/systemd/system/polymarket-${svc}.service.d/limits.conf
done
sudo systemctl daemon-reload
```

## Orderbook collector (WI-16, S235 2026-05-31)

`deploy/polymarket-orderbook.service` + `deploy/polymarket-orderbook.timer`
are committed here for documentation + recovery. They were previously VPS-only
(active and enabled on the VPS, but absent from git). The script they invoke
(`scripts/orderbook_collector.py`) **is** in git.

The timer runs every minute (`OnCalendar=*:*:00`) and is enabled on the VPS.
`deploy.sh`'s timer install loop (`for TIMER_SVC in polymarket-prune-prices
polymarket-audit polymarket-prune-data`) does **not** include
`polymarket-orderbook` — a future decision: add it to the loop so it gets
reinstalled on every deploy (correct long-term), or leave as manual-install.

To restore on a rebuilt host:
```bash
sudo cp deploy/polymarket-orderbook.service deploy/polymarket-orderbook.timer \
     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-orderbook.timer
```

## Adjacent VPS config not in this repo

`/etc/systemd/system/redis-server.service.d/oom.conf` sets
`OOMScoreAdjust=-500` for Redis (highest OOM protection on the system). This
is a system Redis config, not a polymarket service file — it is intentionally
NOT committed to the polymarket repo. Document it here so it is not forgotten
on a host rebuild:
```
[Service]
OOMScoreAdjust=-500
```

## dead_man_watchdog.timer — in git but NOT active on VPS

`deploy/dead_man_watchdog.timer` (and its `.service`) exist in git but
`systemctl is-active dead_man_watchdog.timer` returns `inactive` on the VPS.
Either it was intentionally not installed, or it was installed and later
removed. Noted here to avoid confusion — it is not a drift gap in the same
class as the items above (which were VPS-active-but-not-in-git).

## Verification

**Last verified:** S235 (2026-05-31).
**Method:**
```bash
# Drop-ins (complete inventory)
find /etc/systemd/system -name "*.conf" -path "*service.d*" | sort
# Timers
ls /etc/systemd/system/*.timer
systemctl is-active polymarket-orderbook.timer
# Splinter symlinks (4-way check)
systemctl show polymarket-{mirror,weather,esports,ingestion} -p WorkingDirectory --value
systemctl cat polymarket-{weather,esports}
readlink -f /opt/polymarket-ai-v2 /opt/polymarket-ai-v2-weather /opt/polymarket-ai-v2-esports
ls -la /opt/ | grep -E 'polymarket-ai-v2'
```
