#!/usr/bin/env bash
# Ensure the firehose recorder is running (hourly cron). The 7-day
# zero-based capture must not die silently - a dead recorder is a hole in
# the population study, not a quiet success.
set -u
if pgrep -f "firehose_record[e]r.py" >/dev/null 2>&1; then exit 0; fi
URL=$(grep -m1 "^RTDS_WS_URL=" /opt/pa2-shared/.env.mirror3 | cut -d= -f2-)
[ -n "$URL" ] || { echo "[$(date -u +%FT%TZ)] GUARD: no RTDS_WS_URL"; exit 1; }
sudo -u polymarket bash -c "cd /opt/mirror3 && RTDS_WS_URL=\"$URL\" nohup python3 scripts/firehose_recorder.py >> /opt/pa2-shared/mb_copyable_data/firehose/recorder.log 2>&1 &"
echo "[$(date -u +%FT%TZ)] GUARD: recorder was DEAD - relaunched"
