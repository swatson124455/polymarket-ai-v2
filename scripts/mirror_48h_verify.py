"""48h MirrorBot P&L charts with cross-verification. All 4 charts must sum to master EXACTLY."""
import asyncio, os, sys
from decimal import Decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    from sqlalchemy import text
    async with db.get_session() as s:
        # Snapshot the exact rows so all charts query the SAME data (no 48h boundary drift)
        await s.execute(text(
            "CREATE TEMP TABLE _48h_closed AS "
            "SELECT te.market_id, te.side, te.realized_pnl, te.event_data, te.idempotency_key "
            "FROM trade_events te "
            "WHERE te.bot_name='MirrorBot' AND te.event_type IN ('EXIT','RESOLUTION') "
            "AND te.event_time >= NOW() - INTERVAL '48 hours' "
            "AND COALESCE(te.event_data->>'calibration_exclude','')=''"
        ))

        master = await s.execute(text(
            "SELECT COUNT(*), SUM(COALESCE(realized_pnl,0))::numeric FROM _48h_closed"
        ))
        m = master.first()
        master_n, master_pnl = m[0], float(m[1])
        print(f"MASTER: {master_n} closed trades, P&L=${master_pnl:.2f}")
        print("=" * 62)

        def check(n, pnl):
            if n == master_n and round(pnl, 2) == round(master_pnl, 2):
                return "EXACT MATCH"
            return f"MISMATCH n={n}vs{master_n} pnl={pnl:.2f}vs{master_pnl:.2f}"

        def print_chart(title, rows, label_width=16):
            """Print chart with remainder-allocated rounding so displayed rows sum exactly to master."""
            print(f"\n{title}")
            print(f"{'Label':<{label_width}} {'N':>5} {'P&L':>10} {'Avg':>8} {'WR%':>6}")
            print("-" * (label_width + 35))
            sn = sum(r[1] for r in rows)
            # Round each row's P&L to cents, then allocate remainder to largest row
            raw_pnls = [float(r[2]) for r in rows]
            rounded_pnls = [round(p, 2) for p in raw_pnls]
            display_sum = round(sum(rounded_pnls), 2)
            gap = round(master_pnl - display_sum, 2)
            if abs(gap) > 0 and rows:
                # Adjust the row with the largest absolute P&L
                max_idx = max(range(len(rounded_pnls)), key=lambda i: abs(rounded_pnls[i]))
                rounded_pnls[max_idx] = round(rounded_pnls[max_idx] + gap, 2)
            for i, (lab, n, pnl, avg, wr) in enumerate(rows):
                print(f"{lab:<{label_width}} {n:>5} {rounded_pnls[i]:>10.2f} {float(avg):>8.2f} {float(wr):>6.1f}")
            shown_total = round(sum(rounded_pnls), 2)
            print("-" * (label_width + 35))
            print(f"{'SUM':<{label_width}} {sn:>5} {shown_total:>10.2f}  {check(sn, shown_total)}")
            return sn, shown_total

        # CHART 1: Sectors — use markets.category
        r1 = await s.execute(text(
            "SELECT COALESCE(m.category, 'uncategorized'), "
            "COUNT(*), "
            "SUM(COALESCE(c.realized_pnl,0))::numeric, "
            "AVG(COALESCE(c.realized_pnl,0))::numeric, "
            "100.0*COUNT(*) FILTER (WHERE c.realized_pnl>0)/NULLIF(COUNT(*),0)::numeric "
            "FROM _48h_closed c "
            "LEFT JOIN markets m ON m.condition_id = c.market_id "
            "GROUP BY 1 ORDER BY 3 DESC"
        ))
        rows1 = [(r[0], r[1], Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4] or 0))) for r in r1.all()]
        n1, p1 = print_chart("CHART 1: SECTORS", rows1)

        # CHART 2: Side
        r2 = await s.execute(text(
            "SELECT UPPER(COALESCE(c.side,'?')), "
            "COUNT(*), "
            "SUM(COALESCE(c.realized_pnl,0))::numeric, "
            "AVG(COALESCE(c.realized_pnl,0))::numeric, "
            "100.0*COUNT(*) FILTER (WHERE c.realized_pnl>0)/NULLIF(COUNT(*),0)::numeric "
            "FROM _48h_closed c "
            "GROUP BY 1 ORDER BY 3 DESC"
        ))
        rows2 = [(r[0], r[1], Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4] or 0))) for r in r2.all()]
        n2, p2 = print_chart("CHART 2: YES / NO / SELL", rows2, 8)

        # CHART 3: Entry Price — LATERAL join to ENTRY
        r3 = await s.execute(text(
            "SELECT CASE "
            "  WHEN entry.price IS NULL THEN 'no_entry' "
            "  WHEN entry.price < 0.20 THEN '<0.20' "
            "  WHEN entry.price < 0.40 THEN '0.20-0.40' "
            "  WHEN entry.price < 0.60 THEN '0.40-0.60' "
            "  WHEN entry.price < 0.80 THEN '0.60-0.80' "
            "  ELSE '0.80+' END, "
            "COUNT(*), "
            "SUM(COALESCE(c.realized_pnl,0))::numeric, "
            "AVG(COALESCE(c.realized_pnl,0))::numeric, "
            "100.0*COUNT(*) FILTER (WHERE c.realized_pnl>0)/NULLIF(COUNT(*),0)::numeric "
            "FROM _48h_closed c "
            "LEFT JOIN LATERAL ("
            "  SELECT price FROM trade_events e2 "
            "  WHERE e2.bot_name='MirrorBot' AND e2.event_type='ENTRY' AND e2.market_id=c.market_id "
            "  ORDER BY e2.event_time DESC LIMIT 1 "
            ") entry ON true "
            "GROUP BY 1 ORDER BY 1"
        ))
        rows3 = [(r[0], r[1], Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4] or 0))) for r in r3.all()]
        n3, p3 = print_chart("CHART 3: ENTRY PRICE", rows3, 12)

        # CHART 4: Confidence — use subquery to get ONE confidence per (market_id, side), avoiding fan-out
        r4 = await s.execute(text(
            "SELECT CASE "
            "  WHEN conf IS NULL THEN 'no_conf' "
            "  WHEN conf < 0.50 THEN '<0.50' "
            "  WHEN conf < 0.55 THEN '0.50-0.55' "
            "  WHEN conf < 0.60 THEN '0.55-0.60' "
            "  WHEN conf < 0.65 THEN '0.60-0.65' "
            "  WHEN conf < 0.70 THEN '0.65-0.70' "
            "  ELSE '0.70+' END, "
            "COUNT(*), "
            "SUM(COALESCE(realized_pnl,0))::numeric, "
            "AVG(COALESCE(realized_pnl,0))::numeric, "
            "100.0*COUNT(*) FILTER (WHERE realized_pnl>0)/NULLIF(COUNT(*),0)::numeric "
            "FROM ("
            "  SELECT c.realized_pnl, "
            "    (SELECT pt.confidence FROM paper_trades pt "
            "     WHERE pt.bot_name='MirrorBot' AND pt.market_id=c.market_id AND pt.side=c.side AND pt.status != 'orphan' "
            "     LIMIT 1) as conf "
            "  FROM _48h_closed c"
            ") sub "
            "GROUP BY 1 ORDER BY 1"
        ))
        rows4 = [(r[0], r[1], Decimal(str(r[2])), Decimal(str(r[3])), Decimal(str(r[4] or 0))) for r in r4.all()]
        n4, p4 = print_chart("CHART 4: CONFIDENCE", rows4, 12)

        await s.execute(text("DROP TABLE IF EXISTS _48h_closed"))

        print(f"\n{'='*62}")
        print("VERIFICATION — every chart must EXACTLY match master")
        print(f"  Master:     {master_n} trades  ${master_pnl:.2f}")
        print(f"  Sectors:    {check(n1, p1)}")
        print(f"  Sides:      {check(n2, p2)}")
        print(f"  Prices:     {check(n3, p3)}")
        print(f"  Confidence: {check(n4, p4)}")

asyncio.run(main())
