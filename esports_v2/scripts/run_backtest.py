"""
B7: Full backtest runner for EsportsBot v2.

Wires everything together:
  1. Load historical match data (Oracle's Elixir + GRID/HLTV)
  2. Walk-forward backtest with EsportsPipeline
  3. Compute metrics suite
  4. Shuffle-label control test
  5. Print report + gate check

Usage:
    python -m esports_v2.scripts.run_backtest \
        --lol-csv data/2024_LoL.csv data/2025_LoL.csv \
        --cs2-json data/grid_cs2.json \
        --output-dir output/backtest
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import List

from esports_v2.backtest.metrics import MetricsReport, compute_metrics
from esports_v2.backtest.walk_forward import BacktestResult, run_walk_forward
from esports_v2.model.clv import enrich_with_clv
from esports_v2.model.pipeline import EsportsPipeline
from esports_v2.scripts.load_historical import load_all_matches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def run_backtest(
    lol_csvs: List[str],
    cs2_jsons: List[str],
    cs2_csvs: List[str],
    min_train_months: int = 3,
    fold_months: int = 1,
    alpha: float = 0.10,
    output_dir: str | None = None,
) -> BacktestResult:
    """Run full walk-forward backtest."""
    # Load data
    all_matches = load_all_matches(lol_csvs, cs2_jsons, cs2_csvs)
    if not all_matches:
        print("No matches loaded.")
        sys.exit(1)

    # Create pipeline
    pipeline = EsportsPipeline(alpha=alpha)

    # Run walk-forward
    result = run_walk_forward(
        matches=all_matches,
        pipeline=pipeline,
        min_train_months=min_train_months,
        fold_months=fold_months,
    )

    return result


def run_shuffle_control(
    lol_csvs: List[str],
    cs2_jsons: List[str],
    cs2_csvs: List[str],
    min_train_months: int = 3,
    fold_months: int = 1,
    seed: int = 42,
) -> MetricsReport:
    """
    Shuffle-label control test.

    Same pipeline but with randomized outcomes in training data.
    Accuracy should drop to ~50% — confirms model learns from real signal.
    """
    all_matches = load_all_matches(lol_csvs, cs2_jsons, cs2_csvs)
    if not all_matches:
        print("No matches for shuffle control.")
        sys.exit(1)

    # Shuffle winners
    rng = random.Random(seed)
    for m in all_matches:
        if rng.random() > 0.5:
            m.winner, m.team_a, m.team_b = m.team_b, m.team_b, m.team_a

    pipeline = EsportsPipeline()
    result = run_walk_forward(
        matches=all_matches,
        pipeline=pipeline,
        min_train_months=min_train_months,
        fold_months=fold_months,
    )

    return compute_metrics(result.all_predictions)


def print_report(report: MetricsReport, label: str = "BACKTEST") -> None:
    """Print formatted report."""
    print(f"\n{'='*60}")
    print(f" {label} RESULTS")
    print(f"{'='*60}")
    print(report.summary())

    if report.per_game:
        for game, game_report in sorted(report.per_game.items()):
            print(f"\n--- {game.upper()} ---")
            print(f"  N={game_report.n_predictions} (singletons={game_report.n_singletons})")
            print(f"  Accuracy: {game_report.accuracy:.3f} (singletons: {game_report.accuracy_singletons:.3f})")
            print(f"  Brier:    {game_report.brier:.4f}")
            print(f"  Profit:   ${game_report.profit:.2f}")
            print(f"  ROI:      {game_report.roi:+.2%}")


def main():
    parser = argparse.ArgumentParser(description="EsportsBot v2 — Full Backtest")
    parser.add_argument("--lol-csv", nargs="*", default=[], help="Oracle's Elixir LoL CSV files")
    parser.add_argument("--cs2-json", nargs="*", default=[], help="GRID CS2 JSON files")
    parser.add_argument("--cs2-csv", nargs="*", default=[], help="HLTV CS2 CSV files")
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--min-train-months", type=int, default=3, help="Warmup months")
    parser.add_argument("--fold-months", type=int, default=1, help="Fold duration (months)")
    parser.add_argument("--alpha", type=float, default=0.10, help="Conformal alpha")
    parser.add_argument("--shuffle-control", action="store_true", help="Run shuffle-label control")
    args = parser.parse_args()

    if not args.lol_csv and not args.cs2_json and not args.cs2_csv:
        print("No data files specified. Use --lol-csv, --cs2-json, or --cs2-csv.")
        sys.exit(1)

    # Main backtest
    print("\n[1/2] Running walk-forward backtest...")
    result = run_backtest(
        args.lol_csv, args.cs2_json, args.cs2_csv,
        min_train_months=args.min_train_months,
        fold_months=args.fold_months,
        alpha=args.alpha,
        output_dir=args.output_dir,
    )

    # Enrich with CLV (if odds available in records)
    enrich_with_clv(result.all_predictions)

    # Compute metrics
    report = compute_metrics(result.all_predictions)
    print_report(report, "BACKTEST")

    # Gate check
    passed, failures = report.passes_gate()
    print(f"\n{'='*60}")
    if passed:
        print(" 5v2-B GATE: PASS — proceed to 5v2-C (shadow mode)")
    else:
        print(" 5v2-B GATE: FAIL")
        for f in failures:
            print(f"  - {f}")
        print("\n  Action: iterate features/hyperparams. If still failing after")
        print("  2 iterations, approach lacks edge — stop and reassess.")

    # Shuffle control
    if args.shuffle_control:
        print("\n[2/2] Running shuffle-label control...")
        shuffle_report = run_shuffle_control(
            args.lol_csv, args.cs2_json, args.cs2_csv,
            min_train_months=args.min_train_months,
            fold_months=args.fold_months,
        )
        print_report(shuffle_report, "SHUFFLE CONTROL")
        if shuffle_report.accuracy > 0.55:
            print("\n  WARNING: Shuffle control accuracy > 55% — possible data leakage!")
        else:
            print(f"\n  Shuffle control accuracy: {shuffle_report.accuracy:.3f} (expected ~50%)")
            print("  No evidence of data leakage.")

    # Save results
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save predictions
        preds_path = out_dir / "backtest_predictions.json"
        # Convert numpy/bool types for JSON serialization
        serializable = []
        for p in result.all_predictions:
            row = {}
            for k, v in p.items():
                if hasattr(v, "item"):
                    row[k] = v.item()
                elif isinstance(v, bool):
                    row[k] = v
                else:
                    row[k] = v
            serializable.append(row)
        with open(preds_path, "w") as f:
            json.dump(serializable, f, indent=2, default=str)
        print(f"\nPredictions saved to {preds_path} ({len(serializable)} records)")

        # Save report
        report_path = out_dir / "backtest_report.txt"
        with open(report_path, "w") as f:
            f.write(report.summary())
        print(f"Report saved to {report_path}")

        # Feature importance
        if hasattr(result, "trinity") and result.trinity:
            importance_path = out_dir / "feature_importance.json"
            # Pipeline feature importance would require access to the last fold's model
            # Deferred to integration — just note it here
            print(f"(Feature importance: run with --verbose for per-fold diagnostics)")


if __name__ == "__main__":
    main()
