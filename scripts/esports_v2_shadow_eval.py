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
- `esports_predictions` (mode='shadow', model_version='v2-trinity') is the
  authoritative v2 shadow store. EB v2 writes here directly via
  esports_v2.shadow.db.
- `prediction_log` has only sparse v2 entries — the cross-bot backfill from
  esports_predictions to prediction_log is sparse for v2 (cross-bot
  observability gap; not in scope for this eval).

Brier formula note (S209 fix): p_model is P(team_a wins), per
esports_v2/shadow/match_converter.py:121. The original implementation
computed Brier as (p_model - 1{model_correct})^2, which inverts outcomes
for predictions where p_model<0.5 (model picks team_b). The corrected
formula derives y_a = 1 iff team_a actually won — from the joint of
(predicted_winner == actual_winner) and (p_model > 0.5) — and computes
Brier as (p_model - y_a)^2. BSS climatology denominator now uses
P(team_a wins)*(1-P(team_a wins)), the base rate of the forecast event,
not accuracy*(1-accuracy). See §S209 Corrections Log for verdict-reversal
implications.

Usage:
    python scripts/esports_v2_shadow_eval.py                  # All v2-trinity, no time filter
    python scripts/esports_v2_shadow_eval.py --days 30        # Last 30 days only (rolling window)
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
                SELECT p_model, predicted_winner, actual_winner, game, is_singleton
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

        # Build records. y_a = 1 iff team_a actually won. Derived without a
        # JOIN to esports_matches: predicted_winner == team_a iff p_model > 0.5
        # (per esports_v2/shadow/match_converter.py:121), so:
        #   correct AND p_model>0.5  → predicted=team_a, actual=team_a → y_a=1
        #   correct AND p_model<=0.5 → predicted=team_b, actual=team_b → y_a=0
        #   wrong   AND p_model>0.5  → predicted=team_a, actual=team_b → y_a=0
        #   wrong   AND p_model<=0.5 → predicted=team_b, actual=team_a → y_a=1
        # Equivalently: y_a = 1 iff correct == (p_model > 0.5).
        all_recs: list[dict] = []
        by_game: dict[str, list[dict]] = defaultdict(list)
        for p_model, predicted, actual, g, is_singleton in rows:
            p = float(p_model)
            correct = (predicted == actual)
            y_a = 1 if correct == (p > 0.5) else 0
            rec = {
                "p": p,
                "y_a": y_a,
                "correct": int(correct),
                "is_singleton": bool(is_singleton),
                "game": g or "unknown",
            }
            all_recs.append(rec)
            by_game[g or "unknown"].append(rec)

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

        # ── Full prediction set ──────────────────────────────────────────
        print("FULL PREDICTION SET (all conformal outputs):")
        _print_eval("OVERALL", all_recs)
        if not game:
            for g in sorted(by_game.keys()):
                _print_eval(f"  GAME: {g}", by_game[g])

        # ── Singleton-only (trade-eligible cohort for Phase 5v2-D) ──────
        # Only singleton predictions produce trades — non-singletons abstain
        # via the conformal filter at esports_v2/model/conformal.py. The gate
        # decision should be evaluated against this cohort, not the mixed set.
        sing_recs = [r for r in all_recs if r["is_singleton"]]
        sing_by_game: dict[str, list[dict]] = defaultdict(list)
        for r in sing_recs:
            sing_by_game[r["game"]].append(r)

        print()
        print("SINGLETON-ONLY (trade-eligible cohort for Phase 5v2-D):")
        _print_eval("OVERALL", sing_recs)
        if not game:
            for g in sorted(sing_by_game.keys()):
                _print_eval(f"  GAME: {g}", sing_by_game[g])

        # ── Verdict on singleton-only (the gate-relevant cohort) ────────
        n_sing = len(sing_recs)
        if n_sing == 0:
            print()
            print("=" * 64)
            print("  >>> NO SINGLETON PREDICTIONS — verdict undefined.")
            print("=" * 64)
            return

        brier_sing = sum((r["p"] - r["y_a"]) ** 2 for r in sing_recs) / n_sing
        accuracy_sing = sum(r["correct"] for r in sing_recs) / n_sing
        brier_pass = brier_sing < BRIER_GATE
        accuracy_pass = accuracy_sing > ACCURACY_GATE

        print()
        print("=" * 64)
        print(f"  >>> PARTIAL VERDICT (Brier + Accuracy on singleton-only):")
        if brier_pass and accuracy_pass:
            print(f"      PASS — Brier ({brier_sing:.4f}) < {BRIER_GATE} and Accuracy ({accuracy_sing:.2%}) > {ACCURACY_GATE:.0%}.")
            print(f"      CLV and backtest-to-shadow drop NOT evaluated — require additional")
            print(f"      data sources before full Gate 5v2-C verdict can be issued.")
        else:
            failed = []
            if not brier_pass:
                failed.append(f"Brier={brier_sing:.4f} ≥ {BRIER_GATE}")
            if not accuracy_pass:
                failed.append(f"Accuracy={accuracy_sing:.2%} ≤ {ACCURACY_GATE:.0%}")
            print(f"      FAIL — {', '.join(failed)}.")
            print(f"      Gate 5v2-C cannot pass without these. CLV and backtest-to-shadow")
            print(f"      drop become moot once Brier or Accuracy fails.")
        print("=" * 64)
    finally:
        await db.close()


def _print_eval(label: str, recs: list[dict]) -> None:
    """Print Brier + Accuracy + sample size + base rate for a cohort.

    Brier is computed against y_a = 1{team_a actually won} (the event being
    forecast by p_model). Accuracy is the rate at which predicted_winner
    matched actual_winner. BSS uses base_rate(team_a)*(1-base_rate(team_a))
    as the climatological baseline — the Brier of a constant forecast at the
    actual event base rate.
    """
    n = len(recs)
    if n == 0:
        print(f"  {label}: no entries")
        return
    brier = sum((r["p"] - r["y_a"]) ** 2 for r in recs) / n
    accuracy = sum(r["correct"] for r in recs) / n
    base_rate = sum(r["y_a"] for r in recs) / n  # P(team_a wins) — the forecast event

    # Brier Skill Score vs climatological baseline (constant base_rate forecast)
    brier_clim = base_rate * (1 - base_rate)
    bss = 1.0 - (brier / brier_clim) if brier_clim > 0 else 0.0

    print(f"  {label}:")
    print(f"    n={n}  Brier={brier:.4f}  Accuracy={accuracy:.2%}  BaseRate(P_team_a)={base_rate:.3f}  BSS={bss:+.4f}")


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
