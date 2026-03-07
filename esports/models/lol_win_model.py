"""
LoL Win Probability Model — XGBoost with 8 reliable features.

Based on PandaScore professional match data with patch-weighted sampling.
Only uses features reliably available in BOTH training (post-game stats) and
live inference (mid-game PandaScore data). Dead/unreliable features removed.

Features (8 total):
    game_time_minutes, gold_pct_blue, tower_kills_diff, dragon_kills_diff,
    dragon_soul_blue, herald_blue, inhib_down_diff, baron_buff_count_diff

Patch-weighted training: current=1.0, prev=0.7, 2-ago=0.5, 3+ ago=0.3.
Heuristic fallback: gold+tower+dragon sigmoid when model not yet trained.

Usage::
    model = LoLWinModel()
    model.train(historical_matches, current_patch="14.5")
    prob = model.predict(game_state)
"""
from __future__ import annotations

import asyncio
import collections
import math
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from structlog import get_logger

logger = get_logger()

# Feature names in order — must match training and inference.
# Only features reliably available from PandaScore in BOTH training and live.
# Removed (always 0.0 or unreliable): team_xp_diff, alive_diff, elder_buff_blue,
# baron_buff_blue (redundant with count_diff), 5x gold_diff_role.
FEATURE_NAMES = [
    "game_time_minutes",
    "gold_pct_blue",           # blue team gold / total gold (ratio 0.0-1.0)
    "tower_kills_diff",        # blue towers - red towers
    "dragon_kills_diff",       # blue dragons - red dragons
    "matchup_uncertainty",     # (phi_a + phi_b) / 700 — joint rating uncertainty [0-1]
    "rd_asymmetry",            # (phi_a - phi_b) / 350 — which team has less certain rating [-1,1]
    "team_a_volatility",       # sigma_a / 0.06 — team A rating stability (normalized)
    "team_b_volatility",       # sigma_b / 0.06 — team B rating stability (normalized)
    "team_strength_diff",      # expected_score(a,b) - 0.5 from Glicko-2
]

# Patch weight decay: how much to weight historical patches
PATCH_WEIGHTS = {
    0: 1.0,   # current patch
    1: 0.7,   # previous patch
    2: 0.5,   # 2 patches ago
    3: 0.3,   # 3+ patches ago
}

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "esports_lol_model.pkl"
)


class LoLWinModel:
    """
    XGBoost binary classifier for LoL win probability.

    Predicts P(blue_team_win) given a game state snapshot.
    """

    def __init__(self) -> None:
        self._model = None
        self._calibrator = None
        self._is_trained = False
        self._current_patch: Optional[str] = None
        self._training_patches: List[str] = []
        # Adaptive blend: rolling Brier errors for Glicko-2 vs ML components
        self._blend_errors: collections.deque = collections.deque(maxlen=50)

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def predict(self, game_state: Dict[str, Any]) -> float:
        """
        Predict P(blue_team_win) from a game state dict.

        Args:
            game_state: Dict with keys matching FEATURE_NAMES.

        Returns:
            Probability 0.0-1.0. Falls back to heuristic if model not trained.
        """
        if not self._is_trained or self._model is None:
            return self._predict_heuristic(game_state)

        features = self._extract_features(game_state)
        if features is None:
            return self._predict_heuristic(game_state)

        try:
            X = np.array([features], dtype=np.float32)
            # Sanitize NaN/Inf
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            proba = self._model.predict_proba(X)[0][1]  # P(class=1) = P(blue_win)

            # Apply calibration if available
            if self._calibrator is not None:
                proba = self._calibrator.predict_proba(np.array([[proba]]))[0][1]

            return float(np.clip(proba, 0.01, 0.99))
        except Exception as exc:
            logger.debug("LoLWinModel: predict failed", error=str(exc))
            return 0.5

    def predict_with_glicko2(
        self, game_state: Dict[str, Any], glicko2_expected: float
    ) -> float:
        """
        Two-stage prediction: Glicko-2 baseline + ML residual correction.

        The ML model captures what Glicko-2 misses (game state, economy,
        objectives taken). Glicko-2 provides the team-strength baseline.

        Uses adaptive weights when 20+ blend outcomes are tracked,
        otherwise falls back to hardcoded defaults.

        Args:
            game_state: Feature dict for ML model.
            glicko2_expected: P(blue_win) from Glicko-2 ratings.

        Returns:
            Blended probability (0.05-0.95).
        """
        ml_prob = self.predict(game_state)

        # Adaptive weights if enough blend outcomes tracked
        adaptive_ml_weight = self._compute_adaptive_ml_weight()

        game_time = float(game_state.get("game_time_minutes", 0))
        if game_time > 5.0 and self._is_trained:
            # Mid-game: ML has strong in-game signals (gold, towers, dragons)
            ml_weight = adaptive_ml_weight if adaptive_ml_weight is not None else 0.7
        elif self._is_trained:
            # Pre-game: ML has limited signal — scale adaptive weight down
            ml_weight = (adaptive_ml_weight * 0.7) if adaptive_ml_weight is not None else 0.5
        else:
            # Untrained: ML is just heuristic, lean on Glicko-2
            ml_weight = 0.4

        blend = (1 - ml_weight) * glicko2_expected + ml_weight * ml_prob
        return float(max(0.05, min(0.95, blend)))

    def record_blend_outcome(
        self, glicko2_prob: float, ml_prob: float, actual: int
    ) -> None:
        """
        Record a prediction outcome for adaptive blend weight learning.

        Call after each bet resolves. Tracks per-component Brier errors
        to shift weight toward whichever component performs better.
        """
        glicko2_err = (glicko2_prob - actual) ** 2
        ml_err = (ml_prob - actual) ** 2
        self._blend_errors.append((glicko2_err, ml_err))

    def _compute_adaptive_ml_weight(self) -> Optional[float]:
        """
        Compute ML weight from rolling Brier errors.

        Returns None if insufficient data (<20 outcomes).
        Weight bounded [0.2, 0.8] to prevent one component dominating.
        """
        if len(self._blend_errors) < 20:
            return None

        glicko2_brier = sum(e[0] for e in self._blend_errors) / len(self._blend_errors)
        ml_brier = sum(e[1] for e in self._blend_errors) / len(self._blend_errors)

        # Inverse Brier: lower error → higher weight
        # Add epsilon to avoid division by zero
        eps = 1e-6
        glicko2_inv = 1.0 / (glicko2_brier + eps)
        ml_inv = 1.0 / (ml_brier + eps)

        ml_weight = ml_inv / (glicko2_inv + ml_inv)
        return max(0.2, min(0.8, ml_weight))

    async def predict_async(self, game_state: Dict[str, Any]) -> float:
        """Thread-safe async prediction (offloads to thread pool)."""
        return await asyncio.to_thread(self.predict, game_state)

    async def train(
        self,
        training_data: List[Dict[str, Any]],
        current_patch: str = "",
    ) -> bool:
        """
        Train the model on historical match data with patch-weighted sampling.

        Args:
            training_data: List of dicts, each with FEATURE_NAMES keys + 'blue_win' (0/1).
            current_patch: Current patch version string (e.g., '14.5').

        Returns:
            True if training succeeded.
        """
        if len(training_data) < 50:
            logger.warning("LoLWinModel: insufficient training data", count=len(training_data))
            return False

        self._current_patch = current_patch

        try:
            return await asyncio.to_thread(self._train_sync, training_data, current_patch)
        except Exception as exc:
            logger.error("LoLWinModel: training failed", error=str(exc))
            return False

    def _train_sync(self, training_data: List[Dict[str, Any]], current_patch: str) -> bool:
        """Synchronous training (runs in thread pool)."""
        from xgboost import XGBClassifier

        X_rows = []
        y_rows = []
        weights = []

        for row in training_data:
            features = self._extract_features(row)
            if features is None:
                continue

            label = int(row.get("blue_win", 0))
            patch = str(row.get("patch", ""))

            # Compute patch weight
            patch_age = self._compute_patch_age(patch, current_patch)
            weight = PATCH_WEIGHTS.get(patch_age, 0.3)

            X_rows.append(features)
            y_rows.append(label)
            weights.append(weight)

        if len(X_rows) < 50:
            return False

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_rows, dtype=np.int32)
        w = np.array(weights, dtype=np.float32)

        # Sanitize
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # Split 15% for early stopping eval set (from end = most recent)
        es_split = max(int(len(X) * 0.85), 50)
        X_train, X_es = X[:es_split], X[es_split:]
        y_train, y_es = y[:es_split], y[es_split:]
        w_train = w[:es_split]

        # Complexity tuned for current feature set: only team_strength_diff and
        # game_time_minutes have real signal (importance ~0.5 each), all other
        # features are constant after label-leakage neutralization.
        # With 2 effective features, depth=3 prevents overfitting.
        # When live game features become available, increase depth back to 6.
        model = XGBClassifier(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            early_stopping_rounds=20,
        )

        if len(X_es) >= 10:
            model.fit(X_train, y_train, sample_weight=w_train,
                      eval_set=[(X_es, y_es)], verbose=False)
        else:
            model.fit(X, y, sample_weight=w)

        self._model = model
        self._is_trained = True

        # Log feature importances
        importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
        top_5 = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:5]
        logger.info(
            "LoLWinModel: trained",
            n_samples=len(X_rows),
            current_patch=current_patch,
            top_features=[(n, round(v, 3)) for n, v in top_5],
        )

        return True

    def calibrate(self, val_data: List[Dict[str, Any]]) -> bool:
        """
        Calibrate model using isotonic regression on validation data.

        Args:
            val_data: List of dicts with features + 'blue_win'.

        Returns:
            True if calibration succeeded.
        """
        if not self._is_trained or not val_data:
            return False

        try:
            from sklearn.calibration import CalibratedClassifierCV

            X_rows = []
            y_rows = []
            for row in val_data:
                features = self._extract_features(row)
                if features is None:
                    continue
                X_rows.append(features)
                y_rows.append(int(row.get("blue_win", 0)))

            if len(X_rows) < 20:
                return False

            X = np.array(X_rows, dtype=np.float32)
            y = np.array(y_rows, dtype=np.int32)
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

            self._calibrator = CalibratedClassifierCV(
                self._model, method="isotonic", cv="prefit"
            )
            self._calibrator.fit(X, y)
            logger.info("LoLWinModel: calibrated", n_samples=len(X_rows))
            return True
        except Exception as exc:
            logger.debug("LoLWinModel: calibration failed", error=str(exc))
            return False

    def save(self, path: Optional[str] = None) -> bool:
        """Save model to disk."""
        path = path or MODEL_PATH
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump({
                    "model": self._model,
                    "calibrator": self._calibrator,
                    "current_patch": self._current_patch,
                    "training_patches": self._training_patches,
                }, f)
            logger.info("LoLWinModel: saved", path=path)
            return True
        except Exception as exc:
            logger.warning("LoLWinModel: save failed", error=str(exc))
            return False

    def load(self, path: Optional[str] = None) -> bool:
        """Load model from disk."""
        path = path or MODEL_PATH
        try:
            if not os.path.exists(path):
                return False
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._model = data.get("model")
            self._calibrator = data.get("calibrator")
            self._current_patch = data.get("current_patch")
            self._training_patches = data.get("training_patches", [])
            self._is_trained = self._model is not None
            logger.info("LoLWinModel: loaded", path=path, patch=self._current_patch)
            return self._is_trained
        except Exception as exc:
            logger.warning("LoLWinModel: load failed", error=str(exc))
            return False

    # ── Heuristic fallback ─────────────────────────────────────────────

    @staticmethod
    def _predict_heuristic(game_state: Dict[str, Any]) -> float:
        """Heuristic win probability when ML model is not trained.

        Uses gold share + tower/dragon advantages + team strength + matchup
        uncertainty as logistic regression. Coefficients based on professional
        match analysis. Matchup uncertainty dampens the prediction toward 0.5
        when Glicko-2 ratings are uncertain.
        """
        gold_pct = float(game_state.get("gold_pct_blue", 0.5))
        tower_diff = float(game_state.get("tower_kills_diff", 0.0))
        dragon_diff = float(game_state.get("dragon_kills_diff", 0.0))
        team_str = float(game_state.get("team_strength_diff", 0.0))
        uncertainty = float(game_state.get("matchup_uncertainty", 0.5))
        # Dampen team_str signal when matchup uncertainty is high:
        # uncertainty ≈ 1.0 means both teams at default RD → halve the signal
        # uncertainty ≈ 0.15 means both well-rated → full signal
        certainty_weight = max(0.3, 1.0 - 0.7 * uncertainty)
        z = (8.0 * (gold_pct - 0.5) + 0.08 * tower_diff + 0.06 * dragon_diff
             + 2.0 * team_str * certainty_weight)
        prob = 1.0 / (1.0 + math.exp(-z))
        return float(max(0.05, min(0.95, prob)))

    # ── Feature extraction ──────────────────────────────────────────────

    def _extract_features(self, state: Dict[str, Any]) -> Optional[List[float]]:
        """Extract 9 features from a game state dict."""
        try:
            features = []
            for name in FEATURE_NAMES:
                val = state.get(name, 0.0)
                features.append(float(val) if val is not None else 0.0)
            return features
        except Exception:
            return None

    @staticmethod
    def _compute_patch_age(patch: str, current_patch: str) -> int:
        """Compute how many patches old a given patch version is."""
        if not patch or not current_patch:
            return 3  # Unknown → oldest weight

        try:
            # Parse major.minor from patch strings like "14.5.1"
            cur_parts = current_patch.split(".")
            old_parts = patch.split(".")
            cur_num = int(cur_parts[0]) * 100 + int(cur_parts[1])
            old_num = int(old_parts[0]) * 100 + int(old_parts[1])
            age = cur_num - old_num
            return min(max(age, 0), 3)
        except (ValueError, IndexError):
            return 3

    @staticmethod
    def build_game_state_from_timeline(frame: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a feature dict from a Riot API timeline frame.

        This is a helper for converting raw timeline data into the format
        expected by predict(). Production version would parse the full
        Riot timeline JSON.
        """
        # Template — caller fills in actual values from Riot API response
        return {name: frame.get(name, 0.0) for name in FEATURE_NAMES}
