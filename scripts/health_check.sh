#!/bin/bash
# Polymarket AI V2 — Base Engine Health Check
# Run locally: bash scripts/health_check.sh
# Run via SSH: ssh -i KEY ubuntu@34.251.224.21 'bash -s' < scripts/health_check.sh

echo "=== POLYMARKET AI HEALTH CHECK ==="
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

echo "--- LAYER 1: PROCESS ---"
echo "1.1 systemd: $(systemctl is-active polymarket-ai 2>/dev/null || echo 'NOT FOUND')"
echo "1.2 processes: $(pgrep -f 'python.*main.py' | wc -l)"
echo "1.3 memory: $(systemctl show polymarket-ai --property=MemoryCurrent --value 2>/dev/null || echo N/A)"
echo "1.4 bot_log_lines_30m:"
for b in EnsembleBot ArbitrageBot MirrorBot CrossPlatformArbBot WeatherBot LogicalArbBot; do
  echo "    $b: $(journalctl -u polymarket-ai --since '30 min ago' --no-pager 2>/dev/null | grep -c "$b")"
done
echo "1.5 scan_lines_1h:"
for b in EnsembleBot ArbitrageBot MirrorBot CrossPlatformArbBot WeatherBot LogicalArbBot; do
  scans=$(journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep "$b" | grep -ci 'scan\|opportunities\|markets evaluated')
  echo "    $b: $scans"
done
echo "1.6 errors_1h: $(journalctl -u polymarket-ai --since '1 hour ago' -p err --no-pager 2>/dev/null | grep -c 'Traceback\|Exception')"
echo ""

echo "--- LAYER 2: DATABASE ---"
echo "2.1 postgresql: $(systemctl is-active postgresql 2>/dev/null || echo 'NOT FOUND')"
echo "2.2 connectivity: $(sudo -u postgres psql -d polymarket -c 'SELECT 1' -t -A 2>&1 | head -1)"
echo "2.3 active_conns: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket'" -t -A 2>&1)"
echo "2.4 idle_in_tx_leaked: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket' AND state='idle in transaction' AND query_start < NOW()-INTERVAL '60s'" -t -A 2>&1)"
echo "2.5 slow_queries_30s: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='polymarket' AND state='active' AND query_start < NOW()-INTERVAL '30s'" -t -A 2>&1)"
echo "2.6 core_tables:"
sudo -u postgres psql -d polymarket -c "
  SELECT 'markets' AS t, COUNT(*) FROM markets
  UNION ALL SELECT 'positions', COUNT(*) FROM positions
  UNION ALL SELECT 'paper_trades', COUNT(*) FROM paper_trades
  UNION ALL SELECT 'prediction_log', COUNT(*) FROM prediction_log
  UNION ALL SELECT 'system_config', COUNT(*) FROM system_config
  UNION ALL SELECT 'market_prices', COUNT(*) FROM market_prices
  ORDER BY t;" -t -A 2>&1 | sed 's/^/    /'
echo "2.7 db_size: $(sudo -u postgres psql -d polymarket -c "SELECT pg_size_pretty(pg_database_size('polymarket'))" -t -A 2>&1)"
echo "2.7 disk: $(df -h /var/lib/postgresql 2>/dev/null | tail -1 | awk '{print $5 " used of " $2}')"
echo ""

echo "--- LAYER 3: DATA PIPELINE ---"
echo "3.1 markets: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*)||' total, '||COUNT(*) FILTER (WHERE active)||' active, '||ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(updated_at)))/3600,1)||'h stale' FROM markets" -t -A 2>&1)"
echo "3.2 price_staleness_min: $(sudo -u postgres psql -d polymarket -c "SELECT ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(timestamp)))/60,1) FROM market_prices" -t -A 2>&1)"
echo "3.3 ingestion_24h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FILTER (WHERE status='success')||'/'||COUNT(*) FROM sync_log WHERE started_at > NOW()-INTERVAL '24h'" -t -A 2>&1)"
echo "3.4 predictions_1h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM prediction_log WHERE prediction_time > NOW()-INTERVAL '1 hour'" -t -A 2>&1)"
echo "3.5 resolution_backfill_h: $(sudo -u postgres psql -d polymarket -c "SELECT ROUND(EXTRACT(EPOCH FROM (NOW()-MAX(completed_at)))/3600,1) FROM sync_log WHERE component='resolution_backfill' AND status='success'" -t -A 2>&1)"
echo ""

echo "--- LAYER 4: RISK & EXECUTION ---"
echo "4.1 kill_switch: $(sudo -u postgres psql -d polymarket -c "SELECT COALESCE(value,'not_set') FROM system_config WHERE key='kill_switch'" -t -A 2>&1)"
echo "4.2 simulation_mode: $(grep '^SIMULATION_MODE' /opt/polymarket-ai-v2/.env 2>/dev/null || echo 'NOT SET')"
echo "4.3 paper_trades_24h: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM paper_trades WHERE created_at > NOW()-INTERVAL '24h'" -t -A 2>&1)"
echo "4.4 position_prices:"
sudo -u postgres psql -d polymarket -c "
  SELECT COUNT(*) AS open,
         COUNT(*) FILTER (WHERE current_price IS NOT NULL AND current_price != entry_price) AS updated,
         COUNT(*) FILTER (WHERE current_price IS NULL OR current_price = entry_price) AS stale
  FROM positions WHERE status='open';" -t -A 2>&1 | sed 's/^/    /'
echo "4.5 stale_reservations: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE status='reserving' AND opened_at < NOW()-INTERVAL '8 min'" -t -A 2>&1)"
echo "4.6 conflicting_sides: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM (SELECT market_id FROM positions WHERE status IN ('open','reserving') GROUP BY market_id HAVING COUNT(DISTINCT side)>1) x" -t -A 2>&1)"
echo "4.7 risk_config:"
grep -E '^RISK_MIN_PRICE|^RISK_MAX_PRICE|^MODEL_REVERSAL_THRESHOLD|^ENSEMBLE_MAX_RELATIVE_SPREAD' /opt/polymarket-ai-v2/.env 2>/dev/null | sed 's/^/    /'
echo ""

echo "--- LAYER 5: MONITORING ---"
echo "5.1 health_checks_2h: $(journalctl -u polymarket-ai --since '2 hours ago' --no-pager 2>/dev/null | grep -c 'Health check')"
echo "5.2 redis: $(redis-cli -a 78psiRhepTgrmWSoy3cgNEIr ping 2>&1)"
echo "5.3 alert_webhooks: $(grep -cE '^SLACK_WEBHOOK|^DISCORD_WEBHOOK|^ALERT_WEBHOOK|^SMTP_HOST' /opt/polymarket-ai-v2/.env 2>/dev/null) configured"
echo "5.4 platt_scaling: $(grep '^PLATT_SCALING_ENABLED' /opt/polymarket-ai-v2/.env 2>/dev/null || echo 'NOT SET')"
echo "5.4 resolved_predictions: $(sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM prediction_log WHERE resolution IS NOT NULL" -t -A 2>&1)"
echo ""

echo "--- LAYER 6: RECOVERY ---"
echo "6.1 systemd_restarts: $(systemctl show polymarket-ai --property=NRestarts --value 2>/dev/null)"
echo "6.2 watchdog_restarts_24h: $(journalctl -u polymarket-ai --since '24h ago' --no-pager 2>/dev/null | grep -ci 'watchdog.*restart')"
echo "6.3 position_seeding: $(journalctl -u polymarket-ai --no-pager 2>/dev/null | grep -i 'seed.*position\|positions seeded' | tail -1)"
echo "6.4 ws_status: $(journalctl -u polymarket-ai --since '1 hour ago' --no-pager 2>/dev/null | grep -i 'websocket' | tail -1)"
echo ""

echo "=== END HEALTH CHECK ==="
