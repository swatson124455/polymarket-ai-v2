"""Diagnose why only 112 markets have price data: check market_id format in market_prices."""
import asyncio
import io
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    async with db.get_session() as s:
        # 1. How many distinct market_ids in market_prices?
        r = await s.execute(text("SELECT COUNT(DISTINCT market_id) FROM market_prices"))
        print(f"Distinct market_ids in market_prices: {r.scalar()}")

        # 2. How many match markets.id?
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT mp.market_id) FROM market_prices mp "
            "JOIN markets m ON m.id::text = mp.market_id"
        ))
        print(f"market_prices.market_id matching markets.id: {r.scalar()}")

        # 3. How many match markets.condition_id?
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT mp.market_id) FROM market_prices mp "
            "JOIN markets m ON m.condition_id = mp.market_id"
        ))
        print(f"market_prices.market_id matching markets.condition_id: {r.scalar()}")

        # 4. How many match NEITHER?
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT mp.market_id) FROM market_prices mp "
            "WHERE mp.market_id NOT IN (SELECT id::text FROM markets) "
            "AND mp.market_id NOT IN (SELECT condition_id FROM markets WHERE condition_id IS NOT NULL)"
        ))
        print(f"market_prices.market_id matching NEITHER: {r.scalar()}")

        # 5. Sample market_ids from market_prices
        r = await s.execute(text(
            "SELECT DISTINCT market_id FROM market_prices ORDER BY market_id LIMIT 10"
        ))
        print(f"\nSample market_ids from market_prices:")
        for row in r.fetchall():
            print(f"  {row[0]}")

        # 6. Sample markets.id and markets.condition_id
        r = await s.execute(text(
            "SELECT id, condition_id FROM markets LIMIT 5"
        ))
        print(f"\nSample markets (id vs condition_id):")
        for row in r.fetchall():
            print(f"  id={row[0]}  condition_id={row[1]}")

        # 7. Total active markets with token IDs
        r = await s.execute(text(
            "SELECT COUNT(*) FROM markets WHERE active = true "
            "AND (yes_token_id IS NOT NULL OR no_token_id IS NOT NULL)"
        ))
        print(f"\nActive markets with tokens: {r.scalar()}")

        # 8. Active markets that have price data (JOIN on id)
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON mp.market_id = m.id::text "
            "WHERE m.active = true"
        ))
        print(f"Active markets with prices (JOIN on m.id): {r.scalar()}")

        # 9. Active markets that have price data (JOIN on condition_id)
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON mp.market_id = m.condition_id "
            "WHERE m.active = true"
        ))
        print(f"Active markets with prices (JOIN on m.condition_id): {r.scalar()}")

        # 10. Active markets with prices (JOIN on EITHER)
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON (mp.market_id = m.id::text OR mp.market_id = m.condition_id) "
            "WHERE m.active = true"
        ))
        print(f"Active markets with prices (JOIN on EITHER): {r.scalar()}")

        # 11. Check if m.id is integer or UUID
        r = await s.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'markets' AND column_name = 'id'"
        ))
        print(f"\nmarkets.id data type: {r.scalar()}")

        r = await s.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'market_prices' AND column_name = 'market_id'"
        ))
        print(f"market_prices.market_id data type: {r.scalar()}")

        # 12. When was last price ingested?
        r = await s.execute(text("SELECT MAX(timestamp) FROM market_prices"))
        print(f"\nLatest price timestamp: {r.scalar()}")

        r = await s.execute(text("SELECT MIN(timestamp) FROM market_prices"))
        print(f"Oldest price timestamp: {r.scalar()}")

        # 13. Prices per source (roughly - by market_id format)
        r = await s.execute(text(
            "SELECT COUNT(*) FROM market_prices WHERE market_id ~ '^0x'"
        ))
        print(f"\nPrices with 0x-prefixed market_id (condition_id format): {r.scalar()}")

        r = await s.execute(text(
            "SELECT COUNT(*) FROM market_prices WHERE market_id !~ '^0x'"
        ))
        print(f"Prices with non-0x market_id (numeric id format): {r.scalar()}")

        # 14. Check sync_log for recent ingestion runs
        r = await s.execute(text(
            "SELECT id, sync_type, component, status, started_at, completed_at, error_message "
            "FROM sync_log ORDER BY started_at DESC LIMIT 10"
        ))
        print(f"\nRecent sync_log entries:")
        for row in r.fetchall():
            err = str(row[6])[:80] if row[6] else ""
            print(f"  #{row[0]} {row[1]}/{row[2]} {row[3]} started={row[4]} completed={row[5]} err={err}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
