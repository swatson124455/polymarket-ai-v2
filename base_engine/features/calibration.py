"""
Favorite-Longshot Bias Calibration (P3-06).

Contracts at extreme prices (< 20c or > 80c) are systematically mispriced.
Build isotonic regression calibration curve from resolved prediction_log.
Apply to raw predictions before edge computation.

Dependencies: scikit-learn (IsotonicRegression).
"""
import numpy as np
from typing import Optional, Any, List, Dict
from structlog import get_logger

logger = get_logger()

MIN_RESOLVED_FOR_CALIBRATION = 50


class FavoriteLongshotCalibrator:
    """Calibrate raw predictions using isotonic regression on resolved outcomes."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._calibrator = None
        self._fitted = False

    async def fit_from_prediction_log(self, n_days: int = 90) -> bool:
        """
        Fit isotonic regression from resolved prediction_log entries.
        Returns True if calibration curve was fitted, False if insufficient data.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return False

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT predicted_prob, resolution
                    FROM prediction_log
                    WHERE resolution IS NOT NULL
                      AND prediction_time > NOW() - INTERVAL ':days days'
                    ORDER BY prediction_time DESC
                    LIMIT 5000
                """.replace(":days", str(n_days))))
                rows = r.fetchall()

            if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
                logger.info("Insufficient data for calibration: %d rows (need %d)", len(rows), MIN_RESOLVED_FOR_CALIBRATION)
                return False

            predictions = np.array([float(r[0]) for r in rows])
            outcomes = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows])

            from sklearn.isotonic import IsotonicRegression
            self._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
            self._calibrator.fit(predictions, outcomes)
            self._fitted = True

            logger.info("Calibration fitted on %d resolved predictions", len(rows))
            return True

        except ImportError:
            logger.warning("sklearn not available for isotonic regression calibration")
            return False
        except Exception as e:
            logger.debug("Calibration fit failed: %s", e)
            return False

    def calibrate(self, raw_prob: float) -> float:
        """
        Apply calibration curve to raw prediction.
        If not fitted, returns raw_prob unchanged (identity).
        """
        if not self._fitted or self._calibrator is None:
            return raw_prob
        try:
            result = self._calibrator.predict([raw_prob])[0]
            return float(np.clip(result, 0.01, 0.99))
        except Exception:
            return raw_prob

    @property
    def is_fitted(self) -> bool:
        return self._fitted


class DomainCalibrator:
    """
    Per-category isotonic regression calibrators (crypto, politics, sports, economics).

    Each category has its own calibration curve because systematic biases differ:
    - Crypto markets tend to overshoot on hype
    - Political markets have known favourite-longshot bias
    - Sports markets are best-calibrated (deep liquidity)
    """

    CATEGORIES = ("crypto", "politics", "sports", "economics")

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._calibrators: Dict[str, FavoriteLongshotCalibrator] = {}
        self._global = FavoriteLongshotCalibrator(db=db)

    async def fit_all(self, n_days: int = 90) -> Dict[str, bool]:
        """Fit per-category calibrators + global fallback."""
        results = {}
        # Global
        results["global"] = await self._global.fit_from_prediction_log(n_days)

        # Per-category
        for cat in self.CATEGORIES:
            cal = FavoriteLongshotCalibrator(db=self.db)
            fitted = await self._fit_category(cal, cat, n_days)
            results[cat] = fitted
            if fitted:
                self._calibrators[cat] = cal

        return results

    async def _fit_category(self, cal: FavoriteLongshotCalibrator, category: str, n_days: int) -> bool:
        """Fit calibrator for a single category."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return False
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT pl.predicted_prob, pl.resolution
                    FROM prediction_log pl
                    JOIN markets m ON pl.market_id = m.id
                    WHERE pl.resolution IS NOT NULL
                      AND LOWER(m.market_category) = :category
                      AND pl.prediction_time > NOW() - INTERVAL ':days days'
                    ORDER BY pl.prediction_time DESC
                    LIMIT 5000
                """.replace(":days", str(n_days)).replace(":category", f"'{category}'")),
                    {"category": category})
                rows = r.fetchall()

            if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
                return False

            predictions = np.array([float(r[0]) for r in rows])
            outcomes = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows])

            from sklearn.isotonic import IsotonicRegression
            cal._calibrator = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip")
            cal._calibrator.fit(predictions, outcomes)
            cal._fitted = True
            logger.info("Category calibrator fitted: %s (%d samples)", category, len(rows))
            return True
        except Exception as e:
            logger.debug("Category calibrator fit failed for %s: %s", category, e)
            return False

    def calibrate(self, raw_prob: float, category: str = "") -> float:
        """Calibrate probability using category-specific calibrator (falls back to global)."""
        cat = category.lower().strip()
        if cat in self._calibrators and self._calibrators[cat].is_fitted:
            return self._calibrators[cat].calibrate(raw_prob)
        if self._global.is_fitted:
            return self._global.calibrate(raw_prob)
        return raw_prob


class FocalTemperatureCalibrator:
    """
    Focal Temperature Scaling (Komisarenko & Kull, ECAI 2024).

    Calibrates predictions by fitting a temperature parameter T that minimizes
    focal loss on resolved prediction data:

        calibrated_prob = sigmoid(logit(p) / T)

    where logit(p) = log(p / (1 - p)) and sigmoid(x) = 1 / (1 + exp(-x)).

    Focal loss down-weights well-classified examples via a focusing parameter gamma:

        FL = -(1 - p_cal)^gamma * y * log(p_cal) - p_cal^gamma * (1 - y) * log(1 - p_cal)

    T and gamma are fitted jointly by grid search over resolved prediction_log entries.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._temperature: float = 1.0
        self._gamma: float = 0.0
        self._fitted: bool = False

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        """Numerically stable sigmoid."""
        return np.where(
            x >= 0,
            1.0 / (1.0 + np.exp(-x)),
            np.exp(x) / (1.0 + np.exp(x)),
        )

    @staticmethod
    def _logit(p: np.ndarray) -> np.ndarray:
        """Logit (log-odds) with clipping to avoid inf."""
        p_clip = np.clip(p, 1e-7, 1.0 - 1e-7)
        return np.log(p_clip / (1.0 - p_clip))

    @classmethod
    def _calibrate_array(cls, raw: np.ndarray, temperature: float) -> np.ndarray:
        """Apply temperature scaling to an array of probabilities."""
        logits = cls._logit(raw)
        scaled = logits / temperature
        return cls._sigmoid(scaled)

    @classmethod
    def _focal_loss(cls, predictions: np.ndarray, outcomes: np.ndarray,
                    temperature: float, gamma: float) -> float:
        """
        Mean focal loss over the dataset.

        FL_i = -(1 - p_cal)^gamma * y * log(p_cal) - p_cal^gamma * (1 - y) * log(1 - p_cal)
        """
        p_cal = cls._calibrate_array(predictions, temperature)
        p_cal = np.clip(p_cal, 1e-7, 1.0 - 1e-7)

        term_pos = -((1.0 - p_cal) ** gamma) * outcomes * np.log(p_cal)
        term_neg = -(p_cal ** gamma) * (1.0 - outcomes) * np.log(1.0 - p_cal)
        return float(np.mean(term_pos + term_neg))

    async def fit_from_prediction_log(self, n_days: int = 90) -> bool:
        """
        Fit T and gamma by grid search over resolved prediction_log entries.
        Returns True if fitted, False if insufficient data.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return False

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT predicted_prob, resolution
                    FROM prediction_log
                    WHERE resolution IS NOT NULL
                      AND prediction_time > NOW() - INTERVAL ':days days'
                    ORDER BY prediction_time DESC
                    LIMIT 5000
                """.replace(":days", str(n_days))))
                rows = r.fetchall()

            if len(rows) < MIN_RESOLVED_FOR_CALIBRATION:
                logger.info(
                    "FocalTemp: insufficient data for calibration: %d rows (need %d)",
                    len(rows), MIN_RESOLVED_FOR_CALIBRATION,
                )
                return False

            predictions = np.array([float(r[0]) for r in rows])
            outcomes = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows])

            self._fit_grid_search(predictions, outcomes)
            return True

        except Exception as e:
            logger.debug("FocalTemp calibration fit failed: %s", e)
            return False

    def _fit_grid_search(self, predictions: np.ndarray, outcomes: np.ndarray) -> None:
        """Grid search over T and gamma to minimize focal loss."""
        best_loss = float("inf")
        best_t = 1.0
        best_gamma = 0.0

        # T in [0.5, 3.0] step 0.1, gamma in [0.0, 5.0] step 0.5
        t_values = np.arange(0.5, 3.05, 0.1)
        gamma_values = np.arange(0.0, 5.5, 0.5)

        for t in t_values:
            for g in gamma_values:
                loss = self._focal_loss(predictions, outcomes, float(t), float(g))
                if loss < best_loss:
                    best_loss = loss
                    best_t = float(t)
                    best_gamma = float(g)

        self._temperature = best_t
        self._gamma = best_gamma
        self._fitted = True

        logger.info(
            "FocalTemp fitted: T=%.2f, gamma=%.1f, focal_loss=%.4f on %d samples",
            best_t, best_gamma, best_loss, len(predictions),
        )

    def calibrate(self, raw_prob: float) -> float:
        """
        Apply focal temperature scaling to a raw prediction.
        If not fitted, returns raw_prob unchanged (identity).
        """
        if not self._fitted:
            return raw_prob
        try:
            p = np.clip(raw_prob, 1e-7, 1.0 - 1e-7)
            logit = np.log(p / (1.0 - p))
            scaled = logit / self._temperature
            result = 1.0 / (1.0 + np.exp(-scaled))
            return float(np.clip(result, 0.01, 0.99))
        except Exception:
            return raw_prob

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def gamma(self) -> float:
        return self._gamma


class HorizonBiasCalibrator:
    """
    Le (2026) domain x time-to-resolution bias correction.

    Prediction markets exhibit systematic biases that depend on both the domain
    (politics, weather, sports, crypto) and time remaining to resolution.
    Fits a power-law parameter `b` per (domain, horizon) bucket:

        recalibrated = 1 / (1 + ((1-p)/p)^(1/b))

    b > 1: market underestimates extremes (pushes toward 0/1).
    b < 1: market overestimates extremes (shrinks toward 0.5).
    b = 1: identity (no correction).

    Reference: Le (2026), arXiv:2602.19520
    """

    TTR_BUCKETS = [
        ("0_7d", 0, 7),
        ("7_30d", 7, 30),
        ("30_90d", 30, 90),
        ("90d_plus", 90, 9999),
    ]

    MIN_SAMPLES_PER_BUCKET = 15

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._b_params: Dict[str, float] = {}
        self._global_b: float = 1.0
        self._fitted = False

    async def fit_from_paper_trades(self, n_days: int = 180) -> bool:
        """Fit b parameters from resolved paper_trades."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return False

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text(
                    "SELECT pt.bot_name,"
                    " LOWER(COALESCE(m.market_category, 'unknown')) AS domain,"
                    " pt.predicted_prob, pt.entry_price, pt.realized_pnl,"
                    " EXTRACT(EPOCH FROM (m.end_date_iso - pt.created_at)) / 86400.0 AS ttr_days"
                    " FROM paper_trades pt"
                    " JOIN markets m ON pt.market_id = m.id"
                    " WHERE pt.realized_pnl IS NOT NULL"
                    " AND pt.side IN ('YES', 'NO')"
                    " AND LOWER(pt.side) != 'sell'"
                    " AND pt.created_at > NOW() - INTERVAL '" + str(int(n_days)) + " days'"
                    " AND m.end_date_iso IS NOT NULL"
                    " ORDER BY pt.created_at DESC LIMIT 10000"
                ))
                rows = r.fetchall()

            if len(rows) < self.MIN_SAMPLES_PER_BUCKET:
                logger.info("HorizonBias: insufficient data (%d rows)", len(rows))
                return False

            from collections import defaultdict
            buckets: Dict[str, List] = defaultdict(list)
            global_data: List = []

            for row in rows:
                domain = row[1] or "unknown"
                predicted_prob = float(row[2]) if row[2] is not None else None
                entry_price = float(row[3]) if row[3] is not None else None
                realized_pnl = float(row[4])
                ttr_days = float(row[5]) if row[5] is not None else None

                p = entry_price if entry_price and 0 < entry_price < 1 else predicted_prob
                if p is None or p <= 0 or p >= 1:
                    continue

                outcome = 1.0 if realized_pnl > 0 else 0.0
                global_data.append((p, outcome))

                if ttr_days is not None:
                    for bucket_name, lo, hi in self.TTR_BUCKETS:
                        if lo <= ttr_days < hi:
                            buckets[f"{domain}_{bucket_name}"].append((p, outcome))
                            break

            from scipy.optimize import minimize_scalar

            def _neg_ll(b, probs, outcomes):
                ll = 0.0
                for pr, y in zip(probs, outcomes):
                    odds = (1 - pr) / pr
                    cal_p = 1.0 / (1.0 + odds ** (1.0 / max(0.1, b)))
                    cal_p = max(1e-6, min(1 - 1e-6, cal_p))
                    ll += y * np.log(cal_p) + (1 - y) * np.log(1 - cal_p)
                return -ll

            for key, data in buckets.items():
                if len(data) >= self.MIN_SAMPLES_PER_BUCKET:
                    probs = [d[0] for d in data]
                    outcomes = [d[1] for d in data]
                    result = minimize_scalar(
                        _neg_ll, bounds=(0.3, 3.0),
                        method="bounded", args=(probs, outcomes),
                    )
                    self._b_params[key] = result.x
                    logger.info("HorizonBias: %s -> b=%.3f (%d samples)", key, result.x, len(data))

            if len(global_data) >= self.MIN_SAMPLES_PER_BUCKET:
                g_probs = [d[0] for d in global_data]
                g_outcomes = [d[1] for d in global_data]
                g_result = minimize_scalar(
                    _neg_ll, bounds=(0.3, 3.0),
                    method="bounded", args=(g_probs, g_outcomes),
                )
                self._global_b = g_result.x
                logger.info("HorizonBias: global b=%.3f (%d samples)", g_result.x, len(global_data))

            self._fitted = True
            logger.info("HorizonBias: fitted %d buckets from %d trades", len(self._b_params), len(rows))
            return True

        except Exception as e:
            logger.debug("HorizonBias fit failed: %s", e)
            return False

    def calibrate(self, raw_prob: float, category: str = "", ttr_days: Optional[float] = None) -> float:
        """Apply Le (2026) power-law recalibration."""
        if not self._fitted:
            return raw_prob

        p = max(1e-6, min(1 - 1e-6, raw_prob))

        b = self._global_b
        if category and ttr_days is not None:
            domain = category.lower().strip()
            for bucket_name, lo, hi in self.TTR_BUCKETS:
                if lo <= ttr_days < hi:
                    key = f"{domain}_{bucket_name}"
                    if key in self._b_params:
                        b = self._b_params[key]
                    break

        if abs(b - 1.0) < 0.01:
            return raw_prob

        odds = (1 - p) / p
        recal = 1.0 / (1.0 + odds ** (1.0 / b))
        return float(np.clip(recal, 0.01, 0.99))

    @property
    def is_fitted(self) -> bool:
        return self._fitted
