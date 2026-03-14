#!/usr/bin/env python3
"""Delete duplicate RESOLUTION events across all partitions. Keep MIN(sequence_num) per combo."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

async def go():
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        for month in ["01", "02", "03"]:
            tbl = f"trade_events_2026_{month}"
            try:
                # Check for dupes in this partition
                r0 = await s.execute(text(f"""
                    SELECT COUNT(*), COUNT(DISTINCT (market_id, side, bot_name))
                    FROM {tbl} WHERE event_type = 'RESOLUTION'
                """))
                row = r0.fetchone()
                if not row[0] or row[0] == row[1]:
                    print(f"{tbl}: {row[0] or 0} events, no dupes")
                    continue

                print(f"{tbl}: {row[0]} events for {row[1]} unique = {row[0]/max(row[1],1):.1f}x — cleaning...")

                # Disable trigger
                await s.execute(text(f"ALTER TABLE {tbl} DISABLE TRIGGER trg_trade_events_immutable"))
                await s.commit()

                # Delete dupes
                r = await s.execute(text(f"""
                    DELETE FROM {tbl}
                    WHERE event_type = 'RESOLUTION'
                      AND sequence_num NOT IN (
                        SELECT MIN(sequence_num)
                        FROM {tbl}
                        WHERE event_type = 'RESOLUTION'
                        GROUP BY bot_name, market_id, side
                      )
                """))
                deleted = r.rowcount
                await s.commit()

                # Re-enable
                await s.execute(text(f"ALTER TABLE {tbl} ENABLE TRIGGER trg_trade_events_immutable"))
                await s.commit()
                print(f"  Deleted {deleted} duplicates")
            except Exception as e:
                print(f"  {tbl} error: {e}")

        # Final verification
        r2 = await s.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT (market_id, side, bot_name))
            FROM trade_events WHERE event_type = 'RESOLUTION'
        """))
        row = r2.fetchone()
        print(f"\nFinal: {row[0]} RESOLUTION events for {row[1]} unique combos = {row[0]/max(row[1],1):.1f}x")

        # EsportsBot summary
        r3 = await s.execute(text("""
            SELECT event_type, COUNT(*), COALESCE(SUM(realized_pnl),0)
            FROM trade_events WHERE bot_name = 'EsportsBot'
            GROUP BY event_type ORDER BY event_type
        """))
        print("\nEsportsBot trade_events (CLEAN):")
        for row in r3.fetchall():
            print(f"  {row[0]:>12}: {row[1]:>4} events, rpnl=${float(row[2]):.2f}")
    await db.close()

asyncio.run(go())
