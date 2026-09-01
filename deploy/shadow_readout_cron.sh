#!/usr/bin/env bash
# Daily shadow readout (cron, runs as polymarket). Refreshes the durable readout
# clone to branch head, then runs the per-cohort FRESH-label readout — which
# appends /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt and
# writes shadow_readout_ALERT.txt when a cohort hits >=30 resolved OR its edge
# is convincingly negative. A steward session (or the daily check-in routine)
# relays the ALERT file to the operator. Read-only vs the DB + shadow log.
set -uo pipefail
D=/opt/pa2-shared/mb_readout
BR=claude/repo-setup-docs-fq9bhn   # branch pin — update when the MB lane moves branches
LOG=/opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt
# safe.directory: guarded — an unguarded --add appends a duplicate line to
# ~/.gitconfig every day (review finding D); usually unnecessary (clone is
# polymarket-owned) but kept for the root-touched-.git case
git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$D" \
  || git config --global --add safe.directory "$D" 2>/dev/null || true
if ! { git -C "$D" fetch -q --depth 1 origin "$BR" && git -C "$D" reset -q --hard FETCH_HEAD; }; then
  # surface the failure where sessions actually look (the readout log), not
  # only the unmonitored cron log (review finding E); keep running frozen code
  echo "[$(date -u +%FT%TZ)] WARN: readout clone refresh FAILED — running frozen code at $(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo '?')" | tee -a "$LOG"
fi
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
[ -n "$DBURL" ] || { echo "FATAL: no DATABASE_URL"; exit 1; }
cd /opt/polymarket-ai-v2
DATABASE_URL="$DBURL" PYTHONPATH="$D" /opt/polymarket-ai-v2/venv/bin/python \
  "$D/scripts/shadow_readout.py"
