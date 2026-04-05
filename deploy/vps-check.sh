#!/bin/bash
# S157: Added error checking + updated to per-bot services
set -euo pipefail

echo "=== VPS Status Check ==="
echo ""

# DB stats
echo "--- Database ---"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'resolved_yes_no=' || count(*) FROM markets WHERE resolved = TRUE AND resolution IN ('YES','NO')" || echo "ERROR: DB query failed"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'paper_trades=' || count(*) FROM paper_trades" || echo "ERROR: DB query failed"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'trades_on_resolved=' || count(*) FROM trades t JOIN markets m ON (t.market_id = m.id::text OR t.market_id = m.condition_id) WHERE m.resolved = TRUE AND m.resolution IN ('YES','NO')" || echo "ERROR: DB query failed"

# Service status (S157: per-bot services)
echo ""
echo "--- Services ---"
for svc in polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion polymarket-dashboard; do
    echo "$svc: $(systemctl is-active $svc 2>/dev/null || echo 'inactive')"
done

# Recent logs (S157: per-bot services)
echo ""
echo "--- Recent Logs (last 10 lines per bot) ---"
for svc in polymarket-weather polymarket-mirror polymarket-esports; do
    echo "-- $svc --"
    journalctl -u $svc --no-pager -n 10 2>/dev/null || echo "  (no logs)"
done

# Backfill status
echo ""
echo "--- Backfill process ---"
pgrep -af backfill || echo "No backfill process running"

# Memory
echo ""
echo "--- Memory ---"
free -m | head -2
