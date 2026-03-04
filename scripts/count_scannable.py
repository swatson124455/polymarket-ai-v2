"""Count how many markets are actually scannable."""
import asyncio, io, os, sys
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
        # Total markets
        r = await s.execute(text("SELECT COUNT(*) FROM markets"))
        print(f"Total markets: {r.scalar()}")

        # Active markets
        r = await s.execute(text("SELECT COUNT(*) FROM markets WHERE active = true"))
        print(f"Active markets: {r.scalar()}")

        # Active with condition_id (needed for price data)
        r = await s.execute(text("SELECT COUNT(*) FROM markets WHERE active = true AND condition_id IS NOT NULL"))
        print(f"Active with condition_id: {r.scalar()}")

        # Active with token IDs
        r = await s.execute(text(
            "SELECT COUNT(*) FROM markets WHERE active = true "
            "AND (yes_token_id IS NOT NULL OR no_token_id IS NOT NULL)"
        ))
        print(f"Active with token IDs: {r.scalar()}")

        # Active with price data in market_prices
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON mp.market_id = m.condition_id "
            "WHERE m.active = true"
        ))
        print(f"Active with price data: {r.scalar()}")

        # Active with recent prices (last 7 days)
        r = await s.execute(text(
            "SELECT COUNT(DISTINCT m.id) FROM markets m "
            "JOIN market_prices mp ON mp.market_id = m.condition_id "
            "WHERE m.active = true AND mp.timestamp > NOW() - INTERVAL '7 days'"
        ))
        print(f"Active with recent prices (7d): {r.scalar()}")

        # Liquidity distribution of active markets
        for threshold in [0, 10, 50, 100, 500, 1000, 5000, 10000]:
            r = await s.execute(text(
                f"SELECT COUNT(*) FROM markets WHERE active = true AND "
                f"COALESCE(liquidity, 0) >= {threshold}"
            ))
            print(f"Active with liquidity >= ${threshold}: {r.scalar()}")

        # Active with liquidity AND price data
        for threshold in [0, 100, 500, 1000]:
            r = await s.execute(text(
                "SELECT COUNT(DISTINCT m.id) FROM markets m "
                "JOIN market_prices mp ON mp.market_id = m.condition_id "
                f"WHERE m.active = true AND COALESCE(m.liquidity, 0) >= {threshold}"
            ))
            print(f"Active + prices + liq >= ${threshold}: {r.scalar()}")

        # How many does the API typically return?
        print("\n--- API comparison ---")
        r = await s.execute(text(
            "SELECT COUNT(*) FROM markets WHERE active = true "
            "AND COALESCE(liquidity, 0) >= 100 "
            "AND (yes_token_id IS NOT NULL OR no_token_id IS NOT NULL)"
        ))
        print(f"Scannable (active + liq>=$100 + tokens): {r.scalar()}")

    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
