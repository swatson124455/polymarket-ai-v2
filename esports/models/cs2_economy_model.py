"""
CS2 Economy Model — 3-tier probability chain: round → map → match.

Counter-Strike 2 is round-based. The chain is:
  economy state → round win probability → map win probability → match win probability

Key features:
  - Team money + equipment value (buy quality: full/force/eco)
  - Round score + half (CT/T win rates vary by map)
  - Loss streak bonus ($1,400 → $3,400 over 5 rounds)
  - Players alive (mid-round)
  - Bomb state (planted/defused)
  - Map name (CT/T rates: Nuke 57% CT, Dust2 52% T)

Usage::
    model = CS2EconomyModel()
    round_prob = model.predict_round(round_state)
    map_prob = model.predict_map(map_state)
    match_prob = model.predict_match(match_state)
"""
from __future__ import annotations

import asyncio
import collections
import math
import os
import pickle
from typing import Any, Dict, List, Optional

import numpy as np
from structlog import get_logger

logger = get_logger()

# CS2 economy constants
FULL_BUY_THRESHOLD = 4500   # Full buy: rifles + utility
FORCE_BUY_THRESHOLD = 2000  # Force buy: SMGs/shotguns + some utility
ECO_THRESHOLD = 0            # Eco: pistols only

# Loss bonus escalation: $1,400 base + $500 per consecutive loss, cap $3,400
LOSS_BONUS = [1400, 1900, 2400, 2900, 3400]

# Default map CT/T win rates (professional average)
MAP_SIDE_RATES = {
    "nuke":    {"ct": 0.57, "t": 0.43},
    "ancient": {"ct": 0.55, "t": 0.45},
    "anubis":  {"ct": 0.54, "t": 0.46},
    "vertigo": {"ct": 0.54, "t": 0.46},
    "inferno": {"ct": 0.53, "t": 0.47},
    "mirage":  {"ct": 0.52, "t": 0.48},
    "dust2":   {"ct": 0.48, "t": 0.52},
}

# Round model feature names
ROUND_FEATURES = [
    "team_a_money",
    "team_b_money",
    "team_a_equip_value",
    "team_b_equip_value",
    "round_score_a",
    "round_score_b",
    "map_ct_rate",      # CT win rate for current map
    "team_a_is_ct",     # 1.0 if team A is CT side
    "team_a_loss_streak",
    "team_b_loss_streak",
    "bomb_planted",     # 1.0 if bomb is currently planted
    "team_a_alive",
    "team_b_alive",
    "team_strength_diff",  # team_a win_rate - team_b win_rate (from PandaScore)
]

# Sensible defaults for features missing at live inference time.
# Critical: team_a_alive/team_b_alive default to 5.0 (pre-round, all alive)
# instead of 0.0, which would invert the signal vs training data.
_ROUND_FEATURE_DEFAULTS = {
    "team_a_money": 0.0,
    "team_b_money": 0.0,
    "team_a_equip_value": 0.0,
    "team_b_equip_value": 0.0,
    "round_score_a": 0.0,
    "round_score_b": 0.0,
    "map_ct_rate": 0.50,
    "team_a_is_ct": 1.0,
    "team_a_loss_streak": 0.0,
    "team_b_loss_streak": 0.0,
    "bomb_planted": 0.0,
    "team_a_alive": 5.0,
    "team_b_alive": 5.0,
    "team_strength_diff": 0.0,
}

PREGAME_FEATURES = [
    "team_strength_diff",
    "matchup_uncertainty",
    "rd_asymmetry",
    "team_a_volatility",
    "team_b_volatility",
    "best_of",
]

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "esports_cs2_model.pkl"
)


class CS2EconomyModel:
    """
    3-tier CS2 match probability model.

    Tier 1 (Round): Given economy + score, P(team_a wins this round)
    Tier 2 (Map): Given round probs + score, P(team_a wins this map)
    Tier 3 (Match): Given map probs + series score, P(team_a wins match)
    """

    FEATURE_NAMES = PREGAME_FEATURES  # For _onnx_predict_game compatibility

    def __init__(self) -> None:
        self._round_model = None
        self._pregame_model = None  # XGBoost on 6 Glicko-2 features (pre-game)
        self._calibrator = None  # IsotonicRegression for probability calibration
        self._is_trained = False
        # Adaptive blend: rolling Brier errors for Glicko-2 vs ML components
        self._blend_errors: collections.deque = collections.deque(maxlen=50)

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    # ── Pre-game: Glicko-2 features (no economy data) ─────────────────

    def predict_pregame(self, game_state: Dict[str, Any]) -> float:
        """Predict P(team_a wins match) from Glicko-2 features (pre-game).

        Uses same 6-feature pattern as Valorant/Dota2 models.
        Falls back to logistic heuristic when pregame model not trained.
        """
        if self._pregame_model is not None:
            try:
                features = [float(game_state.get(f, 0.0)) for f in PREGAME_FEATURES]
                X = np.array([features], dtype=np.float32)
                X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
                proba = self._pregame_model.predict_proba(X)[0][1]
                return float(np.clip(proba, 0.05, 0.95))
            except Exception:
                pass
        return self._predict_pregame_heuristic(game_state)

    @staticmethod
    def _predict_pregame_heuristic(game_state: Dict[str, Any]) -> float:
        """Logistic heuristic on team_strength_diff (mirrors Valorant/Dota2)."""
        team_str = float(game_state.get("team_strength_diff", 0.0))
        uncertainty = float(game_state.get("matchup_uncertainty", 0.5))
        certainty_weight = max(0.3, 1.0 - 0.7 * uncertainty)
        z = 2.0 * team_str * certainty_weight
        prob = 1.0 / (1.0 + math.exp(-z))
        return float(max(0.05, min(0.95, prob)))

    def train_pregame(self, training_data: List[Dict[str, Any]]) -> bool:
        """Train pregame XGBoost on 6 Glicko-2 features (match-level)."""
        if len(training_data) < 50:
            return False
        try:
            from xgboost import XGBClassifier

            X_rows, y_rows = [], []
            for row in training_data:
                features = [float(row.get(f, 0.0)) for f in PREGAME_FEATURES]
                label = int(row.get("team_a_won_round", row.get("team_a_won", 0)))
                X_rows.append(features)
                y_rows.append(label)

            if len(X_rows) < 50:
                return False

            X = np.nan_to_num(np.array(X_rows, dtype=np.float32), nan=0.0)
            y = np.array(y_rows, dtype=np.int32)

            es_split = max(int(len(X) * 0.85), 50)
            X_train, X_es = X[:es_split], X[es_split:]
            y_train, y_es = y[:es_split], y[es_split:]

            model = XGBClassifier(
                n_estimators=60, max_depth=2, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8,
                eval_metric="logloss", random_state=42,
                early_stopping_rounds=20,
            )
            if len(X_es) >= 10:
                model.fit(X_train, y_train, eval_set=[(X_es, y_es)], verbose=False)
            else:
                model.fit(X, y)

            self._pregame_model = model
            importances = dict(zip(PREGAME_FEATURES, model.feature_importances_))
            top = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:3]
            logger.info("CS2EconomyModel: pregame trained", n_samples=len(X_rows),
                        top_features=[(n, round(v, 3)) for n, v in top])
            return True
        except Exception as exc:
            logger.error("CS2EconomyModel: pregame training failed", error=str(exc))
            return False

    # ── Tier 1: Round probability ───────────────────────────────────────

    def predict_round(self, round_state: Dict[str, Any]) -> float:
        """
        Predict P(team_a wins this round) from economy + game state.

        If ML model not trained, uses heuristic based on equipment value diff.
        """
        if self._round_model is not None and self._is_trained:
            return self._predict_round_ml(round_state)
        return self._predict_round_heuristic(round_state)

    def predict_round_with_glicko2(
        self, round_state: Dict[str, Any], glicko2_expected: float
    ) -> float:
        """
        Two-stage round prediction: Glicko-2 baseline + ML/heuristic correction.

        For CS2 rounds, the economy model captures round-specific economy state
        while Glicko-2 provides the team-strength baseline.
        Uses adaptive weights when 20+ blend outcomes are tracked.

        Args:
            round_state: Round economy + game state features.
            glicko2_expected: P(team_a wins round) from Glicko-2 ratings.

        Returns:
            Blended probability (0.05-0.95).
        """
        round_prob = self.predict_round(round_state)

        # Adaptive weights if enough blend outcomes tracked
        adaptive_ml_weight = self._compute_adaptive_ml_weight()

        # CS2 rounds are heavily economy-dependent, ML/heuristic gets more weight
        if self._is_trained:
            ml_weight = adaptive_ml_weight if adaptive_ml_weight is not None else 0.7
        else:
            ml_weight = 0.6

        blend = (1 - ml_weight) * glicko2_expected + ml_weight * round_prob
        return float(np.clip(blend, 0.05, 0.95))

    def record_blend_outcome(
        self, glicko2_prob: float, ml_prob: float, actual: int
    ) -> None:
        """
        Record a prediction outcome for adaptive blend weight learning.

        Call after each round resolves. Tracks per-component Brier errors
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
        eps = 1e-6
        glicko2_inv = 1.0 / (glicko2_brier + eps)
        ml_inv = 1.0 / (ml_brier + eps)

        ml_weight = ml_inv / (glicko2_inv + ml_inv)
        return max(0.2, min(0.8, ml_weight))

    def _predict_round_heuristic(self, state: Dict[str, Any]) -> float:
        """Heuristic round probability based on economy and numbers."""
        equip_a = float(state.get("team_a_equip_value", 0))
        equip_b = float(state.get("team_b_equip_value", 0))
        alive_a = int(state.get("team_a_alive", 5))
        alive_b = int(state.get("team_b_alive", 5))
        map_name = str(state.get("map_name", "")).lower()
        team_a_is_ct = bool(state.get("team_a_is_ct", True))
        bomb_planted = bool(state.get("bomb_planted", False))

        # Base: map-specific side advantage
        side_rates = MAP_SIDE_RATES.get(map_name, {"ct": 0.50, "t": 0.50})
        if team_a_is_ct:
            base_prob = side_rates["ct"]
        else:
            base_prob = side_rates["t"]

        # Equipment advantage: sigmoid of normalised diff
        total_equip = equip_a + equip_b
        if total_equip > 0:
            equip_advantage = (equip_a - equip_b) / total_equip
            equip_factor = 1 / (1 + math.exp(-3 * equip_advantage))
            base_prob = 0.3 * base_prob + 0.7 * equip_factor

        # Numbers advantage (mid-round)
        if alive_a + alive_b < 10:  # Mid-round, some players dead
            if alive_a > alive_b:
                base_prob += 0.10 * (alive_a - alive_b)
            elif alive_b > alive_a:
                base_prob -= 0.10 * (alive_b - alive_a)

        # Bomb planted: favours T side
        if bomb_planted:
            if not team_a_is_ct:
                base_prob += 0.10  # Team A is T and planted
            else:
                base_prob -= 0.10  # Team A is CT and must defuse

        return float(np.clip(base_prob, 0.05, 0.95))

    def _predict_round_ml(self, state: Dict[str, Any]) -> float:
        """ML-based round probability using trained XGBoost model."""
        try:
            features = [float(state.get(f, _ROUND_FEATURE_DEFAULTS.get(f, 0.0))) for f in ROUND_FEATURES]
            X = np.array([features], dtype=np.float32)
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            proba = self._round_model.predict_proba(X)[0][1]

            # Apply isotonic calibration if available
            if self._calibrator is not None:
                proba = float(self._calibrator.predict([proba])[0])

            return float(np.clip(proba, 0.05, 0.95))
        except Exception:
            return self._predict_round_heuristic(state)

    # ── Tier 2: Map probability ─────────────────────────────────────────

    def predict_map(self, map_state: Dict[str, Any]) -> float:
        """
        Predict P(team_a wins this map) from current score + economy trajectory.

        Uses the current round score and projected economy to estimate
        remaining round win rates, then computes map probability.
        """
        score_a = int(map_state.get("score_a", 0))
        score_b = int(map_state.get("score_b", 0))
        map_name = str(map_state.get("map_name", "")).lower()
        team_a_is_ct = bool(map_state.get("team_a_is_ct", True))

        # Get per-round win probability for current half
        round_prob = self.predict_round(map_state)

        # Rounds needed to win: first to 13 (regulation), OT at 12-12
        rounds_to_win = 13
        remaining_a = rounds_to_win - score_a
        remaining_b = rounds_to_win - score_b

        if remaining_a <= 0:
            return 1.0  # Team A already won
        if remaining_b <= 0:
            return 0.0  # Team B already won

        # Half-awareness: at halftime (12 rounds), sides switch
        # Remaining rounds = rounds until 13
        # Binomial-like model: P(team_a wins K more before team_b wins L more)
        return self._binomial_race(round_prob, remaining_a, remaining_b)

    @staticmethod
    def _binomial_race(p: float, needs_a: int, needs_b: int) -> float:
        """
        Probability that A wins a race: first to win needs_a rounds,
        when A wins each round with probability p.

        Uses negative binomial / recursive formula.
        """
        if needs_a <= 0:
            return 1.0
        if needs_b <= 0:
            return 0.0

        # Dynamic programming approach
        # dp[i][j] = P(A wins | A needs i more, B needs j more)
        dp = {}

        def solve(a_needs: int, b_needs: int) -> float:
            if a_needs <= 0:
                return 1.0
            if b_needs <= 0:
                return 0.0
            if (a_needs, b_needs) in dp:
                return dp[(a_needs, b_needs)]

            result = p * solve(a_needs - 1, b_needs) + (1 - p) * solve(a_needs, b_needs - 1)
            dp[(a_needs, b_needs)] = result
            return result

        return solve(needs_a, needs_b)

    @staticmethod
    def _heterogeneous_series_prob(
        map_probs: list, needs_a: int, needs_b: int
    ) -> float:
        """
        Series win probability with per-map heterogeneous win probabilities.

        Unlike _binomial_race (uniform p), each remaining map has its own
        probability. Recursive with memoization.
        """
        memo: dict = {}

        def solve(map_idx: int, a_needs: int, b_needs: int) -> float:
            if a_needs <= 0:
                return 1.0
            if b_needs <= 0:
                return 0.0
            if map_idx >= len(map_probs):
                return 0.5  # Ran out of maps — tiebreaker
            key = (map_idx, a_needs, b_needs)
            if key in memo:
                return memo[key]
            p = map_probs[map_idx]
            result = (p * solve(map_idx + 1, a_needs - 1, b_needs) +
                      (1 - p) * solve(map_idx + 1, a_needs, b_needs - 1))
            memo[key] = result
            return result

        return solve(0, needs_a, needs_b)

    # ── Tier 3: Match (series) probability ──────────────────────────────

    def predict_match(self, match_state: Dict[str, Any]) -> float:
        """
        Predict P(team_a wins the match/series).

        For BO1: equivalent to map probability.
        For BO3/BO5: uses per-map probabilities weighted by map picks.
        """
        best_of = int(match_state.get("best_of", 1))
        maps_won_a = int(match_state.get("maps_won_a", 0))
        maps_won_b = int(match_state.get("maps_won_b", 0))

        if best_of <= 1:
            return self.predict_map(match_state)

        # Per-map probabilities (if available)
        map_probs = match_state.get("map_probabilities", [])
        if not map_probs:
            # Use uniform probability based on current round prediction
            base_map_prob = self.predict_map(match_state)
            maps_needed = (best_of // 2) + 1
            return self._binomial_race(base_map_prob, maps_needed - maps_won_a, maps_needed - maps_won_b)

        # With map-specific probabilities, compute conditional match prob
        maps_needed = (best_of // 2) + 1
        remaining_a = maps_needed - maps_won_a
        remaining_b = maps_needed - maps_won_b

        if remaining_a <= 0:
            return 1.0
        if remaining_b <= 0:
            return 0.0

        # Use per-map heterogeneous probabilities (not averaged)
        remaining_probs = map_probs[maps_won_a + maps_won_b:]
        if not remaining_probs:
            return 0.5

        return self._heterogeneous_series_prob(remaining_probs, remaining_a, remaining_b)

    # ── Training ────────────────────────────────────────────────────────

    async def train(
        self, training_data: List[Dict[str, Any]], val_data: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """Train the round-level XGBoost model on historical round data."""
        if len(training_data) < 100:
            logger.warning("CS2EconomyModel: insufficient data", count=len(training_data))
            return False

        try:
            return await asyncio.to_thread(self._train_sync, training_data, val_data)
        except Exception as exc:
            logger.error("CS2EconomyModel: training failed", error=str(exc))
            return False

    def _train_sync(
        self, training_data: List[Dict[str, Any]], val_data: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """Synchronous training (runs in thread pool)."""
        from xgboost import XGBClassifier

        X_rows = []
        y_rows = []

        for row in training_data:
            features = [float(row.get(f, _ROUND_FEATURE_DEFAULTS.get(f, 0.0))) for f in ROUND_FEATURES]
            label = int(row.get("team_a_won_round", 0))
            X_rows.append(features)
            y_rows.append(label)

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_rows, dtype=np.int32)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # Build eval set for early stopping from val_data (if provided)
        eval_set = None
        if val_data and len(val_data) >= 20:
            X_val_rows = []
            y_val_rows = []
            for row in val_data:
                features = [float(row.get(f, _ROUND_FEATURE_DEFAULTS.get(f, 0.0))) for f in ROUND_FEATURES]
                X_val_rows.append(features)
                y_val_rows.append(int(row.get("team_a_won_round", 0)))
            X_val = np.nan_to_num(np.array(X_val_rows, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
            y_val = np.array(y_val_rows, dtype=np.int32)
            eval_set = [(X_val, y_val)]

        # Complexity tuned for current feature set: only team_strength_diff has
        # real signal (importance=1.0), all other features are constant placeholders.
        # With 1 effective feature, depth=3 (8 leaves) prevents overfitting a 1D
        # function — depth=5 (32 leaves) memorized training noise and hurt calibration.
        # When live economy features become available, increase depth back to 5.
        model = XGBClassifier(
            n_estimators=80,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            eval_metric="logloss",
            random_state=42,
            early_stopping_rounds=20 if eval_set else None,
        )

        if eval_set:
            model.fit(X, y, eval_set=eval_set, verbose=False)
        else:
            model.fit(X, y)

        self._round_model = model
        self._is_trained = True

        # Log feature importances (matches LoL pattern)
        importances = dict(zip(ROUND_FEATURES, model.feature_importances_))
        sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
        logger.info(
            "CS2EconomyModel: trained",
            n_samples=len(X_rows),
            top_features=[(n, round(v, 3)) for n, v in sorted_imp[:5]],
            bottom_features=[(n, round(v, 3)) for n, v in sorted_imp[-3:]],
        )
        return True

    def calibrate(self, val_data: List[Dict[str, Any]]) -> bool:
        """
        Calibrate round model using isotonic regression on validation data.

        Collects raw model probabilities and actual outcomes, then fits
        an isotonic regression to map raw probs → calibrated probs.

        Args:
            val_data: List of dicts with round features + 'team_a_won_round'.

        Returns:
            True if calibration succeeded.
        """
        if not self._is_trained or not val_data:
            return False

        try:
            from sklearn.isotonic import IsotonicRegression

            # Disable any existing calibrator to collect raw probs
            old_calibrator = self._calibrator
            self._calibrator = None

            raw_probs = []
            actuals = []
            for row in val_data:
                prob = self._predict_round_ml(row)
                actual = int(row.get("team_a_won_round", 0))
                raw_probs.append(prob)
                actuals.append(actual)

            if len(raw_probs) < 20:
                self._calibrator = old_calibrator
                return False

            self._calibrator = IsotonicRegression(out_of_bounds="clip")
            self._calibrator.fit(raw_probs, actuals)
            logger.info("CS2EconomyModel: calibrated", n_samples=len(raw_probs))
            return True
        except Exception as exc:
            logger.debug("CS2EconomyModel: calibration failed", error=str(exc))
            self._calibrator = old_calibrator
            return False

    def save(self, path: Optional[str] = None) -> bool:
        path = path or MODEL_PATH
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump({
                    "round_model": self._round_model,
                    "calibrator": self._calibrator,
                    "pregame_model": self._pregame_model,
                }, f)
            return True
        except Exception as exc:
            logger.warning("CS2EconomyModel: save failed", error=str(exc))
            return False

    def load(self, path: Optional[str] = None) -> bool:
        path = path or MODEL_PATH
        try:
            if not os.path.exists(path):
                return False
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._round_model = data.get("round_model")
            self._calibrator = data.get("calibrator")
            self._pregame_model = data.get("pregame_model")
            self._is_trained = self._round_model is not None
            return self._is_trained
        except Exception as exc:
            logger.warning("CS2EconomyModel: load failed", error=str(exc))
            return False

