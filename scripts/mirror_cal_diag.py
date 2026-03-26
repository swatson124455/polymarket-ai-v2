import asyncio, os, asyncpg, json

async def main():
    dsn = None
    with open("/opt/pa2-shared/.env") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    conn = await asyncpg.connect(dsn)

    # 1) conf_base vs outcomes
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'conf_base')::numeric < 0.48 THEN '<0.48'
                WHEN (te.event_data->>'conf_base')::numeric < 0.50 THEN '0.48-0.49'
                WHEN (te.event_data->>'conf_base')::numeric < 0.52 THEN '0.50-0.51'
                WHEN (te.event_data->>'conf_base')::numeric >= 0.52 THEN '0.52+'
            END as base_bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'conf_base' IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """)
    print("=== conf_base vs outcomes ===")
    print(f"{'Base':>10} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['base_bucket']:>10} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 2) contrarian vs consensus
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'conf_price_adj')::numeric > 0.01 THEN 'contrarian'
                WHEN (te.event_data->>'conf_price_adj')::numeric < -0.01 THEN 'consensus_pen'
                ELSE 'neutral'
            END as price_type,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'conf_price_adj' IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== contrarian vs consensus ===")
    print(f"{'Type':>15} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['price_type']:>15} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 3) whale reliability
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'rel_mult')::numeric < 0.90 THEN '<0.90'
                WHEN (te.event_data->>'rel_mult')::numeric < 1.00 THEN '0.90-0.99'
                WHEN (te.event_data->>'rel_mult')::numeric < 1.05 THEN '1.00-1.04'
                WHEN (te.event_data->>'rel_mult')::numeric >= 1.05 THEN '1.05+'
            END as rel_bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'rel_mult' IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== rel_mult (whale reliability) ===")
    print(f"{'Reliability':>15} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['rel_bucket']:>15} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 4) consensus
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'consensus')::int = 1 THEN '1 whale'
                WHEN (te.event_data->>'consensus')::int = 2 THEN '2 whales'
                WHEN (te.event_data->>'consensus')::int >= 3 THEN '3+ whales'
            END as consensus,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'consensus' IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== consensus (# whales same side) ===")
    print(f"{'Consensus':>12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['consensus']:>12} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 5) YES vs NO
    rows = await conn.fetch("""
        SELECT pt.side,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM paper_trades pt
        WHERE pt.bot_name = 'MirrorBot' AND pt.resolved_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== YES vs NO side ===")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"  {r['side']}: {r['trades']} trades, {r['wins']}W, {wr:.1f}% WR, ${r['pnl']:.2f}")

    # 6) entry price bucket
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN pt.price < 0.20 THEN '0.00-0.19'
                WHEN pt.price < 0.40 THEN '0.20-0.39'
                WHEN pt.price < 0.60 THEN '0.40-0.59'
                WHEN pt.price < 0.80 THEN '0.60-0.79'
                ELSE '0.80-1.00'
            END as price_bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM paper_trades pt
        WHERE pt.bot_name = 'MirrorBot' AND pt.resolved_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== Entry price bucket ===")
    print(f"{'Price':>12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['price_bucket']:>12} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 7) ML shadow
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN te.event_data->>'ml_score_combo' IS NULL THEN 'no_ml'
                WHEN te.event_data->>'ml_decision_combo' = 'true' THEN 'ml_trade'
                ELSE 'ml_skip'
            END as ml_decision,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== ML shadow decision ===")
    print(f"{'ML':>12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['ml_decision']:>12} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 8) whale_trade_usd size buckets
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'whale_trade_usd')::numeric < 50 THEN '<$50'
                WHEN (te.event_data->>'whale_trade_usd')::numeric < 500 THEN '$50-500'
                WHEN (te.event_data->>'whale_trade_usd')::numeric < 5000 THEN '$500-5K'
                WHEN (te.event_data->>'whale_trade_usd')::numeric >= 5000 THEN '$5K+'
            END as size_bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'whale_trade_usd' IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== Whale trade size ===")
    print(f"{'Size':>12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['size_bucket']:>12} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 9) spread bucket
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN (te.event_data->>'spread')::numeric < 0.05 THEN '<5c'
                WHEN (te.event_data->>'spread')::numeric < 0.10 THEN '5-10c'
                WHEN (te.event_data->>'spread')::numeric < 0.20 THEN '10-20c'
                WHEN (te.event_data->>'spread')::numeric >= 0.20 THEN '20c+'
            END as spread_bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM trade_events te
        JOIN paper_trades pt ON pt.bot_name = te.bot_name AND pt.market_id = te.market_id
        WHERE te.bot_name = 'MirrorBot'
          AND te.event_type = 'ENTRY'
          AND pt.resolved_at IS NOT NULL
          AND te.event_data->>'spread' IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    print("\n=== Spread at entry ===")
    print(f"{'Spread':>12} {'Trades':>7} {'Wins':>6} {'WR':>7} {'PnL':>12}")
    for r in rows:
        wr = (r['wins'] / r['trades'] * 100) if r['trades'] > 0 else 0
        print(f"{r['spread_bucket']:>12} {r['trades']:>7} {r['wins']:>6} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    await conn.close()

asyncio.run(main())
