# MB Coordination Request — Splinter Service-File Clobber

**From:** EB session, 2026-05-24 (`eb/main` branch)
**To:** Next MB session reading this on `master` checkout
**Status:** Operator-gated. Apply when MB session has clear shared-resource window.
**Urgency:** Medium — splinter is functional today; clobber risk activates on next MB-from-master deploy.

---

## TL;DR

EB now runs from a separate splinter (`eb/main` branch, `/opt/polymarket-ai-v2-esports` symlink, `/opt/pa2-esports-releases/<stamp>` release path). Master's `deploy/deploy.sh` still installs and restarts `polymarket-esports.service` on every MB deploy — which will:

1. Clobber the splinter's installed service file at `/etc/systemd/system/polymarket-esports.service` (overwriting `WorkingDirectory=/opt/polymarket-ai-v2-esports` with `WorkingDirectory=/opt/polymarket-ai-v2`).
2. Restart `polymarket-esports` against master's release — silently bypassing the splinter.

The splinter remains intact at `/opt/pa2-esports-releases/<latest>/` but is no longer the live code. Until the splinter is re-deployed, EB runs on whatever master happens to have committed.

This is **one surgical change to master's `deploy/deploy.sh`** to remove `polymarket-esports` from the install/start loop. Once applied, MB deploys leave EB alone and the splinter stays canonical for EB.

---

## What MB session needs to change on `master`

File: `deploy/deploy.sh` (on `master` branch only — do NOT touch `eb/main`).

### Change 1: install loop (line 194 area)

**Current:**
```bash
for SVC in polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion; do
    sudo cp "$NEW_RELEASE/deploy/${SVC}.service" /etc/systemd/system/
done
```

**Change to:**
```bash
# EB SPLINTER COORDINATION (2026-05-24): polymarket-esports owned by eb/main
# splinter (see EB-SPLINTER.md on that branch). Master deploys do NOT install
# polymarket-esports.service — that's the splinter's responsibility.
for SVC in polymarket-weather polymarket-mirror polymarket-ingestion; do
    sudo cp "$NEW_RELEASE/deploy/${SVC}.service" /etc/systemd/system/
done
```

### Change 2: .env.esports ensure (line ~201)

**Current:**
```bash
[ -f $SHARED/.env.esports   ] || sudo cp $SHARED/.env $SHARED/.env.esports
sudo chown polymarket:polymarket $SHARED/.env.weather $SHARED/.env.mirror $SHARED/.env.esports $SHARED/.env.ingestion
```

**Change to:**
```bash
# .env.esports is EB-splinter owned (see EB-SPLINTER.md). Splinter deploys
# manage it. Master deploys do NOT chown or touch it.
sudo chown polymarket:polymarket $SHARED/.env.weather $SHARED/.env.mirror $SHARED/.env.ingestion
```

### Change 3: enable/stop/start loop (lines 210-213 area)

**Current:**
```bash
sudo systemctl enable polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion
sudo systemctl stop polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion 2>/dev/null || true
sleep 2  # Let PgBouncer reclaim slots
sudo systemctl start polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion
echo "  polymarket-weather, polymarket-mirror, polymarket-esports, polymarket-ingestion started (clean)"
```

**Change to:**
```bash
# polymarket-esports omitted — owned by eb/main splinter
sudo systemctl enable polymarket-weather polymarket-mirror polymarket-ingestion
sudo systemctl stop polymarket-weather polymarket-mirror polymarket-ingestion 2>/dev/null || true
sleep 2  # Let PgBouncer reclaim slots
sudo systemctl start polymarket-weather polymarket-mirror polymarket-ingestion
echo "  polymarket-weather, polymarket-mirror, polymarket-ingestion started (clean)"
```

### Change 4: matching update to `deploy/rollback.sh`

**Current line ~46:**
```bash
sudo systemctl restart polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion
```

**Change to:**
```bash
# polymarket-esports omitted — owned by eb/main splinter (see EB-SPLINTER.md)
sudo systemctl restart polymarket-weather polymarket-mirror polymarket-ingestion
```

### Change 5: matching update to `deploy/healthcheck_probe.sh`

**Current line ~38-39:**
```bash
BOT_SERVICES=(polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion)
SCAN_SERVICES=(polymarket-weather polymarket-mirror polymarket-esports)
```

**Change to:**
```bash
# polymarket-esports omitted — owned by eb/main splinter (see EB-SPLINTER.md)
BOT_SERVICES=(polymarket-weather polymarket-mirror polymarket-ingestion)
SCAN_SERVICES=(polymarket-weather polymarket-mirror)
```

---

## Verification after applying

After landing the change on master and running an MB deploy:

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "
# 1. EB service file should still point at splinter (untouched by MB deploy)
grep WorkingDirectory /etc/systemd/system/polymarket-esports.service
# Expected: WorkingDirectory=/opt/polymarket-ai-v2-esports

# 2. EB process cwd should still be splinter release
EB_PID=\$(systemctl show polymarket-esports -p MainPID --value)
sudo readlink /proc/\$EB_PID/cwd
# Expected: /opt/pa2-esports-releases/<some splinter timestamp>

# 3. EB symlink should still point at splinter release
readlink /opt/polymarket-ai-v2-esports
# Expected: /opt/pa2-esports-releases/<splinter timestamp>

# 4. Master symlink should point at the new master release
readlink /opt/polymarket-ai-v2
# Expected: /opt/pa2-releases/<new master timestamp>

# 5. All 4 services should be active
systemctl is-active polymarket-esports polymarket-mirror polymarket-weather polymarket-ingestion
"
```

If all 5 checks pass, the coordination is working: MB deploys leave EB alone.

---

## What MB session must NOT do

- **Do NOT merge `eb/main` into `master`.** The splinter is intentionally divergent (cascade=never per `EB-SPLINTER.md`). Merging would collapse the isolation.
- **Do NOT delete `eb/main` branch.** It is the live source for `/opt/pa2-esports-releases/<latest>`.
- **Do NOT touch `/opt/pa2-esports-releases/`** — that's splinter-owned.
- **Do NOT touch `/opt/polymarket-ai-v2-esports` symlink** — splinter-owned.
- **Do NOT touch `polymarket-esports.service` on disk** — splinter manages it.
- **Do NOT touch `/opt/pa2-shared/.env.esports`** — splinter-owned per-bot env.
- **Do not redeploy EB from master** — that's what we're fixing here. EB deploys exclusively from `eb/main`.

---

## Why this matters

EB had two prior collisions (2026-05-15, 2026-05-18) where EB session deploys atomic-swapped the shared `/opt/polymarket-ai-v2` symlink and pinned MB+WB+ingestion at EB-released code, reverting MB work. The operator directed the EB splinter as the structural fix.

For the splinter to ACTUALLY isolate, master's deploy.sh must stop treating polymarket-esports as a master-owned service. The five surgical changes above are the deal closer.

---

## Rollback (if MB session decides splinter should be retired)

See `EB-SPLINTER.md` §"Rescission" on `eb/main` for the operator-authorized retire procedure. Until rescinded, the splinter is live and these changes are the steady state.
