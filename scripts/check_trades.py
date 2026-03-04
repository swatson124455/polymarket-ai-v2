"""Check recent paper trade activity."""
import asyncio, os, sys, io
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
        r = await s.execute(text("SELECT COUNT(*) FROM paper_trades"))
        print(f"Total paper trades: {r.scalar()}")

        # Get actual columns
        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'paper_trades' ORDER BY ordinal_position"
        ))
        cols = [row[0] for row in r.fetchall()]
        print(f"\npaper_trades columns: {cols}")

        r = await s.execute(text("""
            SELECT pt.id, pt.market_id, pt.bot_name, pt.side, pt.size, pt.price, pt.confidence,
                   pt.created_at, LEFT(m.question, 55) as q
            FROM paper_trades pt
            LEFT JOIN markets m ON m.id = pt.market_id OR m.condition_id = pt.market_id
            ORDER BY pt.created_at DESC LIMIT 10
        """))
        rows = r.fetchall()
        print(f"\nLast 10 paper trades:")
        for row in rows:
            q = (row[8] or "").encode("ascii", "replace").decode("ascii")
            conf = row[6] or 0
            print(f"  #{row[0]} {row[4]:.1f}@${row[5]:.4f} {row[3]:>4s} conf={conf:.1%} bot={row[2]} {row[7]} | {q}")

        r = await s.execute(text("SELECT bot_name, COUNT(*), MAX(created_at) FROM paper_trades GROUP BY bot_name ORDER BY COUNT(*) DESC"))
        print(f"\nTrades by bot:")
        for row in r.fetchall():
            print(f"  {row[0]:20s}: {row[1]:>4d} trades, last={row[2]}")

        r = await s.execute(text("SELECT COUNT(*) FROM paper_trades WHERE created_at >= NOW() - INTERVAL '24 hours'"))
        print(f"\nTrades in last 24h: {r.scalar()}")

        r = await s.execute(text("SELECT COUNT(*) FROM paper_trades WHERE created_at >= NOW() - INTERVAL '1 hour'"))
        print(f"Trades in last 1h: {r.scalar()}")

        # Check if main.py is currently running (look at predictions table)
        r = await s.execute(text("SELECT COUNT(*) FROM prediction_log WHERE created_at >= NOW() - INTERVAL '1 hour'"))
        print(f"Predictions in last 1h: {r.scalar()}")

    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
