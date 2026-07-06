import asyncio, sys
sys.path.insert(0, '.')

async def main():
    import asyncpg
    from config.settings import settings
    conn = await asyncpg.connect(settings.DATABASE_URL)

    for tname in ('weather_calibration', 'weather_climatology', 'weather_forecasts'):
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name=$1 ORDER BY ordinal_position", tname
        )
        col_names = [c['column_name'] for c in cols]
        cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {tname}")
        print(f"\n{tname} ({cnt} rows): {col_names}")

        city_col = next((c for c in col_names if 'city' in c.lower() or 'station' in c.lower()), None)
        if city_col and cnt > 0:
            rows = await conn.fetch(
                f"SELECT {city_col}, COUNT(*) AS cnt FROM {tname} GROUP BY 1 ORDER BY 2 DESC"
            )
            for r in rows:
                print(f"  {str(r[city_col]).ljust(28)} {r['cnt']:>6}")

    await conn.close()

asyncio.run(main())
