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

    # 1) ALL-TIME confidence buckets
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN confidence < 0.50 THEN '0.00-0.49'
                WHEN confidence < 0.55 THEN '0.50-0.54'
                WHEN confidence < 0.60 THEN '0.55-0.59'
                WHEN confidence < 0.65 THEN '0.60-0.64'
                WHEN confidence < 0.70 THEN '0.65-0.69'
                WHEN confidence >= 0.70 THEN '0.70+'
            END as bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE resolution = side) as wins,
            COUNT(*) FILTER (WHERE resolution IS NOT NULL AND resolution != side) as losses,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """)
    print("=== ALL-TIME confidence buckets (paper_trades) ===")
    print(f"{'Bucket':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>12}")
    for r in rows:
        total = r['wins'] + r['losses']
        wr = (r['wins'] / total * 100) if total > 0 else 0
        print(f"{r['bucket']:<12} {r['trades']:>7} {r['wins']:>6} {r['losses']:>7} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 2) Pre-cleanup (resolved before our session started)
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN confidence < 0.50 THEN '0.00-0.49'
                WHEN confidence < 0.55 THEN '0.50-0.54'
                WHEN confidence < 0.60 THEN '0.55-0.59'
                WHEN confidence < 0.65 THEN '0.60-0.64'
                WHEN confidence < 0.70 THEN '0.65-0.69'
                WHEN confidence >= 0.70 THEN '0.70+'
            END as bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE resolution = side) as wins,
            COUNT(*) FILTER (WHERE resolution IS NOT NULL AND resolution != side) as losses,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
          AND resolved_at < '2026-03-25 20:00:00'
        GROUP BY 1
        ORDER BY 1
    """)
    print("\n=== Pre-S131 cleanup (resolved before Mar 25 20:00 UTC) ===")
    print(f"{'Bucket':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>12}")
    for r in rows:
        total = r['wins'] + r['losses']
        wr = (r['wins'] / total * 100) if total > 0 else 0
        print(f"{r['bucket']:<12} {r['trades']:>7} {r['wins']:>6} {r['losses']:>7} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 3) Last 24h only
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN confidence < 0.50 THEN '0.00-0.49'
                WHEN confidence < 0.55 THEN '0.50-0.54'
                WHEN confidence < 0.60 THEN '0.55-0.59'
                WHEN confidence < 0.65 THEN '0.60-0.64'
                WHEN confidence < 0.70 THEN '0.65-0.69'
                WHEN confidence >= 0.70 THEN '0.70+'
            END as bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE resolution = side) as wins,
            COUNT(*) FILTER (WHERE resolution IS NOT NULL AND resolution != side) as losses,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
          AND resolved_at >= NOW() - INTERVAL '24 hours'
        GROUP BY 1
        ORDER BY 1
    """)
    print("\n=== Last 24h confidence buckets ===")
    print(f"{'Bucket':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>12}")
    for r in rows:
        total = r['wins'] + r['losses']
        wr = (r['wins'] / total * 100) if total > 0 else 0
        print(f"{r['bucket']:<12} {r['trades']:>7} {r['wins']:>6} {r['losses']:>7} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 4) NON-CRYPTO only all-time
    rows = await conn.fetch("""
        SELECT
            CASE
                WHEN pt.confidence < 0.50 THEN '0.00-0.49'
                WHEN pt.confidence < 0.55 THEN '0.50-0.54'
                WHEN pt.confidence < 0.60 THEN '0.55-0.59'
                WHEN pt.confidence < 0.65 THEN '0.60-0.64'
                WHEN pt.confidence < 0.70 THEN '0.65-0.69'
                WHEN pt.confidence >= 0.70 THEN '0.70+'
            END as bucket,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE pt.resolution = pt.side) as wins,
            COUNT(*) FILTER (WHERE pt.resolution IS NOT NULL AND pt.resolution != pt.side) as losses,
            COALESCE(SUM(pt.realized_pnl), 0) as pnl
        FROM paper_trades pt
        LEFT JOIN LATERAL (
            SELECT event_data->>'category' as category
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot'
              AND te.event_type = 'ENTRY'
              AND te.market_id = pt.market_id
            LIMIT 1
        ) cat ON true
        WHERE pt.bot_name = 'MirrorBot'
          AND pt.resolved_at IS NOT NULL
          AND LOWER(COALESCE(cat.category, '')) NOT LIKE '%crypto%'
        GROUP BY 1
        ORDER BY 1
    """)
    print("\n=== NON-CRYPTO all-time confidence buckets ===")
    print(f"{'Bucket':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>12}")
    total_pnl = 0
    for r in rows:
        total = r['wins'] + r['losses']
        wr = (r['wins'] / total * 100) if total > 0 else 0
        total_pnl += float(r['pnl'])
        print(f"{r['bucket']:<12} {r['trades']:>7} {r['wins']:>6} {r['losses']:>7} {wr:>6.1f}% ${r['pnl']:>11.2f}")
    print(f"{'TOTAL':<12} {'':>7} {'':>6} {'':>7} {'':>7} ${total_pnl:>11.2f}")

    # 5) By week - did WR change over time or always bad?
    rows = await conn.fetch("""
        SELECT
            date_trunc('week', resolved_at)::date as week,
            COUNT(*) as trades,
            COUNT(*) FILTER (WHERE resolution = side) as wins,
            COUNT(*) FILTER (WHERE resolution IS NOT NULL AND resolution != side) as losses,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """)
    print("\n=== Weekly P&L trend ===")
    print(f"{'Week':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>12}")
    for r in rows:
        total = r['wins'] + r['losses']
        wr = (r['wins'] / total * 100) if total > 0 else 0
        print(f"{str(r['week']):<12} {r['trades']:>7} {r['wins']:>6} {r['losses']:>7} {wr:>6.1f}% ${r['pnl']:>11.2f}")

    # 6) Were the S120 handoff numbers from trade_events or paper_trades?
    # S120 reported +$26,986 — let's see what paper_trades showed at that time
    row = await conn.fetchrow("""
        SELECT
            COUNT(*) as cnt,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
          AND resolved_at < '2026-03-23 00:00:00'
        """)
    print(f"\n=== paper_trades P&L before S120 (Mar 23) ===")
    print(f"  resolved={row['cnt']}, pnl=${row['pnl']:.2f}")

    row = await conn.fetchrow("""
        SELECT
            COUNT(*) as cnt,
            COALESCE(SUM(realized_pnl), 0) as pnl
        FROM paper_trades
        WHERE bot_name = 'MirrorBot'
          AND resolved_at IS NOT NULL
        """)
    print(f"\n=== paper_trades total all-time ===")
    print(f"  resolved={row['cnt']}, pnl=${row['pnl']:.2f}")

    await conn.close()

asyncio.run(main())
