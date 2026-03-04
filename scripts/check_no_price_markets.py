"""Analyze the 1,727 active markets with tokens but NO price data — are they viable?"""
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
        # 1. Basic breakdown: liquidity distribution of no-price markets
        r = await s.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) >= 10000 THEN 1 END) as liq_10k_plus,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) >= 1000 AND COALESCE(m.liquidity, 0) < 10000 THEN 1 END) as liq_1k_10k,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) >= 100 AND COALESCE(m.liquidity, 0) < 1000 THEN 1 END) as liq_100_1k,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) > 0 AND COALESCE(m.liquidity, 0) < 100 THEN 1 END) as liq_under_100,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) = 0 OR m.liquidity IS NULL THEN 1 END) as liq_zero,
                AVG(COALESCE(m.liquidity, 0)) as avg_liq,
                AVG(COALESCE(m.volume, 0)) as avg_vol
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print("=== 1,727 MARKETS WITH NO PRICE DATA ===\n")
        print(f"  Total: {row[0]}")
        print(f"  Liquidity >= $10,000:  {row[1]}")
        print(f"  Liquidity $1K-$10K:    {row[2]}")
        print(f"  Liquidity $100-$1K:    {row[3]}")
        print(f"  Liquidity < $100:      {row[4]}")
        print(f"  Liquidity = $0/NULL:   {row[5]}")
        print(f"  Avg liquidity: ${float(row[6] or 0):,.0f}")
        print(f"  Avg volume:    ${float(row[7] or 0):,.0f}")

        # 2. Volume distribution
        r = await s.execute(text("""
            SELECT
                COUNT(CASE WHEN COALESCE(m.volume, 0) >= 100000 THEN 1 END) as vol_100k_plus,
                COUNT(CASE WHEN COALESCE(m.volume, 0) >= 10000 AND COALESCE(m.volume, 0) < 100000 THEN 1 END) as vol_10k_100k,
                COUNT(CASE WHEN COALESCE(m.volume, 0) >= 1000 AND COALESCE(m.volume, 0) < 10000 THEN 1 END) as vol_1k_10k,
                COUNT(CASE WHEN COALESCE(m.volume, 0) > 0 AND COALESCE(m.volume, 0) < 1000 THEN 1 END) as vol_under_1k,
                COUNT(CASE WHEN COALESCE(m.volume, 0) = 0 OR m.volume IS NULL THEN 1 END) as vol_zero
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print(f"\n  Volume >= $100K:  {row[0]}")
        print(f"  Volume $10K-$100K: {row[1]}")
        print(f"  Volume $1K-$10K:   {row[2]}")
        print(f"  Volume < $1K:      {row[3]}")
        print(f"  Volume = $0/NULL:  {row[4]}")

        # 3. Have token IDs? (both YES and NO)
        r = await s.execute(text("""
            SELECT
                COUNT(CASE WHEN m.yes_token_id IS NOT NULL AND m.yes_token_id != ''
                           AND m.no_token_id IS NOT NULL AND m.no_token_id != '' THEN 1 END) as both_tokens,
                COUNT(CASE WHEN (m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
                           AND (m.no_token_id IS NULL OR m.no_token_id = '') THEN 1 END) as yes_only,
                COUNT(CASE WHEN (m.no_token_id IS NOT NULL AND m.no_token_id != '')
                           AND (m.yes_token_id IS NULL OR m.yes_token_id = '') THEN 1 END) as no_only
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print(f"\n  Both YES+NO tokens: {row[0]}")
        print(f"  YES token only:     {row[1]}")
        print(f"  NO token only:      {row[2]}")

        # 4. price_fetch_attempts (have they been tried and returned empty?)
        r = await s.execute(text("""
            SELECT
                COUNT(CASE WHEN COALESCE(m.price_fetch_attempts, 0) = 0 THEN 1 END) as never_tried,
                COUNT(CASE WHEN COALESCE(m.price_fetch_attempts, 0) BETWEEN 1 AND 4 THEN 1 END) as tried_few,
                COUNT(CASE WHEN COALESCE(m.price_fetch_attempts, 0) >= 5 THEN 1 END) as tried_many_empty
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print(f"\n  Never attempted price fetch: {row[0]}")
        print(f"  Tried 1-4 times (empty):     {row[1]}")
        print(f"  Tried 5+ times (blacklisted): {row[2]}")

        # 5. Categories
        r = await s.execute(text("""
            SELECT COALESCE(m.category, '(none)') as cat, COUNT(*) as cnt
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
            GROUP BY cat ORDER BY cnt DESC LIMIT 15
        """))
        print(f"\n  Top categories (no-price markets):")
        for row in r.fetchall():
            print(f"    {row[0]:25s}: {row[1]}")

        # 6. When were these markets created? (are they new or old/stale?)
        r = await s.execute(text("""
            SELECT
                COUNT(CASE WHEN m.created_at >= NOW() - INTERVAL '7 days' THEN 1 END) as last_7d,
                COUNT(CASE WHEN m.created_at >= NOW() - INTERVAL '30 days' AND m.created_at < NOW() - INTERVAL '7 days' THEN 1 END) as last_30d,
                COUNT(CASE WHEN m.created_at < NOW() - INTERVAL '30 days' THEN 1 END) as older,
                COUNT(CASE WHEN m.created_at IS NULL THEN 1 END) as no_date,
                MIN(m.created_at) as oldest,
                MAX(m.created_at) as newest
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print(f"\n  Created in last 7 days:   {row[0]}")
        print(f"  Created 7-30 days ago:    {row[1]}")
        print(f"  Created 30+ days ago:     {row[2]}")
        print(f"  No created_at:            {row[3]}")
        print(f"  Oldest: {row[4]}")
        print(f"  Newest: {row[5]}")

        # 7. Compare: what do markets WITH price data look like?
        print(f"\n{'='*60}")
        print(f"=== COMPARISON: 735 MARKETS WITH PRICE DATA ===\n")
        r = await s.execute(text("""
            SELECT
                COUNT(*) as total,
                AVG(COALESCE(m.liquidity, 0)) as avg_liq,
                AVG(COALESCE(m.volume, 0)) as avg_vol,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) >= 1000 THEN 1 END) as liq_1k_plus,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) >= 100 AND COALESCE(m.liquidity, 0) < 1000 THEN 1 END) as liq_100_1k,
                COUNT(CASE WHEN COALESCE(m.liquidity, 0) < 100 THEN 1 END) as liq_under_100
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
        """))
        row = r.fetchone()
        print(f"  Total: {row[0]}")
        print(f"  Avg liquidity: ${float(row[1] or 0):,.0f}")
        print(f"  Avg volume:    ${float(row[2] or 0):,.0f}")
        print(f"  Liq >= $1K:    {row[3]}")
        print(f"  Liq $100-$1K:  {row[4]}")
        print(f"  Liq < $100:    {row[5]}")

        # 8. Top 15 highest-liquidity markets with NO price data (the biggest misses)
        print(f"\n{'='*60}")
        print("=== TOP 15 HIGHEST-LIQ MARKETS WITH NO PRICES ===\n")
        r = await s.execute(text("""
            SELECT m.id, COALESCE(m.liquidity, 0) as liq, COALESCE(m.volume, 0) as vol,
                   LEFT(m.question, 60) as q,
                   COALESCE(m.price_fetch_attempts, 0) as attempts
            FROM markets m
            WHERE m.active = true
            AND (m.yes_token_id IS NOT NULL OR m.no_token_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM market_prices mp
                WHERE mp.market_id = m.id OR mp.market_id = m.condition_id
            )
            ORDER BY COALESCE(m.liquidity, 0) DESC
            LIMIT 15
        """))
        for row in r.fetchall():
            q = (row[3] or "").encode("ascii", "replace").decode("ascii")
            print(f"  id={row[0]:>10s} liq=${float(row[1]):>10,.0f} vol=${float(row[2]):>12,.0f} attempts={row[4]} | {q}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
