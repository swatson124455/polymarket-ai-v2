"""Show all paper trades and current positions."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from base_engine.data.database import Database, Position
from sqlalchemy import select, text

async def main():
    db = Database()
    await db.init()

    # All positions (open + closed)
    async with db.get_session() as session:
        result = await session.execute(
            select(Position).order_by(Position.id)
        )
        positions = result.scalars().all()

        print("=" * 120)
        print(f"{'ID':>4} {'Status':<10} {'Bot':<14} {'Market':<10} {'Side':<6} {'Size':>10} {'Entry':>8} {'Current':>8} {'PnL':>10} {'Opened':<22}")
        print("-" * 120)
        for p in positions:
            opened = getattr(p, "opened_at", None) or getattr(p, "created_at", None)
            opened_str = str(opened)[:19] if opened else "N/A"
            size = p.size or 0
            entry = p.entry_price or 0
            current = p.current_price or 0
            pnl = p.unrealized_pnl or 0
            print(f"{p.id:>4} {p.status:<10} {(p.bot_id or 'unknown'):<14} {str(p.market_id):<10} {(p.side or '?'):<6} {size:>10.2f} {entry:>8.4f} {current:>8.4f} {pnl:>10.4f} {opened_str}")

        print("-" * 120)
        open_pos = [p for p in positions if p.status == "open"]
        closed_pos = [p for p in positions if p.status == "closed"]
        print(f"Total: {len(positions)} positions ({len(open_pos)} open, {len(closed_pos)} closed)")

        # Get market names for the markets we traded
        market_ids = list(set(str(p.market_id) for p in positions))
        if market_ids:
            print("\n" + "=" * 120)
            print("MARKET DETAILS")
            print("-" * 120)
            from base_engine.data.database import Market
            for mid in sorted(market_ids):
                r = await session.execute(
                    select(Market.question, Market.outcome_prices, Market.volume, Market.liquidity)
                    .where(Market.id == mid)
                )
                row = r.first()
                if row:
                    q = (row[0] or "")[:80]
                    prices = row[1] or ""
                    vol = row[2] or 0
                    liq = row[3] or 0
                    print(f"  {mid:<10} {q:<80} prices={prices:<16} vol=${vol:,.0f}  liq=${liq:,.0f}")
                else:
                    print(f"  {mid:<10} (not found in markets table)")

    # Paper trade log from paper_trades table if it exists
    async with db.get_session() as session:
        try:
            result = await session.execute(text(
                "SELECT id, bot_name, market_id, side, size, price, fill_price, slippage_bps, trade_id, created_at "
                "FROM paper_trades ORDER BY id"
            ))
            rows = result.fetchall()
            if rows:
                print("\n" + "=" * 120)
                print("PAPER TRADE LOG")
                print("-" * 120)
                for r in rows:
                    print(f"  id={r[0]} bot={r[1]} mkt={r[2]} side={r[3]} size={r[4]:.2f} price={r[5]:.4f} fill={r[6]:.4f} slip={r[7]:.1f}bps at={str(r[9])[:19]}")
        except Exception:
            pass  # table might not exist

    await db.close()

asyncio.run(main())
