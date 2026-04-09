#!/usr/bin/env python3
"""
MirrorBot Factor Evaluation Script — S168 Phase 1.

Diagnostic foundation for all subsequent elevation phases.
Reads trade_events ENTRY+RESOLUTION for MirrorBot, computes:
  1. Factor WR buckets (quintiles, Wilson CI, split by side)
  2. NO edge formula validation (old vs new kelly_prob)
  3. Calibration Brier analysis (70/30 OOS split)
  4. Category information efficiency

Usage:
    python scripts/mirror_factor_eval.py --since 2026-03-30 --bot MirrorBot
"""
import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# ── Bootstrap path so script can be run from repo root ──
sys.path.insert(0, ".")

from config.settings import settings  # noqa: E402


# ── Wilson CI ──
def wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score confidence interval for binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p_hat = wins / n
    denom = 1 + z * z / n
    centre = p_hat + z * z / (2 * n)
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * n)) / n)
    lo = (centre - spread) / denom
    hi = (centre + spread) / denom
    return (max(0.0, lo), min(1.0, hi))


def quintile_bucket(val: float, lo: float = 0.0, hi: float = 1.0) -> int:
    """Map value to quintile 0-4."""
    if val <= lo:
        return 0
    if val >= hi:
        return 4
    frac = (val - lo) / (hi - lo)
    return min(4, int(frac * 5))


def bucket_label(q: int) -> str:
    """Human-readable quintile label."""
    labels = ["Q1 (lowest)", "Q2", "Q3", "Q4", "Q5 (highest)"]
    return labels[q] if 0 <= q <= 4 else f"Q{q}"


# ── Data Loading ──
async def load_entries_and_resolutions(
    bot_name: str, since: str
) -> Tuple[List[Dict], List[Dict]]:
    """Load ENTRY and RESOLUTION events from trade_events."""
    from datetime import datetime
    from base_engine.data.database import Database

    db = Database()
    await db.init()

    since_dt = datetime.fromisoformat(since) if "T" in since else datetime.strptime(since, "%Y-%m-%d")

    from sqlalchemy import text

    async with db.get_session() as session:
        # ENTRY events with event_data
        r = await session.execute(
            text(
                "SELECT market_id, side, price, confidence, event_data, event_time "
                "FROM trade_events "
                "WHERE bot_name = :bot AND event_type = 'ENTRY' "
                "  AND event_time >= :since "
                "ORDER BY event_time"
            ),
            {"bot": bot_name, "since": since_dt},
        )
        entries = []
        for row in r.fetchall():
            ed = row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}")
            entries.append({
                "market_id": row[0],
                "side": row[1],
                "price": float(row[2]) if row[2] else 0.0,
                "confidence": float(row[3]) if row[3] else 0.0,
                "event_data": ed,
                "event_time": row[5],
            })

        # RESOLUTION events
        r2 = await session.execute(
            text(
                "SELECT market_id, side, realized_pnl, event_time "
                "FROM trade_events "
                "WHERE bot_name = :bot AND event_type = 'RESOLUTION' "
                "  AND event_time >= :since "
                "  AND realized_pnl IS NOT NULL "
                "ORDER BY event_time"
            ),
            {"bot": bot_name, "since": since_dt},
        )
        resolutions = []
        for row in r2.fetchall():
            resolutions.append({
                "market_id": row[0],
                "side": row[1],
                "realized_pnl": float(row[2]),
                "event_time": row[3],
            })

    await db.close()
    return entries, resolutions


def join_entry_resolution(
    entries: List[Dict], resolutions: List[Dict]
) -> List[Dict]:
    """Join entries to resolutions on (market_id, side). Returns merged list."""
    res_map: Dict[Tuple[str, str], Dict] = {}
    for r in resolutions:
        key = (r["market_id"], r["side"])
        res_map[key] = r  # last resolution wins (dedup)

    joined = []
    for e in entries:
        key = (e["market_id"], e["side"])
        if key in res_map:
            r = res_map[key]
            merged = {**e, "realized_pnl": r["realized_pnl"], "resolved": True}
            joined.append(merged)
    return joined


# ── Section 1: Factor WR Buckets ──
def compute_factor_buckets(joined: List[Dict]) -> None:
    """Print factor WR analysis bucketed by quintile, split by side."""
    factors = {
        "gate_score": (0.0, 1.0),
        "rel_mult": (0.0, 1.0),
        "conf_base": (0.3, 0.7),
        "copy_tier": None,  # categorical: 1, 2, 3
        "geo_mean": (0.0, 1.0),
        "spread": (0.0, 0.15),
        "kelly_prob": (0.3, 0.7),
        "eff_prior": (0.4, 0.7),
        "gate_decay_w": (0.0, 1.0),
    }

    for side_filter in ["YES", "NO", "ALL"]:
        print(f"\n{'='*70}")
        print(f"  FACTOR WR BUCKETS — side={side_filter}")
        print(f"{'='*70}")

        subset = [
            r for r in joined
            if side_filter == "ALL" or r["side"] == side_filter
        ]
        if not subset:
            print("  (no data)")
            continue

        for factor_name, bounds in factors.items():
            if factor_name == "copy_tier":
                _print_categorical_factor(subset, factor_name)
                continue

            lo, hi = bounds
            buckets: Dict[int, List[Dict]] = defaultdict(list)
            skipped = 0
            for row in subset:
                val = row["event_data"].get(factor_name)
                if val is None:
                    skipped += 1
                    continue
                q = quintile_bucket(float(val), lo, hi)
                buckets[q].append(row)

            print(f"\n  {factor_name} (range {lo}-{hi}, {skipped} skipped):")
            print(f"  {'Bucket':<16} {'N':>5} {'Wins':>5} {'WR%':>6} {'AvgPnL':>8} {'TotalPnL':>10} {'Wilson CI':>14}")
            print(f"  {'-'*16} {'-'*5} {'-'*5} {'-'*6} {'-'*8} {'-'*10} {'-'*14}")
            for q in range(5):
                rows = buckets.get(q, [])
                n = len(rows)
                if n == 0:
                    print(f"  {bucket_label(q):<16} {0:>5} {0:>5} {'—':>6} {'—':>8} {'—':>10} {'—':>14}")
                    continue
                wins = sum(1 for r in rows if r["realized_pnl"] > 0)
                wr = wins / n * 100
                avg_pnl = sum(r["realized_pnl"] for r in rows) / n
                total_pnl = sum(r["realized_pnl"] for r in rows)
                ci_lo, ci_hi = wilson_ci(wins, n)
                ci_width = ci_hi - ci_lo
                print(
                    f"  {bucket_label(q):<16} {n:>5} {wins:>5} {wr:>5.1f}% "
                    f"${avg_pnl:>+7.2f} ${total_pnl:>+9.2f} "
                    f"[{ci_lo:.2f}-{ci_hi:.2f}]"
                )


def _print_categorical_factor(subset: List[Dict], factor_name: str) -> None:
    """Print WR for categorical factor (e.g., copy_tier 1/2/3)."""
    buckets: Dict[Any, List[Dict]] = defaultdict(list)
    skipped = 0
    for row in subset:
        val = row["event_data"].get(factor_name)
        if val is None:
            skipped += 1
            continue
        buckets[val].append(row)

    print(f"\n  {factor_name} (categorical, {skipped} skipped):")
    print(f"  {'Value':<16} {'N':>5} {'Wins':>5} {'WR%':>6} {'AvgPnL':>8} {'TotalPnL':>10} {'Wilson CI':>14}")
    print(f"  {'-'*16} {'-'*5} {'-'*5} {'-'*6} {'-'*8} {'-'*10} {'-'*14}")
    for val in sorted(buckets.keys()):
        rows = buckets[val]
        n = len(rows)
        wins = sum(1 for r in rows if r["realized_pnl"] > 0)
        wr = wins / n * 100
        avg_pnl = sum(r["realized_pnl"] for r in rows) / n
        total_pnl = sum(r["realized_pnl"] for r in rows)
        ci_lo, ci_hi = wilson_ci(wins, n)
        print(
            f"  Tier {val:<12} {n:>5} {wins:>5} {wr:>5.1f}% "
            f"${avg_pnl:>+7.2f} ${total_pnl:>+9.2f} "
            f"[{ci_lo:.2f}-{ci_hi:.2f}]"
        )


# ── Section 2: NO Edge Formula Validation ──
def compute_no_edge_validation(joined: List[Dict]) -> None:
    """Compare old vs new kelly_prob formula for NO-side entries."""
    no_entries = [r for r in joined if r["side"] == "NO"]
    if not no_entries:
        print("\n  NO EDGE VALIDATION: No NO-side resolved entries found.")
        return

    print(f"\n{'='*70}")
    print("  NO EDGE FORMULA VALIDATION (old vs new kelly_prob)")
    print(f"{'='*70}")

    no_max_edge = 0.10  # proposed MIRROR_NO_MAX_KELLY_EDGE
    min_edge_gate = 0.05  # MIRROR_NO_MIN_EDGE

    old_pass = 0
    new_pass_only = 0
    new_pass_win = 0
    new_pass_lose = 0
    new_pass_pnl = 0.0

    print(f"\n  {'Market':<18} {'Price':>6} {'Base':>6} {'OldKP':>6} {'NewKP':>6} "
          f"{'OldEdge':>8} {'NewEdge':>8} {'Gate':>5} {'PnL':>9}")
    print(f"  {'-'*18} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*5} {'-'*9}")

    for row in no_entries:
        ed = row["event_data"]
        price = row["price"]
        conf_base = float(ed.get("conf_base", 0.50))
        eff_prior = float(ed.get("eff_prior", 0.53))
        decay_w = float(ed.get("gate_decay_w", 1.0))
        rel_mult = float(ed.get("rel_mult", 0.35))
        pnl = row["realized_pnl"]

        # Estimate _eq_n from rel_mult: if rel_mult >= 0.35 (cold-start floor), likely n<5
        # If rel_mult is higher and tracks sample_ramp, n is proportional
        # We use a heuristic: if rel_mult == cold-start floor, assume cold-start
        is_cold_start = rel_mult <= 0.36  # near the 0.35 floor

        # Old formula: kelly_prob = price + max(0, _base - 0.50) * ramp
        if is_cold_start:
            old_trader_edge = max(0.01, max(0.0, eff_prior - 0.50) * decay_w * 0.60)
        else:
            # Estimate ramp from rel_mult (rough: ramp ≈ rel_mult since LR capped at 1.0)
            ramp = min(1.0, rel_mult)
            old_trader_edge = max(0.0, conf_base - 0.50) * ramp
        old_max_edge = 0.05  # MIRROR_MAX_KELLY_EDGE
        old_kelly_prob = min(price + old_max_edge, price + old_trader_edge)
        old_kelly_prob = max(price + 0.005, min(0.95, old_kelly_prob))
        old_edge = old_kelly_prob - price

        # New formula (Phase 3):
        if is_cold_start:
            cold_no_edge = max(0.0, eff_prior - price) * decay_w * 0.60
            new_kelly_prob = max(price + 0.005, min(price + no_max_edge,
                                                     price + cold_no_edge))
        else:
            new_kelly_prob = max(price + 0.005, min(price + no_max_edge, conf_base))
        new_kelly_prob = max(price + 0.005, min(0.95, new_kelly_prob))
        new_edge = new_kelly_prob - price

        old_passes = old_edge >= min_edge_gate
        new_passes = new_edge >= min_edge_gate

        if old_passes:
            old_pass += 1
        if new_passes and not old_passes:
            new_pass_only += 1
            new_pass_pnl += pnl
            if pnl > 0:
                new_pass_win += 1
            else:
                new_pass_lose += 1

        # Print detail for newly-passing trades
        gate_str = "OLD" if old_passes else ("NEW" if new_passes else "FAIL")
        print(
            f"  {str(row['market_id'])[:18]:<18} {price:>6.3f} {conf_base:>6.3f} "
            f"{old_kelly_prob:>6.3f} {new_kelly_prob:>6.3f} "
            f"{old_edge:>+8.4f} {new_edge:>+8.4f} {gate_str:>5} "
            f"${pnl:>+8.2f}"
        )

    print(f"\n  SUMMARY:")
    print(f"  Total NO resolved:     {len(no_entries)}")
    print(f"  Pass old gate:         {old_pass}")
    print(f"  NEW pass only:         {new_pass_only}")
    if new_pass_only > 0:
        new_wr = new_pass_win / new_pass_only * 100
        ci_lo, ci_hi = wilson_ci(new_pass_win, new_pass_only)
        print(f"  NEW-only wins:         {new_pass_win} / {new_pass_only} = {new_wr:.1f}%")
        print(f"  NEW-only Wilson CI:    [{ci_lo:.2f} - {ci_hi:.2f}]")
        print(f"  NEW-only total P&L:    ${new_pass_pnl:+.2f}")
    else:
        print(f"  (all NO trades already pass or fail both formulas)")


# ── Section 3: Calibration Brier Analysis ──
def compute_calibration_brier(joined: List[Dict]) -> None:
    """Compute raw vs calibrated Brier scores with 70/30 OOS split."""
    print(f"\n{'='*70}")
    print("  CALIBRATION BRIER ANALYSIS (70/30 chronological OOS split)")
    print(f"{'='*70}")

    # Sort by event_time for chronological split
    sorted_data = sorted(joined, key=lambda r: r["event_time"])
    if len(sorted_data) < 50:
        print("  Insufficient data for calibration analysis (need 50+)")
        return

    split_idx = int(len(sorted_data) * 0.70)
    train = sorted_data[:split_idx]
    test = sorted_data[split_idx:]

    for side_filter in ["YES", "NO", "ALL"]:
        train_sub = [r for r in train if side_filter == "ALL" or r["side"] == side_filter]
        test_sub = [r for r in test if side_filter == "ALL" or r["side"] == side_filter]

        if len(test_sub) < 10:
            print(f"\n  {side_filter}: insufficient test data ({len(test_sub)})")
            continue

        # Raw Brier: use confidence as prediction, realized_pnl > 0 as outcome
        raw_predictions = [r["confidence"] for r in test_sub]
        outcomes = [1.0 if r["realized_pnl"] > 0 else 0.0 for r in test_sub]
        raw_brier = sum((p - o) ** 2 for p, o in zip(raw_predictions, outcomes)) / len(outcomes)

        # FTS calibration: fit temperature on training set
        train_preds = [r["confidence"] for r in train_sub]
        train_outcomes = [1.0 if r["realized_pnl"] > 0 else 0.0 for r in train_sub]

        best_t, best_brier = _fit_temperature(train_preds, train_outcomes)

        # Apply temperature to test set
        cal_predictions = [_apply_temperature(p, best_t) for p in raw_predictions]
        cal_brier = sum((p - o) ** 2 for p, o in zip(cal_predictions, outcomes)) / len(outcomes)

        delta = raw_brier - cal_brier
        print(f"\n  {side_filter} (train={len(train_sub)}, test={len(test_sub)}):")
        print(f"    Raw Brier:        {raw_brier:.4f}")
        print(f"    FTS Brier (T={best_t:.2f}): {cal_brier:.4f}")
        print(f"    Delta:            {delta:+.4f} ({'IMPROVES' if delta > 0.005 else 'NO IMPROVEMENT'})")


def _fit_temperature(predictions: List[float], outcomes: List[float]) -> Tuple[float, float]:
    """Grid search for best temperature T minimizing Brier on training data."""
    best_t = 1.0
    best_brier = float("inf")
    for t_int in range(5, 21):  # T from 0.5 to 2.0
        t = t_int / 10.0
        cal = [_apply_temperature(p, t) for p in predictions]
        brier = sum((c - o) ** 2 for c, o in zip(cal, outcomes)) / len(outcomes)
        if brier < best_brier:
            best_brier = brier
            best_t = t
    return best_t, best_brier


def _apply_temperature(p: float, t: float) -> float:
    """Apply focal temperature scaling: sigmoid(logit(p) / T)."""
    p = max(1e-7, min(1 - 1e-7, p))
    logit = math.log(p / (1 - p))
    scaled = logit / t
    return 1.0 / (1.0 + math.exp(-scaled))


# ── Section 4: Category Information Efficiency ──
def compute_category_ie(joined: List[Dict]) -> None:
    """Per-category WR as proxy for information efficiency."""
    print(f"\n{'='*70}")
    print("  CATEGORY INFORMATION EFFICIENCY")
    print(f"{'='*70}")

    cat_data: Dict[str, List[Dict]] = defaultdict(list)
    for row in joined:
        cat = row["event_data"].get("category", "unknown") or "unknown"
        cat_data[cat].append(row)

    print(f"\n  {'Category':<20} {'N':>5} {'Wins':>5} {'WR%':>6} {'TotalPnL':>10} "
          f"{'YES_WR':>7} {'NO_WR':>7} {'Wilson CI':>14}")
    print(f"  {'-'*20} {'-'*5} {'-'*5} {'-'*6} {'-'*10} {'-'*7} {'-'*7} {'-'*14}")

    for cat in sorted(cat_data.keys(), key=lambda c: -len(cat_data[c])):
        rows = cat_data[cat]
        n = len(rows)
        if n < 5:
            continue
        wins = sum(1 for r in rows if r["realized_pnl"] > 0)
        wr = wins / n * 100
        total_pnl = sum(r["realized_pnl"] for r in rows)
        ci_lo, ci_hi = wilson_ci(wins, n)

        yes_rows = [r for r in rows if r["side"] == "YES"]
        no_rows = [r for r in rows if r["side"] == "NO"]
        yes_wr = (sum(1 for r in yes_rows if r["realized_pnl"] > 0) / len(yes_rows) * 100
                  if yes_rows else 0)
        no_wr = (sum(1 for r in no_rows if r["realized_pnl"] > 0) / len(no_rows) * 100
                 if no_rows else 0)

        print(
            f"  {cat:<20} {n:>5} {wins:>5} {wr:>5.1f}% ${total_pnl:>+9.2f} "
            f"{yes_wr:>6.1f}% {no_wr:>6.1f}% [{ci_lo:.2f}-{ci_hi:.2f}]"
        )


# ── Main ──
async def main() -> None:
    parser = argparse.ArgumentParser(description="MirrorBot Factor Evaluation")
    parser.add_argument("--since", default="2026-03-30", help="Start date (ISO)")
    parser.add_argument("--bot", default="MirrorBot", help="Bot name")
    args = parser.parse_args()

    print(f"Loading {args.bot} data since {args.since}...")
    entries, resolutions = await load_entries_and_resolutions(args.bot, args.since)
    print(f"  Loaded {len(entries)} entries, {len(resolutions)} resolutions")

    joined = join_entry_resolution(entries, resolutions)
    print(f"  Joined: {len(joined)} entry-resolution pairs")

    yes_count = sum(1 for r in joined if r["side"] == "YES")
    no_count = sum(1 for r in joined if r["side"] == "NO")
    print(f"  YES: {yes_count}, NO: {no_count}")

    compute_factor_buckets(joined)
    compute_no_edge_validation(joined)
    compute_calibration_brier(joined)
    compute_category_ie(joined)

    print(f"\n{'='*70}")
    print("  DONE")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
