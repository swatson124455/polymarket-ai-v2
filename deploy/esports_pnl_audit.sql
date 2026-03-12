-- ============================================================================
-- Esports P&L Audit & Cleanup Script
-- Run on VPS: psql -U polymarket -d polymarket -f esports_pnl_audit.sql
-- ============================================================================
-- This script is SAFE to run multiple times (idempotent).
-- All destructive operations are preceded by diagnostic SELECTs.
-- Review output before running the UPDATE/DELETE sections.
-- ============================================================================

\echo '=== SECTION 1: DUPLICATE PAPER TRADES PER MARKET ==='
\echo 'Trades where the same bot has multiple BUY entries on the same market+side.'
\echo 'DISTINCT ON picks an arbitrary row — duplicates corrupt P&L aggregation.'

SELECT market_id, bot_name, side, COUNT(*) as dupes,
       ARRAY_AGG(id ORDER BY created_at) as trade_ids,
       ARRAY_AGG(price ORDER BY created_at) as prices,
       ARRAY_AGG(size ORDER BY created_at) as sizes,
       ARRAY_AGG(realized_pnl ORDER BY created_at) as pnls
FROM paper_trades
WHERE bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND side IN ('YES', 'NO')
GROUP BY market_id, bot_name, side
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC;


\echo '=== SECTION 2: ORPHANED PREDICTION LOG ENTRIES ==='
\echo 'Predictions with no corresponding paper_trade (edge was found but trade was not placed).'
\echo 'These are normal — just informational.'

SELECT epl.game, COUNT(*) as orphan_count
FROM esports_prediction_log epl
LEFT JOIN paper_trades pt ON epl.market_id = pt.market_id
  AND pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
WHERE pt.id IS NULL
GROUP BY epl.game
ORDER BY orphan_count DESC;


\echo '=== SECTION 3: UNRESOLVED PAPER TRADES ON RESOLVED MARKETS ==='
\echo 'Trades where the market resolved but paper_trade has no realized_pnl.'
\echo 'These should be caught by backfill_paper_trades_resolution().'

SELECT pt.id, pt.market_id, pt.bot_name, pt.side, pt.price, pt.size,
       pt.resolution as trade_resolution, m.resolution as market_resolution,
       pt.realized_pnl, pt.created_at
FROM paper_trades pt
JOIN markets m ON (CAST(m.id AS TEXT) = pt.market_id OR m.condition_id = pt.market_id)
WHERE pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND m.resolution IN ('YES', 'NO')
  AND pt.side IN ('YES', 'NO')
  AND (pt.realized_pnl IS NULL OR pt.resolution IS NULL OR pt.resolution NOT IN ('YES', 'NO'))
ORDER BY pt.created_at DESC;


\echo '=== SECTION 4: PREDICTION LOG WITH NULL actual_outcome ON RESOLVED TRADES ==='
\echo 'Predictions that should have been backfilled by _backfill_esports_outcomes().'

SELECT epl.id, epl.market_id, epl.game, epl.side, epl.predicted_prob,
       epl.actual_outcome, pt.realized_pnl, pt.resolution
FROM esports_prediction_log epl
JOIN paper_trades pt ON epl.market_id = pt.market_id
  AND pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND pt.side IN ('YES', 'NO')
WHERE pt.realized_pnl IS NOT NULL
  AND epl.actual_outcome IS NULL
ORDER BY epl.created_at DESC;


\echo '=== SECTION 5: STALE/DEAD POSITIONS FOR ESPORTS BOTS ==='
\echo 'Positions still marked open but market has resolved.'

SELECT p.id, p.market_id, p.bot_name, p.side, p.size, p.entry_price,
       p.status, p.unrealized_pnl, m.resolution, p.created_at
FROM positions p
JOIN markets m ON p.market_id = m.id
WHERE p.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND m.resolution IN ('YES', 'NO')
  AND p.status = 'open'
ORDER BY p.created_at DESC;


\echo '=== SECTION 6: P&L SANITY CHECK ==='
\echo 'Per-bot P&L summary from paper_trades (ground truth).'

SELECT pt.bot_name,
       COUNT(*) as total_trades,
       COUNT(*) FILTER (WHERE pt.realized_pnl > 0) as wins,
       COUNT(*) FILTER (WHERE pt.realized_pnl <= 0) as losses,
       ROUND(SUM(pt.realized_pnl)::numeric, 2) as total_pnl,
       ROUND(AVG(pt.realized_pnl)::numeric, 4) as avg_pnl,
       ROUND(AVG(pt.price)::numeric, 4) as avg_entry_price
FROM paper_trades pt
WHERE pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND pt.realized_pnl IS NOT NULL
  AND pt.side IN ('YES', 'NO')
GROUP BY pt.bot_name
ORDER BY pt.bot_name;


\echo '=== SECTION 7: GAME-LEVEL P&L (using corrected side-aware join) ==='

WITH game_map AS (
    SELECT DISTINCT ON (market_id, side) market_id, side, game, edge
    FROM esports_prediction_log
    ORDER BY market_id, side, created_at DESC
)
SELECT
    COALESCE(gm.game, 'unknown') as game,
    COUNT(*) as trades,
    COUNT(*) FILTER (WHERE pt.realized_pnl > 0) as wins,
    ROUND(SUM(pt.realized_pnl)::numeric, 2) as pnl,
    ROUND(AVG(ABS(gm.edge))::numeric, 4) as avg_edge
FROM paper_trades pt
LEFT JOIN game_map gm ON pt.market_id = gm.market_id
    AND UPPER(gm.side) = UPPER(pt.side)
WHERE pt.bot_name LIKE 'Esports%'
  AND pt.realized_pnl IS NOT NULL
  AND pt.side IN ('YES', 'NO')
GROUP BY COALESCE(gm.game, 'unknown')
ORDER BY trades DESC;


\echo '=== SECTION 8: GLICKO-2 RATINGS HEALTH ==='
\echo 'Teams with extreme or default ratings that may indicate bad data.'

SELECT game, COUNT(*) as total_teams,
       COUNT(*) FILTER (WHERE mu = 1500.0 AND phi = 350.0) as default_ratings,
       ROUND(MIN(mu)::numeric, 0) as min_mu,
       ROUND(MAX(mu)::numeric, 0) as max_mu,
       ROUND(AVG(phi)::numeric, 1) as avg_phi
FROM glicko2_ratings
GROUP BY game
ORDER BY game;


-- ============================================================================
-- CLEANUP OPERATIONS (review diagnostic output first!)
-- ============================================================================

\echo '=== CLEANUP 1: Re-run resolution backfill for unresolved esports trades ==='
\echo '(This is handled by the application code; run manually if needed)'
\echo 'UPDATE paper_trades pt SET resolution = m.resolution, ...'
\echo 'Skipping automated UPDATE — use application backfill_paper_trades_resolution() instead.'


\echo '=== CLEANUP 2: Close stale open positions on resolved markets ==='
\echo 'Uncomment to execute:'

-- UPDATE positions p
-- SET status = 'closed',
--     unrealized_pnl = (
--         CASE
--             WHEN UPPER(p.side) = m.resolution THEN (1.0 - p.entry_price) * p.size
--             ELSE (0.0 - p.entry_price) * p.size
--         END
--     ) - (p.entry_price * p.size * 0.015)
-- FROM markets m
-- WHERE p.market_id = m.id
--   AND m.resolution IN ('YES', 'NO')
--   AND p.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
--   AND p.status = 'open';


\echo '=== AUDIT COMPLETE ==='
