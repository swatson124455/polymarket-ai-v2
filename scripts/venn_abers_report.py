#!/usr/bin/env python3
"""
7K: Venn-ABERS calibration report for MirrorBot gate_score.

Fits VennAbersIntervalCalibrator on resolved prediction_log rows and reports:
  - Sample size and base rate
  - Per-bucket midpoint + interval width at representative gate_scores
  - Whether data is sufficient for actionable calibration

Usage:
    python scripts/venn_abers_report.py
    python scripts/venn_abers_report.py --json
    python scripts/venn_abers_report.py --bot MirrorBot
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


REPORT_SCORES = [0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85]


async def main(bot_name: str, as_json: bool):
    from base_engine.data.database import Database
    from base_engine.learning.venn_abers_intervals import VennAbersIntervalCalibrator

    db = Database()
    await db.init()

    cal = VennAbersIntervalCalibrator(min_samples=30)
    ok = await cal.fit_from_prediction_log(db, bot_name=bot_name)

    # Also count totals for context
    from sqlalchemy import text
    async with db.get_session() as s:
        r = await s.execute(
            text("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN was_correct IS NOT NULL THEN 1 END) AS resolved,
                    AVG(CASE WHEN was_correct THEN 1.0 ELSE 0.0 END) AS base_rate
                FROM prediction_log
                WHERE bot_name = :bot AND confidence IS NOT NULL
            """),
            {"bot": bot_name},
        )
        totals = r.fetchone()

    await db.close()

    intervals = {}
    for score in REPORT_SCORES:
        p0, p1 = cal.predict_interval(score)
        intervals[str(score)] = {
            "score": score,
            "p_low": round(p0, 4),
            "p_high": round(p1, 4),
            "midpoint": round((p0 + p1) / 2, 4),
            "width": round(p1 - p0, 4),
        }

    if as_json:
        output = {
            "bot": bot_name,
            "fitted": cal.is_fitted,
            "n_samples": cal.n_samples,
            "totals": {
                "all_predictions": int(totals[0]) if totals[0] else 0,
                "resolved": int(totals[1]) if totals[1] else 0,
                "base_rate": float(totals[2]) if totals[2] is not None else None,
            },
            "intervals": intervals,
        }
        print(json.dumps(output, indent=2))
        return

    print("=" * 75)
    print(f"VENN-ABERS CALIBRATION REPORT — {bot_name}")
    print("=" * 75)
    print(f"  All predictions: {totals[0]:,}")
    print(f"  Resolved:        {totals[1]:,} ({100*(totals[1] or 0)/max(totals[0] or 1, 1):.2f}%)")
    if totals[2] is not None:
        print(f"  Base rate:       {float(totals[2]):.4f} (P(was_correct=1))")
    print(f"  Fitted:          {'YES' if cal.is_fitted else 'NO (insufficient data)'}")
    print(f"  N samples used:  {cal.n_samples}")
    print()

    if not cal.is_fitted:
        print("  Intervals below use FALLBACK (±0.25 around score — not calibrated).")
        print("  Accumulate resolutions (>= 30) to produce real calibration.")
    else:
        print("  Intervals below are real Venn-ABERS brackets.")
        print("  Width shrinks as more resolutions accumulate.")
    print()

    hdr = f"  {'Score':>6s}   {'p_low':>6s}  {'p_mid':>6s}  {'p_high':>6s}  {'Width':>6s}"
    print(hdr)
    print("  " + "-" * (len(hdr.strip())))
    for score in REPORT_SCORES:
        data = intervals[str(score)]
        print(
            f"  {data['score']:>6.2f}   "
            f"{data['p_low']:>6.4f}  "
            f"{data['midpoint']:>6.4f}  "
            f"{data['p_high']:>6.4f}  "
            f"{data['width']:>6.4f}"
        )

    if cal.is_fitted:
        print()
        avg_width = sum(intervals[str(s)]["width"] for s in REPORT_SCORES) / len(REPORT_SCORES)
        if avg_width < 0.15:
            print(f"  Average width {avg_width:.4f} — intervals are tight, actionable.")
        elif avg_width < 0.35:
            print(f"  Average width {avg_width:.4f} — moderate; keep accumulating.")
        else:
            print(f"  Average width {avg_width:.4f} — wide; calibration still thin.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Venn-ABERS calibration report")
    parser.add_argument("--bot", default="MirrorBot", help="Bot name (default: MirrorBot)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    asyncio.run(main(args.bot, args.json))
