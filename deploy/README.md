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

## Known adjacent drift (NOT addressed here — flagged for a separate item)

Each service also has a VPS-only `limits.conf` drop-in
(`MemoryMax` / `OOMScoreAdjust`) that is **not** in this repo. Values observed
on the VPS S235 (2026-05-30): mirror `2560M / -100`, ingestion `512M / +100`,
weather `2G / -200`, esports `2560M / 0`. These also differ from the `MemoryMax`
in the base units (e.g. mirror base says `3G`, drop-in says `2560M` — the drop-in
wins). Same memory≠reality class as the splinter drop-ins, but a different
category (resource limits, not release-path topology). Left for a dedicated item
rather than folded into the WI-2 splinter commit.

## Verification

**Last verified:** S235 (2026-05-30).
**Method** (read-only, triangulated 4 ways):
```bash
# 1. Effective working dir per service
systemctl show polymarket-{mirror,weather,esports,ingestion} -p WorkingDirectory --value
# 2. Full merged unit + drop-ins
systemctl cat polymarket-{weather,esports}
# 3. Resolve the symlinks
readlink -f /opt/polymarket-ai-v2 /opt/polymarket-ai-v2-weather /opt/polymarket-ai-v2-esports
# 4. Directory listing of /opt
ls -la /opt/ | grep -E 'polymarket-ai-v2'
```
All four agreed: mirror+ingestion → `pa2-releases/`, weather → `pa2-weather-releases/`,
esports → `pa2-esports-releases/`; drop-ins present and active for weather/esports,
absent (only `limits.conf`) for mirror/ingestion.
