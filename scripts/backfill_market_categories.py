#!/usr/bin/env python3
"""
S113: One-time backfill for market_categories table.

Finds all unique market_ids in the `trades` table that are NOT in
the `markets` table, fetches their details from the CLOB API, infers
category via _infer_category(), and inserts into market_categories.

Usage:
    python scripts/backfill_market_categories.py [--limit 500] [--delay 0.05]

Run on VPS:
    sudo /opt/polymarket-ai-v2/venv/bin/python3 scripts/backfill_market_categories.py
"""
import argparse
import asyncio
import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(limit: int = 500, delay: float = 0.05):
    import httpx
    import asyncpg
    from base_engine.data.data_ingestion import _infer_category

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        # Try common VPS path
        env_path = "/opt/polymarket-ai-v2/.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("DATABASE_URL="):
                        db_url = line.strip().split("=", 1)[1].strip('"').strip("'")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return

    conn = await asyncpg.connect(db_url)

    # Find market_ids in trades NOT in markets AND NOT already in market_categories
    rows = await conn.fetch(
        "SELECT DISTINCT t.market_id "
        "FROM trades t "
        "LEFT JOIN markets m ON t.market_id = m.id "
        "LEFT JOIN market_categories mc ON t.market_id = mc.condition_id "
        "WHERE m.id IS NULL AND mc.condition_id IS NULL "
        "LIMIT $1",
        limit,
    )
    print(f"Found {len(rows)} market_ids to backfill")

    inserted = 0
    failed = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, row in enumerate(rows):
            mid = row["market_id"]
            try:
                resp = await client.get(f"https://clob.polymarket.com/markets/{mid}")
                if resp.status_code != 200:
                    failed += 1
                    continue
                data = resp.json()
                question = data.get("question") or ""
                category = _infer_category(question) if question else "unknown"

                tokens = data.get("tokens") or []
                yes_tid = ""
                no_tid = ""
                resolved = bool(data.get("closed"))
                resolution = None
                for ti, tok in enumerate(tokens):
                    outcome = (tok.get("outcome") or "").upper()
                    if outcome == "YES":
                        yes_tid = tok.get("token_id", "")
                    elif outcome == "NO":
                        no_tid = tok.get("token_id", "")
                    if tok.get("winner"):
                        resolution = "YES" if ti == 0 else "NO"

                await conn.execute(
                    "INSERT INTO market_categories "
                    "(condition_id, category, question, yes_token_id, no_token_id, resolved, resolution) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) "
                    "ON CONFLICT (condition_id) DO UPDATE SET "
                    "category = EXCLUDED.category, "
                    "question = COALESCE(NULLIF(EXCLUDED.question, ''), market_categories.question), "
                    "yes_token_id = COALESCE(NULLIF(EXCLUDED.yes_token_id, ''), market_categories.yes_token_id), "
                    "no_token_id = COALESCE(NULLIF(EXCLUDED.no_token_id, ''), market_categories.no_token_id), "
                    "resolved = EXCLUDED.resolved OR market_categories.resolved, "
                    "resolution = COALESCE(EXCLUDED.resolution, market_categories.resolution)",
                    mid, category, question[:500], yes_tid, no_tid, resolved, resolution,
                )
                inserted += 1

                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i+1}/{len(rows)} ({inserted} inserted, {failed} failed)")

            except Exception as e:
                failed += 1
                if (i + 1) % 50 == 0:
                    print(f"  Error on {mid[:16]}: {e}")

            await asyncio.sleep(delay)

    await conn.close()
    print(f"\nDone: {inserted} inserted, {failed} failed out of {len(rows)} markets")

    # Summary query
    conn2 = await asyncpg.connect(db_url)
    stats = await conn2.fetch(
        "SELECT category, COUNT(*) as cnt, "
        "SUM(CASE WHEN resolved THEN 1 ELSE 0 END) as resolved_cnt "
        "FROM market_categories GROUP BY category ORDER BY cnt DESC"
    )
    print("\nCategory distribution:")
    for s in stats:
        print(f"  {s['category']:20s} {s['cnt']:5d} total, {s['resolved_cnt']:5d} resolved")
    await conn2.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500, help="Max markets to backfill")
    parser.add_argument("--delay", type=float, default=0.05, help="Delay between API calls (seconds)")
    args = parser.parse_args()
    asyncio.run(main(limit=args.limit, delay=args.delay))
