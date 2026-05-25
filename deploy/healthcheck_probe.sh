#!/usr/bin/env bash
# healthcheck_probe.sh — WB SPLINTER health probe (3-gate tiered check).
# Branch: wb/main (long-lived splinter, see WB-SPLINTER.md)
#
# SPLINTER SEMANTICS:
#   - BOT_SERVICES = polymarket-weather ONLY (no mirror/esports/ingestion).
#   - Splinter probe deliberately ignores MB/EB/ingestion health.

set -euo pipefail

NO_WAIT=false
[ "${1:-}" = "--no-wait" ] && NO_WAIT=true

BOT_SERVICES=(polymarket-weather)
SCAN_SERVICES=(polymarket-weather)

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

# ── Gate 3: up to T+420s soft-wait for scan_ms from polymarket-weather ───────
echo "[Gate 3] Waiting for scan_ms from polymarket-weather (soft, up to 420s)..."
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
        echo "HEALTH_OK at ${ELAPSED}s — polymarket-weather scanning"
        PGB=$(sudo grep -oP 'default_pool_size\s*=\s*\K[0-9]+' /etc/pgbouncer/pgbouncer.ini 2>/dev/null || echo "0")
        echo "PGB_POOL=$PGB"
        if ! find /opt/pa2-backups -name '*.dump' -mmin -1500 2>/dev/null | grep -q .; then
            echo "BACKUP_STALE"
        fi
        exit 0
    fi
    echo "  Waiting... ${ELAPSED}s" >&2
done

echo "[Gate 3] scan_ms not seen after ${MAX_WAIT}s. Checking service still active..."
for SVC in "${BOT_SERVICES[@]}"; do
    systemctl is-enabled "$SVC" &>/dev/null || continue
    if ! systemctl is-active --quiet "$SVC"; then
        STATE=$(systemctl is-active "$SVC" 2>&1 || true)
        echo "HEALTH_FAIL_GATE3_SERVICE_DIED: $SVC no longer active (state=$STATE)"
        exit 1
    fi
done

echo "HEALTH_WARN: scan_ms not observed within ${MAX_WAIT}s from polymarket-weather,"
echo "             but service still active. Continuing deploy."

PGB=$(sudo grep -oP 'default_pool_size\s*=\s*\K[0-9]+' /etc/pgbouncer/pgbouncer.ini 2>/dev/null || echo "0")
echo "PGB_POOL=$PGB"
if ! find /opt/pa2-backups -name '*.dump' -mmin -1500 2>/dev/null | grep -q .; then
    echo "BACKUP_STALE"
fi

exit 0
