#!/usr/bin/env python3
"""
7G: Re-entry cooldown analytical review for MirrorBot.

Simulates 4 cooldown windows (1h, 4h, 12h, 24h) against historical
prediction_log data and measures opportunity cost of blocking re-entries.

Only counts entries where was_correct IS NOT NULL (resolved).
Reports resolution percentage per window — flags "wide uncertainty"
if <50% of entries have resolved outcomes.

Usage:
    python scripts/cooldown_analysis.py
    python scripts/cooldown_analysis.py --json
"""
import argparse
import asyncio
import io
import json
import os
import sys
from collections import defaultdict

os.environ["SIMULATION_MODE"] = "true"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


COOLDOWN_WINDOWS = [
    ("1h", 3600),
    ("4h", 14400),
    ("12h", 43200),
    ("24h", 86400),
]


async def main(as_json: bool = False):
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    # Fetch all MirrorBot prediction_log entries ordered by market, time
    async with db.get_session() as s:
        r = await s.execute(text("""
            SELECT market_id, prediction_time, confidence, was_correct,
                   realized_edge, trade_executed, market_price, predicted_prob
            FROM prediction_log
            WHERE bot_name = 'MirrorBot'
            ORDER BY market_id, prediction_time
        """))
        rows = r.fetchall()

    await db.close()

    if not rows:
        print("No MirrorBot prediction_log entries found.")
        return

    # Group by market_id
    markets = defaultdict(list)
    for row in rows:
        markets[row[0]].append({
            "prediction_time": row[1],
            "confidence": float(row[2]) if row[2] is not None else None,
            "was_correct": row[3],
            "realized_edge": float(row[4]) if row[4] is not None else None,
            "trade_executed": row[5],
            "market_price": float(row[6]) if row[6] is not None else None,
            "predicted_prob": float(row[7]) if row[7] is not None else None,
        })

    total_signals = len(rows)
    total_markets = len(markets)

    # Simulate each cooldown window
    results = {}
    for label, window_secs in COOLDOWN_WINDOWS:
        blocked_total = 0
        blocked_resolved = 0
        blocked_correct = 0
        blocked_edge_sum = 0.0
        allowed_total = 0
        allowed_resolved = 0
        allowed_correct = 0

        for market_id, signals in markets.items():
            last_allowed_time = None
            for sig in signals:
                pt = sig["prediction_time"]
                if pt is None:
                    continue

                if last_allowed_time is None:
                    # First signal for this market — always allowed
                    last_allowed_time = pt
                    allowed_total += 1
                    if sig["was_correct"] is not None:
                        allowed_resolved += 1
                        if sig["was_correct"]:
                            allowed_correct += 1
                    continue

                elapsed = (pt - last_allowed_time).total_seconds()
                if elapsed >= window_secs:
                    # Cooldown expired — allowed
                    last_allowed_time = pt
                    allowed_total += 1
                    if sig["was_correct"] is not None:
                        allowed_resolved += 1
                        if sig["was_correct"]:
                            allowed_correct += 1
                else:
                    # Blocked by cooldown
                    blocked_total += 1
                    if sig["was_correct"] is not None:
                        blocked_resolved += 1
                        if sig["was_correct"]:
                            blocked_correct += 1
                        if sig["realized_edge"] is not None:
                            blocked_edge_sum += sig["realized_edge"]

        resolution_pct = blocked_resolved / max(blocked_total, 1)
        allowed_wr = allowed_correct / max(allowed_resolved, 1)
        blocked_wr = blocked_correct / max(blocked_resolved, 1)

        results[label] = {
            "window_secs": window_secs,
            "blocked_total": blocked_total,
            "blocked_resolved": blocked_resolved,
            "blocked_correct": blocked_correct,
            "blocked_wr": round(blocked_wr, 4),
            "opportunity_cost": round(blocked_edge_sum, 4),
            "allowed_total": allowed_total,
            "allowed_resolved": allowed_resolved,
            "allowed_correct": allowed_correct,
            "allowed_wr": round(allowed_wr, 4),
            "resolution_pct": round(resolution_pct, 4),
            "wide_uncertainty": resolution_pct < 0.50,
        }

    if as_json:
        output = {
            "total_signals": total_signals,
            "total_markets": total_markets,
            "windows": results,
        }
        print(json.dumps(output, indent=2, default=str))
        return

    # Text output
    print("=" * 90)
    print("RE-ENTRY COOLDOWN OPPORTUNITY COST ANALYSIS — MirrorBot")
    print("=" * 90)
    print(f"  Total signals: {total_signals:,}")
    print(f"  Unique markets: {total_markets:,}")
    print()

    header = f"  {'Window':>8s} {'Blocked':>8s} {'Blk/Res':>8s} {'BlkWR':>7s} {'OppCost':>10s} {'Allowed':>8s} {'AllWR':>7s} {'ResPct':>7s} {'Flag':>12s}"
    print(header)
    print("  " + "-" * (len(header.strip())))

    best_label = None
    best_expectancy = float("-inf")

    for label, data in results.items():
        flag = "UNCERTAIN" if data["wide_uncertainty"] else ""
        if label == "24h":
            flag += " <<current"
        print(
            f"  {label:>8s} "
            f"{data['blocked_total']:8d} "
            f"{data['blocked_resolved']:8d} "
            f"{data['blocked_wr']:7.2%} "
            f"${data['opportunity_cost']:9.2f} "
            f"{data['allowed_total']:8d} "
            f"{data['allowed_wr']:7.2%} "
            f"{data['resolution_pct']:7.2%} "
            f"{flag}"
        )

        # Best = highest allowed_wr * allowed_resolved + blocked opportunity cost
        # Simple heuristic: best window maximizes (allowed WR - 0.5) * allowed_resolved
        # while minimizing positive opportunity cost (blocked profitable trades)
        if data["allowed_resolved"] > 0 and not data["wide_uncertainty"]:
            score = data["allowed_wr"] - data["opportunity_cost"] / max(data["allowed_resolved"], 1)
            if score > best_expectancy:
                best_expectancy = score
                best_label = label

    print()
    if best_label:
        print(f"  Recommendation: {best_label} cooldown (best expectancy-adjusted)")
        if results[best_label]["wide_uncertainty"]:
            print("  WARNING: Resolution data insufficient for confident recommendation.")
    else:
        print("  Insufficient resolved data for recommendation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-entry cooldown analysis for MirrorBot")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    asyncio.run(main(as_json=args.json))
