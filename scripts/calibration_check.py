#!/usr/bin/env python3
"""
Calibration Check — Verify prediction quality on clean post-cutoff data.

Usage:
    python scripts/calibration_check.py                  # All bots, default cutoff
    python scripts/calibration_check.py EsportsBot       # Specific bot
    python scripts/calibration_check.py --cutoff 2026-04-08T16:01:40Z

Reads from prediction_log (not trade_events) for maximum data volume.
Reports: per-bot calibration curve (10 bins), Brier score, per-category breakdown.

S169: Data quality verification pipeline.
"""
import asyncio
import sys
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database


async def calibration_check(bot_name: str = "", cutoff: str = "2026-04-08T16:01:40Z"):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Build query
        bot_clause = ""
        params = {"cutoff": cutoff}
        if bot_name:
            bot_clause = "AND pl.bot_name = :bot_name"
            params["bot_name"] = bot_name

        # Check data availability first
        count_result = await s.execute(text(f"""
            SELECT count(*) AS total,
                   count(resolution) AS resolved
            FROM prediction_log pl
            WHERE pl.prediction_time > :cutoff
            {bot_clause}
        """), params)
        counts = count_result.fetchone()
        total, resolved = counts[0], counts[1]
        print(f"prediction_log rows post-cutoff: {total} total, {resolved} resolved")
        if resolved < 10:
            print("ERROR: Not enough resolved predictions for calibration analysis.")
            print("Resolution backfill may not be running. Check:")
            print("  SELECT count(*), count(resolution) FROM prediction_log WHERE prediction_time > '{cutoff}';")
            return

        # Fetch resolved predictions
        result = await s.execute(text(f"""
            SELECT pl.predicted_prob,
                   CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome,
                   pl.bot_name,
                   m.category
            FROM prediction_log pl
            LEFT JOIN markets m ON (pl.market_id = CAST(m.id AS TEXT) OR pl.market_id = m.condition_id)
            WHERE pl.resolution IS NOT NULL
              AND pl.prediction_time > :cutoff
              {bot_clause}
            ORDER BY pl.prediction_time
        """), params)
        rows = result.fetchall()

        if not rows:
            print("No resolved predictions found.")
            return

        # Group by bot
        by_bot = defaultdict(list)
        by_category = defaultdict(list)
        for prob, outcome, bot, category in rows:
            by_bot[bot or "unknown"].append((float(prob), int(outcome)))
            cat = category or "unknown"
            by_category[cat].append((float(prob), int(outcome)))

        # Print per-bot calibration
        for bot, entries in sorted(by_bot.items()):
            print(f"\n{'=' * 60}")
            print(f"BOT: {bot} ({len(entries)} resolved predictions)")
            print(f"{'=' * 60}")
            _print_calibration(entries)

        # Print per-category breakdown (top 10 by count)
        print(f"\n{'=' * 60}")
        print("PER-CATEGORY BREAKDOWN (top 10)")
        print(f"{'=' * 60}")
        sorted_cats = sorted(by_category.items(), key=lambda x: -len(x[1]))
        for cat, entries in sorted_cats[:10]:
            brier = sum((p - o) ** 2 for p, o in entries) / len(entries)
            wr = sum(o for _, o in entries) / len(entries)
            print(f"  {cat:<30} n={len(entries):>5}  Brier={brier:.4f}  base_rate={wr:.2f}")

    await db.close()


def _print_calibration(entries):
    """Print 10-bin calibration curve + Brier score."""
    n_bins = 10
    bins = [(i / n_bins, (i + 1) / n_bins) for i in range(n_bins)]

    # Overall Brier
    brier = sum((p - o) ** 2 for p, o in entries) / len(entries)
    accuracy = sum(1 for p, o in entries if (p >= 0.5) == (o == 1)) / len(entries)
    print(f"  Overall Brier: {brier:.4f}  Accuracy: {accuracy:.2%}  N: {len(entries)}")

    print(f"\n  {'Bin':>10}  {'Predicted':>10}  {'Actual':>10}  {'Count':>6}  {'Gap':>8}")
    print(f"  {'-' * 50}")

    for lo, hi in bins:
        bin_entries = [(p, o) for p, o in entries if lo <= p < hi]
        if not bin_entries:
            continue
        avg_pred = sum(p for p, _ in bin_entries) / len(bin_entries)
        avg_actual = sum(o for _, o in bin_entries) / len(bin_entries)
        gap = avg_actual - avg_pred
        label = f"[{lo:.1f}-{hi:.1f})"
        print(f"  {label:>10}  {avg_pred:>10.3f}  {avg_actual:>10.3f}  {len(bin_entries):>6}  {gap:>+8.3f}")

    # Flag bins with large gaps
    print()
    for lo, hi in bins:
        bin_entries = [(p, o) for p, o in entries if lo <= p < hi]
        if len(bin_entries) >= 10:
            avg_pred = sum(p for p, _ in bin_entries) / len(bin_entries)
            avg_actual = sum(o for _, o in bin_entries) / len(bin_entries)
            gap = abs(avg_actual - avg_pred)
            if gap > 0.10:
                print(f"  WARNING: [{lo:.1f}-{hi:.1f}) miscalibrated by {gap:.1%} "
                      f"(predicted {avg_pred:.2f}, actual {avg_actual:.2f}, n={len(bin_entries)})")


if __name__ == "__main__":
    bot = ""
    cutoff = "2026-04-08T16:01:40Z"
    for arg in sys.argv[1:]:
        if arg.startswith("--cutoff"):
            continue
        if arg.startswith("2"):  # timestamp
            cutoff = arg
        elif not arg.startswith("-"):
            bot = arg
    # Handle --cutoff VALUE
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--cutoff" and i < len(sys.argv) - 1:
            cutoff = sys.argv[i + 1]

    asyncio.run(calibration_check(bot, cutoff))
