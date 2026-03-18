"""One-time script: backfill RESOLUTION trade_events for esports markets
that were resolved in traded_markets but missing RESOLUTION events
due to the entry_price column bug in S104.

Usage: python scripts/backfill_esports_resolution_events.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text


async def backfill():
    db_url = None
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                db_url = line.strip().split("=", 1)[1].replace(
                    "postgresql://", "postgresql+asyncpg://"
                )
    if not db_url:
        print("ERROR: DATABASE_URL not found in .env")
        return

    engine = create_async_engine(db_url)

    async with AsyncSession(engine) as s:
        # Get markets resolved but missing RESOLUTION events
        r = await s.execute(
            text(
                """SELECT tm.market_id, tm.resolution, tm.resolved_at
                FROM traded_markets tm
                WHERE (tm.bot_names LIKE '%EsportsBot%'
                       OR tm.bot_names LIKE '%EsportsLiveBot%'
                       OR tm.bot_names LIKE '%EsportsSeriesBot%')
                AND tm.resolved = TRUE
                AND NOT EXISTS (
                    SELECT 1 FROM trade_events te
                    WHERE te.market_id = tm.market_id
                    AND te.event_type = 'RESOLUTION'
                    AND te.bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot')
                )"""
            )
        )
        missing_markets = r.fetchall()
        print(f"Found {len(missing_markets)} markets needing RESOLUTION events")

        created = 0
        for market_id, resolution, resolved_at in missing_markets:
            # Get paper_trades for this market
            pt_r = await s.execute(
                text(
                    """SELECT bot_name, side, price, size
                    FROM paper_trades
                    WHERE market_id = :mid
                    AND bot_name IN ('EsportsBot', 'EsportsLiveBot', 'EsportsSeriesBot')
                    AND LOWER(side) != 'sell'"""
                ),
                {"mid": market_id},
            )
            trades = pt_r.fetchall()

            for bot_name, side, entry_price, size in trades:
                entry_price = float(entry_price) if entry_price else 0.0
                size = float(size) if size else 0.0
                won = side == resolution
                pnl = (1.0 - entry_price) * size if won else -entry_price * size

                try:
                    ins = await s.execute(
                        text(
                            """INSERT INTO trade_events
                            (event_type, bot_name, market_id, side, size, price,
                             realized_pnl, correlation_id, event_time, event_data)
                            SELECT 'RESOLUTION', :bn, :mid, :side, :size, 0.0,
                                   :pnl, :corr, :evt, '{}'::jsonb
                            WHERE NOT EXISTS (
                                SELECT 1 FROM trade_events
                                WHERE correlation_id = :corr
                                AND bot_name = :bn
                                AND event_type = 'RESOLUTION'
                            )"""
                        ),
                        {
                            "bn": bot_name,
                            "mid": market_id,
                            "side": side,
                            "size": size,
                            "pnl": round(pnl, 6),
                            "corr": f"resolution:{market_id}",
                            "evt": resolved_at,
                        },
                    )
                    if ins.rowcount > 0:
                        created += 1
                        print(
                            f"  Created: {market_id[:30]} {bot_name} {side} pnl=${pnl:.2f}"
                        )
                except Exception as e:
                    print(f"  Error: {market_id[:30]} {bot_name}: {e}")

            # Also update paper_trades resolution
            await s.execute(
                text(
                    """UPDATE paper_trades SET resolution = :res, resolved_at = :rat
                    WHERE market_id = :mid AND resolution IS NULL"""
                ),
                {"res": resolution, "rat": resolved_at, "mid": market_id},
            )

        await s.commit()
        print(f"\nDone. Created {created} RESOLUTION trade_events")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
