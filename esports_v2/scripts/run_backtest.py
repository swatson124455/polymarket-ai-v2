"""
B7: Full backtest runner for EsportsBot v2.

Wires everything together:
  1. Load historical match data (Oracle's Elixir + GRID/HLTV)
  2. Walk-forward backtest with EsportsPipeline
  3. Compute metrics suite
  4. Shuffle-label control test (MANDATORY on first run)
  5. Print report + gate check
  6. Write results to esports_predictions DB (mode='backtest')

Usage:
    python -m esports_v2.scripts.run_backtest \
        --lol-csv data/2024_LoL.csv data/2025_LoL.csv \
        --cs2-json data/grid_cs2.json \
        --output-dir output/backtest

First run REQUIRES --shuffle-control (or --skip-shuffle to explicitly opt out).
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import List, Optional

from esports_v2.backtest.metrics import MetricsReport, compute_metrics
from esports_v2.backtest.walk_forward import BacktestResult, run_walk_forward
from esports_v2.model.clv import enrich_with_clv
from esports_v2.model.pipeline import EsportsPipeline
from esports_v2.scripts.load_historical import load_all_matches

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# Sentinel file indicating shuffle control has been run at least once
_SHUFFLE_SENTINEL = ".shuffle_control_passed"


def _shuffle_sentinel_path(output_dir: Optional[str]) -> Path:
    """Path to the shuffle control sentinel file."""
    if output_dir:
        return Path(output_dir) / _SHUFFLE_SENTINEL
    return Path(_SHUFFLE_SENTINEL)


def write_predictions_to_db(predictions: List[dict], db_url: Optional[str] = None) -> int:
    """
    Write backtest predictions to esports_predictions table (mode='backtest').

    Uses raw SQL via psycopg2 to avoid depending on the full ORM stack.
    Falls back gracefully if DB is unavailable (file-based backtest still works).

    Args:
        predictions: List of prediction dicts from the walk-forward backtest.
        db_url: PostgreSQL connection string. If None, reads DATABASE_URL env var.

    Returns:
        Number of rows written.
    """
    import os

    url = db_url or os.environ.get("DATABASE_URL")
    if not url:
        logger.warning("No DATABASE_URL set — skipping DB write. Use --output-dir for file output.")
        return 0

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        logger.warning("psycopg2 not installed — skipping DB write.")
        return 0

    insert_sql = """
        INSERT INTO esports_predictions (
            match_id, game, predicted_winner, p_model, p_raw,
            conformal_set, is_singleton, market_price, pinnacle_odds,
            edge, kelly_fraction, actual_winner, correct, mode, model_version
        ) VALUES %s
        ON CONFLICT DO NOTHING
    """

    rows = []
    for p in predictions:
        actual = p.get("actual")
        p_model = p.get("p_model", 0.5)
        predicted_a = p_model > 0.5
        actual_a = actual == 1 if actual is not None else None
        correct = (predicted_a == actual_a) if actual is not None else None

        conformal_set = p.get("conformal_set")
        if isinstance(conformal_set, list):
            conformal_set = [str(c) for c in conformal_set]

        rows.append((
            p.get("match_id"),
            p.get("game"),
            p.get("team_a") if predicted_a else p.get("team_b"),
            p_model,
            p.get("p_raw"),
            conformal_set,
            p.get("is_singleton"),
            p.get("market_price"),
            p.get("pinnacle_prob"),
            p.get("edge"),
            p.get("kelly_fraction"),
            p.get("team_a") if actual_a else p.get("team_b") if actual is not None else None,
            correct,
            "backtest",
            "v2-trinity",
        ))

    try:
        conn = psycopg2.connect(url)
        with conn:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, rows, page_size=500)
        conn.close()
        logger.info(f"Wrote {len(rows)} predictions to esports_predictions (mode='backtest')")
        return len(rows)
    except Exception as e:
        logger.error(f"DB write failed: {e}")
        return 0


def run_backtest(
    lol_csvs: List[str],
    cs2_jsons: List[str],
    cs2_csvs: List[str],
    min_train_months: int = 3,
    fold_months: int = 1,
    alpha: float = 0.10,
) -> BacktestResult:
    """Run full walk-forward backtest."""
    all_matches = load_all_matches(lol_csvs, cs2_jsons, cs2_csvs)
    if not all_matches:
        print("No matches loaded.")
        sys.exit(1)

    pipeline = EsportsPipeline(alpha=alpha)

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

    # Shuffle winners: randomly assign the winner to team_a or team_b
    # This breaks the correlation between team identity and outcome
    # while preserving the match structure (same two teams, same date)
    rng = random.Random(seed)
    for m in all_matches:
        if rng.random() > 0.5:
            # Swap which team is recorded as the winner
            if m.winner == m.team_a:
                m.winner = m.team_b
            elif m.winner == m.team_b:
                m.winner = m.team_a

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
    parser.add_argument("--output-dir", default=None, help="Output directory for file-based results")
    parser.add_argument("--min-train-months", type=int, default=3, help="Warmup months")
    parser.add_argument("--fold-months", type=int, default=1, help="Fold duration (months)")
    parser.add_argument("--alpha", type=float, default=0.10, help="Conformal alpha")
    parser.add_argument("--db-url", default=None, help="PostgreSQL URL (or reads DATABASE_URL env)")
    parser.add_argument(
        "--skip-shuffle", action="store_true",
        help="Explicitly skip shuffle control (not recommended for first run)",
    )
    args = parser.parse_args()

    if not args.lol_csv and not args.cs2_json and not args.cs2_csv:
        print("No data files specified. Use --lol-csv, --cs2-json, or --cs2-csv.")
        sys.exit(1)

    # Issue 6: Shuffle control is mandatory on first run
    sentinel = _shuffle_sentinel_path(args.output_dir)
    first_run = not sentinel.exists()
    run_shuffle = first_run and not args.skip_shuffle

    if first_run and args.skip_shuffle:
        print("WARNING: Skipping shuffle control on first run (--skip-shuffle).")
        print("  This is NOT recommended. Run without --skip-shuffle to validate.")

    if first_run and not args.skip_shuffle:
        print("First run detected — shuffle-label control will run automatically.")
        print("  (Use --skip-shuffle to bypass, NOT recommended.)")

    # ---- Main backtest ----
    step = 1
    total_steps = 2 if run_shuffle else 1
    print(f"\n[{step}/{total_steps}] Running walk-forward backtest...")
    result = run_backtest(
        args.lol_csv, args.cs2_json, args.cs2_csv,
        min_train_months=args.min_train_months,
        fold_months=args.fold_months,
        alpha=args.alpha,
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

    # ---- Shuffle control ----
    if run_shuffle:
        step += 1
        print(f"\n[{step}/{total_steps}] Running shuffle-label control (mandatory first run)...")
        shuffle_report = run_shuffle_control(
            args.lol_csv, args.cs2_json, args.cs2_csv,
            min_train_months=args.min_train_months,
            fold_months=args.fold_months,
        )
        print_report(shuffle_report, "SHUFFLE CONTROL")

        shuffle_ok = shuffle_report.accuracy <= 0.55
        if not shuffle_ok:
            print("\n  FAIL: Shuffle control accuracy > 55% — possible data leakage!")
            print("  Do NOT proceed until this is investigated.")
        else:
            print(f"\n  PASS: Shuffle control accuracy: {shuffle_report.accuracy:.3f} (expected ~50%)")
            print("  No evidence of data leakage.")
            # Write sentinel
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(f"shuffle_accuracy={shuffle_report.accuracy:.4f}\n")
            logger.info(f"Shuffle control passed — sentinel written to {sentinel}")

    # ---- Issue 5: Write to DB ----
    if result.all_predictions:
        n_written = write_predictions_to_db(result.all_predictions, db_url=args.db_url)
        if n_written > 0:
            print(f"\n{n_written} predictions written to esports_predictions (mode='backtest', model_version='v2-trinity')")

    # ---- Save file-based output ----
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save predictions JSON
        preds_path = out_dir / "backtest_predictions.json"
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


if __name__ == "__main__":
    main()
