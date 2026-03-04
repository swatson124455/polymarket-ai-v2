#!/usr/bin/env python3
"""
Data quality report: counts and health for learning/prediction.
Run from project root: python scripts/data_quality_report.py
"""
import asyncio
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


async def main() -> int:
    from base_engine.data.database import Database
    from sqlalchemy import text, select, func
    from base_engine.data.database import Market, MarketPrice, Trade, User

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized. Set DATABASE_URL.")
        return 1

    async with db.get_session() as session:
        # Counts
        markets = (await session.execute(select(func.count(Market.id)))).scalar() or 0
        prices = (await session.execute(select(func.count(MarketPrice.id)))).scalar() or 0
        trades = (await session.execute(select(func.count(Trade.id)))).scalar() or 0
        users = (await session.execute(select(func.count(User.address)))).scalar() or 0

        # Resolved markets
        resolved = (await session.execute(
            select(func.count(Market.id)).where(
                Market.resolved == True,
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalar() or 0

        # Markets with token IDs (needed for price history)
        with_tokens = (await session.execute(
            select(func.count(Market.id)).where(
                Market.yes_token_id.isnot(None),
                Market.no_token_id.isnot(None),
            )
        )).scalar() or 0

        # Trades with stored pnl
        trades_with_pnl = (await session.execute(
            select(func.count(Trade.id)).where(Trade.pnl.isnot(None))
        )).scalar() or 0

        # Trades from resolved markets (learnable via get_trades_since)
        # JOIN on id, condition_id, or slug: trades.market_id can be condition_id (Data API)
        from sqlalchemy import or_
        learnable_trades = (await session.execute(
            select(func.count(Trade.id))
            .select_from(Trade)
            .join(
                Market,
                or_(
                    Trade.market_id == Market.id,
                    Trade.market_id == Market.condition_id,
                    Trade.market_id == Market.slug,
                ),
            )
            .where(
                Trade.market_id.isnot(None),  # Exclude NULL market_id
                Market.resolved == True,
                Market.resolution.in_(["YES", "NO"]),
            )
        )).scalar() or 0

        # Elite users
        elite = (await session.execute(
            select(func.count(User.address)).where(User.is_elite == True)
        )).scalar() or 0

    print("=" * 50)
    print("DATA QUALITY REPORT")
    print("=" * 50)
    print(f"  Markets:           {markets:,}")
    print(f"  Resolved (YES/NO): {resolved:,}")
    print(f"  With token IDs:    {with_tokens:,}")
    print(f"  Price records:     {prices:,}")
    print(f"  Trades:            {trades:,}")
    print(f"  Trades with pnl:   {trades_with_pnl:,}")
    print(f"  Learnable trades:  {learnable_trades:,} (from resolved markets)")
    print(f"  Users:             {users:,}")
    print(f"  Elite users:       {elite:,}")
    print("=" * 50)
    if learnable_trades > 0:
        print("  Learning: OK (get_trades_since will return learnable trades)")
    else:
        print("  Learning: No learnable trades (need resolved markets + trades)")
    if resolved > 0 and (prices > 0 or learnable_trades > 0):
        print("  Prediction: OK (fallbacks available)")
    else:
        print("  Prediction: May need more data (resolved markets + prices or trades)")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
