#!/usr/bin/env python3
"""
Shadow Fill Analysis — retroactive P&L from recorded book data.

Usage:
    python scripts/shadow_analysis.py [bot_name] [hours]

Examples:
    python scripts/shadow_analysis.py              # All bots, last 168h (7d)
    python scripts/shadow_analysis.py WeatherBot    # WeatherBot only, 7d
    python scripts/shadow_analysis.py MirrorBot 24  # MirrorBot, last 24h
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone


async def main():
    from base_engine.data.database import Database

    bot_filter = sys.argv[1] if len(sys.argv) > 1 else None
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 168
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    db = Database()
    await db.initialize()

    try:
        from sqlalchemy import text
        async with db.get_session() as session:
            where = "WHERE sf.created_at > :since"
            params = {"since": since.replace(tzinfo=None)}
            if bot_filter:
                where += " AND sf.bot_name = :bot"
                params["bot"] = bot_filter

            # 1. Summary by bot
            rows = await session.execute(text(f"""
                SELECT sf.bot_name,
                       COUNT(*) AS total_signals,
                       SUM(CASE WHEN sf.trade_executed THEN 1 ELSE 0 END) AS executed,
                       SUM(CASE WHEN NOT sf.trade_executed THEN 1 ELSE 0 END) AS rejected,
                       ROUND(AVG(sf.book_walk_slippage)::numeric, 6) AS avg_slippage,
                       ROUND(AVG(sf.edge_at_vwap)::numeric, 4) AS avg_edge_at_vwap,
                       ROUND(AVG(sf.fill_fraction)::numeric, 4) AS avg_fill_frac,
                       ROUND(AVG(sf.latency_ms)::numeric, 1) AS avg_latency_ms,
                       ROUND(AVG(sf.depth_at_best_usd)::numeric, 2) AS avg_depth_best,
                       ROUND(AVG(sf.spread)::numeric, 4) AS avg_spread,
                       SUM(CASE WHEN sf.book_snapshot IS NOT NULL THEN 1 ELSE 0 END) AS with_book
                FROM shadow_fills sf
                {where}
                GROUP BY sf.bot_name
                ORDER BY total_signals DESC
            """), params)

            print(f"\n{'='*80}")
            print(f"SHADOW FILL ANALYSIS — last {hours}h")
            print(f"{'='*80}\n")

            summary = rows.fetchall()
            if not summary:
                print("No shadow fill data found.")
                return

            for r in summary:
                print(f"  {r[0]}:")
                print(f"    Signals: {r[1]}  (executed: {r[2]}, rejected: {r[3]})")
                print(f"    Avg slippage: {r[4] or 0:.6f}  |  Avg edge@VWAP: {r[5] or 0:.4f}")
                print(f"    Avg fill fraction: {r[6] or 0:.4f}  |  Avg latency: {r[7] or 0:.1f}ms")
                print(f"    Avg depth@best: ${r[8] or 0:.2f}  |  Avg spread: {r[9] or 0:.4f}")
                print(f"    With book data: {r[10]}/{r[1]}")
                print()

            # 2. Resolved P&L (only if resolutions exist)
            res_rows = await session.execute(text(f"""
                SELECT sf.bot_name,
                       COUNT(*) AS resolved,
                       SUM(CASE WHEN sf.trade_executed THEN 1 ELSE 0 END) AS executed_resolved,
                       ROUND(SUM(CASE WHEN sf.trade_executed THEN sf.shadow_pnl ELSE 0 END)::numeric, 2) AS executed_pnl,
                       ROUND(SUM(CASE WHEN NOT sf.trade_executed THEN sf.shadow_pnl ELSE 0 END)::numeric, 2) AS missed_pnl,
                       ROUND(AVG(CASE WHEN sf.trade_executed AND sf.shadow_pnl > 0 THEN 1.0
                                      WHEN sf.trade_executed AND sf.shadow_pnl <= 0 THEN 0.0
                                      ELSE NULL END)::numeric, 4) AS win_rate
                FROM shadow_fills sf
                {where} AND sf.resolved_at IS NOT NULL
                GROUP BY sf.bot_name
                ORDER BY executed_pnl DESC
            """), params)

            res_data = res_rows.fetchall()
            if res_data:
                print(f"{'─'*80}")
                print("RESOLVED P&L (retroactive from book data)")
                print(f"{'─'*80}\n")
                for r in res_data:
                    print(f"  {r[0]}:")
                    print(f"    Resolved: {r[1]}  (executed: {r[2]})")
                    print(f"    Executed P&L: ${r[3] or 0:+.2f}  |  Missed P&L: ${r[4] or 0:+.2f}")
                    print(f"    Win rate: {(r[5] or 0) * 100:.1f}%")
                    print()

            # 3. Latency buckets (executed trades only)
            lat_rows = await session.execute(text(f"""
                SELECT
                    CASE
                        WHEN sf.latency_ms < 100 THEN '<100ms'
                        WHEN sf.latency_ms < 500 THEN '100-500ms'
                        WHEN sf.latency_ms < 2000 THEN '0.5-2s'
                        WHEN sf.latency_ms < 10000 THEN '2-10s'
                        ELSE '>10s'
                    END AS bucket,
                    COUNT(*) AS trades,
                    ROUND(AVG(sf.book_walk_slippage)::numeric, 6) AS avg_slippage,
                    ROUND(AVG(sf.shadow_pnl)::numeric, 4) AS avg_pnl,
                    ROUND(AVG(CASE WHEN sf.shadow_pnl > 0 THEN 1.0 ELSE 0.0 END)::numeric, 4) AS win_rate
                FROM shadow_fills sf
                {where} AND sf.trade_executed AND sf.latency_ms IS NOT NULL AND sf.resolved_at IS NOT NULL
                GROUP BY bucket
                ORDER BY MIN(sf.latency_ms)
            """), params)

            lat_data = lat_rows.fetchall()
            if lat_data:
                print(f"{'─'*80}")
                print("LATENCY vs OUTCOME (resolved executed trades)")
                print(f"{'─'*80}\n")
                print(f"  {'Bucket':<12} {'Trades':>7} {'Avg Slip':>12} {'Avg P&L':>10} {'Win Rate':>10}")
                print(f"  {'─'*51}")
                for r in lat_data:
                    print(f"  {r[0]:<12} {r[1]:>7} {r[2] or 0:>12.6f} {r[3] or 0:>10.4f} {(r[4] or 0)*100:>9.1f}%")
                print()

            # 4. Slippage distribution
            slip_rows = await session.execute(text(f"""
                SELECT
                    CASE
                        WHEN sf.book_walk_slippage = 0 THEN '0 (at best)'
                        WHEN sf.book_walk_slippage < 0.005 THEN '<0.5c'
                        WHEN sf.book_walk_slippage < 0.01 THEN '0.5-1c'
                        WHEN sf.book_walk_slippage < 0.02 THEN '1-2c'
                        ELSE '>2c'
                    END AS bucket,
                    COUNT(*) AS trades,
                    ROUND(AVG(sf.order_size_usd)::numeric, 2) AS avg_size_usd
                FROM shadow_fills sf
                {where} AND sf.trade_executed AND sf.book_walk_slippage IS NOT NULL
                GROUP BY bucket
                ORDER BY MIN(sf.book_walk_slippage)
            """), params)

            slip_data = slip_rows.fetchall()
            if slip_data:
                print(f"{'─'*80}")
                print("BOOK WALK SLIPPAGE DISTRIBUTION")
                print(f"{'─'*80}\n")
                print(f"  {'Slippage':<14} {'Trades':>7} {'Avg Size':>10}")
                print(f"  {'─'*31}")
                for r in slip_data:
                    print(f"  {r[0]:<14} {r[1]:>7} ${r[2] or 0:>9.2f}")
                print()

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
