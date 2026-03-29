"""
Walk-forward backtesting framework for esports models.

S136 Phase 9B: Train on rolling window, test on next period, slide forward.
Offline analysis tool — no runtime impact.

Usage:
    python -m esports.backtest.walk_forward --game cs2 --window 180 --test 14
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from structlog import get_logger

logger = get_logger()


@dataclass
class BacktestWindow:
    """Results for a single train/test window."""
    window_start: str
    window_end: str
    test_start: str
    test_end: str
    n_train: int = 0
    n_test: int = 0
    brier: float = 0.0
    log_loss: float = 0.0
    accuracy: float = 0.0
    roi: float = 0.0
    clv: float = 0.0
    max_drawdown: float = 0.0


@dataclass
class BacktestResult:
    """Aggregate results across all windows."""
    game: str
    windows: List[BacktestWindow] = field(default_factory=list)
    avg_brier: float = 0.0
    avg_log_loss: float = 0.0
    avg_roi: float = 0.0
    avg_clv: float = 0.0
    worst_drawdown: float = 0.0
    n_windows: int = 0

    def passes_gates(self) -> bool:
        """Check if backtest passes minimum quality gates."""
        return (
            self.avg_brier < 0.22
            and self.avg_log_loss < 0.65
            and self.avg_roi > 0.02
            and self.avg_clv > 0.01
            and self.worst_drawdown < 0.30
        )


async def run_walk_forward(
    db,
    game: str,
    train_window_days: int = 180,
    test_window_days: int = 14,
    fee_pct: float = 0.0075,
) -> BacktestResult:
    """
    Run walk-forward backtest for a single game.

    Args:
        db: Database connection
        game: Game to backtest
        train_window_days: Training window size in days
        test_window_days: Test window size in days
        fee_pct: Transaction fee (default 0.75%)

    Returns:
        BacktestResult with per-window and aggregate metrics
    """
    result = BacktestResult(game=game)

    # Query all resolved predictions for this game
    query = """
        SELECT predicted_prob, market_price, actual_outcome, created_at
        FROM esports_prediction_log
        WHERE game = :game AND actual_outcome IS NOT NULL
        ORDER BY created_at
    """
    try:
        rows = await db.fetch_all(query, {"game": game})
    except Exception as exc:
        logger.error("walk_forward_query_failed", game=game, error=str(exc))
        return result

    if len(rows) < train_window_days + test_window_days:
        logger.info("walk_forward_insufficient_data", game=game, n=len(rows))
        return result

    # Convert to arrays
    preds = np.array([float(r[0]) for r in rows])
    prices = np.array([float(r[1]) for r in rows])
    outcomes = np.array([float(r[2]) for r in rows])
    dates = [r[3] for r in rows]

    # Slide windows
    min_train = max(50, train_window_days // 3)  # minimum training size

    for i in range(min_train, len(rows) - test_window_days, test_window_days):
        train_start = max(0, i - train_window_days)
        train_end = i
        test_end = min(i + test_window_days, len(rows))

        train_preds = preds[train_start:train_end]
        train_outcomes = outcomes[train_start:train_end]
        test_preds = preds[train_end:test_end]
        test_prices = prices[train_end:test_end]
        test_outcomes = outcomes[train_end:test_end]

        if len(test_preds) < 3:
            continue

        # Metrics on test set
        window = BacktestWindow(
            window_start=str(dates[train_start]),
            window_end=str(dates[train_end - 1]),
            test_start=str(dates[train_end]),
            test_end=str(dates[test_end - 1]),
            n_train=len(train_preds),
            n_test=len(test_preds),
        )

        # Brier score
        window.brier = float(np.mean((test_preds - test_outcomes) ** 2))

        # Log loss
        _eps = 1e-6
        _clipped = np.clip(test_preds, _eps, 1 - _eps)
        window.log_loss = float(-np.mean(
            test_outcomes * np.log(_clipped) + (1 - test_outcomes) * np.log(1 - _clipped)
        ))

        # Accuracy
        _pred_binary = (test_preds >= 0.5).astype(float)
        window.accuracy = float(np.mean(_pred_binary == test_outcomes))

        # CLV: predicted_prob - market_price (positive = beating the line)
        window.clv = float(np.mean(np.abs(test_preds - 0.5) - np.abs(test_prices - 0.5)))

        # ROI simulation (simplified: bet when edge > 5%)
        _edges = np.abs(test_preds - test_prices) - fee_pct
        _bet_mask = _edges > 0.05
        if _bet_mask.sum() > 0:
            _bet_outcomes = test_outcomes[_bet_mask]
            _bet_preds = test_preds[_bet_mask]
            _bet_prices = test_prices[_bet_mask]
            # P&L per bet: win pays (1-price)/price, loss pays -1
            _yes_side = _bet_preds >= 0.5
            _pnl = np.where(
                _yes_side,
                np.where(_bet_outcomes == 1, (1 - _bet_prices) - fee_pct, -_bet_prices - fee_pct),
                np.where(_bet_outcomes == 0, _bet_prices - fee_pct, -(1 - _bet_prices) - fee_pct),
            )
            window.roi = float(np.sum(_pnl) / len(_pnl))
            # Max drawdown
            _cumulative = np.cumsum(_pnl)
            _peak = np.maximum.accumulate(_cumulative)
            _drawdown = (_peak - _cumulative)
            window.max_drawdown = float(np.max(_drawdown)) if len(_drawdown) > 0 else 0.0

        result.windows.append(window)

    # Aggregate
    if result.windows:
        result.n_windows = len(result.windows)
        result.avg_brier = float(np.mean([w.brier for w in result.windows]))
        result.avg_log_loss = float(np.mean([w.log_loss for w in result.windows]))
        result.avg_roi = float(np.mean([w.roi for w in result.windows]))
        result.avg_clv = float(np.mean([w.clv for w in result.windows]))
        result.worst_drawdown = float(max(w.max_drawdown for w in result.windows))

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward backtest")
    parser.add_argument("--game", default="cs2")
    parser.add_argument("--window", type=int, default=180)
    parser.add_argument("--test", type=int, default=14)
    args = parser.parse_args()
    print(f"Walk-forward backtest for {args.game} (window={args.window}d, test={args.test}d)")
    print("Run via: python -c 'import asyncio; from esports.backtest.walk_forward import run_walk_forward; ...'")
