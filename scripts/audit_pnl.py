"""
DEPRECATED: Use `python scripts/run_audit.py` instead.
This script predates the unified audit system (base_engine/audit/).
Retained for reference only — run_audit.py covers all checks with DB persistence,
trend detection, and alerting.

P&L audit: cross-validate trade_events (authority) vs positions, detect split states.
"""
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
            # ── Section 1: Per-bot P&L from trade_events (AUTHORITY) ────
            _hdr("TRADE EVENTS P&L (authority — EXIT + RESOLUTION)")
            bot_clause_te = "AND te.bot_name = :bot" if bot_filter else ""
            params: dict = {"bot": bot_filter} if bot_filter else {}
            r = await s.execute(text(f"""
                SELECT te.bot_name,
                       te.event_type,
                       COUNT(*) as cnt,
                       ROUND(COALESCE(SUM(CAST(te.realized_pnl AS DOUBLE PRECISION)), 0)::numeric, 2) as pnl,
                       ROUND(COALESCE(SUM(CAST(te.fees AS DOUBLE PRECISION)), 0)::numeric, 2) as fees
                FROM trade_events te
                WHERE 1=1 {bot_clause_te}
                GROUP BY te.bot_name, te.event_type
                ORDER BY te.bot_name, te.event_type
            """), params)
            rows = r.fetchall()
            print(f"{'Bot':<20s} {'Type':<12s} {'Count':>6s} {'P&L':>10s} {'Fees':>8s}")
            print("-" * 60)
            for row in rows:
                print(f"{row[0]:<20s} {row[1]:<12s} {row[2]:>6d} ${row[3]:>9} ${row[4]:>7}")

            # ── Section 2: Per-bot P&L from paper_trades (comparison) ───
            _hdr("PAPER TRADES P&L (comparison — resolution only)")
            bot_clause = "AND pt.bot_name = :bot" if bot_filter else ""
            r = await s.execute(text(f"""
                SELECT pt.bot_name,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE pt.resolution IN ('YES','NO')) as resolved,
                       COUNT(*) FILTER (WHERE pt.realized_pnl > 0) as wins,
                       COUNT(*) FILTER (WHERE pt.realized_pnl <= 0 AND pt.realized_pnl IS NOT NULL) as losses,
                       ROUND(COALESCE(SUM(pt.realized_pnl), 0)::numeric, 2) as pnl
                FROM paper_trades pt
                WHERE pt.side IN ('YES','NO') {bot_clause}
                GROUP BY pt.bot_name ORDER BY pt.bot_name
            """), params)
            rows = r.fetchall()
            print(f"{'Bot':<20s} {'Total':>6s} {'Rslvd':>6s} {'Wins':>5s} {'Loss':>5s} {'P&L':>10s}")
            print("-" * 57)
            for row in rows:
                print(f"{row[0]:<20s} {row[1]:>6d} {row[2]:>6d} {row[3]:>5d} {row[4]:>5d} ${row[5]:>9}")

            # ── Section 3: Open positions unrealized ────────────────────
            _hdr("OPEN POSITIONS UNREALIZED P&L")
            r = await s.execute(text(f"""
                SELECT COALESCE(p.source_bot, p.bot_id) as bot,
                       COUNT(*) as open_count,
                       ROUND(COALESCE(SUM(p.size * (COALESCE(p.current_price, p.entry_price) - p.entry_price)), 0)::numeric, 2) as unrealized,
                       ROUND(COALESCE(SUM(p.size * p.entry_price), 0)::numeric, 2) as cost_basis
                FROM positions p
                WHERE p.status = 'open'
                {"AND COALESCE(p.source_bot, p.bot_id) = :bot" if bot_filter else ""}
                GROUP BY COALESCE(p.source_bot, p.bot_id) ORDER BY 1
            """), params)
            rows = r.fetchall()
            print(f"{'Bot':<20s} {'Open':>6s} {'Unrealized':>12s} {'Cost Basis':>12s}")
            print("-" * 54)
            for row in rows:
                print(f"{row[0]:<20s} {row[1]:>6d} ${row[2]:>11} ${row[3]:>11}")

            # ── Section 4: ENTRY count cross-validation ─────────────────
            _hdr("CROSS-VALIDATION (trade_events ENTRY vs paper_trades count)")
            r = await s.execute(text(f"""
                WITH te_counts AS (
                    SELECT bot_name, COUNT(*) as te_entries
                    FROM trade_events
                    WHERE event_type = 'ENTRY' {bot_clause_te}
                    GROUP BY bot_name
                ), pt_counts AS (
                    SELECT bot_name, COUNT(*) as pt_entries
                    FROM paper_trades
                    WHERE side IN ('YES','NO') {bot_clause}
                    GROUP BY bot_name
                )
                SELECT COALESCE(t.bot_name, p.bot_name) as bot,
                       COALESCE(t.te_entries, 0) as trade_events,
                       COALESCE(p.pt_entries, 0) as paper_trades,
                       ABS(COALESCE(t.te_entries, 0) - COALESCE(p.pt_entries, 0)) as diff
                FROM te_counts t
                FULL OUTER JOIN pt_counts p ON t.bot_name = p.bot_name
                ORDER BY 1
            """), params)
            xval_rows = r.fetchall()
            print(f"{'Bot':<20s} {'trade_events':>14s} {'paper_trades':>14s} {'Diff':>6s}")
            print("-" * 58)
            for row in xval_rows:
                flag = " ⚠" if row[3] > 0 else ""
                print(f"{row[0]:<20s} {row[1]:>14d} {row[2]:>14d} {row[3]:>6d}{flag}")
                if row[3] > 0:
                    issues_found += 1

            # ── Section 5: Split state detection ────────────────────────
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

            # ── Section 6: resolved_at NULL check ───────────────────────
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
    parser = argparse.ArgumentParser(description="P&L audit: trade_events (authority) vs paper_trades vs positions")
    parser.add_argument("--bot", type=str, default=None, help="Filter to specific bot (e.g. WeatherBot)")
    parser.add_argument("--fix", action="store_true", help="Repair split states (resolution set, realized_pnl NULL)")
    parser.add_argument("--verbose", action="store_true", help="Show individual trade details")
    args = parser.parse_args()
    asyncio.run(run_audit(args.bot, args.fix, args.verbose))
