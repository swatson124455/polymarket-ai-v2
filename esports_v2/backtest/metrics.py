"""
B6: Metrics suite for EsportsBot v2 backtest evaluation.

Computes: accuracy, Brier score, log loss, ECE (expected calibration error),
CLV (closing line value), yield, z-score, max drawdown, reliability diagram data.

All metrics operate on lists of prediction dicts with keys:
  p_model (float): calibrated model probability for team_a
  actual (int): 1 if team_a won, 0 if team_b won
  market_price (float, optional): market price at prediction time
  pinnacle_prob (float, optional): Pinnacle implied prob (after shin)
  stake (float, optional): dollar stake for yield/drawdown
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class MetricsReport:
    """Complete metrics report for a backtest run."""
    n_predictions: int = 0
    n_singletons: int = 0
    accuracy: float = 0.0
    accuracy_singletons: float = 0.0
    brier: float = 0.0
    log_loss: float = 0.0
    ece: float = 0.0
    clv_mean: float = 0.0
    clv_median: float = 0.0
    profit: float = 0.0
    total_staked: float = 0.0
    roi: float = 0.0
    z_score: float = 0.0
    max_drawdown: float = 0.0
    singleton_rate: float = 0.0
    reliability_bins: List[Dict] = field(default_factory=list)
    per_game: Dict[str, "MetricsReport"] = field(default_factory=dict)

    def passes_gate(self) -> Tuple[bool, List[str]]:
        """Check 5v2-B hard gate thresholds. Returns (pass, failures)."""
        failures = []
        if self.accuracy_singletons <= 0.58:
            failures.append(f"accuracy_singletons={self.accuracy_singletons:.3f} <= 0.58")
        if self.brier >= 0.23:
            failures.append(f"brier={self.brier:.4f} >= 0.23")
        if self.clv_mean <= 0.015:
            failures.append(f"clv_mean={self.clv_mean:.4f} <= 1.5%")
        if self.singleton_rate <= 0.30:
            failures.append(f"singleton_rate={self.singleton_rate:.3f} <= 30%")
        if self.n_predictions >= 500 and self.z_score <= 1.5:
            failures.append(f"z_score={self.z_score:.3f} <= 1.5 (on {self.n_predictions} preds)")
        for game, report in self.per_game.items():
            if report.profit <= 0:
                failures.append(f"{game} not profitable (profit={report.profit:.2f})")
        return len(failures) == 0, failures

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Predictions: {self.n_predictions} ({self.n_singletons} singletons, {self.singleton_rate:.1%})",
            f"Accuracy:    {self.accuracy:.3f} (singletons: {self.accuracy_singletons:.3f})",
            f"Brier:       {self.brier:.4f}",
            f"Log loss:    {self.log_loss:.4f}",
            f"ECE:         {self.ece:.4f}",
            f"CLV mean:    {self.clv_mean:+.4f}",
            f"ROI:         {self.roi:+.2%}",
            f"z-score:     {self.z_score:.3f}",
            f"Max DD:      {self.max_drawdown:.2%}",
        ]
        passed, failures = self.passes_gate()
        lines.append(f"\n5v2-B Gate: {'PASS' if passed else 'FAIL'}")
        for f in failures:
            lines.append(f"  FAIL: {f}")
        return "\n".join(lines)


def compute_accuracy(preds: List[dict]) -> float:
    """Fraction of correct predictions."""
    if not preds:
        return 0.0
    correct = sum(1 for p in preds if (p["p_model"] > 0.5) == (p["actual"] == 1))
    return correct / len(preds)


def compute_brier(preds: List[dict]) -> float:
    """Mean Brier score = mean((p - y)^2)."""
    if not preds:
        return 1.0
    return sum((p["p_model"] - p["actual"]) ** 2 for p in preds) / len(preds)


def compute_log_loss(preds: List[dict], eps: float = 1e-15) -> float:
    """Mean binary log loss."""
    if not preds:
        return float("inf")
    total = 0.0
    for p in preds:
        prob = max(eps, min(1 - eps, p["p_model"]))
        y = p["actual"]
        total += -(y * math.log(prob) + (1 - y) * math.log(1 - prob))
    return total / len(preds)


def compute_ece(preds: List[dict], n_bins: int = 10) -> Tuple[float, List[Dict]]:
    """
    Expected Calibration Error (histogram binning).

    Returns (ece, reliability_bins) where each bin has:
      {bin_lower, bin_upper, avg_pred, avg_actual, count}
    """
    if not preds:
        return 0.0, []

    bins = [[] for _ in range(n_bins)]
    for p in preds:
        idx = min(int(p["p_model"] * n_bins), n_bins - 1)
        bins[idx].append(p)

    ece = 0.0
    reliability = []
    for i, b in enumerate(bins):
        if not b:
            continue
        avg_pred = sum(p["p_model"] for p in b) / len(b)
        avg_actual = sum(p["actual"] for p in b) / len(b)
        weight = len(b) / len(preds)
        ece += weight * abs(avg_pred - avg_actual)
        reliability.append({
            "bin_lower": i / n_bins,
            "bin_upper": (i + 1) / n_bins,
            "avg_pred": avg_pred,
            "avg_actual": avg_actual,
            "count": len(b),
        })
    return ece, reliability


def compute_clv(preds: List[dict]) -> Tuple[float, float]:
    """
    Closing Line Value: model_prob - pinnacle_prob.
    Returns (mean_clv, median_clv).
    """
    clvs = []
    for p in preds:
        pin = p.get("pinnacle_prob")
        if pin is not None and pin > 0:
            clvs.append(p["p_model"] - pin)
    if not clvs:
        return 0.0, 0.0
    clvs.sort()
    mean_clv = sum(clvs) / len(clvs)
    mid = len(clvs) // 2
    median_clv = clvs[mid] if len(clvs) % 2 else (clvs[mid - 1] + clvs[mid]) / 2
    return mean_clv, median_clv


def compute_pnl(preds: List[dict]) -> Tuple[float, float, float, float]:
    """
    Compute profit, total staked, ROI, max drawdown from stakes.
    Returns (profit, total_staked, roi, max_drawdown_pct).
    """
    profit = 0.0
    total_staked = 0.0
    peak = 0.0
    max_dd = 0.0
    cumulative = 0.0

    for p in preds:
        stake = p.get("stake", 0.0)
        if stake <= 0:
            continue
        total_staked += stake
        y = p["actual"]
        prob = p["p_model"]
        # Payout at fair odds: if correct, win (1/market_price - 1) * stake
        mkt = p.get("market_price", prob)
        if mkt <= 0 or mkt >= 1:
            continue
        if (prob > 0.5 and y == 1) or (prob < 0.5 and y == 0):
            # Won: payout = stake / mkt - stake (for team_a bet at price mkt)
            bet_price = mkt if prob > 0.5 else (1 - mkt)
            payout = stake / bet_price - stake
            cumulative += payout
            profit += payout
        else:
            cumulative -= stake
            profit -= stake

        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / max(peak, 1.0) if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    roi = profit / total_staked if total_staked > 0 else 0.0
    return profit, total_staked, roi, max_dd


def compute_z_score(preds: List[dict]) -> float:
    """
    z-score for binary prediction skill: (observed - expected) / sqrt(var).
    Tests H0: model is no better than market prices.
    """
    if len(preds) < 10:
        return 0.0
    observed_correct = sum(1 for p in preds if (p["p_model"] > 0.5) == (p["actual"] == 1))
    # Expected correct under null (market prices are truth)
    expected = sum(max(p.get("market_price", 0.5), 1 - p.get("market_price", 0.5)) for p in preds)
    variance = sum(
        p.get("market_price", 0.5) * (1 - p.get("market_price", 0.5))
        for p in preds
    )
    if variance <= 0:
        return 0.0
    return (observed_correct - expected) / math.sqrt(variance)


def compute_metrics(
    preds: List[dict],
    singletons: Optional[List[dict]] = None,
) -> MetricsReport:
    """
    Compute full metrics report.

    Args:
        preds: All predictions. Each dict must have 'p_model' and 'actual'.
               Optional: 'pinnacle_prob', 'market_price', 'stake', 'game', 'is_singleton'.
        singletons: Singleton predictions only. If None, filters from preds.

    Returns:
        MetricsReport with all metrics computed.
    """
    if singletons is None:
        singletons = [p for p in preds if p.get("is_singleton", True)]

    report = MetricsReport()
    report.n_predictions = len(preds)
    report.n_singletons = len(singletons)
    report.singleton_rate = len(singletons) / len(preds) if preds else 0.0

    report.accuracy = compute_accuracy(preds)
    report.accuracy_singletons = compute_accuracy(singletons)
    report.brier = compute_brier(preds)
    report.log_loss = compute_log_loss(preds)
    report.ece, report.reliability_bins = compute_ece(preds)
    report.clv_mean, report.clv_median = compute_clv(preds)
    report.profit, report.total_staked, report.roi, report.max_drawdown = compute_pnl(preds)
    report.z_score = compute_z_score(preds)

    # Per-game breakdown (non-recursive — inline computation)
    games = set(p.get("game", "unknown") for p in preds)
    for game in games:
        game_preds = [p for p in preds if p.get("game") == game]
        game_singletons = [p for p in singletons if p.get("game") == game]
        gr = MetricsReport()
        gr.n_predictions = len(game_preds)
        gr.n_singletons = len(game_singletons)
        gr.singleton_rate = len(game_singletons) / len(game_preds) if game_preds else 0.0
        gr.accuracy = compute_accuracy(game_preds)
        gr.accuracy_singletons = compute_accuracy(game_singletons)
        gr.brier = compute_brier(game_preds)
        gr.log_loss = compute_log_loss(game_preds)
        gr.ece, gr.reliability_bins = compute_ece(game_preds)
        gr.clv_mean, gr.clv_median = compute_clv(game_preds)
        gr.profit, gr.total_staked, gr.roi, gr.max_drawdown = compute_pnl(game_preds)
        gr.z_score = compute_z_score(game_preds)
        report.per_game[game] = gr

    return report
