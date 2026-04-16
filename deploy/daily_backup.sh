#!/usr/bin/env bash
# Daily PostgreSQL backup for polymarket database.
# Called by postgres crontab at 04:00 UTC.
# Retention: 7 days (older dumps auto-pruned).

set -euo pipefail

BACKUP_DIR="/opt/pa2-backups"
DB_NAME="polymarket"
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DUMP_FILE="${BACKUP_DIR}/polymarket_${TIMESTAMP}.dump"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting pg_dump..."

# Custom-format dump (compressed, supports selective restore)
pg_dump -d "$DB_NAME" -Fc -f "$DUMP_FILE"

DUMP_SIZE=$(stat -c%s "$DUMP_FILE" 2>/dev/null || echo "unknown")
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup complete: $DUMP_FILE ($DUMP_SIZE bytes)"

# Prune backups older than retention window
PRUNED=$(find "$BACKUP_DIR" -name "polymarket_*.dump" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
if [ "$PRUNED" -gt 0 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Pruned $PRUNED backup(s) older than ${RETENTION_DAYS} days"
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Done. Backups on disk:"
ls -lh "$BACKUP_DIR"/polymarket_*.dump 2>/dev/null || echo "  (none)"
