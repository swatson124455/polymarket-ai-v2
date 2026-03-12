"""P&L audit: cross-validate paper_trades vs positions, detect split states, report per-bot P&L."""
import argparse
import asyncio
import io
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hdr(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


async def run_audit(bot_filter: str | None, fix: bool, verbose: bool) -> None:
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    try:
        await db.init()
    except Exception as e:
        print(f"DB connection failed: {e}")
        return

    issues_found = 0

    try:
        async with db.get_session() as s:
            # ── Section 1: Per-bot P&L from paper_trades ──────────────────
            _hdr("PAPER TRADES P&L (hold-to-resolution)")
            bot_clause = "AND pt.bot_name = :bot" if bot_filter else ""
            params: dict = {"bot": bot_filter} if bot_filter else {}
            r = await s.execute(text(f"""
                SELECT pt.bot_name,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE pt.resolution IN ('YES','NO')) as resolved,
                       COUNT(*) FILTER (WHERE pt.realized_pnl > 0) as wins,
                       COUNT(*) FILTER (WHERE pt.realized_pnl <= 0 AND pt.realized_pnl IS NOT NULL) as losses,
                       ROUND(COALESCE(SUM(pt.realized_pnl), 0)::numeric, 2) as pnl,
                       ROUND(AVG(pt.realized_pnl) FILTER (WHERE pt.realized_pnl IS NOT NULL)::numeric, 2) as avg_pnl
                FROM paper_trades pt
                WHERE pt.side IN ('YES','NO') {bot_clause}
                GROUP BY pt.bot_name ORDER BY pt.bot_name
            """), params)
            rows = r.fetchall()
            print(f"{'Bot':<20s} {'Total':>6s} {'Rslvd':>6s} {'Wins':>5s} {'Loss':>5s} {'P&L':>10s} {'Avg':>8s}")
            print("-" * 64)
            for row in rows:
                print(f"{row[0]:<20s} {row[1]:>6d} {row[2]:>6d} {row[3]:>5d} {row[4]:>5d} ${row[5]:>9} ${row[6] or 0:>7}")

            # ── Section 2: Per-bot P&L from positions ─────────────────────
            _hdr("POSITIONS P&L (exit-based + resolution)")
            r = await s.execute(text(f"""
                SELECT COALESCE(p.source_bot, p.bot_id) as bot,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE p.status = 'closed') as closed,
                       COUNT(*) FILTER (WHERE p.unrealized_pnl > 0) as wins,
                       COUNT(*) FILTER (WHERE p.unrealized_pnl < 0) as losses,
                       ROUND(COALESCE(SUM(p.unrealized_pnl), 0)::numeric, 2) as pnl
                FROM positions p
                {"WHERE COALESCE(p.source_bot, p.bot_id) = :bot" if bot_filter else ""}
                GROUP BY COALESCE(p.source_bot, p.bot_id) ORDER BY 1
            """), params)
            rows = r.fetchall()
            print(f"{'Bot':<20s} {'Total':>6s} {'Closed':>7s} {'Wins':>5s} {'Loss':>5s} {'P&L':>10s}")
            print("-" * 57)
            for row in rows:
                print(f"{row[0]:<20s} {row[1]:>6d} {row[2]:>7d} {row[3]:>5d} {row[4]:>5d} ${row[5]:>9}")

            # ── Section 3: Split state detection ──────────────────────────
            _hdr("SPLIT STATES (resolution set, realized_pnl NULL)")
            r = await s.execute(text(f"""
                SELECT pt.id, pt.bot_name, pt.market_id, pt.side, pt.resolution,
                       pt.price, pt.size
                FROM paper_trades pt
                WHERE pt.resolution IN ('YES', 'NO')
                  AND pt.realized_pnl IS NULL
                  AND pt.side IN ('YES', 'NO')
                  {bot_clause}
                ORDER BY pt.bot_name, pt.created_at
            """), params)
            split_rows = r.fetchall()
            if split_rows:
                issues_found += len(split_rows)
                print(f"  FOUND {len(split_rows)} split-state row(s):")
                for row in split_rows:
                    print(f"    id={row[0]} bot={row[1]} market={str(row[2])[:20]}.. "
                          f"side={row[3]} resolution={row[4]} price={row[5]:.4f} size={row[6]:.2f}")

                if fix:
                    print("\n  Fixing split states...")
                    fr = await s.execute(text("""
                        UPDATE paper_trades pt SET realized_pnl = (
                            CASE
                                WHEN pt.resolution = 'YES' AND LOWER(pt.side) = 'yes' THEN pt.size * (1.0 - pt.price)
                                WHEN pt.resolution = 'YES' AND LOWER(pt.side) = 'no'  THEN pt.size * (0.0 - pt.price)
                                WHEN pt.resolution = 'NO'  AND LOWER(pt.side) = 'yes' THEN pt.size * (0.0 - pt.price)
                                WHEN pt.resolution = 'NO'  AND LOWER(pt.side) = 'no'  THEN pt.size * (1.0 - pt.price)
                            END
                        ) - (pt.size * pt.price * 0.015)
                        WHERE pt.resolution IN ('YES','NO')
                          AND pt.realized_pnl IS NULL
                          AND pt.side IN ('YES','NO')
                    """))
                    await s.commit()
                    print(f"  Fixed {fr.rowcount} row(s)")
            else:
                print("  None found. OK.")

            # ── Section 4: Orphaned positions ─────────────────────────────
            _hdr("ORPHANED POSITIONS (closed, market resolved, no P&L)")
            r = await s.execute(text(f"""
                SELECT COALESCE(p.source_bot, p.bot_id) as bot,
                       p.market_id, p.side, p.entry_price, p.size,
                       m.resolution
                FROM positions p
                JOIN markets m ON p.market_id = m.id
                WHERE m.resolution IN ('YES', 'NO')
                  AND p.status = 'closed'
                  AND (p.unrealized_pnl IS NULL OR p.unrealized_pnl = 0.0)
                  {"AND COALESCE(p.source_bot, p.bot_id) = :bot" if bot_filter else ""}
                ORDER BY 1
                LIMIT 50
            """), params)
            orphan_rows = r.fetchall()
            if orphan_rows:
                issues_found += len(orphan_rows)
                print(f"  FOUND {len(orphan_rows)} orphaned position(s):")
                for row in orphan_rows:
                    ep = float(row[3]) if row[3] else 0
                    sz = float(row[4]) if row[4] else 0
                    print(f"    bot={row[0]} market={str(row[1])[:20]}.. "
                          f"side={row[2]} entry={ep:.4f} size={sz:.2f} resolution={row[5]}")
            else:
                print("  None found. OK.")

            # ── Section 5: Cross-validation ───────────────────────────────
            _hdr("CROSS-VALIDATION (paper_trades vs positions P&L)")
            r = await s.execute(text(f"""
                SELECT pt.bot_name, pt.market_id, pt.side,
                       ROUND(pt.realized_pnl::numeric, 4) as paper_pnl,
                       ROUND(p.unrealized_pnl::numeric, 4) as pos_pnl,
                       ROUND(ABS(pt.realized_pnl - p.unrealized_pnl)::numeric, 4) as diff
                FROM paper_trades pt
                JOIN positions p ON pt.market_id = p.market_id
                  AND pt.bot_name = COALESCE(p.source_bot, p.bot_id)
                  AND UPPER(pt.side) = UPPER(p.side)
                WHERE pt.resolution IN ('YES', 'NO')
                  AND pt.realized_pnl IS NOT NULL
                  AND p.unrealized_pnl IS NOT NULL
                  AND ABS(pt.realized_pnl - p.unrealized_pnl) > 0.01
                  {bot_clause}
                ORDER BY ABS(pt.realized_pnl - p.unrealized_pnl) DESC
                LIMIT 25
            """), params)
            xval_rows = r.fetchall()
            if xval_rows:
                issues_found += len(xval_rows)
                print(f"  FOUND {len(xval_rows)} discrepancy(ies) > $0.01:")
                print(f"  {'Bot':<15s} {'Side':>4s} {'Paper P&L':>10s} {'Pos P&L':>10s} {'Diff':>8s}")
                print(f"  {'-'*51}")
                for row in xval_rows:
                    print(f"  {row[0]:<15s} {row[2]:>4s} ${row[3]:>9} ${row[4]:>9} ${row[5]:>7}")
            else:
                print("  All matching trades agree. OK.")

            # ── Section 6: resolved_at NULL check ─────────────────────────
            _hdr("RESOLVED_AT NULL CHECK")
            r = await s.execute(text(f"""
                SELECT pt.bot_name, COUNT(*) as cnt
                FROM paper_trades pt
                WHERE pt.resolution IN ('YES','NO')
                  AND pt.resolved_at IS NULL
                  AND pt.side IN ('YES','NO')
                  {bot_clause}
                GROUP BY pt.bot_name ORDER BY pt.bot_name
            """), params)
            null_resolved_rows = r.fetchall()
            if null_resolved_rows:
                issues_found += sum(r[1] for r in null_resolved_rows)
                print("  FOUND trades with resolution but NULL resolved_at:")
                for row in null_resolved_rows:
                    print(f"    {row[0]}: {row[1]} row(s)")
            else:
                print("  All resolved trades have resolved_at. OK.")

    except Exception as e:
        print(f"Query error: {e}")
        import traceback
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────────────
    _hdr("SUMMARY")
    if issues_found == 0:
        print("  All checks passed. No data integrity issues found.")
    else:
        print(f"  {issues_found} issue(s) found.")
        if not fix:
            print("  Run with --fix to repair split states.")

    try:
        await db.close()
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P&L audit: cross-validate paper_trades vs positions")
    parser.add_argument("--bot", type=str, default=None, help="Filter to specific bot (e.g. WeatherBot)")
    parser.add_argument("--fix", action="store_true", help="Repair split states (resolution set, realized_pnl NULL)")
    parser.add_argument("--verbose", action="store_true", help="Show individual trade details")
    args = parser.parse_args()
    asyncio.run(run_audit(args.bot, args.fix, args.verbose))
