#!/usr/bin/env bash
# MB VPS one-shot — READ-ONLY. Changes nothing, spends nothing.
# Runs every server-side check the MirrorBot rebuild needs and prints it all.
# Intended: sudo -u polymarket bash mb_vps_oneshot.sh
set -uo pipefail
echo "================= MB VPS ONE-SHOT  $(date -u) ================="

# Load the same env files the mirror unit uses, so DATABASE_URL etc. are set.
for f in /opt/pa2-shared/.env /opt/pa2-shared/.env.mirror; do
  [ -f "$f" ] && { set -a; . "$f"; set +a; }
done

echo
echo "### 1. Current mirror mode (is real money on?) ###"
systemctl show -p ActiveState -p SubState polymarket-mirror 2>/dev/null || echo "(service query unavailable)"
echo "SIMULATION_MODE=${SIMULATION_MODE:-<unset>}  CANARY_STAGE=${CANARY_STAGE:-<unset>}  CANARY_AUTO_ADVANCE=${CANARY_AUTO_ADVANCE:-<unset>}"
echo "  paper (safe) = SIMULATION_MODE true; live (real money) = false"

echo
echo "### 2. Sports-share of labeled whale signals (decides the Pinnacle idea) ###"
psql polymarket -c "SELECT COALESCE(m.category,'(unknown)') AS category, COUNT(*) AS labeled_signals
FROM mirror_rejected_signals r
LEFT JOIN markets m ON (m.condition_id = r.market_id OR CAST(m.id AS TEXT) = r.market_id)
WHERE r.resolution IN ('YES','NO')
GROUP BY 1 ORDER BY 2 DESC LIMIT 20;" 2>&1

echo
echo "### 3. Algo brain go/no-go (validate stage) ###"
cd /tmp && rm -rf mbfr && \
  git clone -q --depth 1 -b claude/mb-formula-review-vdxmtr \
  https://github.com/swatson124455/polymarket-ai-v2 mbfr 2>&1 | tail -1
if [ -f /tmp/mbfr/scripts/mirror_scoring_run.py ]; then
  cd /opt/polymarket-ai-v2 && \
    DB_STATEMENT_TIMEOUT_MS=300000 PYTHONPATH=/tmp/mbfr \
    venv/bin/python /tmp/mbfr/scripts/mirror_scoring_run.py \
      --stage validate --cutoff 2026-05-25T00:00:00 2>&1 | tail -30
else
  echo "(clone failed — check network / branch name)"
fi

echo
echo "================= END — copy everything above back to the session ================="
