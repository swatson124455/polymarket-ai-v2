"""
B2: XGBoost meta-model for EsportsBot v2.

Takes Trinity features + game-specific features, predicts P(team_a wins).
Trained per walk-forward fold on historical data.

Features:
  - Trinity core (5): p_elo, p_glicko, p_openskill, trinity_spread, trinity_mean
  - Pairwise disagreements (3): p_elo-p_glicko, p_elo-p_openskill, p_glicko-p_openskill
  - Game context (3): event_tier (ordinal), is_lan (bool), best_of (1/3/5)
  - Game one-hot (1): is_cs2

Target: binary (1 if team_a wins, 0 otherwise)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Feature column definitions
TRINITY_FEATURES = ["p_elo", "p_glicko", "p_openskill", "trinity_spread", "trinity_mean"]
DISAGREEMENT_FEATURES = ["elo_glicko_diff", "elo_openskill_diff", "glicko_openskill_diff"]
CONTEXT_FEATURES = ["event_tier_ord", "is_lan_int", "best_of"]
GAME_FEATURES = ["is_cs2"]
ALL_FEATURES = TRINITY_FEATURES + DISAGREEMENT_FEATURES + CONTEXT_FEATURES + GAME_FEATURES

TIER_MAP = {"s_tier": 4, "a_tier": 3, "b_tier": 2, "c_tier": 1, None: 0, "": 0}


def record_to_features(record: dict) -> np.ndarray:
    """Convert a record dict to feature vector."""
    p_elo = record.get("p_elo", 0.5)
    p_glicko = record.get("p_glicko", 0.5)
    p_openskill = record.get("p_openskill", 0.5)

    features = [
        # Trinity core
        p_elo,
        p_glicko,
        p_openskill,
        record.get("trinity_spread", 0.0),
        record.get("trinity_mean", 0.5),
        # Pairwise disagreements
        p_elo - p_glicko,
        p_elo - p_openskill,
        p_glicko - p_openskill,
        # Context
        TIER_MAP.get(record.get("event_tier"), 0),
        int(record.get("is_lan", False)),
        record.get("best_of") or 1,
        # Game
        int(record.get("game") == "cs2"),
    ]
    return np.array(features, dtype=np.float32)


def records_to_matrix(records: List[dict]) -> Tuple[np.ndarray, np.ndarray]:
    """Convert list of records to feature matrix X and target vector y."""
    X = np.array([record_to_features(r) for r in records])
    y = np.array([r["actual"] for r in records], dtype=np.float32)
    return X, y


class XGBoostMetaModel:
    """
    XGBoost binary classifier for match outcome prediction.

    Wraps xgboost.XGBClassifier with feature engineering from Trinity records.
    """

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        min_child_weight: int = 5,
        reg_alpha: float = 0.1,
        reg_lambda: float = 1.0,
        random_state: int = 42,
    ) -> None:
        self._params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "min_child_weight": min_child_weight,
            "reg_alpha": reg_alpha,
            "reg_lambda": reg_lambda,
            "random_state": random_state,
            "use_label_encoder": False,
            "eval_metric": "logloss",
            "verbosity": 0,
        }
        self._model = None

    def fit(self, records: List[dict]) -> None:
        """Train on records with Trinity features + outcome."""
        import xgboost as xgb

        X, y = records_to_matrix(records)
        self._model = xgb.XGBClassifier(**self._params)
        self._model.fit(X, y)
        logger.info(f"XGBoost trained on {len(records)} records, {X.shape[1]} features")

    def predict_proba(self, record: dict) -> float:
        """Predict P(team_a wins) for a single record.

        Uses the underlying Booster's inplace_predict to skip DMatrix
        construction. For binary:logistic objectives the default
        predict_type="value" returns the same probability as
        XGBClassifier.predict_proba(...)[:, 1].
        """
        if self._model is None:
            return 0.5
        X = record_to_features(record).reshape(1, -1)
        return float(self._model.get_booster().inplace_predict(X)[0])

    def predict_proba_batch(self, records: List[dict]) -> np.ndarray:
        """Predict P(team_a wins) for a batch of records.

        Same DMatrix-skipping path as predict_proba. Returns a 1-D array
        of probabilities.
        """
        if self._model is None:
            return np.full(len(records), 0.5)
        X = np.array([record_to_features(r) for r in records])
        return np.asarray(self._model.get_booster().inplace_predict(X))

    def feature_importance(self) -> Dict[str, float]:
        """Get feature importances (gain-based)."""
        if self._model is None:
            return {}
        importances = self._model.feature_importances_
        return dict(zip(ALL_FEATURES, importances))
