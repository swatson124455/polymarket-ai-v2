"""Check sync_log status and data freshness."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from base_engine.data.database import Database
from sqlalchemy import text


async def check():
    db = Database()
    await db.init()
    async with db.get_session() as session:
        # Recent sync_log entries
        r = await session.execute(text("""
            SELECT id, sync_type, status, records_processed,
                   started_at, completed_at, error_message
            FROM sync_log ORDER BY started_at DESC LIMIT 20
        """))
        rows = r.fetchall()
        print(f"\n{'='*100}")
        print(f"SYNC LOG (last 20 entries)")
        print(f"{'='*100}")
        for row in rows:
            sid, stype, status, recs, started, completed, err = row
            err_short = (str(err)[:80] + "...") if err and len(str(err)) > 80 else (err or "")
            print(f"  #{sid:>4d}  type={str(stype or 'N/A'):15s}  status={str(status or ''):8s}  "
                  f"recs={recs or 0:>6d}  started={str(started)[:19] if started else 'N/A'}")
            if err_short:
                print(f"         error: {err_short}")

        # Data freshness
        print(f"\n{'='*100}")
        print(f"DATA FRESHNESS")
        print(f"{'='*100}")

        r2 = await session.execute(text("""
            SELECT 'markets' as tbl, COUNT(*), MAX(updated_at) FROM markets
            UNION ALL
            SELECT 'market_prices', COUNT(*), MAX(timestamp) FROM market_prices
            UNION ALL
            SELECT 'trades', COUNT(*), MAX(created_at) FROM trades
            UNION ALL
            SELECT 'paper_trades', COUNT(*), MAX(created_at) FROM paper_trades
            UNION ALL
            SELECT 'prediction_log', COUNT(*), MAX(created_at) FROM prediction_log
        """))
        for row in r2.fetchall():
            tbl, cnt, latest = row
            print(f"  {tbl:20s}  count={cnt:>10d}  latest={str(latest)[:19] if latest else 'N/A'}")

        # Currently running syncs
        r3 = await session.execute(text("""
            SELECT id, sync_type, started_at FROM sync_log
            WHERE status = 'running'
        """))
        running = r3.fetchall()
        print(f"\n  Currently running syncs: {len(running)}")
        for row in running:
            print(f"    #{row[0]} type={row[1]} started={str(row[2])[:19]}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(check())
