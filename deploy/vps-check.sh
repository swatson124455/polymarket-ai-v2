#!/bin/bash
echo "=== VPS Status Check ==="
echo ""

# DB stats
echo "--- Database ---"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'resolved_yes_no=' || count(*) FROM markets WHERE resolved = TRUE AND resolution IN ('YES','NO')"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'paper_trades=' || count(*) FROM paper_trades"
sudo -u polymarket psql -d polymarket -t -A -c "SELECT 'trades_on_resolved=' || count(*) FROM trades t JOIN markets m ON (t.market_id = m.id::text OR t.market_id = m.condition_id) WHERE m.resolved = TRUE AND m.resolution IN ('YES','NO')"

# Service status
echo ""
echo "--- Services ---"
systemctl is-active polymarket-ai
systemctl is-active polymarket-dashboard

# Recent logs
echo ""
echo "--- Recent Logs (last 25 lines) ---"
journalctl -u polymarket-ai --no-pager -n 25 2>/dev/null

# Backfill status
echo ""
echo "--- Backfill process ---"
pgrep -af backfill || echo "No backfill process running"

# Memory
echo ""
echo "--- Memory ---"
free -m | head -2
