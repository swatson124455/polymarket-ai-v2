"""Paper trade breakdown diagnostic."""
import asyncio


async def breakdown():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    async with db.get_session() as session:
        r = await session.execute(text("""
            SELECT
                pt.id, pt.bot_name, pt.market_id, pt.side, pt.price, pt.size,
                pt.confidence, pt.created_at, pt.resolution, pt.realized_pnl,
                m.question,
                m.resolved,
                m.resolution as market_resolution
            FROM paper_trades pt
            LEFT JOIN markets m ON (pt.market_id = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id)
            ORDER BY pt.created_at ASC
        """))
        rows = r.fetchall()

        print(f"\n{'='*80}")
        print(f"PAPER TRADE BREAKDOWN ({len(rows)} trades)")
        print(f"{'='*80}\n")

        total_cost = 0.0
        total_pnl = 0.0

        for row in rows:
            tid, bot, mid, side, price, size, conf, created, resolution, pnl, question, resolved, mkt_res = row
            cost = price * size if side != "SELL" else 0
            total_cost += cost
            if pnl:
                total_pnl += pnl

            q = (question[:65] + "...") if question and len(question) > 65 else (question or "N/A")

            print(f"Trade #{tid}  {created}")
            print(f"  Market: {mid}")
            print(f"  Question: {q}")
            print(f"  Side: {side}  Price: ${price:.4f}  Size: {size:.2f}  Cost: ${cost:.2f}")
            print(f"  Confidence: {conf:.4f} ({conf*100:.1f}%)")
            print(f"  Resolved: {resolved}  Resolution: {mkt_res or 'pending'}")
            if pnl:
                print(f"  P&L: ${pnl:.2f}")
            else:
                print(f"  P&L: pending")
            print()

        print(f"{'='*80}")
        print(f"SUMMARY")
        print(f"{'='*80}")
        print(f"Total trades: {len(rows)}")
        buys = [r for r in rows if r[3] != "SELL"]
        sells = [r for r in rows if r[3] == "SELL"]
        print(f"Buys: {len(buys)}  Sells: {len(sells)}")
        print(f"Total capital deployed: ${total_cost:.2f}")
        print(f"Realized P&L: ${total_pnl:.2f}")

        # Open positions
        r2 = await session.execute(text("""
            SELECT p.market_id, p.side, p.entry_price, p.size, p.status, p.unrealized_pnl, p.bot_id
            FROM positions p WHERE p.status = 'open'
        """))
        open_pos = r2.fetchall()
        print(f"\nOpen positions: {len(open_pos)}")
        for pos in open_pos:
            print(f"  {pos[6]}: market={pos[0]} side={pos[1]} entry=${pos[2]:.4f} size={pos[3]:.2f}")

        # Prediction distribution
        r3 = await session.execute(text("""
            SELECT COUNT(*), AVG(predicted_prob),
                   PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY predicted_prob),
                   PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY predicted_prob)
            FROM prediction_log WHERE created_at > NOW() - INTERVAL '7 days'
        """))
        pred = r3.fetchone()
        if pred and pred[0] > 0:
            print(f"\nPrediction distribution (7d): n={pred[0]} avg={pred[1]:.3f} p25={pred[2]:.3f} p75={pred[3]:.3f}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(breakdown())
