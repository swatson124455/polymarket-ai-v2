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

    def __init__(self) -> None:
        self._round_model = None
        self._is_trained = False

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    # ── Tier 1: Round probability ───────────────────────────────────────

    def predict_round(self, round_state: Dict[str, Any]) -> float:
        """
        Predict P(team_a wins this round) from economy + game state.

        If ML model not trained, uses heuristic based on equipment value diff.
        """
        if self._round_model is not None and self._is_trained:
            return self._predict_round_ml(round_state)
        return self._predict_round_heuristic(round_state)

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
            features = [float(state.get(f, 0.0)) for f in ROUND_FEATURES]
            X = np.array([features], dtype=np.float32)
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
            proba = self._round_model.predict_proba(X)[0][1]
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
        total_remaining = remaining_a + remaining_b - 1  # one team wins before all are played

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

        # Average remaining map probabilities for this projection
        remaining_probs = map_probs[maps_won_a + maps_won_b:]
        if remaining_probs:
            avg_prob = sum(remaining_probs) / len(remaining_probs)
        else:
            avg_prob = 0.5

        return self._binomial_race(avg_prob, remaining_a, remaining_b)

    # ── Training ────────────────────────────────────────────────────────

    async def train(self, training_data: List[Dict[str, Any]]) -> bool:
        """Train the round-level XGBoost model on historical round data."""
        if len(training_data) < 100:
            logger.warning("CS2EconomyModel: insufficient data", count=len(training_data))
            return False

        try:
            return await asyncio.to_thread(self._train_sync, training_data)
        except Exception as exc:
            logger.error("CS2EconomyModel: training failed", error=str(exc))
            return False

    def _train_sync(self, training_data: List[Dict[str, Any]]) -> bool:
        """Synchronous training (runs in thread pool)."""
        from xgboost import XGBClassifier

        X_rows = []
        y_rows = []

        for row in training_data:
            features = [float(row.get(f, 0.0)) for f in ROUND_FEATURES]
            label = int(row.get("team_a_won_round", 0))
            X_rows.append(features)
            y_rows.append(label)

        X = np.array(X_rows, dtype=np.float32)
        y = np.array(y_rows, dtype=np.int32)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        model = XGBClassifier(
            n_estimators=150,
            max_depth=5,
            learning_rate=0.1,
            subsample=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X, y)

        self._round_model = model
        self._is_trained = True
        logger.info("CS2EconomyModel: trained", n_samples=len(X_rows))
        return True

    def save(self, path: Optional[str] = None) -> bool:
        path = path or MODEL_PATH
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump({"round_model": self._round_model}, f)
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
            self._is_trained = self._round_model is not None
            return self._is_trained
        except Exception as exc:
            logger.warning("CS2EconomyModel: load failed", error=str(exc))
            return False

    @staticmethod
    def classify_buy(money: float) -> str:
        """Classify team's buy quality from their money."""
        if money >= FULL_BUY_THRESHOLD:
            return "full"
        elif money >= FORCE_BUY_THRESHOLD:
            return "force"
        return "eco"

    @staticmethod
    def projected_loss_bonus(consecutive_losses: int) -> int:
        """Calculate loss bonus income for next round."""
        idx = min(consecutive_losses, len(LOSS_BONUS) - 1)
        return LOSS_BONUS[idx] if consecutive_losses > 0 else 0
