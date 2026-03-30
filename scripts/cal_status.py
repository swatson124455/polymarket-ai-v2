#!/usr/bin/env python3
"""WeatherBot calibrator fit history — diagnostic viewer.

Usage:
    python scripts/cal_status.py

Reads the last 20 calibrator fit records from system_kv and prints a table.
S143: Added for calibrator monitoring (true OOS holdout, YES-side widening).
"""
import asyncio
import json
import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from base_engine.data.database import PolymarketDatabase
    from config.settings import settings

    db = PolymarketDatabase(settings)
    await db.initialize()

    try:
        from sqlalchemy import text

        async with db.get_session() as session:
            result = await session.execute(text(
                "SELECT value FROM system_kv WHERE key = 'weatherbot_cal_fit_history'"
            ))
            raw = result.scalar_one_or_none()

        if not raw:
            print("No calibrator fit history found in system_kv.")
            print("History is populated after the first calibrator reload (every 6h).")
            return

        history = json.loads(raw)
        if not history:
            print("Calibrator fit history is empty.")
            return

        # Header
        print(f"\n{'WeatherBot Calibrator Fit History':^90}")
        print("=" * 90)
        print(f"{'Timestamp':<22} {'n_NO':>5} {'n_YES':>6} {'Train':>7} {'OOS':>7} "
              f"{'Hold':>5} {'YES+':>5} {'Fitted':>6}")
        print("-" * 90)

        for rec in history:
            ts = rec.get("ts", "?")[:19]
            n_no = rec.get("n_no", "?")
            n_yes = rec.get("n_yes", "?")
            train_b = rec.get("train_brier")
            oos_b = rec.get("oos_brier")
            holdout = rec.get("holdout_valid", False)
            yes_w = rec.get("yes_widened", False)
            fitted = rec.get("fitted", False)

            # Format Brier scores
            train_str = f"{train_b:.4f}" if train_b is not None else "  n/a "
            oos_str = f"{oos_b:.4f}" if oos_b is not None else "  n/a "

            # Status indicators
            hold_str = "  Y  " if holdout else " FALL"
            yes_str = " 90d " if yes_w else "  -  "
            fit_str = "  OK  " if fitted else " FAIL"

            # Color hints (terminal escape codes)
            warn = ""
            reset = ""
            if oos_b is not None and oos_b > 0.25:
                warn = "\033[33m"  # yellow — bad OOS
                reset = "\033[0m"
            elif isinstance(n_yes, int) and n_yes < 30 and not yes_w:
                warn = "\033[31m"  # red — YES identity fallback
                reset = "\033[0m"

            print(f"{warn}{ts:<22} {n_no:>5} {n_yes:>6} {train_str:>7} {oos_str:>7} "
                  f"{hold_str:>5} {yes_str:>5} {fit_str:>6}{reset}")

        print("-" * 90)
        print(f"  Total records: {len(history)}")
        print(f"  Legend: Hold=holdout valid, YES+=YES widened to 90d, FALL=fallback to full window")
        print(f"  Colors: \033[33myellow\033[0m=OOS Brier>0.25, \033[31mred\033[0m=YES identity fallback\n")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
