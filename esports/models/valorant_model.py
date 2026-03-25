"""
Valorant Win Model — XGBoost with 6 Glicko-2 features.

Simpler than LoL model (no patch weighting, no live game-state features).
Uses team_strength_diff + rating uncertainty features from Glicko-2.

Features (6):
    team_strength_diff, matchup_uncertainty, rd_asymmetry,
    team_a_volatility, team_b_volatility, best_of

Heuristic fallback: logistic(team_strength_diff) when model not trained.

Usage::
    model = ValorantModel()
    model.train(data)
    prob = model.predict(game_state)
"""
from __future__ import annotations

import math
import os
import pickle
from typing import Any, Dict, List, Optional

import numpy as np
from structlog import get_logger

logger = get_logger()

FEATURE_NAMES = [
    "team_strength_diff",
    "matchup_uncertainty",
    "rd_asymmetry",
    "team_a_volatility",
    "team_b_volatility",
    "best_of",
]

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "saved_models", "valorant_xgb.json"
)


class ValorantModel:
    """XGBoost binary classifier for Valorant match winner prediction."""

    FEATURE_NAMES = FEATURE_NAMES

    def __init__(self) -> None:
        self._model = None
        self._is_trained = False

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def predict(self, game_state: Dict[str, Any]) -> float:
        """Predict P(team_a wins) from game state dict."""
        if not self._is_trained or self._model is None:
            return self._predict_heuristic(game_state)

        features = self._extract_features(game_state)
        if features is None:
            return self._predict_heuristic(game_state)

        try:
            X = np.array([features], dtype=np.float32)
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            proba = self._model.predict_proba(X)[0][1]
            return float(np.clip(proba, 0.01, 0.99))
        except Exception as exc:
            logger.debug("ValorantModel: predict failed", error=str(exc))
            return 0.5

    def train(self, training_data: List[Dict[str, Any]]) -> bool:
        """Train on historical match data. Returns True if successful."""
        if len(training_data) < 50:
            logger.warning("ValorantModel: insufficient data", count=len(training_data))
            return False

        try:
            return self._train_sync(training_data)
        except Exception as exc:
            logger.error("ValorantModel: training failed", error=str(exc))
            return False

    def _train_sync(self, training_data: List[Dict[str, Any]]) -> bool:
        from xgboost import XGBClassifier

        X_rows = []
        y_rows = []

        for row in training_data:
            features = self._extract_features(row)
            if features is None:
                continue
            label = int(row.get("team_a_won", 0))
            X_rows.append(features)
            y_rows.append(label)

        if len(X_rows) < 50:
            return False

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_rows, dtype=np.int32)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # 15% early stopping split
        es_split = max(int(len(X) * 0.85), 50)
        X_train, X_es = X[:es_split], X[es_split:]
        y_train, y_es = y[:es_split], y[es_split:]

        model = XGBClassifier(
            n_estimators=60,
            max_depth=2,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            early_stopping_rounds=20,
        )

        if len(X_es) >= 10:
            model.fit(X_train, y_train, eval_set=[(X_es, y_es)], verbose=False)
        else:
            model.fit(X, y)

        self._model = model
        self._is_trained = True

        importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
        top = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:3]
        logger.info(
            "ValorantModel: trained",
            n_samples=len(X_rows),
            top_features=[(n, round(v, 3)) for n, v in top],
        )
        return True

    def save(self, path: Optional[str] = None) -> bool:
        path = path or MODEL_PATH
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump({"model": self._model}, f)
            logger.info("ValorantModel: saved", path=path)
            return True
        except Exception as exc:
            logger.warning("ValorantModel: save failed", error=str(exc))
            return False

    def load(self, path: Optional[str] = None) -> bool:
        path = path or MODEL_PATH
        try:
            if not os.path.exists(path):
                return False
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._model = data.get("model")
            self._is_trained = self._model is not None
            logger.info("ValorantModel: loaded", path=path)
            return self._is_trained
        except Exception as exc:
            logger.warning("ValorantModel: load failed", error=str(exc))
            return False

    @staticmethod
    def _predict_heuristic(game_state: Dict[str, Any]) -> float:
        """Logistic heuristic based on team_strength_diff."""
        team_str = float(game_state.get("team_strength_diff", 0.0))
        uncertainty = float(game_state.get("matchup_uncertainty", 0.5))
        certainty_weight = max(0.3, 1.0 - 0.7 * uncertainty)
        z = 2.0 * team_str * certainty_weight
        prob = 1.0 / (1.0 + math.exp(-z))
        return float(max(0.05, min(0.95, prob)))

    def _extract_features(self, state: Dict[str, Any]) -> Optional[List[float]]:
        try:
            return [float(state.get(name, 0.0) or 0.0) for name in FEATURE_NAMES]
        except Exception:
            return None
