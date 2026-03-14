#!/usr/bin/env python3
"""EsportsBot diagnostic — trade_events P&L, positions, waterfall, exposure."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

async def diag():
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        BOTS = ["EsportsBot", "EsportsLiveBot", "EsportsSeriesBot"]
        BOTS_CLAUSE = "('EsportsBot','EsportsLiveBot','EsportsSeriesBot')"

        # 1. trade_events last 16h per bot
        for bot in BOTS:
            r = await s.execute(text("""
                SELECT te.event_type, te.market_id, te.side, te.size, te.price,
                       te.realized_pnl, te.event_time,
                       te.event_data->>'game' as game,
                       te.event_data->>'question' as question
                FROM trade_events te
                WHERE te.bot_name = :bot
                  AND te.event_time > NOW() - INTERVAL '16 hours'
                ORDER BY te.event_time ASC
            """), {"bot": bot})
            events = r.fetchall()
            total = sum(float(e[5] or 0) for e in events)
            print(f"\n=== {bot} TRADE EVENTS (last 16h): {len(events)} | realized=${total:+.2f} ===")
            for e in events:
                rpnl = float(e[5] or 0)
                q = (e[8] or "")[:50]
                g = e[7] or ""
                print(f"  {e[6]} {e[0]:>10} {e[2]:>3} sz={float(e[3]):>7.1f} px={float(e[4]):.4f} rpnl=${rpnl:>+8.2f} [{g}] {q}")

        # 2. Open positions
        print(f"\n=== OPEN POSITIONS (all esports bots) ===")
        r3 = await s.execute(text(f"""
            SELECT p.bot_id, p.market_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl, p.opened_at, p.source_bot
            FROM positions p
            WHERE p.status = 'open'
              AND (p.bot_id IN {BOTS_CLAUSE} OR p.source_bot IN {BOTS_CLAUSE})
            ORDER BY p.opened_at ASC
        """))
        positions = r3.fetchall()
        total_invested = 0.0
        total_upnl = 0.0
        for p in positions:
            bot = p[0] or p[8] or "?"
            entry = float(p[4] or 0)
            cur = float(p[5] or 0)
            sz = float(p[3] or 0)
            upnl = float(p[6] or 0)
            invested = entry * sz
            total_invested += invested
            total_upnl += upnl
            mid = p[1][:14] + "..."
            print(f"  {bot:>18} {mid} {p[2]:>3} sz={sz:>7.1f} entry={entry:.4f} cur={cur:.4f} uPnL=${upnl:>+8.2f} opened={p[7]}")
        print(f"  Total invested: ${total_invested:.2f}")
        print(f"  Total unrealized (DB mark-to-market): ${total_upnl:.2f}")

        # 3. Recently closed (via EXIT trade_events)
        r4 = await s.execute(text(f"""
            SELECT te.bot_name, te.market_id, te.side, te.size, te.price,
                   te.realized_pnl, te.event_time
            FROM trade_events te
            WHERE te.bot_name IN {BOTS_CLAUSE}
              AND te.event_type = 'EXIT'
              AND te.event_time > NOW() - INTERVAL '16 hours'
            ORDER BY te.event_time ASC
        """))
        closed = r4.fetchall()
        exit_total = sum(float(c[5] or 0) for c in closed)
        print(f"\n=== EXITS (last 16h): {len(closed)} | realized=${exit_total:+.2f} ===")
        for c in closed:
            rpnl = float(c[5] or 0)
            mid = c[1][:14] + "..."
            print(f"  {c[0]:>18} {mid} {c[2]:>3} sz={float(c[3]):>7.1f} px={float(c[4]):.4f} rpnl=${rpnl:>+8.2f} {c[6]}")

        # 4. Resolutions
        r5 = await s.execute(text(f"""
            SELECT te.bot_name, te.market_id, te.side, te.size, te.price,
                   te.realized_pnl, te.event_time,
                   te.event_data->>'question' as question
            FROM trade_events te
            WHERE te.bot_name IN {BOTS_CLAUSE}
              AND te.event_type = 'RESOLUTION'
              AND te.event_time > NOW() - INTERVAL '16 hours'
            ORDER BY te.event_time ASC
        """))
        resolutions = r5.fetchall()
        res_total = sum(float(r[5] or 0) for r in resolutions)
        print(f"\n=== RESOLUTIONS (last 16h): {len(resolutions)} | total=${res_total:+.2f} ===")
        for r in resolutions:
            rpnl = float(r[5] or 0)
            q = (r[7] or "")[:55]
            print(f"  {r[0]:>18} rpnl=${rpnl:>+8.2f} {r[2]:>3} sz={float(r[3]):>7.1f} px={float(r[4]):.4f} | {q}")

        # 5. Prediction log waterfall
        r7 = await s.execute(text(f"""
            SELECT pl.trade_executed, pl.model_name, COUNT(*) as cnt,
                   AVG(pl.edge) as avg_edge, AVG(pl.confidence) as avg_conf
            FROM prediction_log pl
            WHERE pl.bot_name IN {BOTS_CLAUSE}
              AND pl.created_at > NOW() - INTERVAL '16 hours'
            GROUP BY pl.trade_executed, pl.model_name
            ORDER BY cnt DESC
            LIMIT 15
        """))
        waterfall = r7.fetchall()
        print(f"\n=== PREDICTION WATERFALL (last 16h) ===")
        for w in waterfall:
            executed = "TRADED" if w[0] else "SKIPPED"
            avg_edge = float(w[3] or 0)
            avg_conf = float(w[4] or 0)
            print(f"  {w[2]:>5}x  {executed:>7} [{w[1] or '?'}] avg_edge={avg_edge:.3f} avg_conf={avg_conf:.3f}")

        # 6. System-wide exposure
        r8 = await s.execute(text("""
            SELECT p.bot_id, COUNT(*),
                   SUM(p.entry_price * p.size) as invested
            FROM positions p
            WHERE p.status = 'open'
            GROUP BY p.bot_id
            ORDER BY invested DESC
        """))
        exposure = r8.fetchall()
        print(f"\n=== SYSTEM-WIDE EXPOSURE ===")
        grand = 0.0
        for ex in exposure:
            inv = float(ex[2] or 0)
            grand += inv
            print(f"  {ex[0]:>20}: {ex[1]:>4} pos, ${inv:>10.2f}")
        print(f"  {'TOTAL':>20}: ${grand:>10.2f}")
        print(f"  Max allowed: $20,000.00")
        print(f"  Headroom: ${20000 - grand:.2f}")

    await db.close()

asyncio.run(diag())
