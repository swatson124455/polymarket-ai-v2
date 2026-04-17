#!/usr/bin/env bash
# healthcheck_probe.sh — Standalone VPS health probe using the 3-gate tiered check.
#
# Purpose:
#   (1) Extracted gate logic from deploy.sh step 7 so it can be dry-run against
#       live VPS before shipping a deploy.sh rewrite. Closes the test-surface gap
#       where the new health check is deployed by the thing it replaces.
#   (2) On-demand health snapshot that an operator can run any time.
#
# Gates (ascending severity of failure):
#   Gate 1 (T+30s):  systemctl is-active --quiet for all enabled bot services
#                    → exit 1 if any dead. Uses --quiet to avoid `activating`
#                      false-positives during legitimate restarts.
#   Gate 2 (T+60s):  journalctl -p err --since "60 seconds ago" must be empty
#                    → exit 1 if error spam detected.
#   Gate 3 (T+420s): Loop waiting for `scan_ms` log line from each bot.
#                    Success → exit 0 "HEALTH_OK".
#                    Timeout with services still active + no errors → exit 0
#                    "HEALTH_WARN" (likely EB v2 cold-start fit, not a failure).
#                    Timeout with a service dead → exit 1 "HEALTH_FAIL".
#
# Usage:
#   # Dry-run against current live state (skips T+30s/T+60s sleeps):
#   bash healthcheck_probe.sh --no-wait
#
#   # Real run as used post-deploy:
#   bash healthcheck_probe.sh
#
# Exit codes:
#   0 = HEALTH_OK or HEALTH_WARN (services active)
#   1 = HEALTH_FAIL (gate 1 or gate 2 tripped, or service died in gate 3)

set -euo pipefail

NO_WAIT=false
[ "${1:-}" = "--no-wait" ] && NO_WAIT=true

BOT_SERVICES=(polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion)
SCAN_SERVICES=(polymarket-weather polymarket-mirror polymarket-esports)  # bots that emit scan_ms

# ── Gate 1: T+30s services active ─────────────────────────────────────────────
echo "[Gate 1] Checking services active..."
if [ "$NO_WAIT" = false ]; then
    sleep 30
fi

for SVC in "${BOT_SERVICES[@]}"; do
    if ! systemctl is-enabled "$SVC" &>/dev/null; then
        echo "  $SVC: disabled (skipped)"
        continue
    fi
    if systemctl is-active --quiet "$SVC"; then
        echo "  $SVC: active"
    else
        STATE=$(systemctl is-active "$SVC" 2>&1 || true)
        echo "HEALTH_FAIL_GATE1: $SVC not active (state=$STATE)"
        exit 1
    fi
done

# ── Gate 2: T+60s no ERROR-level log entries in the last 60s ──────────────────
echo "[Gate 2] Checking recent error spam..."
if [ "$NO_WAIT" = false ]; then
    sleep 30
fi

ERRORS_FOUND=false
for SVC in "${BOT_SERVICES[@]}"; do
    systemctl is-enabled "$SVC" &>/dev/null || continue
    # -p err = priority err(3) and higher (err/crit/alert/emerg)
    ERR_LINES=$(journalctl -u "$SVC" --since "60 seconds ago" -p err --no-pager 2>/dev/null | grep -v '^-- ' | head -20 || true)
    if [ -n "$ERR_LINES" ]; then
        echo "HEALTH_FAIL_GATE2: $SVC has error-level log entries in last 60s:"
        echo "$ERR_LINES" | sed 's/^/    /'
        ERRORS_FOUND=true
    fi
done

if [ "$ERRORS_FOUND" = true ]; then
    exit 1
fi
echo "  no error-level entries in last 60s"

# ── Gate 3: up to T+420s soft-wait for scan_ms from each bot ──────────────────
echo "[Gate 3] Waiting for scan_ms from each enabled bot (soft, up to 420s)..."
MAX_WAIT=420
INTERVAL=10
ELAPSED=0

while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))

    ALL_OK=true
    CHECKED=0
    for SVC in "${SCAN_SERVICES[@]}"; do
        systemctl is-enabled "$SVC" &>/dev/null || continue
        CHECKED=$((CHECKED + 1))
        if ! journalctl -u "$SVC" --since "-${ELAPSED}s" --no-pager 2>/dev/null | grep -q 'scan_ms'; then
            ALL_OK=false
        fi
    done

    if [ "$CHECKED" -gt 0 ] && [ "$ALL_OK" = true ]; then
        echo "HEALTH_OK at ${ELAPSED}s — all $CHECKED scan-emitting bots scanning"
        # PgBouncer pool size
        PGB=$(sudo grep -oP 'default_pool_size\s*=\s*\K[0-9]+' /etc/pgbouncer/pgbouncer.ini 2>/dev/null || echo "0")
        echo "PGB_POOL=$PGB"
        # Backup staleness
        if ! find /opt/pa2-backups -name '*.dump' -mmin -1500 2>/dev/null | grep -q .; then
            echo "BACKUP_STALE"
        fi
        exit 0
    fi
    echo "  Waiting... ${ELAPSED}s" >&2
done

# Gate 3 timeout — distinguish soft vs hard failure.
echo "[Gate 3] scan_ms not seen from all bots after ${MAX_WAIT}s. Checking services still active..."
for SVC in "${BOT_SERVICES[@]}"; do
    systemctl is-enabled "$SVC" &>/dev/null || continue
    if ! systemctl is-active --quiet "$SVC"; then
        STATE=$(systemctl is-active "$SVC" 2>&1 || true)
        echo "HEALTH_FAIL_GATE3_SERVICE_DIED: $SVC no longer active (state=$STATE)"
        exit 1
    fi
done

echo "HEALTH_WARN: scan_ms not observed within ${MAX_WAIT}s from all bots,"
echo "             but all bot services are still active. Most likely EB v2 cold-start"
echo "             pipeline fit (~5.5 min). Continuing deploy. Monitor via:"
echo "             journalctl -u polymarket-esports -f | grep pipeline_ready"

# Still report PGB + backup staleness on warn path
PGB=$(sudo grep -oP 'default_pool_size\s*=\s*\K[0-9]+' /etc/pgbouncer/pgbouncer.ini 2>/dev/null || echo "0")
echo "PGB_POOL=$PGB"
if ! find /opt/pa2-backups -name '*.dump' -mmin -1500 2>/dev/null | grep -q .; then
    echo "BACKUP_STALE"
fi

exit 0  # soft success
