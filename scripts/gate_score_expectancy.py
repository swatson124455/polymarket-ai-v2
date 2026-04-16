#!/usr/bin/env python3
"""
7E: Gate score expectancy analysis for MirrorBot.

Buckets prediction_log entries by gate_score (stored in `confidence` column)
and computes win rate, realized edge, and expectancy per bucket.
Splits by trade_executed to compare traded vs gate-blocked signals.

Usage:
    python scripts/gate_score_expectancy.py          # text table
    python scripts/gate_score_expectancy.py --json    # JSON output
"""
import argparse
import asyncio
import io
import json
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


BUCKETS = [
    ("0.20-0.35", 0.20, 0.35),
    ("0.35-0.50", 0.35, 0.50),
    ("0.50-0.65", 0.50, 0.65),
    ("0.65-0.75", 0.65, 0.75),
    ("0.75-0.85", 0.75, 0.85),
]


async def main(as_json: bool = False):
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    async with db.get_session() as s:
        r = await s.execute(text("""
            SELECT
                CASE
                    WHEN confidence < 0.35 THEN '0.20-0.35'
                    WHEN confidence < 0.50 THEN '0.35-0.50'
                    WHEN confidence < 0.65 THEN '0.50-0.65'
                    WHEN confidence < 0.75 THEN '0.65-0.75'
                    ELSE '0.75-0.85'
                END AS bucket,
                trade_executed,
                COUNT(*) AS n,
                COUNT(CASE WHEN was_correct = true THEN 1 END) AS wins,
                COUNT(CASE WHEN was_correct = false THEN 1 END) AS losses,
                AVG(CASE WHEN was_correct = true THEN realized_edge END) AS avg_win_edge,
                AVG(CASE WHEN was_correct = false THEN realized_edge END) AS avg_loss_edge,
                AVG(realized_edge) AS avg_realized_edge,
                SUM(realized_edge) AS total_edge
            FROM prediction_log
            WHERE bot_name = 'MirrorBot'
              AND was_correct IS NOT NULL
              AND confidence IS NOT NULL
            GROUP BY bucket, trade_executed
            ORDER BY bucket, trade_executed
        """))
        rows = r.fetchall()

    # Totals for context
    async with db.get_session() as s:
        r2 = await s.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN was_correct IS NOT NULL THEN 1 END) AS resolved,
                COUNT(CASE WHEN confidence IS NOT NULL THEN 1 END) AS has_confidence
            FROM prediction_log
            WHERE bot_name = 'MirrorBot'
        """))
        totals = r2.fetchone()

    await db.close()

    # Parse rows into structured data
    results = {}
    for row in rows:
        bucket, traded, n, wins, losses, avg_win, avg_loss, avg_edge, total = row
        traded_label = "traded" if traded else "gate_blocked"
        win_rate = wins / n if n > 0 else 0
        loss_rate = losses / n if n > 0 else 0
        avg_win_f = float(avg_win) if avg_win is not None else 0.0
        avg_loss_f = float(avg_loss) if avg_loss is not None else 0.0
        expectancy = win_rate * avg_win_f - loss_rate * abs(avg_loss_f)

        if bucket not in results:
            results[bucket] = {}
        results[bucket][traded_label] = {
            "n": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "avg_win_edge": round(avg_win_f, 4),
            "avg_loss_edge": round(avg_loss_f, 4),
            "avg_realized_edge": round(float(avg_edge) if avg_edge else 0, 4),
            "total_edge": round(float(total) if total else 0, 4),
            "expectancy": round(expectancy, 4),
        }

    if as_json:
        output = {
            "totals": {
                "total_predictions": totals[0],
                "resolved": totals[1],
                "has_confidence": totals[2],
            },
            "buckets": results,
        }
        print(json.dumps(output, indent=2))
        return

    # Text table output
    print("=" * 80)
    print("GATE SCORE EXPECTANCY ANALYSIS — MirrorBot")
    print("=" * 80)
    print(f"  Total predictions: {totals[0]:,}")
    print(f"  Resolved (was_correct IS NOT NULL): {totals[1]:,} ({100*totals[1]/max(totals[0],1):.1f}%)")
    print(f"  Has confidence: {totals[2]:,}")
    print()

    header = f"  {'Bucket':12s} {'Status':14s} {'N':>5s} {'WinR':>6s} {'AvgWin':>8s} {'AvgLoss':>8s} {'Expect':>8s} {'TotEdge':>9s}"
    sep = "  " + "-" * len(header.strip())

    for label in ["TRADED SIGNALS", "GATE-BLOCKED SIGNALS"]:
        key = "traded" if "TRADED" in label else "gate_blocked"
        print(f"  --- {label} ---")
        print(header)
        print(sep)
        for bname, _, _ in BUCKETS:
            d = results.get(bname, {}).get(key)
            if d is None:
                print(f"  {bname:12s} {key:14s}     -      -        -        -        -         -")
                continue
            print(f"  {bname:12s} {key:14s} {d['n']:5d} {d['win_rate']:6.2%} {d['avg_win_edge']:8.4f} {d['avg_loss_edge']:8.4f} {d['expectancy']:8.4f} {d['total_edge']:9.2f}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gate score expectancy analysis for MirrorBot")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    asyncio.run(main(as_json=args.json))
