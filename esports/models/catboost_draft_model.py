"""
CatBoost Draft Model — per-game match outcome prediction from draft composition.

Wraps CatBoostClassifier with native categorical feature support.
Champion/agent names are passed as categorical features (ordered target encoding),
avoiding one-hot encoding for high-cardinality features (150+ champions in LoL).

Usage:
    model = CatBoostDraftModel("lol")
    metrics = model.fit(X_dicts, y_labels, cat_feature_names=["team_a_pick_0", ...])
    prob = model.predict_proba(feature_dict)
    model.save("saved_models/catboost_lol.cbm")
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np
from structlog import get_logger

from config.settings import settings

logger = get_logger()

# Graduation thresholds (same as existing esports model pattern)
_MIN_ACCURACY = float(getattr(settings, "ESPORTS_MODEL_MIN_ACCURACY", 0.55))
_MAX_BRIER = float(getattr(settings, "ESPORTS_MODEL_MAX_BRIER", 0.24))


class CatBoostDraftModel:
    """Per-game CatBoost model for draft-based match outcome prediction.

    Features:
        Numeric: avg_champ_wr_a/b, synergy_score_a/b, counter_score_a/b,
                 ban_impact_a/b, pool_depth_a/b, draft_advantage,
                 + Glicko-2 features (team_strength_diff, matchup_uncertainty, etc.)
        Categorical: team_a_pick_0..4, team_b_pick_0..4 (champion/agent names)
    """

    def __init__(self, game: str) -> None:
        self.game = game
        self._model: Any = None
        self._fitted: bool = False
        self._feature_names: List[str] = []
        self._cat_feature_names: List[str] = []
        self._cat_feature_indices: List[int] = []
        self._train_metrics: Dict[str, float] = {}

    @property
    def is_fitted(self) -> bool:
        return self._fitted and self._model is not None

    def fit(
        self,
        X: List[Dict[str, Any]],
        y: List[int],
        cat_feature_names: List[str],
        all_feature_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Train CatBoostClassifier on feature dicts.

        Args:
            X: List of feature dicts (from DraftFeatureBuilder + Glicko-2 features).
            y: Labels (1=team_a_won, 0=team_b_won).
            cat_feature_names: Names of categorical columns.
            all_feature_names: Ordered feature names. If None, derived from X[0].

        Returns:
            Metrics dict: {accuracy, brier, logloss, n_samples, graduated}.
        """
        if not X or not y:
            return {"error": "empty data", "graduated": False}

        try:
            from catboost import CatBoostClassifier, Pool
        except ImportError:
            logger.warning("catboost_not_installed")
            return {"error": "catboost not installed", "graduated": False}

        # Determine feature order
        if all_feature_names:
            self._feature_names = list(all_feature_names)
        else:
            self._feature_names = sorted(X[0].keys())

        self._cat_feature_names = [n for n in cat_feature_names if n in self._feature_names]
        self._cat_feature_indices = [self._feature_names.index(n) for n in self._cat_feature_names]

        # Convert dicts to 2D array (objects for mixed types)
        n_features = len(self._feature_names)
        X_arr = np.empty((len(X), n_features), dtype=object)
        for i, row in enumerate(X):
            for j, fname in enumerate(self._feature_names):
                X_arr[i, j] = row.get(fname, 0.0 if fname not in self._cat_feature_names else "__NONE__")

        y_arr = np.array(y, dtype=np.int32)

        # Train/val split (80/20)
        val_frac = float(getattr(settings, "ESPORTS_VALIDATION_SPLIT", 0.2))
        split_idx = int(len(X_arr) * (1 - val_frac))
        X_train, X_val = X_arr[:split_idx], X_arr[split_idx:]
        y_train, y_val = y_arr[:split_idx], y_arr[split_idx:]

        if len(X_val) < 10:
            return {"error": "insufficient validation data", "graduated": False}

        # CatBoost parameters
        iterations = int(getattr(settings, "ESPORTS_CATBOOST_ITERATIONS", 500))
        depth = int(getattr(settings, "ESPORTS_CATBOOST_DEPTH", 6))
        lr = float(getattr(settings, "ESPORTS_CATBOOST_LR", 0.05))

        train_pool = Pool(X_train, y_train, cat_features=self._cat_feature_indices,
                          feature_names=self._feature_names)
        val_pool = Pool(X_val, y_val, cat_features=self._cat_feature_indices,
                        feature_names=self._feature_names)

        model = CatBoostClassifier(
            iterations=iterations,
            depth=depth,
            learning_rate=lr,
            l2_leaf_reg=3.0,
            eval_metric="Logloss",
            early_stopping_rounds=50,
            verbose=0,
            random_seed=42,
            auto_class_weights="Balanced",
        )

        model.fit(train_pool, eval_set=val_pool, use_best_model=True)

        # Evaluate
        probs = model.predict_proba(val_pool)[:, 1]
        preds = (probs > 0.5).astype(int)
        accuracy = float((preds == y_val).mean())
        brier = float(((probs - y_val.astype(float)) ** 2).mean())
        logloss = float(model.get_best_score()["validation"]["Logloss"])

        graduated = accuracy >= _MIN_ACCURACY and brier < _MAX_BRIER

        self._model = model
        self._fitted = True
        self._train_metrics = {
            "accuracy": round(accuracy, 4),
            "brier": round(brier, 4),
            "logloss": round(logloss, 4),
            "n_samples": len(X),
            "n_train": len(X_train),
            "n_val": len(X_val),
            "graduated": graduated,
            "game": self.game,
        }

        logger.info(
            "catboost_draft_trained",
            game=self.game,
            accuracy=round(accuracy, 4),
            brier=round(brier, 4),
            logloss=round(logloss, 4),
            graduated=graduated,
            n_samples=len(X),
        )

        return dict(self._train_metrics)

    def predict_proba(self, features: Dict[str, Any]) -> float:
        """Return P(team_a_wins) from single feature dict.

        Returns 0.5 if not fitted (no-information prior).
        """
        if not self._fitted or self._model is None:
            return 0.5

        try:
            from catboost import Pool

            X_arr = np.empty((1, len(self._feature_names)), dtype=object)
            for j, fname in enumerate(self._feature_names):
                X_arr[0, j] = features.get(fname, 0.0 if fname not in self._cat_feature_names else "__NONE__")

            pool = Pool(X_arr, cat_features=self._cat_feature_indices,
                        feature_names=self._feature_names)
            probs = self._model.predict_proba(pool)
            return float(probs[0][1])
        except Exception as exc:
            logger.debug("catboost_predict_failed", game=self.game, error=str(exc))
            return 0.5

    def save(self, path: str) -> None:
        """Save model to path using CatBoost native format."""
        if self._model is None:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._model.save_model(path)
            logger.info("catboost_draft_saved", game=self.game, path=path)
        except Exception as exc:
            logger.warning("catboost_save_failed", game=self.game, error=str(exc))

    def load(self, path: str) -> bool:
        """Load model from path. Returns True if successful."""
        if not os.path.exists(path):
            return False
        try:
            from catboost import CatBoostClassifier
            model = CatBoostClassifier()
            model.load_model(path)
            self._model = model
            self._fitted = True
            # Recover feature names from model
            self._feature_names = model.feature_names_ if hasattr(model, "feature_names_") else []
            # Recover cat feature indices
            if hasattr(model, "get_cat_feature_indices"):
                self._cat_feature_indices = list(model.get_cat_feature_indices())
                if self._feature_names:
                    self._cat_feature_names = [
                        self._feature_names[i] for i in self._cat_feature_indices
                        if i < len(self._feature_names)
                    ]
            logger.info("catboost_draft_loaded", game=self.game, path=path)
            return True
        except Exception as exc:
            logger.warning("catboost_load_failed", game=self.game, error=str(exc))
            return False
