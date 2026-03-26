import asyncio, os, asyncpg

async def main():
    dsn = None
    with open("/opt/pa2-shared/.env") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    conn = await asyncpg.connect(dsn)

    row = await conn.fetchrow("""
        SELECT COUNT(DISTINCT event_data->>'trader') as unique_traders
        FROM trade_events
        WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
          AND event_data->>'trader' IS NOT NULL
    """)
    print(f"=== Unique traders copied: {row['unique_traders']} ===")

    rows = await conn.fetch("""
        WITH trader_entries AS (
            SELECT event_data->>'trader' as trader, te.market_id
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'ENTRY'
              AND event_data->>'trader' IS NOT NULL
        ),
        trader_pnl AS (
            SELECT t.trader, COUNT(*) as trades,
                   COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
                   COALESCE(SUM(pt.realized_pnl), 0) as pnl
            FROM trader_entries t
            JOIN paper_trades pt ON pt.bot_name = 'MirrorBot' AND pt.market_id = t.market_id
            WHERE pt.resolved_at IS NOT NULL
            GROUP BY t.trader
        )
        SELECT trader, trades, wins, pnl,
               CASE WHEN trades > 0 THEN wins::float / trades * 100 ELSE 0 END as wr
        FROM trader_pnl
        ORDER BY trades DESC
        LIMIT 20
    """)
    print(f"\n=== Top 20 by volume ===")
    print(f"{'Trader':>14} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        print(f"{r['trader'][:14]:>14} {r['trades']:>7} {r['wins']:>6} {r['wr']:>6.1f}% ${r['pnl']:>11.2f}")

    rows = await conn.fetch("""
        WITH trader_entries AS (
            SELECT event_data->>'trader' as trader, te.market_id
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'ENTRY'
              AND event_data->>'trader' IS NOT NULL
        ),
        trader_pnl AS (
            SELECT t.trader, COUNT(*) as trades,
                   COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
                   COALESCE(SUM(pt.realized_pnl), 0) as pnl
            FROM trader_entries t
            JOIN paper_trades pt ON pt.bot_name = 'MirrorBot' AND pt.market_id = t.market_id
            WHERE pt.resolved_at IS NOT NULL
            GROUP BY t.trader
        )
        SELECT trader, trades, wins, pnl,
               CASE WHEN trades > 0 THEN wins::float / trades * 100 ELSE 0 END as wr
        FROM trader_pnl WHERE trades >= 5
        ORDER BY pnl DESC LIMIT 15
    """)
    print(f"\n=== Top 15 by PnL (min 5) ===")
    print(f"{'Trader':>14} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        print(f"{r['trader'][:14]:>14} {r['trades']:>7} {r['wins']:>6} {r['wr']:>6.1f}% ${r['pnl']:>11.2f}")

    rows = await conn.fetch("""
        WITH trader_entries AS (
            SELECT event_data->>'trader' as trader, te.market_id
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'ENTRY'
              AND event_data->>'trader' IS NOT NULL
        ),
        trader_pnl AS (
            SELECT t.trader, COUNT(*) as trades,
                   COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
                   COALESCE(SUM(pt.realized_pnl), 0) as pnl
            FROM trader_entries t
            JOIN paper_trades pt ON pt.bot_name = 'MirrorBot' AND pt.market_id = t.market_id
            WHERE pt.resolved_at IS NOT NULL
            GROUP BY t.trader
        )
        SELECT trader, trades, wins, pnl,
               CASE WHEN trades > 0 THEN wins::float / trades * 100 ELSE 0 END as wr
        FROM trader_pnl WHERE trades >= 5
        ORDER BY pnl ASC LIMIT 15
    """)
    print(f"\n=== Bottom 15 by PnL (min 5) ===")
    print(f"{'Trader':>14} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        print(f"{r['trader'][:14]:>14} {r['trades']:>7} {r['wins']:>6} {r['wr']:>6.1f}% ${r['pnl']:>11.2f}")

    rows = await conn.fetch("""
        WITH trader_entries AS (
            SELECT event_data->>'trader' as trader, te.market_id
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'ENTRY'
              AND event_data->>'trader' IS NOT NULL
        ),
        trader_pnl AS (
            SELECT t.trader, COUNT(*) as trades,
                   COALESCE(SUM(pt.realized_pnl), 0) as pnl
            FROM trader_entries t
            JOIN paper_trades pt ON pt.bot_name = 'MirrorBot' AND pt.market_id = t.market_id
            WHERE pt.resolved_at IS NOT NULL
            GROUP BY t.trader
            HAVING COUNT(*) >= 5
        )
        SELECT
            COUNT(*) as total_traders,
            COUNT(*) FILTER (WHERE pnl > 0) as profitable,
            COUNT(*) FILTER (WHERE pnl <= 0) as unprofitable,
            COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0) as profit_sum,
            COALESCE(SUM(pnl) FILTER (WHERE pnl <= 0), 0) as loss_sum
        FROM trader_pnl
    """)
    r = rows[0]
    print(f"\n=== Trader profitability (min 5 trades) ===")
    print(f"  Total: {r['total_traders']}, Profitable: {r['profitable']} (${r['profit_sum']:.2f}), Unprofitable: {r['unprofitable']} (${r['loss_sum']:.2f})")

    rows = await conn.fetch("""
        SELECT COALESCE(event_data->>'source', 'unknown') as source, COUNT(*) as entries
        FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
        GROUP BY 1 ORDER BY 2 DESC
    """)
    print(f"\n=== Entry source ===")
    for r in rows:
        print(f"  {r['source']}: {r['entries']}")

    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (event_data->>'whale_trade_usd')::numeric IS NULL THEN 'null'
                WHEN (event_data->>'whale_trade_usd')::numeric = 0 THEN '$0'
                WHEN (event_data->>'whale_trade_usd')::numeric < 10 THEN '<$10'
                WHEN (event_data->>'whale_trade_usd')::numeric < 50 THEN '$10-50'
                WHEN (event_data->>'whale_trade_usd')::numeric < 100 THEN '$50-100'
                WHEN (event_data->>'whale_trade_usd')::numeric < 500 THEN '$100-500'
                WHEN (event_data->>'whale_trade_usd')::numeric < 1000 THEN '$500-1K'
                ELSE '$1K+'
            END as bucket, COUNT(*) as trades
        FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
        GROUP BY 1 ORDER BY 1
    """)
    print(f"\n=== whale_trade_usd distribution ===")
    for r in rows:
        print(f"  {r['bucket']}: {r['trades']}")

    row = await conn.fetchrow("SELECT COUNT(*) as cnt FROM elite_traders WHERE is_active = true")
    print(f"\n=== Active elite traders (watchlist): {row['cnt']} ===")

    # Non-crypto only: trader profitability
    rows = await conn.fetch("""
        WITH trader_entries AS (
            SELECT event_data->>'trader' as trader, te.market_id
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'ENTRY'
              AND event_data->>'trader' IS NOT NULL
              AND LOWER(COALESCE(event_data->>'category', '')) NOT LIKE '%crypto%'
        ),
        trader_pnl AS (
            SELECT t.trader, COUNT(*) as trades,
                   COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
                   COALESCE(SUM(pt.realized_pnl), 0) as pnl
            FROM trader_entries t
            JOIN paper_trades pt ON pt.bot_name = 'MirrorBot' AND pt.market_id = t.market_id
            WHERE pt.resolved_at IS NOT NULL
            GROUP BY t.trader
            HAVING COUNT(*) >= 3
        )
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE pnl > 0) as profitable,
            COUNT(*) FILTER (WHERE pnl <= 0) as unprofitable,
            COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0) as win_pnl,
            COALESCE(SUM(pnl) FILTER (WHERE pnl <= 0), 0) as loss_pnl
        FROM trader_pnl
    """)
    r = rows[0]
    print(f"\n=== NON-CRYPTO trader profitability (min 3 trades) ===")
    print(f"  Total: {r['total']}, Profitable: {r['profitable']} (${r['win_pnl']:.2f}), Unprofitable: {r['unprofitable']} (${r['loss_pnl']:.2f})")

    await conn.close()

asyncio.run(main())
