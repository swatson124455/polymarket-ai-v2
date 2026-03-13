"""
Le (2026) Domain-Specific Bias Decomposition for Esports.

Decomposes prediction calibration error per game (LoL, CS2, Dota2, etc.)
into systematic bias components. Computes per-game recalibration parameter b
from resolved esports_predictions data.

Reference: Le (2026), arXiv:2602.19520 -- 87.3% of calibration variance explained.
Adapted from cross-platform (Polymarket/Kalshi) to per-game esports decomposition.
"""

import copy
from typing import Dict, List, Optional

import numpy as np
from scipy.optimize import minimize_scalar
from sqlalchemy import text as _text
from structlog import get_logger

logger = get_logger()

DEFAULT_GAMES = ["lol", "cs2", "dota2", "valorant", "cod", "r6", "sc2", "rl"]

MIN_SAMPLES = 30  # per-game minimum for fitting (sparser than FavoriteLongshot's 50)
ECE_BINS = 5
B_LOWER = 0.5
B_UPPER = 2.0
CLIP_RAW_LO = 0.02
CLIP_RAW_HI = 0.98
CLIP_OUT_LO = 0.05
CLIP_OUT_HI = 0.95


def _recalibrate_prob(p: float, b: float) -> float:
    """Apply logistic recalibration: 1 / (1 + (1/p - 1)^b).

    b=1.0 -> identity (no change)
    b>1.0 -> predictions are underconfident, push toward extremes
    b<1.0 -> predictions are overconfident, push toward 0.5
    """
    p = np.clip(p, CLIP_RAW_LO, CLIP_RAW_HI)
    odds_ratio = (1.0 / p - 1.0) ** b
    result = 1.0 / (1.0 + odds_ratio)
    return float(np.clip(result, CLIP_OUT_LO, CLIP_OUT_HI))


def _brier_score(predicted: np.ndarray, actual: np.ndarray, b: float) -> float:
    """Compute Brier score after recalibrating predictions with parameter b."""
    recal = np.clip(
        1.0 / (1.0 + (1.0 / np.clip(predicted, CLIP_RAW_LO, CLIP_RAW_HI) - 1.0) ** b),
        CLIP_OUT_LO,
        CLIP_OUT_HI,
    )
    return float(np.mean((recal - actual) ** 2))


def _compute_ece(predicted: np.ndarray, actual: np.ndarray, n_bins: int = ECE_BINS) -> float:
    """Compute Expected Calibration Error with equal-width bins."""
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (predicted >= bin_edges[i]) & (predicted < bin_edges[i + 1])
        if i == n_bins - 1:
            mask = mask | (predicted == bin_edges[i + 1])
        count = mask.sum()
        if count == 0:
            continue
        avg_pred = predicted[mask].mean()
        avg_actual = actual[mask].mean()
        ece += (count / len(predicted)) * abs(avg_pred - avg_actual)
    return float(ece)


class EsportsBiasDecomposition:
    """Per-game bias analysis for esports predictions."""

    def __init__(self):
        self._game_params: Dict[str, Dict] = {}

    async def fit_from_db(
        self,
        db,
        games: Optional[List[str]] = None,
        days: int = 90,
    ) -> Dict[str, Dict]:
        """Compute bias decomposition per game from resolved esports_predictions.

        For each game with >= 30 resolved predictions:
        1. base_bias = mean(predicted) - mean(actual)
        2. Fit recalibration parameter b minimizing Brier score
        3. Compute ECE in 5 bins
        4. horizon_effect: correlation(hours_to_resolve, |predicted - actual|)

        Returns dict per game: {b, base_bias, ece, horizon_corr, n_samples}
        """
        if games is None:
            games = list(DEFAULT_GAMES)

        if db is None:
            logger.warning("bias_decomposition: no db provided")
            return {}

        query = _text(
            "SELECT game, predicted_prob, actual_outcome, "
            "EXTRACT(EPOCH FROM (resolved_at - created_at))/3600 as hours_to_resolve "
            "FROM esports_predictions "
            "WHERE actual_outcome IS NOT NULL "
            "AND created_at > NOW() - INTERVAL :interval_days "
        )

        rows_by_game: Dict[str, list] = {g: [] for g in games}

        try:
            async with db.get_session() as session:
                result = await session.execute(
                    query, {"interval_days": f"{days} days"}
                )
                for row in result:
                    game_key = row.game.lower() if row.game else None
                    if game_key in rows_by_game:
                        rows_by_game[game_key].append(row)
        except Exception as exc:
            logger.error("bias_decomposition: query failed", error=str(exc))
            return {}

        results: Dict[str, Dict] = {}
        for game, rows in rows_by_game.items():
            if len(rows) < MIN_SAMPLES:
                logger.debug(
                    "bias_decomposition: insufficient data",
                    game=game,
                    n_samples=len(rows),
                    min_required=MIN_SAMPLES,
                )
                continue

            predicted = np.array([float(r.predicted_prob) for r in rows])
            actual = np.array([float(r.actual_outcome) for r in rows])
            hours = np.array([float(r.hours_to_resolve) for r in rows])

            # 1. Base bias
            base_bias = float(predicted.mean() - actual.mean())

            # 2. Fit recalibration parameter b
            opt = minimize_scalar(
                lambda b: _brier_score(predicted, actual, b),
                bounds=(B_LOWER, B_UPPER),
                method="bounded",
            )
            b_star = float(opt.x)

            # 3. ECE
            ece = _compute_ece(predicted, actual)

            # 4. Horizon effect: correlation(hours_to_resolve, |error|)
            abs_error = np.abs(predicted - actual)
            if np.std(hours) > 1e-9 and np.std(abs_error) > 1e-9:
                horizon_corr = float(np.corrcoef(hours, abs_error)[0, 1])
            else:
                horizon_corr = 0.0

            params = {
                "b": b_star,
                "base_bias": base_bias,
                "ece": ece,
                "horizon_corr": horizon_corr,
                "n_samples": len(rows),
            }
            results[game] = params
            self._game_params[game] = params

            logger.info(
                "bias_decomposition: fitted",
                game=game,
                b=round(b_star, 4),
                base_bias=round(base_bias, 4),
                ece=round(ece, 4),
                horizon_corr=round(horizon_corr, 4),
                n_samples=len(rows),
            )

        return results

    def recalibrate(self, raw_prob: float, game: str) -> float:
        """Apply game-specific recalibration: 1 / (1 + (1/p - 1)^b).

        If no fitted params for game, returns raw_prob unchanged.
        """
        params = self._game_params.get(game.lower())
        if params is None:
            return raw_prob
        return _recalibrate_prob(raw_prob, params["b"])

    @property
    def game_params(self) -> Dict[str, Dict]:
        return copy.deepcopy(self._game_params)
