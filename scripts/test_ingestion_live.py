"""Quick test: Can ingestion actually connect and fetch data?"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

async def test():
    from base_engine.data.polymarket_client import PolymarketClient
    from base_engine.data.database import Database
    from config.settings import settings

    print("=== API CONNECTIVITY ===")
    client = PolymarketClient()
    try:
        ok, msg = await client.check_gamma_connectivity()
        print(f"  Gamma API: {'OK' if ok else 'FAILED'} - {msg}")
    except Exception as e:
        print(f"  Gamma API ERROR: {type(e).__name__}: {e}")

    # Try fetching a small batch
    try:
        events = await client.get_events(active=True, limit=5, offset=0)
        print(f"  get_events(5): returned {len(events) if events else 0} items")
    except Exception as e:
        print(f"  get_events ERROR: {type(e).__name__}: {e}")

    print("\n=== DATABASE CONNECTIVITY ===")
    db = Database()
    try:
        await db.init()
        print(f"  DB init: OK")
        await db._verify_database()
        print(f"  DB verify: OK")
    except Exception as e:
        print(f"  DB ERROR: {type(e).__name__}: {e}")

    # Check sync_log status
    print("\n=== SYNC LOG STATUS ===")
    try:
        from sqlalchemy import text
        async with db.get_session() as session:
            # Check for stuck "running" entries
            r = await session.execute(text(
                "SELECT id, sync_type, status, started_at FROM sync_log WHERE status = 'running'"
            ))
            running = r.fetchall()
            print(f"  Currently 'running' entries: {len(running)}")
            for row in running:
                print(f"    #{row[0]} type={row[1]} started={str(row[2])[:19]}")

            # Last successful sync
            r2 = await session.execute(text(
                "SELECT sync_type, started_at, records_processed FROM sync_log "
                "WHERE status = 'success' ORDER BY started_at DESC LIMIT 3"
            ))
            successes = r2.fetchall()
            print(f"\n  Last successful syncs:")
            for row in successes:
                print(f"    type={row[0]} at={str(row[1])[:19]} records={row[2]}")

            # Market freshness
            r3 = await session.execute(text(
                "SELECT COUNT(*), MAX(updated_at) FROM markets"
            ))
            mkt = r3.fetchone()
            print(f"\n  Markets: count={mkt[0]}, latest_update={str(mkt[1])[:19] if mkt[1] else 'N/A'}")

            # market_prices freshness (use 'timestamp' column)
            r4 = await session.execute(text(
                "SELECT COUNT(*), MAX(timestamp) FROM market_prices"
            ))
            mp = r4.fetchone()
            print(f"  Market prices: count={mp[0]}, latest={str(mp[1])[:19] if mp[1] else 'N/A'}")

    except Exception as e:
        print(f"  Query ERROR: {type(e).__name__}: {e}")

    # Now test if a SMALL ingestion works
    print("\n=== SMALL INGESTION TEST (5 markets) ===")
    try:
        from base_engine.data.data_ingestion import DataIngestionService
        svc = DataIngestionService(db=db, client=client)
        count = await svc.ingest_all_markets(top_markets_count=5, include_closed=False)
        print(f"  Ingested {count} markets successfully!")
    except Exception as e:
        print(f"  Ingestion ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    await db.close()
    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(test())
