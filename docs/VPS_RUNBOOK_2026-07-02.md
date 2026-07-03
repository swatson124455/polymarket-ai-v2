# VPS Runbook — 3 steps, copy-paste (2026-07-02)

All code is on `master` (`75c88ca`). One SSH session does everything:

```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0
```

## 1. Pause old MB (flip to paper — keeps signal data flowing)

```bash
sudo tee -a /opt/pa2-shared/.env.mirror >/dev/null <<'EOF'
SIMULATION_MODE=true
CANARY_STAGE=0
CANARY_AUTO_ADVANCE=false
EOF
sudo systemctl restart polymarket-mirror
journalctl -u polymarket-mirror -n 20 --no-pager | grep -iE "simulation|paper"
```

## 2. Run the algo go/no-go (crash fixed; needs --cutoff now)

```bash
cd /tmp && rm -rf mbfr && git clone --depth 1 -b claude/mb-formula-review-vdxmtr \
  https://github.com/swatson124455/polymarket-ai-v2 mbfr
cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbfr \
  venv/bin/python /tmp/mbfr/scripts/mirror_scoring_run.py --stage validate --cutoff 2026-05-25T00:00:00
```

→ Paste the `VALIDATION: PASS/FAIL` output back to the session.

## 3. First boot of the v3 silo (paper, strategy empty — proves the safety spine)

```bash
cd /tmp && rm -rf mb3 && git clone --depth 1 https://github.com/swatson124455/polymarket-ai-v2 mb3
sudo cp /tmp/mb3/deploy/env.mirror3.example /opt/pa2-shared/.env.mirror3
sudo nano /opt/pa2-shared/.env.mirror3        # set the real DATABASE_URL (only edit needed)
sudo cp /tmp/mb3/deploy/polymarket-mirror3.service /etc/systemd/system/
sudo rsync -a /tmp/mb3/mirror_v3 /opt/polymarket-ai-v2/
sudo chown -R polymarket:polymarket /opt/polymarket-ai-v2/mirror_v3
sudo systemctl daemon-reload && sudo systemctl enable --now polymarket-mirror3
journalctl -u polymarket-mirror3 -n 20 --no-pager
```

**Expected:** `env guard OK` → `restored: entered=0 open=0` → heartbeats with
`strategy=EMPTY(gated)`. If it refuses to boot, the guard prints exactly which
env key is wrong — that's it working, not failing.

Done. Everything else waits on step 2's output.
