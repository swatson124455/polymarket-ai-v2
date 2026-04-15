"""
B1: Walk-forward backtester for EsportsBot v2.

Temporal walk-forward:
  - Sort matches chronologically
  - Split into folds by date windows
  - For each fold: train model on all prior matches, predict current fold
  - Strict no-lookahead: Trinity.process_match() captures pre-match predictions

This module provides the backtesting engine. The model (XGBoost + calibration +
conformal filter) is injected via a Pipeline interface so the backtester doesn't
know about model internals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from esports_v2.data.normalizer import RawMatch, raw_to_match_result
from esports_v2.ratings.trinity import Trinity, TrinityPrediction

logger = logging.getLogger(__name__)


class PredictionPipeline(Protocol):
    """
    Interface for model pipeline.

    The backtester calls `fit()` with training records, then `predict()` for
    each test record. The pipeline handles XGBoost + Venn-ABERS + MAPIE internally.
    """

    def fit(self, records: List[dict]) -> None:
        """Train on historical records (features + outcome)."""
        ...

    def predict(self, record: dict) -> dict:
        """
        Predict for a single record.

        Returns dict with:
          p_model (float): calibrated probability for team_a winning
          is_singleton (bool): whether conformal set is singleton (bet-worthy)
          kelly_fraction (float): suggested Kelly fraction
          conformal_set (list): prediction set labels
        """
        ...


@dataclass
class FoldResult:
    """Results from one walk-forward fold."""
    fold_idx: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    n_train: int
    n_test: int
    predictions: List[dict] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Complete backtest results across all folds."""
    folds: List[FoldResult] = field(default_factory=list)
    all_predictions: List[dict] = field(default_factory=list)
    trinity: Optional[Trinity] = None


def _parse_date(d: Optional[str]) -> datetime:
    """Parse ISO date string, handling various formats. Always returns naive UTC."""
    if not d:
        return datetime.min
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
        # Strip timezone to naive UTC for consistent comparison
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, AttributeError):
        return datetime.min


def _build_record(raw: RawMatch, prediction: TrinityPrediction) -> dict:
    """Combine raw match data with Trinity prediction into training/test record."""
    actual_a_wins = 1 if raw.winner == raw.team_a else 0
    return {
        "match_id": raw.match_id,
        "game": raw.game,
        "team_a": raw.team_a,
        "team_b": raw.team_b,
        "winner": raw.winner,
        "match_date": raw.match_date,
        "event_name": raw.event_name,
        "event_tier": raw.event_tier,
        "is_lan": raw.is_lan,
        "best_of": raw.best_of,
        "score_a": raw.score_a,
        "score_b": raw.score_b,
        # Trinity features
        **prediction.to_feature_dict(),
        "high_agreement": prediction.high_agreement,
        "should_abstain": prediction.should_abstain,
        # Target
        "actual": actual_a_wins,
    }


def generate_date_folds(
    matches: List[RawMatch],
    min_train_months: int = 3,
    fold_months: int = 1,
) -> List[Tuple[List[int], List[int]]]:
    """
    Generate walk-forward fold indices.

    Args:
        matches: Chronologically sorted matches.
        min_train_months: Minimum months of training data before first fold.
        fold_months: Size of each test fold in months.

    Returns:
        List of (train_indices, test_indices) tuples.
    """
    if not matches:
        return []

    dates = [_parse_date(m.match_date) for m in matches]
    start_date = dates[0]

    # First fold starts after min_train_months
    from dateutil.relativedelta import relativedelta
    first_test_start = start_date + relativedelta(months=min_train_months)

    folds = []
    test_start = first_test_start

    while True:
        test_end = test_start + relativedelta(months=fold_months)
        train_idx = [i for i, d in enumerate(dates) if d < test_start]
        test_idx = [i for i, d in enumerate(dates) if test_start <= d < test_end]

        if not test_idx:
            break
        if len(train_idx) < 50:
            test_start = test_end
            continue

        folds.append((train_idx, test_idx))
        test_start = test_end

    return folds


def run_walk_forward(
    matches: List[RawMatch],
    pipeline: PredictionPipeline,
    min_train_months: int = 3,
    fold_months: int = 1,
    trinity_kwargs: Optional[Dict] = None,
) -> BacktestResult:
    """
    Run walk-forward backtest.

    1. Process ALL matches through Trinity chronologically (ratings accumulate).
    2. Split into temporal folds.
    3. For each fold: fit pipeline on train records, predict test records.

    Args:
        matches: Chronologically sorted raw matches.
        pipeline: Model pipeline implementing PredictionPipeline.
        min_train_months: Months of warmup before first test fold.
        fold_months: Test fold duration in months.
        trinity_kwargs: Override Trinity params (elo_k, glicko_tau, etc).

    Returns:
        BacktestResult with all fold results and aggregated predictions.
    """
    if not matches:
        return BacktestResult()

    tk = trinity_kwargs or {}
    trinity = Trinity(**tk)

    # Step 1: Process ALL matches through Trinity to build features
    logger.info(f"Processing {len(matches)} matches through Trinity...")
    all_records = []
    for raw in matches:
        mr = raw_to_match_result(raw)
        prediction = trinity.process_match(mr)
        record = _build_record(raw, prediction)
        all_records.append(record)

    logger.info(f"Trinity done. {trinity.match_count} matches processed.")

    # Step 2: Generate temporal folds
    folds = generate_date_folds(matches, min_train_months, fold_months)
    logger.info(f"Generated {len(folds)} walk-forward folds.")

    if not folds:
        logger.warning("No folds generated. Need more data.")
        return BacktestResult(trinity=trinity)

    # Step 3: Walk-forward
    result = BacktestResult(trinity=trinity)

    for fold_idx, (train_idx, test_idx) in enumerate(folds):
        train_records = [all_records[i] for i in train_idx]
        test_records = [all_records[i] for i in test_idx]

        train_start = train_records[0].get("match_date", "?")
        train_end = train_records[-1].get("match_date", "?")
        test_start = test_records[0].get("match_date", "?")
        test_end = test_records[-1].get("match_date", "?")

        logger.info(
            f"Fold {fold_idx}: train={len(train_records)} "
            f"[{train_start[:10]}..{train_end[:10]}], "
            f"test={len(test_records)} [{test_start[:10]}..{test_end[:10]}]"
        )

        # Train
        pipeline.fit(train_records)

        # Predict each test record
        fold_preds = []
        for rec in test_records:
            pred = pipeline.predict(rec)
            # Merge original record with prediction output
            merged = {**rec, **pred}
            fold_preds.append(merged)

        fold_result = FoldResult(
            fold_idx=fold_idx,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            n_train=len(train_records),
            n_test=len(test_records),
            predictions=fold_preds,
        )
        result.folds.append(fold_result)
        result.all_predictions.extend(fold_preds)

    logger.info(
        f"Walk-forward complete: {len(result.folds)} folds, "
        f"{len(result.all_predictions)} total predictions."
    )
    return result
