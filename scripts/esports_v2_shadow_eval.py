#!/usr/bin/env python3
"""
EsportsBot v2 Gate 5v2-C Shadow Prediction Evaluation

Computes the measurable Gate 5v2-C metrics on EB v2 shadow predictions stored
in the `esports_predictions` table. Per S172 plan §5v2-C (line 346):

    Gate 5v2-C (HARD): Shadow accuracy >55%, Brier <0.25,
                      CLV >+2% vs Polymarket, backtest-to-shadow drop <5%.

This script computes the FIRST TWO metrics (Brier + Accuracy). The other two
(CLV vs Polymarket, backtest-to-shadow drop) require additional inputs:
- CLV needs market price at match-resolution time (only prediction-time
  `market_price` is stored in `esports_predictions`).
- Backtest-to-shadow drop needs persisted backtest results to compare against.

Both are deferred until the prerequisite data sources are wired. Brier +
Accuracy alone are diagnostic for the shadow-prediction quality question
that gates the `ESPORTS_V2_DRY_RUN=false` flip into Phase 5v2-D paper trading.

Why `esports_predictions` and not `prediction_log`:
- `esports_predictions` (mode='shadow', model_version='v2-trinity') has
  763 v2 predictions / 389 resolved as of 2026-05-02 — the authoritative
  v2 shadow store. EB v2 writes here directly via esports_v2.shadow.db.
- `prediction_log` has only 124 EB v2 entries / 6 resolved — backfill
  from esports_predictions to prediction_log is sparse for v2 (cross-bot
  observability gap; not in scope for this eval).

Usage:
    python scripts/esports_v2_shadow_eval.py                  # All v2-trinity, no time filter
    python scripts/esports_v2_shadow_eval.py --days 30        # Last 30 days only
    python scripts/esports_v2_shadow_eval.py --game cs2       # Filter by game

Requires PYTHONPATH=/opt/polymarket-ai-v2 (project root) when invoking.
"""
import argparse
import asyncio
import sys
from collections import defaultdict
from typing import Optional

from base_engine.data.database import Database


# Gate 5v2-C thresholds (per S172_CONSOLIDATED_PLAN.md line 346)
BRIER_GATE = 0.25
ACCURACY_GATE = 0.55


async def evaluate(days: Optional[int] = None, game: Optional[str] = None) -> None:
    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            from sqlalchemy import text

            # Build query with optional filters.
            # mode='shadow' + model_version='v2-trinity' is the canonical v2 cohort
            # (excludes 'v2-trinity-contaminated' subset).
            where = [
                "mode = 'shadow'",
                "model_version = 'v2-trinity'",
                "actual_winner IS NOT NULL",
            ]
            params: dict = {}
            if days is not None:
                where.append("created_at > NOW() - (:days_str || ' days')::INTERVAL")
                params["days_str"] = str(days)
            if game:
                where.append("game = :game")
                params["game"] = game

            sql = text(f"""
                SELECT p_model, predicted_winner, actual_winner, game
                FROM esports_predictions
                WHERE {' AND '.join(where)}
            """)
            rows = (await s.execute(sql, params)).fetchall()

        if not rows:
            scope = f"days={days}" if days else "all-time"
            if game:
                scope += f", game={game}"
            print(f"No resolved v2-trinity shadow predictions in window ({scope}).")
            return

        # Aggregate overall + per-game
        all_terms = []  # list of (p_model, outcome) tuples
        by_game: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for p_model, predicted, actual, g in rows:
            outcome = 1 if predicted == actual else 0
            t = (float(p_model), outcome)
            all_terms.append(t)
            by_game[g or "unknown"].append(t)

        scope = f"last {days} days" if days else "all-time"
        if game:
            scope += f", game={game}"

        print("=" * 64)
        print("  EB v2 Gate 5v2-C Shadow Prediction Evaluation")
        print("=" * 64)
        print(f"  Source:  esports_predictions (mode=shadow, model_version=v2-trinity)")
        print(f"  Scope:   {scope}")
        print(f"  Gates:   Brier <{BRIER_GATE}, Accuracy >{ACCURACY_GATE:.0%}")
        print()

        # Overall
        _print_eval("OVERALL", all_terms)

        # Per-game
        if not game:
            print()
            for g in sorted(by_game.keys()):
                _print_eval(f"GAME: {g}", by_game[g])

        # Verdict on overall
        n = len(all_terms)
        brier = sum((p - o) ** 2 for p, o in all_terms) / n
        accuracy = sum(o for _, o in all_terms) / n
        brier_pass = brier < BRIER_GATE
        accuracy_pass = accuracy > ACCURACY_GATE

        print()
        print("=" * 64)
        print(f"  >>> PARTIAL VERDICT (Brier + Accuracy only):")
        if brier_pass and accuracy_pass:
            print(f"      PASS — both Brier ({brier:.4f}) and Accuracy ({accuracy:.2%}) gates met.")
            print(f"      CLV and backtest-to-shadow drop NOT evaluated — require additional")
            print(f"      data sources before full Gate 5v2-C verdict can be issued.")
        else:
            failed = []
            if not brier_pass:
                failed.append(f"Brier={brier:.4f} ≥ {BRIER_GATE}")
            if not accuracy_pass:
                failed.append(f"Accuracy={accuracy:.2%} ≤ {ACCURACY_GATE:.0%}")
            print(f"      FAIL — {', '.join(failed)}.")
            print(f"      Gate 5v2-C cannot pass without these. CLV and backtest-to-shadow")
            print(f"      drop become moot once Brier or Accuracy fails.")
        print("=" * 64)
    finally:
        await db.close()


def _print_eval(label: str, entries: list[tuple[float, int]]) -> None:
    """Print Brier + Accuracy + sample size + base rate for a cohort."""
    n = len(entries)
    if n == 0:
        print(f"  {label}: no entries")
        return
    brier = sum((p - o) ** 2 for p, o in entries) / n
    accuracy = sum(o for _, o in entries) / n
    base_rate = sum(o for _, o in entries) / n  # same as accuracy here

    # Brier Skill Score vs climatological baseline
    brier_clim = base_rate * (1 - base_rate)
    bss = 1.0 - (brier / brier_clim) if brier_clim > 0 else 0.0

    print(f"  {label}:")
    print(f"    n={n}  Brier={brier:.4f}  Accuracy={accuracy:.2%}  BaseRate={base_rate:.3f}  BSS={bss:+.4f}")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EB v2 Gate 5v2-C Shadow Eval")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Rolling window in days (default: all-time)",
    )
    p.add_argument(
        "--game",
        default=None,
        help="Filter by game (e.g., cs2, lol). Default: all games.",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    ns = _parse_args(sys.argv[1:])
    asyncio.run(evaluate(days=ns.days, game=ns.game))
