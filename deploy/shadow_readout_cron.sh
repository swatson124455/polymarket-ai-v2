#!/usr/bin/env bash
# Daily shadow readout (cron, runs as polymarket). Refreshes the durable readout
# clone to branch head, then runs the per-cohort FRESH-label readout — which
# appends /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt and
# writes shadow_readout_ALERT.txt when a cohort hits >=30 resolved OR its edge
# is convincingly negative. A steward session (or the daily check-in routine)
# relays the ALERT file to the operator. Read-only vs the DB + shadow log.
set -uo pipefail
D=/opt/pa2-shared/mb_readout
BR=claude/repo-setup-docs-fq9bhn
git config --global --add safe.directory "$D" 2>/dev/null || true
git -C "$D" fetch -q --depth 1 origin "$BR" && git -C "$D" reset -q --hard FETCH_HEAD || echo "warn: clone refresh failed; running existing code"
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
[ -n "$DBURL" ] || { echo "FATAL: no DATABASE_URL"; exit 1; }
cd /opt/polymarket-ai-v2
DATABASE_URL="$DBURL" PYTHONPATH="$D" /opt/polymarket-ai-v2/venv/bin/python \
  "$D/scripts/shadow_readout.py"
