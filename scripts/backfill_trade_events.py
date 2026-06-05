"""
backfill_trade_events.py — One-time migration of existing paper_trades into trade_events
and traded_markets upgrade with share/investment data.

Run after migrations 043-049 are applied.

Usage:
    python scripts/backfill_trade_events.py
"""
import asyncio
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))


async def main():
    from base_engine.data.database import Database

    db = Database()
    await db.init()

    print("=== Backfill Trade Events ===")

    # Step 1: Backfill trade_events from paper_trades
    from sqlalchemy import text as sa_text
    async with db.get_session() as session:
        result = await session.execute(
            sa_text(
                "SELECT id, order_id, market_id, token_id, bot_name, side, size, price,"
                "  confidence, correlation_id, realized_pnl, latency_ms, status,"
                "  submitted_at, filled_at, created_at "
                "FROM paper_trades "
                "WHERE side IN ('YES', 'NO') "
                "ORDER BY created_at ASC"
            )
        )
        rows = result.fetchall()

    print(f"Found {len(rows)} paper_trades to backfill")

    inserted = 0
    for r in rows:
        (pt_id, order_id, market_id, token_id, bot_name, side, size, price,
         confidence, correlation_id, realized_pnl, latency_ms, status,
         submitted_at, filled_at, created_at) = r

        # ENTRY event
        # Phase-1 live-P&L: this script backfills from paper_trades, which is
        # paper-only by construction — so every emit here is execution_mode="paper".
        # Default is already "paper"; made explicit so intent is unambiguous and this
        # legacy path never accidentally tags live (Protocol 16 pattern-completeness).
        idem_key = f"backfill:entry:{pt_id}"
        seq = await db.insert_trade_event(
            event_type="ENTRY",
            execution_mode="paper",
            bot_name=bot_name,
            market_id=market_id,
            side=side,
            size=float(size or 0),
            price=float(price or 0),
            token_id=token_id,
            confidence=float(confidence) if confidence else None,
            correlation_id=correlation_id,
            order_id=order_id or idem_key,
            event_time=created_at,
        )
        if seq:
            inserted += 1

        # RESOLUTION event for resolved trades
        if status == "resolved" and realized_pnl is not None:
            res_idem_key = f"backfill:resolution:{pt_id}"
            seq2 = await db.insert_trade_event(
                event_type="RESOLUTION",
                execution_mode="paper",
                bot_name=bot_name,
                market_id=market_id,
                side=side,
                size=float(size or 0),
                price=1.0 if float(realized_pnl) > 0 else 0.0,
                realized_pnl=float(realized_pnl),
                order_id=res_idem_key,
                event_time=created_at,
            )
            if seq2:
                inserted += 1

        if inserted % 100 == 0 and inserted > 0:
            print(f"  ... {inserted} events inserted")

    print(f"Step 1 complete: {inserted} trade events backfilled from {len(rows)} paper_trades")

    # Step 2: Upgrade traded_markets with share/investment data
    # (migration 044 already does this via SQL UPDATE, but run again for safety)
    async with db.get_session() as session:
        await session.execute(
            sa_text(
                "UPDATE traded_markets tm SET "
                "  trade_count = sub.cnt,"
                "  net_yes_shares = sub.yes_shares,"
                "  net_no_shares = sub.no_shares,"
                "  total_invested = sub.invested,"
                "  question = sub.question "
                "FROM ("
                "  SELECT pt.market_id, COUNT(*) AS cnt,"
                "    SUM(CASE WHEN pt.side = 'YES' THEN pt.size ELSE 0 END) AS yes_shares,"
                "    SUM(CASE WHEN pt.side = 'NO' THEN pt.size ELSE 0 END) AS no_shares,"
                "    SUM(pt.size * pt.price) AS invested,"
                "    MAX(m.question) AS question "
                "  FROM paper_trades pt "
                "  LEFT JOIN markets m ON pt.market_id = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id "
                "  WHERE pt.side IN ('YES', 'NO') "
                "  GROUP BY pt.market_id"
                ") sub "
                "WHERE tm.market_id = sub.market_id"
            )
        )
        await session.commit()
    print("Step 2 complete: traded_markets upgraded with share/investment data")

    # Step 3: Validation
    async with db.get_session() as session:
        te_count = (await session.execute(sa_text("SELECT COUNT(*) FROM trade_events"))).scalar()
        tm_count = (await session.execute(sa_text("SELECT COUNT(*) FROM traded_markets"))).scalar()
        tm_open = (await session.execute(
            sa_text("SELECT COUNT(*) FROM traded_markets WHERE status = 'open'")
        )).scalar()

        old_pnl = (await session.execute(
            sa_text("SELECT COALESCE(SUM(realized_pnl), 0) FROM paper_trades WHERE realized_pnl IS NOT NULL AND side IN ('YES', 'NO')")
        )).scalar()
        new_pnl = (await session.execute(
            sa_text("SELECT COALESCE(SUM(realized_pnl), 0) FROM trade_events WHERE event_type = 'RESOLUTION'")
        )).scalar()

    print(f"\n=== Validation ===")
    print(f"trade_events: {te_count} rows")
    print(f"traded_markets: {tm_count} total ({tm_open} open)")
    print(f"PnL check: paper_trades={old_pnl:.2f}, trade_events={new_pnl:.2f}, delta={abs(float(old_pnl or 0) - float(new_pnl or 0)):.2f}")

    if abs(float(old_pnl or 0) - float(new_pnl or 0)) > 0.01:
        print("WARNING: PnL mismatch detected — investigate before going live")
    else:
        print("PnL match confirmed")

    await db.close()
    print("\n=== Backfill Complete ===")


if __name__ == "__main__":
    asyncio.run(main())
