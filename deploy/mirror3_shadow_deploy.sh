#!/usr/bin/env bash
# MirrorBot v3 SHADOW deploy — one-paste operator script (run as root):
#   sudo bash /tmp/mbpc2/deploy/mirror3_shadow_deploy.sh
#
# What it does (idempotent, fail-loud, secrets never leave the box):
#   1. Clone/refresh the investigation branch to /opt/mirror3 (persistent —
#      /tmp clones die on reboot; a systemd service must not).
#   2. Create /opt/pa2-shared/.env.mirror3 IF ABSENT: safety trio + the
#      DATABASE_URL line copied server-side from the shared .env + the
#      shadow-watcher block. An existing file is NEVER modified (report-only).
#   3. Preflight: roster JSON present with CLEAN addresses; venv imports
#      web3/aiohttp; shadow sink writable by the service user.
#   4. Install the shadow unit as polymarket-mirror3.service, enable, start,
#      show first heartbeat lines.
set -euo pipefail

BRANCH="claude/mirrorbot-persistence-check-irq7r5"
REPO="https://github.com/swatson124455/polymarket-ai-v2"
CODE_DIR="/opt/mirror3"
ENV_FILE="/opt/pa2-shared/.env.mirror3"
SHARED_ENV="/opt/pa2-shared/.env"
ROSTER="/opt/pa2-shared/mb_copyable_data/chain_audit.json"
SINK="/opt/pa2-shared/mirror3_shadow.jsonl"
VENV_PY="/opt/polymarket-ai-v2/venv/bin/python"
UNIT_SRC_REL="deploy/polymarket-mirror3-shadow.service"
UNIT_DST="/etc/systemd/system/polymarket-mirror3.service"

echo "== [1/4] code -> ${CODE_DIR} (${BRANCH})"
if [ -d "${CODE_DIR}/.git" ]; then
    git -C "${CODE_DIR}" fetch --depth 1 origin "${BRANCH}"
    git -C "${CODE_DIR}" reset --hard FETCH_HEAD
else
    git clone -q --depth 1 -b "${BRANCH}" "${REPO}" "${CODE_DIR}"
fi
git -C "${CODE_DIR}" log --oneline -1
chmod -R o+rX "${CODE_DIR}"

echo "== [2/4] env file ${ENV_FILE}"
if [ -f "${ENV_FILE}" ]; then
    echo "   exists — NOT modified. Watcher keys present:"
    grep -c '^MIRROR3_' "${ENV_FILE}" || echo "   WARNING: no MIRROR3_ keys; add the block from deploy/env.mirror3.example"
else
    DB_LINE="$(grep -m1 '^DATABASE_URL=' "${SHARED_ENV}" || true)"
    [ -n "${DB_LINE}" ] || { echo "FATAL: no DATABASE_URL in ${SHARED_ENV}"; exit 1; }
    umask 027
    cat > "${ENV_FILE}" <<EOF
# MirrorBot v3 allowlist env — generated $(date -u +%FT%TZ) by mirror3_shadow_deploy.sh
# (from deploy/env.mirror3.example; DATABASE_URL copied server-side)
SIMULATION_MODE=true
CANARY_AUTO_ADVANCE=false
CANARY_STAGE=0
BOT_ENABLED_MIRROR=false
${DB_LINE}
RTDS_WS_URL=wss://ws-live-data.polymarket.com
# ── copy-trade SHADOW watcher (no orders, no DB writes) ──
MIRROR3_COPY_WATCHER=true
MIRROR3_ROSTER_PATH=${ROSTER}
MIRROR3_RPC_URL=https://polygon.gateway.tenderly.co
MIRROR3_SHADOW_PATH=${SINK}
MIRROR3_MAX_CHASE_C=2
MIRROR3_MAX_SPREAD_C=5
MIRROR3_POLL_S=2
EOF
    chown root:polymarket "${ENV_FILE}"
    chmod 640 "${ENV_FILE}"
    echo "   created (DATABASE_URL copied from ${SHARED_ENV}, never printed)."
fi

echo "== [3/4] preflight"
sudo -u polymarket "${VENV_PY}" - "${ROSTER}" <<'PYEOF'
import json, sys
clean = json.load(open(sys.argv[1])).get("clean", [])
assert clean, "roster has no CLEAN addresses"
print(f"   roster OK: {len(clean)} CLEAN traders")
PYEOF
sudo -u polymarket "${VENV_PY}" -c "import web3, aiohttp; print('   venv OK: web3', web3.__version__)"
touch "${SINK}"; chown polymarket:polymarket "${SINK}"
echo "   sink OK: ${SINK}"

echo "== [4/4] systemd"
cp "${CODE_DIR}/${UNIT_SRC_REL}" "${UNIT_DST}"
systemctl daemon-reload
systemctl enable --now polymarket-mirror3.service
sleep 8
systemctl --no-pager --lines=0 status polymarket-mirror3.service || true
journalctl -u polymarket-mirror3.service -n 12 --no-pager
echo
echo "DONE. Watch it:  journalctl -u polymarket-mirror3.service -f"
echo "Shadow records: tail -f ${SINK}"
