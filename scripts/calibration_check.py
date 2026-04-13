#!/usr/bin/env python3
"""
Calibration Check — Verify prediction quality on rolling 90-day window.

Usage:
    python scripts/calibration_check.py                  # All bots, 90-day rolling
    python scripts/calibration_check.py WeatherBot       # Specific bot
    python scripts/calibration_check.py --cutoff 2026-04-08T16:01:40Z  # Explicit cutoff
    python scripts/calibration_check.py --days 30        # Custom rolling window

Reads from prediction_log (not trade_events) for maximum data volume.
Reports: per-bot calibration curve (10 bins), Brier score, per-category breakdown.

S172 1B: Rolling 90-day window, min 50 resolved + 5/bin gate,
         CRPS + PIT + KS test for WeatherBot, exclude EnsembleBot + NULL bot_name.
"""
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database


async def calibration_check(bot_name: str = "", cutoff: str = "", days: int = 90):
    """Run calibration analysis on resolved predictions.

    Args:
        bot_name: Filter to specific bot (empty = all active bots).
        cutoff: Explicit ISO timestamp cutoff. Overrides rolling window.
        days: Rolling window in days (default 90). Ignored if cutoff provided.
    """
    if not cutoff:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        print(f"Rolling {days}-day window: cutoff = {cutoff}")
    else:
        print(f"Explicit cutoff: {cutoff}")

    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Build query — exclude EnsembleBot (dead) and NULL bot_name (pre-migration)
        bot_clause = "AND pl.bot_name NOT IN ('EnsembleBot') AND pl.bot_name IS NOT NULL"
        params = {"cutoff": cutoff}
        if bot_name:
            bot_clause = "AND pl.bot_name = :bot_name"
            params["bot_name"] = bot_name

        # Check data availability
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

        # S172 1B: Min 50 resolved gate
        if resolved < 50:
            print(f"ERROR: Only {resolved} resolved predictions (need 50+).")
            print("Resolution backfill may not be running, or window too narrow.")
            print(f"  SELECT count(*), count(resolution) FROM prediction_log")
            print(f"  WHERE prediction_time > '{cutoff}';")
            return

        # Fetch resolved predictions
        result = await s.execute(text(f"""
            SELECT pl.predicted_prob,
                   CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome,
                   pl.bot_name,
                   m.category
            FROM prediction_log pl
            LEFT JOIN markets m ON (pl.market_id = CAST(m.id AS TEXT)
                                    OR pl.market_id = m.condition_id)
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

            # S172 1B: CRPS + PIT for WeatherBot
            if bot == "WeatherBot":
                _print_crps_pit(entries)

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
    """Print 10-bin calibration curve + Brier score.

    S172 1B: Min 5 per bin gate — bins with <5 samples are flagged as insufficient.
    """
    n_bins = 10
    bins = [(i / n_bins, (i + 1) / n_bins) for i in range(n_bins)]

    # Overall Brier
    brier = sum((p - o) ** 2 for p, o in entries) / len(entries)
    accuracy = sum(1 for p, o in entries if (p >= 0.5) == (o == 1)) / len(entries)

    # Brier Skill Score vs climatological baseline
    base_rate = sum(o for _, o in entries) / len(entries)
    brier_clim = base_rate * (1 - base_rate)
    bss = 1.0 - (brier / brier_clim) if brier_clim > 0 else 0.0

    print(f"  Overall Brier: {brier:.4f}  BSS: {bss:+.4f}  Accuracy: {accuracy:.2%}  N: {len(entries)}")
    print(f"  Base rate: {base_rate:.3f}  Climatological Brier: {brier_clim:.4f}")

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
        # S172 1B: Flag insufficient bins
        flag = " ⚠️ <5" if len(bin_entries) < 5 else ""
        print(f"  {label:>10}  {avg_pred:>10.3f}  {avg_actual:>10.3f}  {len(bin_entries):>6}  {gap:>+8.3f}{flag}")

    # Flag bins with large gaps (only if >=5 samples)
    print()
    for lo, hi in bins:
        bin_entries = [(p, o) for p, o in entries if lo <= p < hi]
        if len(bin_entries) >= 5:
            avg_pred = sum(p for p, _ in bin_entries) / len(bin_entries)
            avg_actual = sum(o for _, o in bin_entries) / len(bin_entries)
            gap = abs(avg_actual - avg_pred)
            if gap > 0.10:
                print(f"  WARNING: [{lo:.1f}-{hi:.1f}) miscalibrated by {gap:.1%} "
                      f"(predicted {avg_pred:.2f}, actual {avg_actual:.2f}, n={len(bin_entries)})")


def _print_crps_pit(entries):
    """S172 1B: CRPS + PIT histogram + KS test for WeatherBot.

    For binary predictions, CRPS simplifies to Brier score. But we compute it
    explicitly and add PIT diagnostics for CDF-derived probabilities.

    PIT (Probability Integral Transform): if the predictive CDF is well-calibrated,
    PIT values should be uniformly distributed. We test with KS.
    """
    try:
        from scipy import stats
    except ImportError:
        print("\n  CRPS/PIT: scipy not available. Install: pip install scipy")
        return

    print(f"\n  --- WeatherBot CRPS/PIT Diagnostics ---")

    # For binary outcomes, CRPS = E[(F(x) - 1{x <= y})^2]
    # With point forecast p and outcome y ∈ {0,1}:
    #   CRPS = (p - y)^2 when the CDF is a Bernoulli(p) step function
    # This equals Brier score for binary. Compute explicitly for verification.
    crps_values = [(p - o) ** 2 for p, o in entries]
    crps_mean = sum(crps_values) / len(crps_values)
    print(f"  CRPS (binary): {crps_mean:.4f}")

    # PIT values: for binary outcome y with predicted P(Y=1) = p:
    #   PIT = p if y=0 (CDF at y=0 is p for P(Y≤0) = 1-p... but for binary:
    #   PIT = 1-p if y=0, PIT = uniform draw in [1-p, 1] if y=1
    # Simplified: PIT = 1 - p + o * p (Dawid 1984)
    # For well-calibrated forecasts, PIT ~ Uniform(0,1)
    import random
    random.seed(42)
    pit_values = []
    for p, o in entries:
        if o == 1:
            # PIT uniform in [1-p, 1]
            pit_values.append(random.uniform(1 - p, 1.0))
        else:
            # PIT uniform in [0, 1-p]
            pit_values.append(random.uniform(0.0, 1 - p))

    # PIT histogram (10 bins — should be ~flat if calibrated)
    n_pit_bins = 10
    print(f"\n  PIT Histogram ({n_pit_bins} bins, should be ~flat):")
    expected_per_bin = len(pit_values) / n_pit_bins
    for i in range(n_pit_bins):
        lo = i / n_pit_bins
        hi = (i + 1) / n_pit_bins
        count = sum(1 for v in pit_values if lo <= v < hi)
        ratio = count / expected_per_bin if expected_per_bin > 0 else 0
        bar = "█" * int(ratio * 20)
        flag = " ◄" if abs(ratio - 1.0) > 0.3 else ""
        print(f"    [{lo:.1f}-{hi:.1f})  {count:>5}  {ratio:.2f}x  {bar}{flag}")

    # Kolmogorov-Smirnov test: H0 = PIT ~ Uniform(0,1)
    ks_stat, ks_pvalue = stats.kstest(pit_values, 'uniform')
    print(f"\n  KS test: stat={ks_stat:.4f}, p-value={ks_pvalue:.4f}")
    if ks_pvalue < 0.05:
        print(f"  *** REJECT calibration (p={ks_pvalue:.4f} < 0.05) — PIT not uniform ***")
        # Diagnose direction
        pit_mean = sum(pit_values) / len(pit_values)
        if pit_mean > 0.55:
            print(f"  Diagnosis: PIT mean={pit_mean:.3f} > 0.5 → overconfident (predictions too extreme)")
        elif pit_mean < 0.45:
            print(f"  Diagnosis: PIT mean={pit_mean:.3f} < 0.5 → underconfident (predictions too conservative)")
        else:
            print(f"  Diagnosis: PIT mean={pit_mean:.3f} ≈ 0.5 → location OK, check spread/shape")
    else:
        print(f"  PASS: Cannot reject uniform PIT (p={ks_pvalue:.4f} ≥ 0.05)")


if __name__ == "__main__":
    bot = ""
    cutoff = ""
    days = 90
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--cutoff" and i + 1 < len(args):
            cutoff = args[i + 1]
            i += 2
        elif args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1])
            i += 2
        elif not args[i].startswith("-"):
            bot = args[i]
            i += 1
        else:
            i += 1

    asyncio.run(calibration_check(bot, cutoff, days))
