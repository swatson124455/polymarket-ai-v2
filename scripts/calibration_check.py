#!/usr/bin/env python3
"""
Calibration Check — Verify prediction quality on rolling 90-day window.

Usage:
    python scripts/calibration_check.py                  # All bots, 90-day rolling
    python scripts/calibration_check.py WeatherBot       # Specific bot
    python scripts/calibration_check.py --cutoff 2026-04-08T16:01:40Z  # Explicit cutoff
    python scripts/calibration_check.py --days 30        # Custom rolling window
    python scripts/calibration_check.py WeatherBot --since 20260414_132211 --clean
        # S204 hygiene #1: deploy-stamp window + CLEAN contamination filter +
        # WB-only per-(trade_side x lead_time_bucket) Brier breakdown for the
        # S203 Track 5 H0' verification.

Reads from prediction_log (not trade_events) for maximum data volume.
Reports: per-bot calibration curve (10 bins), Brier score, per-category breakdown.

S172 1B: Rolling 90-day window, min 50 resolved + 5/bin gate,
         CRPS + PIT + KS test for WeatherBot, exclude EnsembleBot + NULL bot_name.

S204: --since DEPLOY_TIMESTAMP overrides --cutoff/--days for both the main
calibration view and the per-side x lead-time analysis. --clean wires the
canonical contamination CTE from bot_pnl.py into the new analysis only
(not the main view, since the main view spans all bots and contamination
is a WB-cohort property). The per-side x lead-time analysis is gated on
bot_name == "WeatherBot" — that's where the H0' framing lives.
"""
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database
# Single source of truth for `--since` parsing and the contamination CTE body.
# Cross-script import; requires PYTHONPATH=. (project root) when invoking.
from scripts.bot_pnl import parse_deploy_timestamp, _CONTAMINATION_CTE_BODY


def _dedup_latest_per_market(rows):
    """Collapse multiple prediction_log rows for the same market to a single
    row — its latest prediction_time.

    For MODEL calibration the unit of observation is the resolved market, not
    each intra-day re-log. `_log_weather_prediction` re-logs a market whenever
    its probability moves >0.01 or 600s elapse (weather_bot.py:929-947), so a
    long-open market can appear 40+ times and single-handedly dominate a
    reliability / PIT bin. Keeping one row per market removes that weighting bias.

    Rows are (predicted_prob, outcome, bot_name, category, market_id,
    prediction_time[, model_name]). Returns the same tuple shape, one row per
    (market_id, model_name).

    S232 re-review c11 (twin of the write-side dedup bug c2): keying by
    market_id ALONE let a later-logged SECOND model for the same market (the
    experimental nowcast 0.44 signal, which logs after the main analyze phase)
    REPLACE the main model's genuine prediction in the collapse. Key by
    (market_id, model_name) so distinct models are distinct observations.
    Back-compatible: rows without a model_name element key on (market_id, None).
    """
    latest: dict = {}
    for row in rows:
        key = (row[4], row[6] if len(row) > 6 else None)
        prediction_time = row[5]
        current = latest.get(key)
        if (
            current is None
            or current[5] is None
            or (prediction_time is not None and prediction_time > current[5])
        ):
            latest[key] = row
    return list(latest.values())


async def calibration_check(
    bot_name: str = "",
    cutoff: str = "",
    days: int = 90,
    since: datetime | None = None,
    clean: bool = False,
    dedup_markets: bool = False,
):
    """Run calibration analysis on resolved predictions.

    Args:
        bot_name: Filter to specific bot (empty = all active bots).
        cutoff: Explicit ISO timestamp cutoff. Overrides rolling window.
        days: Rolling window in days (default 90). Ignored if cutoff provided.
        since: Deploy-stamp datetime. When set, overrides --cutoff/--days for
            the main fetch and pins the per-side x lead-time analysis to
            event_time/prediction_time >= this stamp.
        clean: When True (and bot_name == "WeatherBot"), wires the canonical
            contamination CTE from bot_pnl.py into the per-side x lead-time
            analysis to mirror bot_pnl.py block 5 CLEAN scope.
        dedup_markets: When True, collapse repeated intra-day logs of the same
            market to its latest prediction before computing the main
            calibration / CRPS / PIT numbers, and count distinct markets for the
            50-minimum gate. Default False preserves the per-log behavior for all
            existing callers. Applies to the main calibration section only; the
            per-(side x lead-time) breakdown still counts per-log (follow-up).
    """
    if since is not None:
        cutoff_dt = since
        print(f"Deploy-stamp window (--since): cutoff = {cutoff_dt.isoformat()}")
    elif not cutoff:
        cutoff_dt = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
        print(f"Rolling {days}-day window: cutoff = {cutoff_dt.isoformat()}")
    else:
        cutoff_dt = datetime.fromisoformat(cutoff).replace(tzinfo=None)
        print(f"Explicit cutoff: {cutoff}")

    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Build query — exclude EnsembleBot (dead) and NULL bot_name (pre-migration)
        bot_clause = "AND pl.bot_name NOT IN ('EnsembleBot') AND pl.bot_name IS NOT NULL"
        params = {"cutoff": cutoff_dt}
        if bot_name:
            bot_clause = "AND pl.bot_name = :bot_name"
            params["bot_name"] = bot_name

        # Check data availability
        count_result = await s.execute(text(f"""
            SELECT count(*) AS total,
                   count(resolution) AS resolved,
                   count(DISTINCT pl.market_id)
                       FILTER (WHERE pl.resolution IS NOT NULL) AS distinct_resolved
            FROM prediction_log pl
            WHERE pl.prediction_time > :cutoff
              -- S232 re-review c11: exclude the experimental nowcast signal
              -- (constant 0.44, own model_name) so the gate count matches the
              -- main-model calibration cut below.
              AND pl.model_name NOT LIKE '%nowcast%'
            {bot_clause}
        """), params)
        counts = count_result.fetchone()
        total, resolved, distinct_resolved = counts[0], counts[1], counts[2]
        print(
            f"prediction_log rows post-cutoff: {total} total, {resolved} resolved "
            f"({distinct_resolved} distinct markets)"
        )

        # S172 1B: Min 50 gate. For model calibration the unit of observation is
        # the resolved MARKET, not each intra-day re-log — one long-open market
        # can be logged 40+ times and dominate a reliability bin. With
        # --dedup-markets the gate counts distinct markets so the 50-minimum is
        # honest; the default (per-log) count is preserved for existing callers.
        effective_n = distinct_resolved if dedup_markets else resolved
        if effective_n < 50:
            unit = "distinct resolved markets" if dedup_markets else "resolved predictions"
            print(f"ERROR: Only {effective_n} {unit} (need 50+).")
            print("Resolution backfill may not be running, or window too narrow.")
            print(f"  SELECT count(*), count(resolution) FROM prediction_log")
            print(f"  WHERE prediction_time > '{cutoff_dt.isoformat()}';")
            return

        # Fetch resolved predictions
        result = await s.execute(text(f"""
            SELECT pl.predicted_prob,
                   CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome,
                   pl.bot_name,
                   m.category,
                   pl.market_id,
                   pl.prediction_time,
                   pl.model_name
            FROM prediction_log pl
            LEFT JOIN markets m ON (pl.market_id = CAST(m.id AS TEXT)
                                    OR pl.market_id = m.condition_id)
            WHERE pl.resolution IS NOT NULL
              AND pl.prediction_time > :cutoff
              -- S232 re-review c11: exclude the experimental nowcast signal
              -- (constant 0.44, own model_name) from the MAIN-model calibration
              -- cut — it is graded separately by model_name. The (market_id,
              -- model_name) dedup key below additionally prevents any second
              -- model from replacing the main prediction.
              AND pl.model_name NOT LIKE '%nowcast%'
              {bot_clause}
            ORDER BY pl.prediction_time
        """), params)
        rows = result.fetchall()

        if not rows:
            print("No resolved predictions found.")
            return

        # Collapse repeated intra-day logs of the same market to its latest
        # prediction so one long-open market can't dominate a reliability / PIT
        # bin (see the --dedup-markets gate note above). Default off.
        if dedup_markets:
            rows = _dedup_latest_per_market(rows)

        # Group by bot
        by_bot = defaultdict(list)
        by_category = defaultdict(list)
        for prob, outcome, bot, category, _market_id, _prediction_time in rows:
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

        # S204: per-(side x lead-time) Brier — WB-only, S203 Track 5 H0' verification.
        if bot_name == "WeatherBot":
            await _print_per_side_lead_time_brier(s, cutoff_dt, clean)

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


def _build_per_side_lead_time_sql(clean: bool) -> str:
    """Build the per-(trade_side x lead_time_bucket) fetch SQL.

    S204: WB-only H0' verification. Joins prediction_log to trade_events ENTRY
    via DISTINCT-ON-latest pattern (mirrors bot_pnl.py block 5) to pull
    lead_time_hours from event_data. Optionally wraps in the canonical
    contamination CTE from bot_pnl.py for the --clean scope. Pulled out
    as a helper so unit tests can verify the SQL shape without a live DB.
    """
    contamination_prefix = ""
    contamination_clause = ""
    if clean:
        # Reuse the canonical CTE body from bot_pnl.py (single source of truth).
        # Note: the CTE filters by `bot_name = ANY(:bot_family)` — we pass the
        # singleton ["WeatherBot"] so the CTE expression doesn't change shape.
        contamination_prefix = f"WITH contaminated AS ({_CONTAMINATION_CTE_BODY})\n"
        contamination_clause = "AND pl.market_id NOT IN (SELECT market_id FROM contaminated)\n"

    return f"""
        {contamination_prefix}
        SELECT pl.predicted_prob,
               CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome,
               e_entry.side AS trade_side,
               (e_entry.event_data->>'lead_time_hours')::float AS lead_time_hours
        FROM prediction_log pl
        JOIN (
            SELECT DISTINCT ON (market_id) market_id, side, event_data
            FROM trade_events
            WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
            ORDER BY market_id, event_time DESC
        ) e_entry ON e_entry.market_id = pl.market_id
        WHERE pl.bot_name = 'WeatherBot'
          AND pl.resolution IS NOT NULL
          AND pl.prediction_time >= :since_dt
          AND e_entry.event_data->>'lead_time_hours' IS NOT NULL
          -- S232 re-review c11: exclude nowcast (its ENTRY events also carry
          -- side=YES + lead_time_hours) from the main-model side x lead view.
          AND pl.model_name NOT LIKE '%nowcast%'
          {contamination_clause}
        ORDER BY e_entry.side, lead_time_hours
    """


def _bucket_for_lead_time(lt: float) -> str:
    """Bucketize lead time in hours. Mirrors bot_pnl.py block 5 boundaries."""
    if lt < 24:
        return "<24h"
    if lt < 48:
        return "24-48h"
    if lt < 72:
        return "48-72h"
    if lt < 120:
        return "72-120h"
    return ">=120h"


async def _print_per_side_lead_time_brier(s, since_dt: datetime, clean: bool):
    """S204 H0' verification: per-(trade_side x lead_time_bucket) Brier on WB CLEAN cohort.

    Verbatim per the S203 close handoff §6 Lead 1 prescription:
      "Single SQL on prediction_log filtered to WeatherBot AND trade_executed=true
       AND prediction_time >= 20260414_132211, grouped by (trade_side,
       lead_time_bucket), computing Brier separately."

    Expected (per H0'): NO 24-48h Brier substantially worse than NO 48-72h Brier
    (the latter is the same-side longer-lead within-bot control identified in
    the S203 Track 5 hypothesis-test). Per Protocol 11, specific P&L magnitudes
    from prior-session bot_pnl.py output are NOT inlined here — operators
    re-run the canonical command in-session to produce fresh comparisons.
    """
    from sqlalchemy import text

    sql = _build_per_side_lead_time_sql(clean)
    params = {"since_dt": since_dt}
    if clean:
        params["bot_family"] = ["WeatherBot"]

    rows = (await s.execute(text(sql), params)).fetchall()
    if not rows:
        print(f"\n{'=' * 60}")
        print(f"PER-(SIDE x LEAD-TIME) BRIER (WB, since={since_dt.isoformat()}, "
              f"{'CLEAN' if clean else 'RAW'}) — no rows")
        print(f"{'=' * 60}")
        return

    # Group: (side, bucket) -> [(p, o), ...]
    by_side_bucket: dict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
    for prob, outcome, side, lt in rows:
        by_side_bucket[(str(side), _bucket_for_lead_time(float(lt)))].append(
            (float(prob), int(outcome))
        )

    scope = f"since={since_dt.isoformat()}, {'CLEAN' if clean else 'RAW'}"
    print(f"\n{'=' * 60}")
    print(f"PER-(SIDE x LEAD-TIME) BRIER ({scope})")
    print(f"{'=' * 60}")
    print(f"  {'Side':<5} {'Bucket':<10} {'N':>5}  {'Brier':>7}  {'Acc':>6}  {'BaseRate':>9}  {'BSS':>8}")
    print(f"  {'-' * 65}")

    bucket_order = ["<24h", "24-48h", "48-72h", "72-120h", ">=120h"]
    for side in sorted({k[0] for k in by_side_bucket}):
        for bucket in bucket_order:
            entries = by_side_bucket.get((side, bucket))
            if not entries:
                continue
            n = len(entries)
            brier = sum((p - o) ** 2 for p, o in entries) / n
            acc = sum(1 for p, o in entries if (p >= 0.5) == (o == 1)) / n
            base_rate = sum(o for _, o in entries) / n
            brier_clim = base_rate * (1 - base_rate)
            bss = 1.0 - (brier / brier_clim) if brier_clim > 0 else 0.0
            print(f"  {side:<5} {bucket:<10} {n:>5}  {brier:>7.4f}  {acc:>5.1%}  "
                  f"{base_rate:>9.3f}  {bss:>+8.4f}")

    # H0' verdict line: compare NO 24-48h Brier to NO 48-72h Brier (within-bot control).
    no_24_48 = by_side_bucket.get(("NO", "24-48h"))
    no_48_72 = by_side_bucket.get(("NO", "48-72h"))
    if no_24_48 and no_48_72:
        b_24_48 = sum((p - o) ** 2 for p, o in no_24_48) / len(no_24_48)
        b_48_72 = sum((p - o) ** 2 for p, o in no_48_72) / len(no_48_72)
        print()
        print(f"  H0' check: NO 24-48h Brier={b_24_48:.4f} (n={len(no_24_48)})  "
              f"vs NO 48-72h Brier={b_48_72:.4f} (n={len(no_48_72)})")
        if b_24_48 > b_48_72:
            print(f"  → 24-48h Brier worse by {(b_24_48 - b_48_72):.4f} "
                  f"({(b_24_48 - b_48_72) / b_48_72:.1%}). Consistent with H0'.")
        else:
            print(f"  → 24-48h Brier NOT worse than 48-72h. H0' falsified by Brier comparison; "
                  f"loss is not calibration-driven on this slice.")


def _parse_args(argv: list[str] | None = None):
    """Parse CLI args. Mirrors bot_pnl.py manual loop for backward-compat with
    positional bot_name. Returns a tuple (bot, cutoff, days, since, clean).
    Pulled out as a function so unit tests can verify flag handling without
    invoking asyncio.
    """
    import argparse
    p = argparse.ArgumentParser(description="Calibration Check (canonical)")
    p.add_argument("bot_name", nargs="?", default="",
                   help="Bot name (default: all bots)")
    p.add_argument("--cutoff", default="",
                   help="Explicit ISO timestamp cutoff. Overrides --days.")
    p.add_argument("--days", type=int, default=90,
                   help="Rolling window in days (default 90). Ignored if --cutoff or --since.")
    p.add_argument("--since", type=parse_deploy_timestamp, default=None,
                   metavar="YYYYMMDD_HHMMSS",
                   help="S204: deploy-stamp window. Overrides --cutoff/--days. "
                        "Format matches deploy timestamps (e.g., 20260414_132211).")
    p.add_argument("--clean", action="store_true", default=False,
                   help="S204: apply contamination CTE from bot_pnl.py to the "
                        "per-(side x lead-time) Brier analysis. WB-only.")
    p.add_argument("--dedup-markets", action="store_true", default=False,
                   help="Collapse repeated intra-day logs of the same market to "
                        "its latest prediction before computing calibration. The "
                        "correct unit for model calibration is the resolved "
                        "market, not each re-log; without this a long-open market "
                        "logged 40+ times dominates a reliability bin. Also makes "
                        "the 50-minimum gate count distinct markets.")
    return p.parse_args(argv)


if __name__ == "__main__":
    ns = _parse_args(sys.argv[1:])
    asyncio.run(calibration_check(
        bot_name=ns.bot_name,
        cutoff=ns.cutoff,
        days=ns.days,
        since=ns.since,
        clean=ns.clean,
        dedup_markets=ns.dedup_markets,
    ))
