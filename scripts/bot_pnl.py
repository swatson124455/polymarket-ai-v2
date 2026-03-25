#!/usr/bin/env python3
"""
Bot P&L Report — Canonical calculation from trade_events + positions.

Usage:
    python scripts/bot_pnl.py                    # All bots, last 24h
    python scripts/bot_pnl.py EsportsBot         # Specific bot, last 24h
    python scripts/bot_pnl.py EsportsBot 8       # Specific bot, last 8h

P&L math rules (uniform for YES and NO):
  - entry_price and current_price are ALWAYS token-specific prices
  - cost_basis = entry_price * size (for BOTH YES and NO)
  - unrealized_pnl = (current_price - entry_price) * size (for BOTH YES and NO)
  - realized_pnl on EXIT = (exit_price - entry_price) * size - fees
  - realized_pnl on RESOLUTION = (resolution_value - entry_price) * size - fees
    where resolution_value = 1.0 if your side wins, 0.0 if it loses

NEVER invert the formula for NO positions. Prices are already side-specific.
The position_manager uses (current - entry) * size uniformly.
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def bot_pnl(bot_name: str, hours: int = 24):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # 1. Open positions — mark-to-market
        r1 = await s.execute(text("""
            SELECT p.market_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl, p.opened_at
            FROM positions p
            WHERE (p.bot_id = :bot OR p.source_bot = :bot)
              AND p.status = 'open'
            ORDER BY p.opened_at DESC
        """), {"bot": bot_name})
        positions = r1.fetchall()

        print(f"=== {bot_name} P&L Report (last {hours}h) ===\n")

        total_cost = 0.0
        total_upnl = 0.0
        total_mkt_value = 0.0
        print(f"OPEN POSITIONS ({len(positions)}):")
        print(f"{'Market':<14} {'Side':>4} {'Shares':>8} {'Entry':>7} {'Curr':>7} {'Cost':>9} {'Value':>9} {'uPnL':>9}")
        print("-" * 80)
        for p in positions:
            mid = p[0][:12] + ".."
            side = p[1]
            sz = float(p[2] or 0)
            entry = float(p[3] or 0)
            cur = float(p[4] or 0)
            # UNIFORM formula — same for YES and NO (prices are token-specific)
            cost = entry * sz
            mkt_val = cur * sz
            upnl = float(p[5]) if p[5] is not None else (cur - entry) * sz
            total_cost += cost
            total_mkt_value += mkt_val
            total_upnl += upnl
            print(f"{mid:<14} {side:>4} {sz:>8.1f} {entry:>7.4f} {cur:>7.4f} ${cost:>8.2f} ${mkt_val:>8.2f} ${upnl:>+8.2f}")
        print("-" * 80)
        print(f"{'TOTAL':<14} {'':>4} {'':>8} {'':>7} {'':>7} ${total_cost:>8.2f} ${total_mkt_value:>8.2f} ${total_upnl:>+8.2f}")

        # 2. Trade events in window
        r2 = await s.execute(text("""
            SELECT event_type, market_id, side, size, price, fees,
                   realized_pnl, event_time, correlation_id
            FROM trade_events
            WHERE bot_name = :bot
              AND event_time > NOW() - INTERVAL '1 hour' * :hours
            ORDER BY event_time DESC
        """), {"bot": bot_name, "hours": hours})
        events = r2.fetchall()

        entries = [e for e in events if e[0] == 'ENTRY']
        exits = [e for e in events if e[0] == 'EXIT']
        resolutions = [e for e in events if e[0] == 'RESOLUTION']

        print(f"\nTRADE EVENTS (last {hours}h):")
        print(f"  Entries: {len(entries)}")
        for e in entries:
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. {e[2]} sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} fee=${float(e[5] or 0):.2f}")

        realized_exit = 0.0
        print(f"  Exits: {len(exits)}")
        for e in exits:
            rpnl = float(e[6] or 0)
            realized_exit += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} pnl=${rpnl:+.2f}")

        realized_res = 0.0
        print(f"  Resolutions: {len(resolutions)}")
        for e in resolutions:
            rpnl = float(e[6] or 0)
            realized_res += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. pnl=${rpnl:+.2f}")

        # 3. All-time from trade_events
        r3 = await s.execute(text("""
            SELECT event_type,
                   COUNT(*),
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0),
                   COALESCE(SUM(CAST(fees AS DOUBLE PRECISION)), 0)
            FROM trade_events
            WHERE bot_name = :bot
            GROUP BY event_type
            ORDER BY event_type
        """), {"bot": bot_name})
        stats = r3.fetchall()
        print(f"\nALL-TIME TRADE EVENTS:")
        total_realized = 0.0
        total_fees = 0.0
        for st in stats:
            rpnl = float(st[2])
            fees = float(st[3])
            total_realized += rpnl
            total_fees += fees
            print(f"  {st[0]:<12} count={st[1]:<5} realized=${rpnl:>+10.2f}  fees=${fees:>8.2f}")
        print(f"  {'TOTAL':<12} {'':5} realized=${total_realized:>+10.2f}  fees=${total_fees:>8.2f}")

        # 4. Data integrity check — detect impossible states (S120 guardrail)
        r4 = await s.execute(text("""
            SELECT market_id, side,
                   SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS entry_sz,
                   SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS exit_sz,
                   SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS res_sz
            FROM trade_events
            WHERE bot_name = :bot
            GROUP BY market_id, side
            HAVING SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                 > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
        """), {"bot": bot_name})
        violations = r4.fetchall()
        if violations:
            print(f"\n{'!'*50}")
            print(f"DATA INTEGRITY WARNINGS ({len(violations)}):")
            print(f"{'!'*50}")
            for v in violations:
                mid = v[0][:14] + ".." if len(v[0]) > 14 else v[0]
                print(f"  {mid} {v[1]}: entry={float(v[2]):.1f} exit={float(v[3]):.1f} res={float(v[4]):.1f} "
                      f"(disposal {float(v[3]) + float(v[4]):.1f} > entry {float(v[2]):.1f})")
            print(f"{'!'*50}")

        # Summary
        print(f"\n{'='*50}")
        print(f"SUMMARY")
        print(f"{'='*50}")
        print(f"  Open positions:     {len(positions)}")
        print(f"  Total cost basis:   ${total_cost:.2f}")
        print(f"  Total mkt value:    ${total_mkt_value:.2f}")
        print(f"  Unrealized P&L:     ${total_upnl:+.2f}")
        print(f"  Realized (exits):   ${realized_exit:+.2f}  (last {hours}h)")
        print(f"  Realized (resol):   ${realized_res:+.2f}  (last {hours}h)")
        print(f"  All-time realized:  ${total_realized:+.2f}")
        print(f"  Net P&L (window):   ${total_upnl + realized_exit + realized_res:+.2f}")

    await db.close()


if __name__ == "__main__":
    bot = sys.argv[1] if len(sys.argv) > 1 else "EsportsBot"
    hrs = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    asyncio.run(bot_pnl(bot, hrs))
