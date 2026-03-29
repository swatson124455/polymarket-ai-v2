#!/usr/bin/env python3
"""
DEPRECATED: Use `python scripts/run_audit.py --bot MirrorBot` instead.
This script predates the unified audit system (base_engine/audit/).
Retained for reference only.

Deep P&L audit for MirrorBot — verify +$19k is real.
"""
import asyncio
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def audit():
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # 1. Total trade events by type
        r = await s.execute(text(
            "SELECT event_type, COUNT(*), "
            "COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0), "
            "COALESCE(SUM(CAST(fees AS DOUBLE PRECISION)), 0), "
            "MIN(event_time), MAX(event_time) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' "
            "GROUP BY event_type ORDER BY event_type"
        ))
        print("=== TRADE EVENTS SUMMARY ===")
        for row in r.fetchall():
            print(f"  {row[0]:<12} count={row[1]:<6} pnl=${row[2]:>+10.2f}  fees=${row[3]:>8.2f}  {row[4]} to {row[5]}")

        # 2. Top 20 resolution winners
        r2 = await s.execute(text(
            "SELECT realized_pnl, market_id, side, size, price, event_time "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' "
            "ORDER BY CAST(realized_pnl AS DOUBLE PRECISION) DESC LIMIT 20"
        ))
        print("\n=== TOP 20 RESOLUTION WINNERS ===")
        for row in r2.fetchall():
            print(f"  pnl=${float(row[0] or 0):>+8.2f}  mkt={str(row[1])[:16]}..  {row[2]}  sz={float(row[3] or 0):.1f}  p={float(row[4] or 0):.4f}")

        # 3. Top 20 losers
        r3 = await s.execute(text(
            "SELECT realized_pnl, market_id, side, size, price, event_time "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' "
            "ORDER BY CAST(realized_pnl AS DOUBLE PRECISION) ASC LIMIT 20"
        ))
        print("\n=== TOP 20 RESOLUTION LOSERS ===")
        for row in r3.fetchall():
            print(f"  pnl=${float(row[0] or 0):>+8.2f}  mkt={str(row[1])[:16]}..  {row[2]}  sz={float(row[3] or 0):.1f}  p={float(row[4] or 0):.4f}")

        # 4. Win/loss (EXIT + RESOLUTION combined)
        r4 = await s.execute(text(
            "SELECT "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0), "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0), "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) = 0), "
            "AVG(CAST(realized_pnl AS DOUBLE PRECISION)) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0), "
            "AVG(CAST(realized_pnl AS DOUBLE PRECISION)) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type IN ('EXIT', 'RESOLUTION')"
        ))
        row = r4.fetchone()
        wins, losses, be = row[0], row[1], row[2]
        print("\n=== WIN/LOSS (EXIT + RESOLUTION) ===")
        print(f"  Wins: {wins}, Losses: {losses}, Breakeven: {be}")
        print(f"  Avg win: ${float(row[3] or 0):+.2f}, Avg loss: ${float(row[4] or 0):+.2f}")
        if wins + losses > 0:
            print(f"  Win rate: {wins/(wins+losses)*100:.1f}%")

        # 5. EXIT-only stats
        r5 = await s.execute(text(
            "SELECT "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0), "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0), "
            "AVG(CAST(realized_pnl AS DOUBLE PRECISION)) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0), "
            "AVG(CAST(realized_pnl AS DOUBLE PRECISION)) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0), "
            "SUM(CAST(realized_pnl AS DOUBLE PRECISION)) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'EXIT'"
        ))
        row5 = r5.fetchone()
        print("\n=== EXIT-ONLY STATS ===")
        print(f"  Wins: {row5[0]}, Losses: {row5[1]}")
        print(f"  Avg win: ${float(row5[2] or 0):+.2f}, Avg loss: ${float(row5[3] or 0):+.2f}")
        print(f"  Total EXIT pnl: ${float(row5[4] or 0):+.2f}")

        # 6. RESOLUTION-only stats
        r6 = await s.execute(text(
            "SELECT "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) > 0), "
            "COUNT(*) FILTER (WHERE CAST(realized_pnl AS DOUBLE PRECISION) < 0), "
            "SUM(CAST(realized_pnl AS DOUBLE PRECISION)) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'"
        ))
        row6 = r6.fetchone()
        print("\n=== RESOLUTION-ONLY STATS ===")
        print(f"  Wins: {row6[0]}, Losses: {row6[1]}")
        print(f"  Total resolution pnl: ${float(row6[2] or 0):+.2f}")

        # 7. Entry stats — avg price, size, total deployed
        r7 = await s.execute(text(
            "SELECT "
            "AVG(CAST(price AS DOUBLE PRECISION)), "
            "AVG(CAST(size AS DOUBLE PRECISION)), "
            "AVG(CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION)), "
            "SUM(CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION)), "
            "COUNT(*) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'"
        ))
        row7 = r7.fetchone()
        total_deployed = float(row7[3] or 0)
        entries = row7[4]
        print("\n=== ENTRY STATS ===")
        print(f"  Entries: {entries}")
        print(f"  Avg entry price: {float(row7[0] or 0):.4f}")
        print(f"  Avg position (shares): {float(row7[1] or 0):.1f}")
        print(f"  Avg position (USD): ${float(row7[2] or 0):.2f}")
        print(f"  Total capital deployed: ${total_deployed:.2f}")
        if total_deployed > 0:
            print(f"  Return on deployed: {19171/total_deployed*100:.1f}%")

        # 8. RESOLUTION P&L vs entry cost — verify math
        r8 = await s.execute(text(
            "SELECT r.market_id, r.side, r.realized_pnl, r.size, r.price, "
            "       e.price as entry_price, e.size as entry_size "
            "FROM trade_events r "
            "LEFT JOIN trade_events e ON e.market_id = r.market_id "
            "  AND e.bot_name = r.bot_name AND e.event_type = 'ENTRY' "
            "  AND e.side = r.side "
            "WHERE r.bot_name = 'MirrorBot' AND r.event_type = 'RESOLUTION' "
            "ORDER BY CAST(r.realized_pnl AS DOUBLE PRECISION) DESC LIMIT 10"
        ))
        print("\n=== TOP 10 RESOLUTION MATH CHECK ===")
        print(f"  {'Market':<18} {'Side':>4} {'rPnL':>8} {'EntryP':>7} {'Size':>7} {'Expected':>9}")
        for row in r8.fetchall():
            rpnl = float(row[2] or 0)
            entry_p = float(row[5] or 0)
            sz = float(row[6] or 0)
            # If side won: pnl = (1.0 - entry) * size
            # If side lost: pnl = (0.0 - entry) * size = -entry * size
            win_pnl = (1.0 - entry_p) * sz
            loss_pnl = -entry_p * sz
            expected = win_pnl if rpnl > 0 else loss_pnl
            match = "OK" if abs(rpnl - expected) < 1.0 else "MISMATCH"
            print(f"  {str(row[0])[:16]}.. {row[1]:>4} ${rpnl:>+7.2f} {entry_p:>7.4f} {sz:>7.1f} ${expected:>+8.2f} {match}")

        # 9. Duplicate check
        r9 = await s.execute(text(
            "SELECT market_id, side, COUNT(*) as cnt "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' "
            "GROUP BY market_id, side HAVING COUNT(*) > 1 ORDER BY cnt DESC LIMIT 10"
        ))
        dupes = r9.fetchall()
        print("\n=== DUPLICATE RESOLUTION CHECK ===")
        if dupes:
            total_dupes = sum(d[2] - 1 for d in dupes)
            print(f"  WARNING: {len(dupes)} combos with {total_dupes} duplicate RESOLUTION events!")
            for d in dupes:
                print(f"    mkt={str(d[0])[:16]}.. side={d[1]} count={d[2]}")
        else:
            print("  CLEAN: No duplicate RESOLUTION events")

        # 10. Fees check
        r10 = await s.execute(text(
            "SELECT SUM(CAST(fees AS DOUBLE PRECISION)) FROM trade_events WHERE bot_name = 'MirrorBot'"
        ))
        total_fees = float(r10.scalar() or 0)
        print(f"\n=== FEES: ${total_fees:.2f} ===")
        if total_fees == 0:
            print("  PAPER TRADING: $0 fees (SIMULATION_MODE=true)")
            print("  LIVE EQUIVALENT: 1.5% taker on entries would be ~${:.0f}".format(total_deployed * 0.015))

        # 11. Timeline — P&L by day
        r11 = await s.execute(text(
            "SELECT DATE(event_time) as day, event_type, COUNT(*), "
            "SUM(CAST(realized_pnl AS DOUBLE PRECISION)) "
            "FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type IN ('EXIT','RESOLUTION') "
            "GROUP BY DATE(event_time), event_type ORDER BY day, event_type"
        ))
        print("\n=== DAILY P&L TIMELINE ===")
        for row in r11.fetchall():
            print(f"  {row[0]}  {row[1]:<12} count={row[2]:<4} pnl=${float(row[3] or 0):>+10.2f}")

        # 12. Capital at risk — max concurrent positions
        r12 = await s.execute(text(
            "SELECT MAX(cnt) FROM ("
            "  SELECT DATE(event_time), COUNT(*) as cnt "
            "  FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY' "
            "  GROUP BY DATE(event_time)"
            ") sub"
        ))
        max_daily = r12.scalar()
        print(f"\n=== MAX ENTRIES IN A DAY: {max_daily} ===")

    await db.close()


if __name__ == "__main__":
    asyncio.run(audit())
