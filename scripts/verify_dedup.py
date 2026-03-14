#!/usr/bin/env python3
"""Verify RESOLUTION dedup is working."""
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
        r = await s.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT (market_id, side, bot_name))
            FROM trade_events WHERE event_type = 'RESOLUTION'
        """))
        row = r.fetchone()
        print(f"RESOLUTION events: {row[0]} for {row[1]} unique combos = {row[0]/max(row[1],1):.1f}x")

        r2 = await s.execute(text("""
            SELECT event_type, COUNT(*), COALESCE(SUM(realized_pnl),0)
            FROM trade_events WHERE bot_name = 'EsportsBot'
            GROUP BY event_type ORDER BY event_type
        """))
        print("\nEsportsBot trade_events:")
        for row in r2.fetchall():
            print(f"  {row[0]:>12}: {row[1]:>4} events, rpnl=${float(row[2]):.2f}")

        # Check for any duplicates created after cleanup
        r3 = await s.execute(text("""
            SELECT market_id, side, bot_name, COUNT(*) as cnt
            FROM trade_events WHERE event_type = 'RESOLUTION'
            GROUP BY market_id, side, bot_name HAVING COUNT(*) > 1
            LIMIT 5
        """))
        dupes = r3.fetchall()
        if dupes:
            print(f"\nWARNING: {len(dupes)} duplicate RESOLUTION combos found!")
            for d in dupes:
                print(f"  {d[0][:14]}... {d[1]} {d[2]} x{d[3]}")
        else:
            print("\nNo duplicate RESOLUTION events found. Dedup working.")
    await db.close()

asyncio.run(go())
