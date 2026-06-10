-- B3 (S241) EXECUTE — see wb_b3_dead_market_cleanup.sql header for criteria/authorization.
BEGIN;

CREATE TEMP TABLE _b3_ids AS
  SELECT m.id FROM markets m
  WHERE m.active = true AND m.resolved = false
    AND LOWER(COALESCE(m.category, '')) = 'weather'
    AND (m.end_date_iso < NOW() - INTERVAL '2 days'
         OR (m.end_date_iso IS NULL AND m.created_at < NOW() - INTERVAL '7 days'))
    AND NOT EXISTS (
      SELECT 1 FROM positions p
      WHERE p.market_id = m.id AND p.status IN ('open', 'reserving')
    );

-- Reversal artifact (client-side copy; survives the transaction either way)
\copy _b3_ids TO '/home/ubuntu/wb_b3_deactivated_ids_20260610.csv' CSV

UPDATE markets SET active = false WHERE id IN (SELECT id FROM _b3_ids);

SELECT 'deactivated_rows' AS k, COUNT(*)::text AS v FROM _b3_ids;

COMMIT;

-- Post-state
SELECT 'post_active_unresolved_weather' AS k, COUNT(*)::text AS v FROM markets
  WHERE active = true AND resolved = false AND LOWER(COALESCE(category, '')) = 'weather';
