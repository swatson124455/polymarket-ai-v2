#!/bin/bash
# Dead Man's Switch Watchdog
# Runs via systemd timer every 60s
# Checks if any bot has written a heartbeat in the last 5 minutes
# If ALL bots are stale AND the process is dead: triggers kill switch via DB + sends webhook alert

set -euo pipefail

HEARTBEAT_DIR="/tmp/polymarket-heartbeats"
MAX_AGE=300  # 5 minutes
DB_NAME="polymarket"
DB_USER="polymarket"
ALERT_WEBHOOK="${DEAD_MAN_WEBHOOK_URL:-}"
COOLDOWN_FILE="/tmp/dead_man_alert_cooldown"
COOLDOWN_SECONDS=1800  # 30 min between alerts

# Check if any heartbeat file exists and is recent
any_alive=false
for hb_file in "$HEARTBEAT_DIR"/*.heartbeat; do
    [ -f "$hb_file" ] || continue
    ts=$(cat "$hb_file" 2>/dev/null)
    now=$(date +%s)
    age=$((now - ${ts:-0}))
    if [ "$age" -lt "$MAX_AGE" ]; then
        any_alive=true
        break
    fi
done

if [ "$any_alive" = true ]; then
    # System is alive — clean up cooldown if exists
    rm -f "$COOLDOWN_FILE"
    exit 0
fi

# ALL bots stale — check cooldown to prevent alert spam
if [ -f "$COOLDOWN_FILE" ]; then
    cooldown_ts=$(cat "$COOLDOWN_FILE" 2>/dev/null)
    now=$(date +%s)
    if [ $((now - ${cooldown_ts:-0})) -lt "$COOLDOWN_SECONDS" ]; then
        exit 0  # Still in cooldown
    fi
fi

# Multi-level check before triggering kill switch:
# 1. Check if the main process is running
if pgrep -f "polymarket-ai" > /dev/null 2>&1; then
    # Process exists but heartbeats stale — might just be slow startup or stuck
    # Log warning but DON'T trigger kill switch yet
    logger -t dead_man_switch "WARNING: heartbeats stale but process alive — not triggering kill switch"

    if [ -n "$ALERT_WEBHOOK" ]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"WARNING: Polymarket bot heartbeats stale for ${MAX_AGE}s but process is still running\"}" \
            > /dev/null 2>&1 || true
    fi

    # Set cooldown so we don't spam warnings
    date +%s > "$COOLDOWN_FILE"
    exit 0
fi

# 2. Process is actually dead AND heartbeats are stale — trigger kill switch
logger -t dead_man_switch "CRITICAL: all bots dead and process not running, triggering kill switch"
psql -U "$DB_USER" -d "$DB_NAME" -c \
    "INSERT INTO system_config (key, value) VALUES ('kill_switch', 'true') ON CONFLICT (key) DO UPDATE SET value = 'true';" \
    > /dev/null 2>&1 || logger -t dead_man_switch "ERROR: failed to set kill_switch in DB"

# 3. Send alert
if [ -n "$ALERT_WEBHOOK" ]; then
    curl -s -X POST "$ALERT_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "{\"text\": \"CRITICAL: Dead man's switch triggered — all Polymarket bots unresponsive for ${MAX_AGE}s and process is dead\"}" \
        > /dev/null 2>&1 || true
fi

# Set cooldown
date +%s > "$COOLDOWN_FILE"
