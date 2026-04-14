"""
Full prediction pipeline: XGBoost + Venn-ABERS + MAPIE conformal.

Implements the PredictionPipeline protocol expected by the walk-forward
backtester. Wires B2 (meta-model) + B3 (calibration) + B4 (conformal filter)
into a single fit/predict interface.

Sizing: Quarter-Kelly with $100 cap, consistent with Phase 5v2 risk controls.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from esports_v2.model.meta_model import XGBoostMetaModel
from esports_v2.model.calibrator import VennAbersCalibrator
from esports_v2.model.conformal import ConformalFilter

logger = logging.getLogger(__name__)

# Sizing constants (Phase 5v2 risk controls)
KELLY_FRACTION = 0.25   # Quarter-Kelly
MAX_BET_USD = 100.0
MAX_BANKROLL_PCT = 0.05
MIN_EDGE = 0.05          # 5% minimum edge to bet
BANKROLL = 20_000.0      # Paper bankroll for sizing


class EsportsPipeline:
    """
    Full prediction pipeline for EsportsBot v2.

    fit() -> train XGBoost, fit Venn-ABERS calibrator, fit conformal filter.
    predict() -> raw prob -> calibrated prob -> conformal set -> Kelly sizing.
    """

    def __init__(
        self,
        xgb_params: Optional[Dict] = None,
        alpha: float = 0.10,
        cal_fraction: float = 0.2,
    ) -> None:
        """
        Args:
            xgb_params: Override XGBoost hyperparameters.
            alpha: Conformal significance level.
            cal_fraction: Fraction of training data held out for calibration.
        """
        self._xgb = XGBoostMetaModel(**(xgb_params or {}))
        self._calibrator = VennAbersCalibrator()
        self._conformal = ConformalFilter(alpha=alpha)
        self._cal_fraction = cal_fraction

    def fit(self, records: List[dict]) -> None:
        """
        Train the full pipeline on historical records.

        Splits records into train (for XGBoost) and calibration (for Venn-ABERS
        + conformal). The split is temporal — last cal_fraction of records used
        for calibration.
        """
        n = len(records)
        if n < 50:
            logger.warning(f"Only {n} records, pipeline may underfit")

        # Temporal split: last cal_fraction for calibration
        cal_size = max(20, int(n * self._cal_fraction))
        train_records = records[:-cal_size]
        cal_records = records[-cal_size:]

        if len(train_records) < 30:
            # Not enough for split — use all for training, calibrate on train
            train_records = records
            cal_records = records

        # B2: Train XGBoost
        self._xgb.fit(train_records)

        # Get raw probabilities on calibration set
        cal_probs = self._xgb.predict_proba_batch(cal_records)
        cal_labels = np.array([r["actual"] for r in cal_records], dtype=np.float32)

        # B3: Fit Venn-ABERS per game
        for game in set(r.get("game", "unknown") for r in cal_records):
            mask = np.array([r.get("game") == game for r in cal_records])
            if mask.sum() < 10:
                continue
            self._calibrator.fit(cal_probs[mask], cal_labels[mask], game)

        # B4: Fit conformal filter on calibrated probabilities
        cal_calibrated = np.array([
            self._calibrator.predict(float(p), r.get("game", "unknown"))[0]
            for p, r in zip(cal_probs, cal_records)
        ])
        self._conformal.fit(cal_calibrated, cal_labels)

        logger.info(
            f"Pipeline fit: {len(train_records)} train, {len(cal_records)} cal"
        )

    def predict(self, record: dict) -> dict:
        """
        Full prediction for a single record.

        Returns dict with:
          p_raw: XGBoost raw probability
          p_model: Venn-ABERS calibrated probability
          p_lower, p_upper: Venn-ABERS interval
          conformal_set: list of class labels
          is_singleton: bool
          kelly_fraction: suggested fraction (0 if not singleton or no edge)
          stake: dollar amount to bet
          edge: model prob - market price (or 0.5 if no market)
        """
        game = record.get("game", "unknown")

        # Raw XGBoost
        p_raw = self._xgb.predict_proba(record)

        # Venn-ABERS calibration
        p_model, p_lower, p_upper = self._calibrator.predict(p_raw, game)

        # Conformal filter
        conf = self._conformal.predict(p_model)

        # Sizing
        market_price = record.get("market_price", 0.5)
        edge = abs(p_model - market_price)
        kelly = 0.0
        stake = 0.0

        if conf["is_singleton"] and edge >= MIN_EDGE:
            # Kelly criterion: f = (bp - q) / b
            # where b = (1/market_price - 1), p = model prob of winning, q = 1-p
            if p_model > 0.5:
                b = (1.0 / market_price) - 1.0 if market_price > 0 else 1.0
                p = p_model
            else:
                b = (1.0 / (1 - market_price)) - 1.0 if market_price < 1 else 1.0
                p = 1 - p_model

            q = 1 - p
            if b > 0:
                kelly = max(0.0, (b * p - q) / b)
                kelly *= KELLY_FRACTION  # Quarter-Kelly
                stake = min(kelly * BANKROLL, MAX_BET_USD, BANKROLL * MAX_BANKROLL_PCT)

        return {
            "p_raw": p_raw,
            "p_model": p_model,
            "p_lower": p_lower,
            "p_upper": p_upper,
            "conformal_set": conf["conformal_set"],
            "is_singleton": conf["is_singleton"],
            "kelly_fraction": kelly,
            "stake": stake,
            "edge": edge,
            "market_price": market_price,
        }
