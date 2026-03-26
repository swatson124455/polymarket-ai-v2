"""Temporary diagnostic: P&L by entry confidence bracket (corrected join)."""
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text


async def diag():
    with open("/opt/pa2-shared/.env") as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                url = line.strip().split("=", 1)[1].replace(
                    "postgresql://", "postgresql+asyncpg://"
                )
                break
    engine = create_async_engine(url)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as s:
        # Correct join: market_id + side (token_id is NULL in RESOLUTION events)
        r = await s.execute(
            text(
                """
            WITH entry_conf AS (
                SELECT market_id, side,
                       AVG(confidence) as avg_conf
                FROM trade_events
                WHERE bot_name=:bot AND event_type='ENTRY'
                GROUP BY market_id, side
            ),
            res_pnl AS (
                SELECT market_id, side,
                       SUM(realized_pnl) as pnl,
                       COUNT(*) as n
                FROM trade_events
                WHERE bot_name=:bot AND event_type='RESOLUTION'
                GROUP BY market_id, side
            )
            SELECT CASE
                WHEN e.avg_conf < 0.30 THEN 'A:<30pct'
                WHEN e.avg_conf < 0.40 THEN 'B:30-40pct'
                WHEN e.avg_conf < 0.50 THEN 'C:40-50pct'
                WHEN e.avg_conf < 0.55 THEN 'D:50-55pct'
                WHEN e.avg_conf >= 0.55 THEN 'E:55pct+'
            END as conf_bucket,
            COUNT(*) as positions,
            SUM(CASE WHEN r.pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN r.pnl <= 0 THEN 1 ELSE 0 END) as losses,
            COALESCE(SUM(r.pnl),0)::numeric(12,2) as total_pnl
            FROM entry_conf e
            INNER JOIN res_pnl r ON e.market_id=r.market_id AND e.side=r.side
            GROUP BY 1 ORDER BY 1
        """
            ),
            {"bot": "MirrorBot"},
        )
        print("=== RESOLUTION P&L by ENTRY confidence (fixed join) ===")
        for row in r.fetchall():
            wr = int(row.wins) / max(int(row.positions), 1) * 100
            pnl_per = float(row.total_pnl) / max(int(row.positions), 1)
            print(
                f"  {row.conf_bucket:14s}  n={row.positions:4d}  "
                f"W={row.wins:3d}  L={row.losses:3d}  WR={wr:.0f}%  "
                f"PnL=${row.total_pnl}  (${pnl_per:.2f}/pos)"
            )

        # Combined EXIT + RESOLUTION
        r = await s.execute(
            text(
                """
            WITH entry_conf AS (
                SELECT market_id, side,
                       AVG(confidence) as avg_conf
                FROM trade_events
                WHERE bot_name=:bot AND event_type='ENTRY'
                GROUP BY market_id, side
            ),
            realized AS (
                SELECT market_id, side,
                       SUM(realized_pnl) as pnl,
                       COUNT(*) as n
                FROM trade_events
                WHERE bot_name=:bot AND event_type IN ('EXIT','RESOLUTION')
                GROUP BY market_id, side
            )
            SELECT CASE
                WHEN e.avg_conf < 0.30 THEN 'A:<30pct'
                WHEN e.avg_conf < 0.40 THEN 'B:30-40pct'
                WHEN e.avg_conf < 0.50 THEN 'C:40-50pct'
                WHEN e.avg_conf < 0.55 THEN 'D:50-55pct'
                WHEN e.avg_conf >= 0.55 THEN 'E:55pct+'
            END as conf_bucket,
            COUNT(*) as positions,
            SUM(CASE WHEN r.pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN r.pnl <= 0 THEN 1 ELSE 0 END) as losses,
            COALESCE(SUM(r.pnl),0)::numeric(12,2) as total_pnl
            FROM entry_conf e
            INNER JOIN realized r ON e.market_id=r.market_id AND e.side=r.side
            GROUP BY 1 ORDER BY 1
        """
            ),
            {"bot": "MirrorBot"},
        )
        print("\n=== COMBINED (EXIT+RESOLUTION) by ENTRY confidence ===")
        for row in r.fetchall():
            wr = int(row.wins) / max(int(row.positions), 1) * 100
            pnl_per = float(row.total_pnl) / max(int(row.positions), 1)
            print(
                f"  {row.conf_bucket:14s}  n={row.positions:4d}  "
                f"W={row.wins:3d}  L={row.losses:3d}  WR={wr:.0f}%  "
                f"PnL=${row.total_pnl}  (${pnl_per:.2f}/pos)"
            )

    await engine.dispose()


asyncio.run(diag())
