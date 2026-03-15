"""
TabPFN v2 ensemble for sparse-data esports games.

SC2, RL, CoD, and R6 have insufficient training data for XGBoost.
TabPFN is a pre-trained transformer that works well with <1000 samples,
making it ideal for these games where Glicko-2 is currently the only signal.

Usage: Blended with Glicko-2 at 30/70 weight (TabPFN/Glicko-2).
Graceful degradation: if tabpfn is not installed, returns None.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Games that benefit from TabPFN (insufficient XGB training data)
SPARSE_GAMES = {"sc2", "rl", "cod", "r6"}


class TabPFNEnsemble:
    """TabPFN ensemble predictor for sparse esports games.

    Wraps TabPFN v2 classifier with game-specific fitting.
    Falls back gracefully when tabpfn is not installed.
    """

    def __init__(self):
        self._classifiers: Dict[str, Any] = {}  # game → fitted TabPFNClassifier
        self._available = False
        self._min_samples = 20  # Minimum samples to fit

        try:
            from tabpfn import TabPFNClassifier
            self._available = True
            logger.info("TabPFNEnsemble: tabpfn available")
        except ImportError:
            logger.debug("TabPFNEnsemble: tabpfn not installed (pip install tabpfn)")

    @property
    def is_available(self) -> bool:
        return self._available

    def fit_game(self, game: str, X: np.ndarray, y: np.ndarray) -> bool:
        """Fit TabPFN for a specific game.

        Args:
            game: Game identifier (sc2, rl, cod, r6).
            X: Feature array (n_samples, n_features). Uses Glicko-2 features.
            y: Labels (0/1).

        Returns:
            True if fitting succeeded.
        """
        if not self._available:
            return False
        if game not in SPARSE_GAMES:
            return False
        if len(X) < self._min_samples:
            logger.debug("TabPFN: insufficient data for %s (%d samples)", game, len(X))
            return False

        try:
            from tabpfn import TabPFNClassifier

            clf = TabPFNClassifier(
                N_ensemble_configurations=16,  # Balance speed vs accuracy
            )
            clf.fit(X, y)
            self._classifiers[game] = clf
            logger.info("TabPFN: fitted for %s (%d samples)", game, len(X))
            return True
        except Exception as e:
            logger.debug("TabPFN: fit failed for %s: %s", game, e)
            return False

    def predict(self, game: str, game_state: Dict[str, float]) -> Optional[float]:
        """Predict P(team_a_wins) using TabPFN for a sparse game.

        Args:
            game: Game identifier.
            game_state: Feature dict with Glicko-2 features.

        Returns:
            Probability or None if not fitted/available.
        """
        clf = self._classifiers.get(game)
        if clf is None:
            return None

        try:
            # Build feature array from game_state (6 Glicko-2 features)
            _FEATURES = [
                "team_strength_diff", "matchup_uncertainty", "rd_asymmetry",
                "team_a_volatility", "team_b_volatility", "best_of",
            ]
            feats = [float(game_state.get(f, 0.0)) for f in _FEATURES]
            X = np.array([feats], dtype=np.float32)
            prob = float(clf.predict_proba(X)[0, 1])
            return max(0.05, min(0.95, prob))
        except Exception as e:
            logger.debug("TabPFN: predict failed for %s: %s", game, e)
            return None

    def is_fitted(self, game: str) -> bool:
        return game in self._classifiers

    @staticmethod
    def get_blend_weight() -> float:
        """TabPFN blend weight when blending with Glicko-2.

        Conservative: 30% TabPFN, 70% Glicko-2.
        TabPFN is pre-trained but not domain-specific.
        """
        return 0.3
