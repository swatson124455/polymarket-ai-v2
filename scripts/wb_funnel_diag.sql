-- WB funnel deep-dive (S241 diagnostic, read-only).
-- Mirrors the live engine query base_engine/base_engine.py:2438-2448
-- (active=true AND resolved=false AND token present AND yes_price 0.01-0.99,
--  ORDER BY liquidity DESC LIMIT 2500) to measure ghost crowd-out.
SELECT '1_active_unresolved_weather_total' AS k, COUNT(*)::text AS v FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather'
UNION ALL
SELECT '2_fresh_end_date_iso_future', COUNT(*)::text FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather' AND end_date_iso >= NOW()
UNION ALL
SELECT '3_ghost_end_date_iso_past', COUNT(*)::text FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather' AND end_date_iso < NOW()
UNION ALL
SELECT '4_null_end_date_iso', COUNT(*)::text FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather' AND end_date_iso IS NULL
UNION ALL
SELECT '5_max_created', MAX(created_at)::text FROM markets WHERE LOWER(COALESCE(category,''))='weather'
UNION ALL
SELECT '6_max_updated', MAX(updated_at)::text FROM markets WHERE LOWER(COALESCE(category,''))='weather';

WITH ranked AS (
  SELECT id, end_date_iso, ROW_NUMBER() OVER (ORDER BY COALESCE(liquidity,0) DESC) rn
  FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather'
    AND ((yes_token_id IS NOT NULL AND yes_token_id <> '') OR (no_token_id IS NOT NULL AND no_token_id <> ''))
    AND COALESCE(yes_price,0.5) BETWEEN 0.01 AND 0.99
)
SELECT '7_fresh_in_top2500' AS k, COUNT(*)::text AS v FROM ranked WHERE rn <= 2500 AND end_date_iso >= NOW()
UNION ALL
SELECT '8_fresh_crowded_out', COUNT(*)::text FROM ranked WHERE rn > 2500 AND end_date_iso >= NOW();

SELECT '9_fresh_q' AS k, LEFT(question,70) AS v FROM markets
  WHERE active=true AND resolved=false AND LOWER(COALESCE(category,''))='weather' AND end_date_iso >= NOW()
  ORDER BY end_date_iso LIMIT 12;
