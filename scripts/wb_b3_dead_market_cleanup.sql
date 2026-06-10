-- B3 (S241): one-time deactivation of dead weather-category market rows.
-- Operator-authorized 2026-06-10 ("clean dead items").
--
-- Scope: WEATHER category ONLY (other categories untouched — MB/EB lanes).
-- Criteria (conservative):
--   * active=true AND resolved=false AND category='weather'
--   * AND ( ended >2 days ago               -- beyond WB's freshness window
--        OR (NULL end_date_iso AND created >7 days ago) )  -- never-enriched corpses;
--                                            -- a just-ingested market is spared
--   * AND no open/reserving position on the market (any bot, paper or live)
--
-- Reversal: ids are exported to /home/ubuntu/wb_b3_deactivated_ids_<date>.csv
-- before the UPDATE;  UPDATE markets SET active=true WHERE id IN (...csv ids...);
--
-- Usage:
--   psql "$URL" -v dryrun=1 -f wb_b3_dead_market_cleanup.sql   # counts only
--   psql "$URL" -f wb_b3_dead_market_cleanup.sql               # execute
--
-- (This file is kept in scripts/ as the audit record of exactly what ran.)

-- ── DRY-RUN / preview counts (always printed) ────────────────────────────────
WITH cand AS (
  SELECT m.id FROM markets m
  WHERE m.active = true AND m.resolved = false
    AND LOWER(COALESCE(m.category, '')) = 'weather'
    AND (m.end_date_iso < NOW() - INTERVAL '2 days'
         OR (m.end_date_iso IS NULL AND m.created_at < NOW() - INTERVAL '7 days'))
)
SELECT 'A_candidates_total' AS k, COUNT(*)::text AS v FROM cand
UNION ALL
SELECT 'B_cand_with_open_or_reserving_pos_EXCLUDED',
       COUNT(DISTINCT c.id)::text
  FROM cand c JOIN positions p ON p.market_id = c.id AND p.status IN ('open', 'reserving')
UNION ALL
SELECT 'C_spared_recent_null_end_date', COUNT(*)::text FROM markets
  WHERE active = true AND resolved = false AND LOWER(COALESCE(category, '')) = 'weather'
    AND end_date_iso IS NULL AND created_at >= NOW() - INTERVAL '7 days'
UNION ALL
SELECT 'D_kept_fresh_future_or_last_2d', COUNT(*)::text FROM markets
  WHERE active = true AND resolved = false AND LOWER(COALESCE(category, '')) = 'weather'
    AND end_date_iso >= NOW() - INTERVAL '2 days';
