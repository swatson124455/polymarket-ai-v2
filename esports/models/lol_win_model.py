"""
LoL Win Probability Model — XGBoost with 17 features (12 Riot/AWS + 5 role-weighted gold).

Based on the official Riot/AWS model deployed for LoL Worlds 2023+.
Trained on professional match timeline data with patch-weighted sampling.

Features (17 total):
  12 Riot/AWS production features:
    game_time, gold_pct, team_xp, alive_count, tower_kills,
    dragon_kills, dragon_soul, herald, inhib_down_count,
    baron_buff_timer, elder_buff_timer, baron_buff_count, elder_buff_count

  5 role-weighted gold decomposition:
    gold_diff_top, gold_diff_jungle, gold_diff_mid, gold_diff_adc, gold_diff_support

Patch-weighted training: current=1.0, prev=0.7, 2-ago=0.5, 3+ ago=0.3.

Usage::
    model = LoLWinModel()
    model.train(historical_matches, current_patch="14.5")
    prob = model.predict(game_state)
"""
from __future__ import annotations

import asyncio
import math
import os
import pickle
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from structlog import get_logger

logger = get_logger()

# Feature names in order — must match training and inference
FEATURE_NAMES = [
    # 12 Riot/AWS features
    "game_time_minutes",
    "gold_pct_blue",           # blue team gold / total gold
    "team_xp_diff",            # blue XP - red XP
    "alive_diff",              # blue alive - red alive (-5 to +5)
    "tower_kills_diff",        # blue towers - red towers
    "dragon_kills_diff",       # blue dragons - red dragons
    "dragon_soul_blue",        # 1.0 if blue has soul, else 0.0
    "herald_blue",             # 1.0 if blue has herald trinket
    "inhib_down_diff",         # blue inhibs down on red - red inhibs down on blue
    "baron_buff_blue",         # 1.0 if blue has baron buff active
    "elder_buff_blue",         # 1.0 if blue has elder buff active
    "baron_buff_count_diff",   # # blue players with baron - # red players with baron
    # 5 role-weighted gold diffs
    "gold_diff_top",
    "gold_diff_jungle",
    "gold_diff_mid",
    "gold_diff_adc",
    "gold_diff_support",
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

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    def predict(self, game_state: Dict[str, Any]) -> float:
        """
        Predict P(blue_team_win) from a game state dict.

        Args:
            game_state: Dict with keys matching FEATURE_NAMES.

        Returns:
            Probability 0.0-1.0. Returns 0.5 if model not trained.
        """
        if not self._is_trained or self._model is None:
            return 0.5

        features = self._extract_features(game_state)
        if features is None:
            return 0.5

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

        model = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )
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

    # ── Feature extraction ──────────────────────────────────────────────

    def _extract_features(self, state: Dict[str, Any]) -> Optional[List[float]]:
        """Extract 17 features from a game state dict."""
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
