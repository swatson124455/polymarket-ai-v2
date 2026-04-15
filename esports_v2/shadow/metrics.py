"""
Shadow mode gate evaluation for EsportsBot v2.

Computes Gate 5v2-C criteria on shadow/paper predictions.
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def compute_shadow_gate(
    stats: Dict,
    backtest_accuracy: float = 0.712,
) -> Tuple[bool, Dict, List[str]]:
    """
    Evaluate Gate 5v2-C criteria from shadow stats.

    Args:
        stats: Dict from shadow_db.get_shadow_stats().
        backtest_accuracy: Backtest singleton accuracy for drop check.

    Returns:
        (passed, metrics, failures) tuple.
    """
    failures = []
    metrics = dict(stats)

    n_resolved = stats.get("n_resolved", 0)
    if n_resolved < 50:
        failures.append(f"n_resolved={n_resolved} < 50 (minimum required)")
        return False, metrics, failures

    # Gate 1: Shadow accuracy (singletons) > 55%
    acc = stats.get("accuracy_singletons")
    if acc is not None:
        if acc <= 0.55:
            failures.append(f"accuracy_singletons={acc:.3f} <= 0.55")
    else:
        failures.append("accuracy_singletons=N/A (no resolved singletons)")

    # Gate 2: Shadow Brier < 0.25
    brier = stats.get("brier")
    if brier is not None:
        if brier >= 0.25:
            failures.append(f"brier={brier:.4f} >= 0.25")
    else:
        failures.append("brier=N/A")

    # Gate 3: CLV vs Polymarket > +2% mean
    clv = stats.get("clv_polymarket_mean")
    if clv is not None:
        if clv <= 0.02:
            failures.append(f"clv_polymarket_mean={clv:.4f} <= 0.02")
    else:
        failures.append("clv_polymarket_mean=N/A (no market prices)")

    # Gate 4: Backtest-to-shadow accuracy drop < 5% absolute
    if acc is not None:
        drop = backtest_accuracy - acc
        metrics["accuracy_drop"] = drop
        if drop >= 0.05:
            failures.append(
                f"accuracy_drop={drop:.3f} >= 0.05 "
                f"(backtest={backtest_accuracy:.3f} → shadow={acc:.3f})"
            )

    passed = len(failures) == 0
    return passed, metrics, failures


def format_gate_report(
    passed: bool, metrics: Dict, failures: List[str]
) -> str:
    """Format gate results as human-readable report."""
    lines = []
    lines.append("=" * 60)
    lines.append(" 5v2-C SHADOW GATE EVALUATION")
    lines.append("=" * 60)

    n = metrics.get("n_total", 0)
    n_sing = metrics.get("n_singletons", 0)
    n_res = metrics.get("n_resolved", 0)
    lines.append(f"Predictions: {n} ({n_sing} singletons, {n_res} resolved)")

    acc = metrics.get("accuracy_singletons")
    if acc is not None:
        lines.append(f"Accuracy (singletons): {acc:.3f} (threshold: >0.55)")

    brier = metrics.get("brier")
    if brier is not None:
        lines.append(f"Brier:                 {brier:.4f} (threshold: <0.25)")

    clv = metrics.get("clv_polymarket_mean")
    if clv is not None:
        lines.append(f"CLV vs Polymarket:     {clv:+.4f} (threshold: >+0.02)")

    drop = metrics.get("accuracy_drop")
    if drop is not None:
        lines.append(f"Accuracy drop:         {drop:.3f} (threshold: <0.05)")

    lines.append("")
    if passed:
        lines.append("GATE: PASS — proceed to live trading")
    else:
        lines.append("GATE: FAIL")
        for f in failures:
            lines.append(f"  - {f}")

    return "\n".join(lines)
