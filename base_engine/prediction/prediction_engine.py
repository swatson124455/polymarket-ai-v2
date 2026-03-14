import asyncio
import warnings
import numpy as np
import pandas as pd
import math
import os
import pickle
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import sklearn

# Suppress noisy LGBMClassifier feature name warnings that spam logs on every prediction.
# These occur because models were fitted with DataFrame (feature names) but predict() receives numpy arrays.
# Harmless — predictions are correct regardless.
warnings.filterwarnings("ignore", message="X does not have valid feature names", category=UserWarning)
from sklearn.ensemble import (
    RandomForestClassifier, GradientBoostingClassifier,
    ExtraTreesClassifier, HistGradientBoostingClassifier,
)
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
from structlog import get_logger
from base_engine.data.database import Database
from base_engine.learning.learning_engine import LearningEngine
from base_engine.learning.feature_engineering import FeatureEngineer
from config.settings import settings

# Local model cache file (fast startup — avoids BYTEA round-trip to DB)
_MODEL_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_MODEL_CACHE_FILE = _MODEL_CACHE_DIR / "model_cache.pkl"

# P6: Feature vector cache TTL — all code paths MUST use this constant.
# 300s prevents expiry between scan cycles (batch precompute takes 50s+).
# Lines 477, 2294, 3265 previously hardcoded 120s, causing mixed-age vectors.
_FV_CACHE_TTL = 300.0


def _get_model_path(bot_name: Optional[str] = None) -> Path:
    """Return model cache path. Per-bot if bot_name provided, else global.

    Session 47: Per-bot model storage structure. Initially all bots load from the
    global model_cache.pkl. As per-bot training accumulates data (200+ predictions),
    bots can train their own models via USE_PER_BOT_MODELS=true.
    """
    if bot_name:
        bot_dir = _MODEL_CACHE_DIR / "models" / bot_name
        bot_dir.mkdir(parents=True, exist_ok=True)
        return bot_dir / "model_cache.pkl"
    return _MODEL_CACHE_FILE

logger = get_logger()


def _on_prediction_log_done(task: asyncio.Task) -> None:
    """Callback for prediction log background task."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.warning("prediction_log_bg_insert_failed", error=str(exc))


def _predict_models_sync(models, model_weights, scaler, features):
    """CPU-bound: scaler + all model.predict_proba (runs in thread pool)."""
    features_scaled = scaler.transform([features])
    predictions = {}
    degraded = []
    for name, model in models.items():
        try:
            prob = model.predict_proba(features_scaled)[0]
            val = float(prob[1]) if len(prob) >= 2 else 0.5
            if np.isnan(val) or np.isinf(val):
                val = 0.5
                degraded.append(name)
            predictions[name] = val
        except Exception:
            predictions[name] = 0.5
            degraded.append(name)
    return predictions, features_scaled, degraded


# Autonomy: learned weights/blend staleness and bounds
MAX_WEIGHT_AGE_HOURS = 72
BLEND_MIN = 0.3
BLEND_MAX = 0.9


class _DriftTracker:
    """
    Tracks model prediction drift by comparing recent prediction distributions
    against historical baselines. Detects calibration drift and feature distribution shifts.
    """

    def __init__(self, window_size: int = 50):
        # I06: window_size reduced 200→50 so ADWIN has a tighter, faster-responding window.
        # 200-sample window requires ~20pp accuracy drop before ADWIN fires; 50 samples ≈ 5× faster.
        self.window_size = window_size
        # M4 FIX: Use deque(maxlen) instead of list with manual slicing.
        # list[-window_size:] copies the entire list on every prediction call → O(n) per call.
        # With 50+ predictions/second, this is significant wasted allocation.
        self._recent_predictions: deque = deque(maxlen=window_size)
        self._recent_outcomes: deque = deque(maxlen=window_size)  # 1=correct, 0=wrong
        self._baseline_mean: Optional[float] = None
        self._baseline_std: Optional[float] = None

        # B1: SUPER relay — high-surprise deque (NeurIPS 2023 selective experience relay)
        # Stores (market_id, predicted_prob, actual_outcome) when prediction error > 0.3.
        # EnsembleBot checks similarity before trading; markets resembling past surprises
        # get a 0.90× confidence penalty.
        self._high_surprise: deque = deque(maxlen=200)

    def record_prediction(self, predicted_prob: float) -> None:
        """Record a prediction probability for drift analysis."""
        self._recent_predictions.append(predicted_prob)  # deque auto-evicts oldest

    def record_outcome(self, predicted_prob: float, actual: int, market_id: Optional[str] = None) -> None:
        """
        Record a prediction outcome (1=resolved YES, 0=resolved NO).
        B1: If abs(predicted - actual) > 0.3, add to high-surprise deque for relay.
        """
        correct = 1 if (predicted_prob >= 0.5) == (actual == 1) else 0
        self._recent_outcomes.append(correct)

        # B1: Capture high-surprise outcomes for SUPER relay
        actual_f = float(actual)
        surprise_magnitude = abs(predicted_prob - actual_f)
        if surprise_magnitude > 0.3 and market_id:
            self._high_surprise.append({
                "market_id": str(market_id),
                "predicted": round(predicted_prob, 4),
                "actual": int(actual),
                "error": round(surprise_magnitude, 4),
            })
            logger.debug(
                "B1 SUPER: high-surprise outcome recorded market=%s pred=%.3f actual=%d error=%.3f",
                market_id, predicted_prob, actual, surprise_magnitude,
            )

    def is_high_surprise_market(self, market_id: str, window: int = 50) -> bool:
        """
        B1: Returns True if this market_id appeared in recent high-surprise outcomes.
        EnsembleBot should apply 0.90× confidence penalty on positive result.
        """
        mid = str(market_id)
        recent = list(self._high_surprise)[-window:]
        return any(entry["market_id"] == mid for entry in recent)

    def set_baseline(self, mean: float, std: float) -> None:
        """Set distribution baseline from training data."""
        self._baseline_mean = mean
        self._baseline_std = std

    def to_dict(self) -> dict:
        """Serialize state for persistence across restarts (P4-3)."""
        return {
            "recent_predictions": list(self._recent_predictions),
            "recent_outcomes": list(self._recent_outcomes),
            "baseline_mean": self._baseline_mean,
            "baseline_std": self._baseline_std,
            "high_surprise": list(self._high_surprise),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_DriftTracker":
        """Restore state from persisted dict (P4-3)."""
        tracker = cls()
        tracker._recent_predictions = deque(data.get("recent_predictions", []), maxlen=tracker.window_size)
        tracker._recent_outcomes = deque(data.get("recent_outcomes", []), maxlen=tracker.window_size)
        tracker._baseline_mean = data.get("baseline_mean")
        tracker._baseline_std = data.get("baseline_std")
        tracker._high_surprise = deque(data.get("high_surprise", []), maxlen=200)
        return tracker

    def _adwin_test(self, data: List[float], delta: float = 0.002) -> bool:
        """
        ADWIN (Adaptive Windowing) test for concept drift.
        Splits data at every point and checks if the means of the two sub-windows
        differ significantly. Returns True if drift detected.
        """
        # Convert deque/iterable to list — deque does not support slice indexing
        if not isinstance(data, list):
            data = list(data)
        n = len(data)
        if n < 10:
            return False
        total = sum(data)
        for i in range(5, n - 5):
            n0 = i
            n1 = n - i
            mu0 = sum(data[:i]) / n0
            mu1 = sum(data[i:]) / n1
            m = 1.0 / (1.0 / n0 + 1.0 / n1)
            epsilon = math.sqrt((1.0 / (2.0 * m)) * math.log(4.0 / delta))
            if abs(mu0 - mu1) >= epsilon:
                return True
        return False

    def _pht_test(self, delta: float = 0.005, lambda_: float = 50.0) -> bool:
        """
        A2: Page-Hinkley Test — fastest abrupt-drift detector.
        DR=1.0 at δ=0.001, FPR=0.0. Detects abrupt drift before ADWIN reacts.
        Operates on the accuracy stream (1=correct, 0=wrong).
        Triggers when cumulative deviation from running min exceeds lambda_.
        """
        outcomes = list(self._recent_outcomes)
        if len(outcomes) < 20:
            return False
        cumsum = 0.0
        min_cumsum = 0.0
        for o in outcomes[-100:]:
            cumsum += (1.0 - o) - delta  # accumulate error above delta threshold
            min_cumsum = min(min_cumsum, cumsum)
            if cumsum - min_cumsum > lambda_:
                return True
        return False

    def check_drift(self) -> Dict[str, Any]:
        """
        Check for prediction drift. Returns drift report.

        Checks:
          0. PHT: Page-Hinkley Test — fastest abrupt-drift detector (added A2).
          1. Distribution shift: Is the mean/std of recent predictions far from training baseline?
          2. Calibration drift: Is recent accuracy dropping below expected?
          3. Confidence collapse: Are predictions clustering near 0.5 (model unsure)?
          4. ADWIN: Adaptive windowing on accuracy stream for statistically significant drift.
          5. Mann-Whitney U: covariate drift via non-parametric distribution comparison (B6).
        """
        report: Dict[str, Any] = {"drifted": False, "checks": {}}

        # A2: PHT check — runs on outcomes stream, fast abrupt-drift alarm
        if len(self._recent_outcomes) >= 20:
            pht_drift = self._pht_test()
            report["checks"]["pht"] = {"drift_detected": pht_drift}
            if pht_drift:
                report["drifted"] = True

        if len(self._recent_predictions) < 50:
            report["checks"]["insufficient_data"] = True
            return report

        # deque does not support negative slice indexing — convert to list first.
        # Since _recent_predictions has maxlen=window_size, list() gives all elements.
        preds = np.array(list(self._recent_predictions)[-self.window_size:])
        current_mean = float(np.mean(preds))
        current_std = float(np.std(preds))

        # Check 1: Distribution shift from training baseline
        if self._baseline_mean is not None and self._baseline_std is not None and self._baseline_std > 0:
            z_score = abs(current_mean - self._baseline_mean) / max(self._baseline_std, 0.01)
            shifted = z_score > 2.0
            report["checks"]["distribution_shift"] = {
                "z_score": round(z_score, 3),
                "current_mean": round(current_mean, 4),
                "baseline_mean": round(self._baseline_mean, 4),
                "shifted": shifted,
            }
            if shifted:
                report["drifted"] = True

        # Check 2: Confidence collapse (predictions clustering near 0.5)
        near_half = float(np.mean(np.abs(preds - 0.5) < 0.1))
        collapsed = near_half > 0.6
        report["checks"]["confidence_collapse"] = {
            "pct_near_0_5": round(near_half, 3),
            "collapsed": collapsed,
        }
        if collapsed:
            report["drifted"] = True

        # Check 3: Calibration drift (recent accuracy)
        if len(self._recent_outcomes) >= 30:
            # deque does not support negative slicing — convert to list first
            recent_acc = float(np.mean(list(self._recent_outcomes)[-100:]))
            poor = recent_acc < 0.45
            report["checks"]["calibration"] = {
                "recent_accuracy": round(recent_acc, 3),
                "poor": poor,
            }
            if poor:
                report["drifted"] = True

        # Check 4: ADWIN on accuracy stream
        # I06: delta tightened 0.002→0.01 (95% CI); old 0.002 required ~20pp drop to fire.
        if len(self._recent_outcomes) >= 30:
            adwin_drift = self._adwin_test(self._recent_outcomes, delta=0.01)
            report["checks"]["adwin"] = {"drift_detected": adwin_drift}
            if adwin_drift:
                report["drifted"] = True

        # B6: Mann-Whitney U test — catches covariate drift that error-based detectors miss.
        # Compare first half vs second half of prediction distribution.
        # scipy is already installed as a transitive sklearn dependency.
        if len(self._recent_predictions) >= 40:
            try:
                from scipy import stats as _scipy_stats
                half = len(preds) // 2
                _mw_stat, _mw_pval = _scipy_stats.mannwhitneyu(
                    preds[:half], preds[half:], alternative="two-sided"
                )
                _mw_drift = _mw_pval < 0.05
                report["checks"]["mann_whitney_u"] = {
                    "p_value": round(float(_mw_pval), 4),
                    "drift_detected": _mw_drift,
                }
                if _mw_drift:
                    report["drifted"] = True
            except Exception:
                pass  # scipy unavailable or error — skip silently

        return report


class _BoundedCache:
    """TTL + max-size bounded dict. Two-phase eviction: purge expired first, then LRU."""

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0):
        self._data: Dict[str, Tuple[Any, float]] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        if len(self._data) >= self.max_size:
            self._evict()
        self._data[key] = (value, time.monotonic() + (ttl or self.default_ttl))

    def delete(self, key: str) -> None:
        """Explicitly invalidate a cache entry."""
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def _evict(self) -> None:
        """Two-phase eviction: purge expired entries first, then remove oldest 25% if still over 90% capacity."""
        if not self._data:
            return
        now = time.monotonic()
        # Phase 1: purge expired entries (O(n), no sorting)
        expired = [k for k, (_, exp) in self._data.items() if exp <= now]
        for k in expired:
            del self._data[k]
        # Phase 2: if still over 90% capacity, remove oldest 25% by expiry time
        if len(self._data) >= int(self.max_size * 0.9):
            sorted_keys = sorted(self._data, key=lambda k: self._data[k][1])
            remove_count = max(1, len(sorted_keys) // 4)
            for k in sorted_keys[:remove_count]:
                del self._data[k]

    def __len__(self) -> int:
        return len(self._data)


class PredictionEngine:
    def __init__(self, db: Database, learning_engine: LearningEngine):
        self.db = db
        self.learning_engine = learning_engine
        self.models = {}
        self.scaler = StandardScaler()
        self.feature_columns = []
        self.initialized = False
        self.last_trained_at = None
        self.model_weights: Dict[str, float] = {}
        # Session 50: was 0.6 — learning_conf returns ~0.5 (sparse resolved data),
        # so 40% weight on it pulls ALL predictions toward 0.5 (coin flip).
        # Set to 1.0 to use pure ensemble output until learning_conf has real signal.
        self.ensemble_blend: float = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
        self.best_feature_names: Optional[List[str]] = None
        # Feature importance learning
        self._feature_engineer: Optional[FeatureEngineer] = None
        if getattr(settings, "USE_FEATURE_ENGINEER", True):
            try:
                self._feature_engineer = FeatureEngineer()
            except Exception as e:
                logger.debug("FeatureEngineer init failed, using standard features: %s", e)
        self._feature_importance_scores: Dict[str, float] = {}
        # P3-05: Cross-market feature extractor
        self._cross_market: Optional[Any] = None
        # P3-06: Favorite-longshot calibrator
        self._calibrator: Optional[Any] = None
        # P5-03: LLM probability estimator
        self._llm_estimator: Optional[Any] = None
        # Phase 10: prediction result cache (bounded)
        self._prediction_cache = _BoundedCache(max_size=2000, default_ttl=float(getattr(settings, "CACHE_TTL_PREDICTIONS", 300)))
        # Phase 11: cache for get_elite_net_direction and detect_regime (bounded)
        self._elite_cache = _BoundedCache(max_size=500, default_ttl=300.0)
        self._regime_cache = _BoundedCache(max_size=200, default_ttl=300.0)
        # ms-latency: signal + path summary caches to avoid DB queries in reactive path
        self._signal_cache = _BoundedCache(max_size=500, default_ttl=30.0)
        self._path_cache = _BoundedCache(max_size=500, default_ttl=60.0)
        self._fe_cache = _BoundedCache(max_size=500, default_ttl=120.0)  # FeatureEngineer cache (was incorrectly sharing _path_cache)
        # Market + User object caches — avoids per-market DB SELECTs during parallel scans.
        # Market data changes infrequently (minutes), user stats even less.
        self._market_cache = _BoundedCache(max_size=2000, default_ttl=300.0)  # 5 min
        self._user_cache = _BoundedCache(max_size=500, default_ttl=300.0)    # 5 min
        # Pre-computed feature vector cache — stores complete float[] ready for scaler + predict.
        # Populated by batch_precompute_all_features() background job and by _extract_features() on every call.
        # Keyed by "fv:{market_id}:{token_id}" → List[float].
        # I44: TTL raised 120→300s. Background precompute takes 50s+; 120s TTL caused 40% vectors
        # to expire before the next scan cycle, forcing expensive re-extraction on every cache miss.
        self._feature_vector_cache = _BoundedCache(max_size=2000, default_ttl=300.0)
        # L8 TA indicator cache (RSI, Bollinger, ATR per market)
        self._l8_cache = _BoundedCache(max_size=2000, default_ttl=120.0)
        # Set to True after first successful batch_precompute_all_features() run
        # Used by EnsembleBot to gate the first scan until cache is warm
        self._feature_cache_warmed: bool = False
        # Tier 2 #16: Resolution clarity — set by base_engine after rra is created
        self._resolution_risk_analyzer: Optional[Any] = None
        # I12: Elevation modules ready flag. Set to True only after _init_elevation_modules()
        # completes successfully. Prevents predictions from attempting LLM/calibrator calls
        # before those modules have finished async initialization.
        self._elevation_ready: bool = False
        # Model drift tracking
        self._drift_tracker = _DriftTracker()
        # PSI feature drift detection (Item 21) — populated on train/cache load
        self._feature_baselines: Dict[str, list] = {}
        self._psi_prediction_count: int = 0
        self._recent_features: deque = deque(maxlen=500)  # O(1) append+popleft

    async def prefetch_markets(self, market_ids: list) -> int:
        """Batch-load Market ORM objects into cache for upcoming parallel scan.

        Called once before scan to eliminate N individual SELECT queries.
        Returns count of markets loaded.
        """
        if not market_ids or not self.db:
            return 0
        # Only fetch IDs not already cached
        uncached = [mid for mid in market_ids if self._market_cache.get(f"mkt:{mid}") is None]
        if not uncached:
            return 0
        try:
            from sqlalchemy import select
            from base_engine.data.database import Market
            # Batch in chunks of 200 to avoid oversized IN clauses
            loaded = 0
            for i in range(0, len(uncached), 200):
                chunk = uncached[i:i + 200]
                async with self.db.get_session() as session:
                    result = await session.execute(
                        select(Market).where(Market.id.in_(chunk))
                    )
                    markets = result.scalars().all()
                    for m in markets:
                        # Detach from session so cache entry is usable across sessions
                        session.expunge(m)
                        self._market_cache.set(f"mkt:{m.id}", m)
                        loaded += 1
            return loaded
        except Exception as e:
            logger.warning("prefetch_markets failed (non-fatal): %s", e)
            return 0

    def update_cached_price(self, market_id: str, token_id: str, new_price: float) -> None:
        """Update the price feature in a cached feature vector (called by WS handler).

        This keeps the feature vector cache fresh when WebSocket delivers price updates,
        so predict() uses the latest price without re-querying the DB.
        """
        fv_key = f"fv:{market_id}:{token_id}"
        cached = self._feature_vector_cache.get(fv_key)
        if cached is None:
            return
        cached = list(cached)
        if self.feature_columns:
            try:
                price_idx = self.feature_columns.index("price")
                cached[price_idx] = new_price
                self._feature_vector_cache.set(fv_key, cached, ttl=_FV_CACHE_TTL)
            except ValueError:
                pass

    async def batch_precompute_all_features(self, market_ids: list) -> int:
        """Background job: pre-compute feature vectors for all active markets.

        Populates _feature_vector_cache so scan_and_trade() predict() calls hit the fast
        path (in-memory lookup + model inference, zero DB queries).
        Uses concurrency=3 to avoid DB pool pressure.
        """
        if not market_ids or not self.initialized or not self.models:
            return 0
        # Prefetch Market ORM objects in batch (already cached ones are skipped)
        await self.prefetch_markets(market_ids)

        # Semaphore(2): allow 2 concurrent precomputes.  Background precompute starts 90s after
        # boot so bots have already taken their first scan before this runs.  Pool math:
        #   bots(4) + arb-parallel(2) + semaphore(2) + ingestion(2) + misc(4) = 14 ≤ 15.
        # Increasing from 1 → 2 cuts background precompute wall time by ~2× (100s → ~50s).
        # After VPS migration (pool_size=40+): raise to Semaphore(5).
        sem = asyncio.Semaphore(2)
        count = 0

        async def _precompute_one(mid: str) -> None:
            nonlocal count
            async with sem:
                # Skip if already cached and fresh
                mkt = self._market_cache.get(f"mkt:{mid}")
                yes_tid = getattr(mkt, "yes_token_id", "") if mkt else ""
                if yes_tid and self._feature_vector_cache.get(f"fv:{mid}:{yes_tid}") is not None:
                    count += 1
                    return
                try:
                    # F17: Use actual market price if available, else 0.5 fallback
                    _price = float(getattr(mkt, "yes_price", None) or 0.5) if mkt else 0.5
                    features = await self._extract_features(mid, _price, None)
                    if features:
                        count += 1
                        # _extract_features already stores in _feature_vector_cache
                except Exception as _pre_err:
                    logger.debug("Feature precompute failed for market %s: %s", mid, _pre_err)

        tasks = [_precompute_one(str(mid)) for mid in market_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
        return count

    async def _init_elevation_modules(self) -> None:
        """Initialize Elevation Plan feature modules (P3-05, P3-06, P5-03).
        Called from init() regardless of whether models were loaded or trained."""
        # P3-05: Cross-market feature extractor
        try:
            from base_engine.features.cross_market_features import CrossMarketFeatureExtractor
            self._cross_market = CrossMarketFeatureExtractor(db=self.db)
        except Exception as e:
            logger.debug("Cross-market features not available: %s", e)

        # P3-06: Favorite-longshot calibrator
        try:
            from base_engine.features.calibration import FavoriteLongshotCalibrator
            self._calibrator = FavoriteLongshotCalibrator(db=self.db)
            await self._calibrator.fit_from_prediction_log()
        except Exception as e:
            logger.debug("Calibrator not available: %s", e)

        # Le (2026) horizon bias calibrator — domain x TTR power-law correction
        try:
            from base_engine.features.calibration import HorizonBiasCalibrator
            self._horizon_calibrator = HorizonBiasCalibrator(db=self.db)
            await self._horizon_calibrator.fit_from_paper_trades()
        except Exception as e:
            self._horizon_calibrator = None
            logger.debug("HorizonBias calibrator not available: %s", e)

        # Focal Temperature Scaling — pre-isotonic calibration step
        try:
            from base_engine.features.calibration import FocalTemperatureCalibrator
            self._focal_temp_calibrator = FocalTemperatureCalibrator(db=self.db)
            await self._focal_temp_calibrator.fit_from_prediction_log()
        except Exception as e:
            self._focal_temp_calibrator = None
            logger.debug("FocalTemp calibrator not available: %s", e)

        # P5-03: LLM probability estimator
        try:
            from base_engine.features.llm_probability import LLMProbabilityEstimator
            self._llm_estimator = LLMProbabilityEstimator(db=self.db)
        except Exception as e:
            logger.debug("LLM probability estimator not available: %s", e)

        # Chronos-2 price trajectory forecaster (Tier 3C)
        try:
            from base_engine.prediction.chronos_forecaster import ChronosForecaster
            self._chronos_forecaster = ChronosForecaster(db=self.db)
            if not self._chronos_forecaster.is_available:
                self._chronos_forecaster = None
                logger.debug("Chronos-2 not available (torch/chronos not installed)")
        except Exception as e:
            self._chronos_forecaster = None
            logger.debug("Chronos-2 forecaster not available: %s", e)

        # I12: Mark elevation modules as ready — set AFTER all modules attempt init.
        # EnsembleBot and other callers can check _elevation_ready before using LLM/calibrator.
        self._elevation_ready = True
        logger.debug("Prediction engine elevation modules ready")

    async def init(self):
        # Priority 1: Load from local file cache (instant, avoids BYTEA over pooler)
        if self._load_models_from_file():
            self.initialized = True
            self.last_trained_at = datetime.now(timezone.utc)
            logger.info("Prediction engine loaded models from local cache")
            await self._init_elevation_modules()
            return

        # Priority 2: Load from database (DISABLED by default — local file cache is faster.
        # DB load is manual recovery via load_models_from_db() if local cache is deleted.)
        # To re-enable: set LOAD_MODELS_FROM_DB=true in .env
        if getattr(settings, "LOAD_MODELS_FROM_DB", False) and self.db.session_factory:
            try:
                await asyncio.wait_for(self.load_models_from_db(), timeout=30)
                if self.models:
                    self.initialized = True
                    self.last_trained_at = datetime.now(timezone.utc)
                    logger.info("Prediction engine loaded models from database")
                    self._save_models_to_file()  # Cache locally for next startup
                    await self._init_elevation_modules()
                    return
            except asyncio.TimeoutError:
                logger.warning("Loading models from database timed out, will train fresh")
            except Exception as e:
                logger.warning("Could not load models from database, will train: %s", e)

        # Priority 3: Train fresh models (non-blocking — schedule in background)
        # Training can take minutes; don't block bot startup.
        logger.info("Scheduling model training in background (bots will start without predictions until training completes)")
        self._training_task = asyncio.ensure_future(self._background_train())
        # Mark as initialized so bots can start (predictions will return None until models are ready)
        self.initialized = True
        await self._init_elevation_modules()
        self.initialized = True
        logger.info("Prediction engine initialized")

    async def _background_train(self):
        """Train models in background. If training fails or times out, bots run without predictions."""
        try:
            # Wait for DB session_factory to be available (may not be ready during startup race)
            for _wait in range(10):
                if self.db and getattr(self.db, "session_factory", None):
                    break
                await asyncio.sleep(2)
            if not self.db or not getattr(self.db, "session_factory", None):
                logger.warning("Background training: DB session_factory not available after 20s, attempting re-init")
                try:
                    await self.db.init()
                except Exception as e:
                    logger.warning("Background training: DB re-init failed: %s", e)
                    return
            # H4 FIX: Use explicit task + asyncio.shield so timeout cancels the task.
            # Without this, asyncio.wait_for() raises TimeoutError but _train_models() keeps
            # running in background, modifying self.models/self.scaler mid-scan (race condition).
            _train_task = asyncio.create_task(self._train_models())
            try:
                await asyncio.wait_for(asyncio.shield(_train_task), timeout=900)  # 15 min — training can take 6+ min with all models
            except asyncio.TimeoutError:
                _train_task.cancel()
                try:
                    await _train_task
                except (asyncio.CancelledError, Exception):
                    pass
                raise  # Re-raise so the outer except handles it
            self.last_trained_at = datetime.now(timezone.utc)
            self._save_models_to_file()
            if getattr(settings, "LEARNING_PERSISTENCE", False) and self.db.session_factory:
                try:
                    await self.save_models_to_db()
                except Exception as e:
                    logger.warning("Could not save models to database: %s", e)
            logger.info("Background model training completed (%d models)", len(self.models))
            await self._init_elevation_modules()
        except asyncio.TimeoutError:
            logger.warning("Background model training timed out after 900s — training cancelled — bots running without predictions")
            await self._recover_db_pool()
        except Exception as e:
            logger.warning("Background model training failed: %s — bots running without predictions", e)
            await self._recover_db_pool()

    async def _recover_db_pool(self):
        """Dispose and recreate the DB connection pool after training kills connections."""
        try:
            if self.db and hasattr(self.db, 'engine') and self.db.engine:
                await self.db.engine.dispose()
                logger.info("DB connection pool recycled after training failure")
        except Exception as e:
            logger.debug("Pool recovery failed: %s", e)

    def _backup_model_cache(self) -> None:
        """Backup current model cache file before retrain. Keeps one previous version for manual rollback."""
        try:
            if _MODEL_CACHE_FILE.exists():
                backup_path = _MODEL_CACHE_FILE.with_suffix(".pkl.bak")
                import shutil
                shutil.copy2(_MODEL_CACHE_FILE, backup_path)
                logger.debug("Model cache backed up to %s", backup_path)
        except Exception as e:
            logger.debug("Model cache backup failed (non-fatal): %s", e)

    def _save_models_to_file(self) -> None:
        """Save models + scaler + feature_columns to local file for fast startup."""
        try:
            _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "models": self.models,
                "scaler": self.scaler,
                "model_weights": self.model_weights,
                "ensemble_blend": self.ensemble_blend,
                "feature_columns": self.feature_columns,
                "best_feature_names": self.best_feature_names,
                "feature_baselines": getattr(self, "_feature_baselines", {}),
                "sklearn_version": getattr(sklearn, "__version__", ""),
                "saved_at": datetime.now(timezone.utc).isoformat(),
                # P4-3: persist drift tracker so auto-retrain-on-drift survives restarts
                "drift_tracker": self._drift_tracker.to_dict(),
            }
            with open(_MODEL_CACHE_FILE, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info("Models cached to local file (%d features)", len(self.feature_columns), path=str(_MODEL_CACHE_FILE))
        except Exception as e:
            logger.debug("Could not cache models to file: %s", e)

    def _load_models_from_file(self) -> bool:
        """Load models + scaler + feature_columns from local file cache. Returns True on success."""
        try:
            if not _MODEL_CACHE_FILE.exists():
                return False
            with open(_MODEL_CACHE_FILE, "rb") as f:
                payload = pickle.load(f)
            # Validate sklearn version match
            stored_skv = payload.get("sklearn_version", "")
            current_skv = getattr(sklearn, "__version__", "")
            if stored_skv and stored_skv != current_skv:
                logger.warning("Local model cache sklearn mismatch (stored=%s current=%s), skipping", stored_skv, current_skv)
                return False
            models = payload.get("models", {})
            if not models:
                return False
            # feature_columns is required — without it predictions return empty vectors
            feature_columns = payload.get("feature_columns", [])
            if not feature_columns:
                logger.warning("Local model cache missing feature_columns — will retrain to rebuild")
                return False
            # Validate each model can predict (use named DataFrame for CatBoost compatibility)
            import pandas as pd
            n_feat = len(feature_columns)
            test_df = pd.DataFrame(np.zeros((1, n_feat), dtype=np.float32), columns=feature_columns)
            for name, obj in models.items():
                if hasattr(obj, "predict"):
                    try:
                        obj.predict(test_df)
                    except Exception as e:
                        logger.debug("Model %s validation failed (non-fatal): %s", name, e)
            self.models = models
            self.scaler = payload.get("scaler", self.scaler)
            self.model_weights = payload.get("model_weights", self.model_weights)
            self.ensemble_blend = payload.get("ensemble_blend", self.ensemble_blend)
            self.feature_columns = feature_columns
            self.best_feature_names = payload.get("best_feature_names", self.best_feature_names)
            self._feature_baselines = payload.get("feature_baselines", {})
            self._psi_prediction_count = 0
            self._recent_features = deque(maxlen=500)
            # P4-3: restore drift tracker state so auto-retrain-on-drift is not reset on restart
            drift_data = payload.get("drift_tracker")
            if drift_data:
                self._drift_tracker = _DriftTracker.from_dict(drift_data)
                logger.info(
                    "Drift tracker restored (%d predictions, %d outcomes, %d high-surprise)",
                    len(self._drift_tracker._recent_predictions),
                    len(self._drift_tracker._recent_outcomes),
                    len(self._drift_tracker._high_surprise),
                )
            logger.info("Loaded %d models with %d features from cache", len(models), len(feature_columns))
            return True
        except Exception as e:
            logger.debug("Could not load models from local cache: %s", e)
            return False
    
    async def _train_models(self):
        logger.info("Training prediction models")
        
        features, labels, sample_weights = await self._prepare_training_data()
        
        min_samples = getattr(settings, "MODEL_MIN_TRAINING_SAMPLES", 50)
        if len(features) < min_samples:
            logger.warning(
                "Insufficient training data: %d samples (minimum: %d). Keeping existing models. Retrain skipped.",
                len(features),
                min_samples,
            )
            raise RuntimeError(
                f"Insufficient training data: {len(features)} samples (minimum: {min_samples}). "
                "Ingest more market data and trades first."
            )
        elif len(features) < 100:
            logger.warning(f"Limited training data: {len(features)} samples. Consider ingesting more data for better model performance.")

        # Temporal walk-forward split: train on past 80%, validate on future 20%
        split_idx = max(int(len(features) * 0.8), min_samples)
        train_f, train_l = features[:split_idx], labels[:split_idx]
        train_w = sample_weights[:split_idx]
        val_f, val_l = features[split_idx:], labels[split_idx:]

        # Class-balanced sample weights: multiply existing weights by inverse class frequency
        # so the minority class gets upweighted proportionally
        train_pos = int(train_l.sum()) if hasattr(train_l, 'sum') else sum(train_l)
        train_neg = len(train_l) - train_pos
        if train_pos > 0 and train_neg > 0:
            # sklearn-style balanced: weight_class = n_samples / (n_classes * n_class_samples)
            w_pos = len(train_l) / (2.0 * train_pos)
            w_neg = len(train_l) / (2.0 * train_neg)
            class_w = np.where(train_l == 1, w_pos, w_neg)
            train_w = train_w * class_w
            train_w = np.clip(train_w, 0.1, 30.0)
            logger.info("Applied class-balanced sample weights", w_pos=round(w_pos, 3), w_neg=round(w_neg, 3))
        train_majority_pct = max(train_pos, train_neg) / len(train_l) * 100 if len(train_l) > 0 else 0
        logger.info(
            "Training label distribution",
            total=len(train_l),
            positive=train_pos,
            negative=train_neg,
            majority_class_pct=round(train_majority_pct, 1),
        )
        if len(val_l) > 0:
            val_pos = int(val_l.sum()) if hasattr(val_l, 'sum') else sum(val_l)
            val_neg = len(val_l) - val_pos
            val_majority_pct = max(val_pos, val_neg) / len(val_l) * 100 if len(val_l) > 0 else 0
            logger.info(
                "Validation label distribution",
                total=len(val_l),
                positive=val_pos,
                negative=val_neg,
                majority_class_pct=round(val_majority_pct, 1),
            )
            if train_majority_pct > 80:
                logger.warning(
                    "HIGH CLASS IMBALANCE: majority class is %.1f%% of training data. "
                    "A DummyClassifier would achieve ~%.1f%% accuracy. "
                    "Models must significantly beat this baseline to be useful.",
                    train_majority_pct, train_majority_pct,
                )

        scaler_new = StandardScaler()
        scaler_new.fit(train_f)
        train_scaled = scaler_new.transform(train_f)
        val_scaled = scaler_new.transform(val_f) if len(val_f) > 0 else train_scaled[:0]

        use_calibrated = getattr(settings, "USE_CALIBRATED_MODELS", False)
        if use_calibrated:
            from sklearn.calibration import CalibratedClassifierCV

        def _wrap_model(base):
            if use_calibrated:
                return CalibratedClassifierCV(base, cv=3, method="isotonic")
            return base

        models_new = {}

        # --- Core tree-based models ---
        if getattr(settings, "MODEL_ENABLE_RANDOM_FOREST", True):
            base_rf = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1, class_weight="balanced")
            models_new["random_forest"] = _wrap_model(base_rf)
            await asyncio.to_thread(models_new["random_forest"].fit, train_scaled, train_l, sample_weight=train_w)

        if getattr(settings, "MODEL_ENABLE_XGBOOST", True):
            # Compute scale_pos_weight for class imbalance (n_neg / n_pos)
            _n_pos = max(int(train_l.sum()), 1)
            _n_neg = max(len(train_l) - _n_pos, 1)
            _spw = _n_neg / _n_pos
            base_xgb = xgb.XGBClassifier(n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42, n_jobs=-1, scale_pos_weight=_spw)
            models_new["xgboost"] = _wrap_model(base_xgb)
            await asyncio.to_thread(models_new["xgboost"].fit, train_scaled, train_l, sample_weight=train_w)

        if getattr(settings, "MODEL_ENABLE_GRADIENT_BOOSTING", True):
            base_gb = GradientBoostingClassifier(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42)
            models_new["gradient_boosting"] = _wrap_model(base_gb)
            await asyncio.to_thread(models_new["gradient_boosting"].fit, train_scaled, train_l, sample_weight=train_w)

        # --- Diversity models: different model families for ensemble decorrelation ---

        # LogisticRegression: linear decision boundary, naturally calibrated, low overfitting on small data
        if getattr(settings, "MODEL_ENABLE_LOGISTIC_REGRESSION", True):
            base_lr = LogisticRegression(
                C=1.0, solver="lbfgs", max_iter=1000,
                random_state=42, class_weight="balanced",
            )
            models_new["logistic_regression"] = _wrap_model(base_lr)
            await asyncio.to_thread(models_new["logistic_regression"].fit, train_scaled, train_l, sample_weight=train_w)

        # ExtraTrees: random split thresholds decorrelate from RandomForest
        if getattr(settings, "MODEL_ENABLE_EXTRA_TREES", True):
            base_et = ExtraTreesClassifier(
                n_estimators=100, max_depth=8, min_samples_leaf=5,
                random_state=42, n_jobs=-1, class_weight="balanced",
            )
            models_new["extra_trees"] = _wrap_model(base_et)
            await asyncio.to_thread(models_new["extra_trees"].fit, train_scaled, train_l, sample_weight=train_w)

        # HistGradientBoosting: sklearn's LightGBM-equivalent, histogram binning, native NaN support
        if getattr(settings, "MODEL_ENABLE_HIST_GRADIENT_BOOSTING", True):
            base_hgb = HistGradientBoostingClassifier(
                max_iter=100, max_depth=5, learning_rate=0.1,
                min_samples_leaf=10, max_bins=64,
                random_state=42, early_stopping=False,
                class_weight="balanced",
            )
            models_new["hist_gradient_boosting"] = _wrap_model(base_hgb)
            await asyncio.to_thread(models_new["hist_gradient_boosting"].fit, train_scaled, train_l, sample_weight=train_w)

        # LightGBM: fast gradient boosting with leaf-wise growth (optional dependency)
        if getattr(settings, "MODEL_ENABLE_LIGHTGBM", True):
            try:
                import lightgbm as lgb
                base_lgb = lgb.LGBMClassifier(
                    n_estimators=100, max_depth=6, learning_rate=0.1,
                    num_leaves=31, min_child_samples=10,
                    random_state=42, n_jobs=-1, verbose=-1,
                    is_unbalance=True,
                )
                models_new["lightgbm"] = _wrap_model(base_lgb)
                await asyncio.to_thread(models_new["lightgbm"].fit, train_scaled, train_l, sample_weight=train_w)
                # lleaves: LLVM-compile LightGBM for 16x faster inference (Linux only)
                try:
                    import lleaves
                    import tempfile, os
                    _lgb_model = base_lgb  # unwrapped model for compilation
                    _cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
                    _lgb_path = os.path.join(_cache_dir, "lgbm_compiled.txt")
                    _compiled_path = os.path.join(_cache_dir, "lgbm_compiled.so")
                    _lgb_model.booster_.save_model(_lgb_path)
                    _llvm_model = lleaves.Model(model_file=_lgb_path)
                    _llvm_model.compile(cache=_compiled_path)
                    self._lleaves_model = lleaves.Model(model_file=_compiled_path)
                    logger.info("lleaves: LightGBM compiled for fast inference")
                except ImportError:
                    pass  # lleaves not installed — use native LightGBM
                except Exception as e:
                    logger.debug("lleaves compilation failed (non-fatal): %s", e)
            except ImportError:
                logger.debug("lightgbm not installed; skipping LGBMClassifier")

        # CatBoost: ordered boosting, handles categorical features (optional dependency)
        # NOTE: CatBoost is NOT compatible with CalibratedClassifierCV (missing __sklearn_tags__),
        # so we skip the _wrap_model calibration wrapper and use CatBoost directly.
        if getattr(settings, "MODEL_ENABLE_CATBOOST", True):
            try:
                from catboost import CatBoostClassifier
                import os
                cb_train_dir = os.path.join(os.environ.get("WORKDIR", os.getcwd()), "data", "catboost_info")
                os.makedirs(cb_train_dir, exist_ok=True)
                base_cb = CatBoostClassifier(
                    iterations=100, depth=6, learning_rate=0.1,
                    random_seed=42, verbose=0, auto_class_weights="Balanced",
                    thread_count=-1, train_dir=cb_train_dir,
                )
                models_new["catboost"] = base_cb  # Skip _wrap_model (CatBoost incompatible with CalibratedClassifierCV)
                await asyncio.to_thread(models_new["catboost"].fit, train_scaled, train_l, sample_weight=train_w)
            except ImportError:
                logger.debug("catboost not installed; skipping CatBoostClassifier")

        # --- GAP-4: Non-tree diversity models (different inductive biases) ---

        # RidgeClassifier: L2-regularized linear classifier, fast, complements LogReg with different loss function
        # NOTE: RidgeClassifier lacks predict_proba — always wrap with CalibratedClassifierCV for ensemble compatibility
        if getattr(settings, "MODEL_ENABLE_RIDGE", True):
            from sklearn.calibration import CalibratedClassifierCV as _CCV
            base_ridge = RidgeClassifier(alpha=1.0, class_weight="balanced")
            models_new["ridge"] = _CCV(base_ridge, cv=3, method="sigmoid")
            await asyncio.to_thread(models_new["ridge"].fit, train_scaled, train_l, sample_weight=train_w)

        # KNeighborsClassifier: pure instance-based (no parametric model), decorrelates from all tree/linear models
        if getattr(settings, "MODEL_ENABLE_KNN", True):
            n_neighbors = min(15, max(3, len(train_l) // 20))  # Scale with data size
            base_knn = KNeighborsClassifier(
                n_neighbors=n_neighbors, weights="distance", n_jobs=-1,
            )
            # NOTE: KNN does not support sample_weight in fit(), so we skip it
            models_new["knn"] = base_knn  # Skip _wrap_model (KNN has predict_proba natively)
            await asyncio.to_thread(models_new["knn"].fit, train_scaled, train_l)

        # MLPClassifier: genuine neural network — only non-tree, non-linear, non-instance model
        # sklearn 1.7+ supports sample_weight in fit() — uses class-balanced weights like tree models
        if getattr(settings, "MODEL_ENABLE_MLP", True):
            mlp = MLPClassifier(
                hidden_layer_sizes=(64, 32),
                activation="relu",
                solver="adam",
                alpha=0.001,          # L2 regularization
                max_iter=300,
                early_stopping=True,  # 10% internal holdout, stops on plateau
                random_state=42,
            )
            models_new["mlp"] = mlp
            await asyncio.to_thread(models_new["mlp"].fit, train_scaled, train_l, sample_weight=train_w)

        # TabPFN: transformer tabular foundation model (Prior Labs 2025)
        # In-context learning — no weight updates, fits in ~1 forward pass
        # Outperforms tuned XGBoost on <10K rows; already calibrated, no _wrap_model needed
        if getattr(settings, "MODEL_ENABLE_TABPFN", True):
            try:
                from tabpfn import TabPFNClassifier
                _tabpfn_n = min(len(train_scaled), 10000)  # TabPFN context limit
                _tabpfn_X = train_scaled[:_tabpfn_n]
                _tabpfn_y = train_l[:_tabpfn_n]
                _tabpfn = TabPFNClassifier(device="cpu", n_estimators=4)  # 4 for fast retrain
                await asyncio.to_thread(_tabpfn.fit, _tabpfn_X, _tabpfn_y)
                models_new["tabpfn"] = _tabpfn
                logger.info("TabPFN model fitted", n_samples=_tabpfn_n)
            except ImportError:
                logger.debug("tabpfn not installed — skipping (pip install tabpfn)")
            except Exception as _tabpfn_err:
                logger.warning("TabPFN fitting failed (non-fatal, skipping): %s", _tabpfn_err)

        # Validation: reject if models perform worse than random on hold-out
        if len(val_f) >= 10:
            from sklearn.metrics import accuracy_score, brier_score_loss
            from base_engine.prediction.bias_detector import BiasDetector

            val_accs = []
            new_brier_scores = []
            for name, model in models_new.items():
                preds = model.predict(val_scaled)
                acc = accuracy_score(val_l, preds)
                val_accs.append(acc)
                # Compute Brier score for rollback comparison
                if hasattr(model, "predict_proba"):
                    probs = model.predict_proba(val_scaled)
                    if probs.ndim == 2 and probs.shape[1] >= 2:
                        new_brier_scores.append(brier_score_loss(val_l, probs[:, 1]))
                # Bias checks: price parroting, base rate
                price_idx = self.feature_columns.index("price") if "price" in self.feature_columns else 0
                current_prices = val_f[:, price_idx] if val_f.shape[1] > price_idx else None
                bias_warnings = BiasDetector.run_checks(
                    model, val_scaled, val_l,
                    current_prices=current_prices,
                )
                if bias_warnings:
                    logger.warning("BiasDetector flags for %s", name, warnings=bias_warnings)

            # H3: DummyClassifier baseline — compare models against majority-class predictor
            try:
                from sklearn.dummy import DummyClassifier
                dummy = DummyClassifier(strategy="most_frequent")
                dummy.fit(train_scaled, train_l)
                dummy_preds = dummy.predict(val_scaled)
                dummy_acc = accuracy_score(val_l, dummy_preds)
                dummy_brier = None
                if hasattr(dummy, "predict_proba"):
                    dummy_probs = dummy.predict_proba(val_scaled)
                    if dummy_probs.ndim == 2 and dummy_probs.shape[1] >= 2:
                        dummy_brier = brier_score_loss(val_l, dummy_probs[:, 1])
                logger.info(
                    "DummyClassifier baseline (most_frequent)",
                    dummy_accuracy=round(dummy_acc * 100, 1),
                    dummy_brier=round(dummy_brier, 4) if dummy_brier is not None else None,
                    val_samples=len(val_l),
                )
                # Compare each model against the dummy baseline
                models_beating_dummy = 0
                for i, (name, _) in enumerate(models_new.items()):
                    model_acc = val_accs[i] if i < len(val_accs) else 0
                    margin = model_acc - dummy_acc
                    if margin > 0.01:  # At least 1% above dummy
                        models_beating_dummy += 1
                    logger.info(
                        "Model vs DummyClassifier",
                        model=name,
                        model_accuracy=round(model_acc * 100, 1),
                        dummy_accuracy=round(dummy_acc * 100, 1),
                        margin_pct=round(margin * 100, 1),
                        beats_dummy=margin > 0.01,
                    )
                # T9 FIX: Require a MAJORITY of models to beat the dummy, not just 1.
                # On imbalanced data (70/30 YES/NO), a 51% model is worse than majority-class.
                min_beating = max(1, (len(models_new) + 1) // 2)  # majority: ceil(N/2)
                if models_beating_dummy < min_beating:
                    logger.warning(
                        "REJECTED: Only %d/%d models beat DummyClassifier baseline (%.1f%%). "
                        "Need majority (%d). Keeping previous models.",
                        models_beating_dummy, len(models_new), dummy_acc * 100, min_beating,
                    )
                    return  # Don't update self.models
                else:
                    logger.info(
                        "Models beating DummyClassifier: %d/%d (majority=%d)",
                        models_beating_dummy, len(models_new), min_beating,
                    )
            except Exception as e:
                logger.debug("DummyClassifier baseline check failed: %s", e)

            # T9 FIX: NEVER drop models — weight by effectiveness with anti-domination cap.
            # Training data may be noisy; a model that looks bad today may be right tomorrow.
            # Every model stays in the ensemble. Weights are Brier-score-based percentiles
            # with a hard cap so no single model can overpower the system.
            model_names = list(models_new.keys())
            _max_weight_cap = 0.20  # No model can exceed 20% of ensemble
            _min_weight_floor = 0.01  # Worst model still gets 1%

            # Build effectiveness score: prefer Brier (probability-aware) over accuracy
            _effectiveness = {}
            for i, name in enumerate(model_names):
                brier_i = new_brier_scores[i] if i < len(new_brier_scores) else None
                acc_i = val_accs[i] if i < len(val_accs) else 0.5
                if brier_i is not None:
                    # Brier: lower is better → invert. Range [0,1] → score [0,1]
                    _effectiveness[name] = max(0.01, 1.0 - brier_i)
                else:
                    # Fallback to accuracy
                    _effectiveness[name] = max(0.01, acc_i)

            # Convert to percentile-based weights (rank-based to prevent outlier domination)
            _sorted_models = sorted(_effectiveness.items(), key=lambda x: x[1])
            n = len(_sorted_models)
            _rank_scores = {}
            for rank, (name, eff) in enumerate(_sorted_models):
                # Rank 0 = worst, rank n-1 = best. Use rank^1.5 for gentle nonlinearity
                _rank_scores[name] = (rank + 1) ** 1.5

            # Normalize to weights summing to 1.0
            total_rank = sum(_rank_scores.values())
            _new_weights = {name: score / total_rank for name, score in _rank_scores.items()}

            # Apply anti-domination cap and minimum floor
            _capped = False
            for name in _new_weights:
                if _new_weights[name] > _max_weight_cap:
                    _new_weights[name] = _max_weight_cap
                    _capped = True
                if _new_weights[name] < _min_weight_floor:
                    _new_weights[name] = _min_weight_floor

            # Re-normalize after capping
            w_sum = sum(_new_weights.values())
            if w_sum > 0:
                _new_weights = {k: v / w_sum for k, v in _new_weights.items()}

            self.model_weights = _new_weights

            # A3: Stacked Ridge meta-learner — replace rank weights with OOF-trained Ridge.
            # Uses out-of-fold predictions so each model sees unseen data → no leakage.
            # Falls back to rank weights if not enough data or fewer than 3 models.
            try:
                from sklearn.linear_model import RidgeCV
                from sklearn.model_selection import cross_val_predict as _cvp
                if len(train_scaled) >= 50 and len(models_new) >= 3:
                    _oof_cols = []
                    _oof_names = []
                    for _m_name, _m in models_new.items():
                        if hasattr(_m, "predict_proba"):
                            try:
                                _oof = _cvp(_m, train_scaled, train_l, cv=3, method="predict_proba")
                                _oof_cols.append(_oof[:, 1])
                                _oof_names.append(_m_name)
                            except Exception:
                                pass
                    if len(_oof_cols) >= 3:
                        _oof_matrix = np.column_stack(_oof_cols)
                        _meta = RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0])
                        _meta.fit(_oof_matrix, train_l)
                        _raw_coef = _meta.coef_
                        # Ensure all weights are positive (Ridge can go negative)
                        _pos_coef = np.maximum(_raw_coef, 0.001)
                        _stacked_weights = _pos_coef / _pos_coef.sum()
                        # Merge back — only update models that participated in stacking
                        _stacked_dict = dict(zip(_oof_names, _stacked_weights.tolist()))
                        # Models without predict_proba keep their rank weight; re-normalize
                        _total_oof_share = sum(
                            _new_weights[n] for n in _new_weights if n not in _stacked_dict
                        )
                        _oof_budget = 1.0 - _total_oof_share
                        _oof_budget = max(0.1, min(0.9, _oof_budget))  # keep it sane
                        for _n, _w in _stacked_dict.items():
                            _new_weights[_n] = _w * _oof_budget
                        # Re-normalize the full dict
                        _w_total = sum(_new_weights.values())
                        if _w_total > 0:
                            _new_weights = {k: v / _w_total for k, v in _new_weights.items()}
                        self.model_weights = _new_weights
                        logger.info(
                            "A3 Ridge stacking weights: %s",
                            {k: round(v, 3) for k, v in sorted(self.model_weights.items(), key=lambda x: -x[1])},
                        )
            except Exception as _a3_err:
                logger.debug("A3 Ridge stacking failed (using rank weights): %s", _a3_err)

            # Log the full weight breakdown
            _weight_summary = sorted(
                [(name, _new_weights[name], _effectiveness[name])
                 for name in model_names],
                key=lambda x: -x[1],
            )
            for name, weight, eff in _weight_summary:
                logger.info(
                    "Model weight: %-25s  weight=%.1f%%  effectiveness=%.3f%s",
                    name, weight * 100, eff,
                    "  [CAPPED]" if weight * w_sum > _max_weight_cap * 0.99 else "",
                )

            # Ensemble health: median accuracy across ALL models
            median_acc = sorted(val_accs)[len(val_accs) // 2] if val_accs else 0.0

            # --- Model rollback gate: compare new vs old on validation set ---
            # Only promote new models if they match or beat the old ensemble's Brier score.
            # This prevents a retrain that passes the >0.50 bar but is worse than what we had.
            new_brier = float(np.mean(new_brier_scores)) if new_brier_scores else None
            if new_brier is not None and self.models and self.scaler is not None:
                old_brier_scores = []
                try:
                    old_val_scaled = self.scaler.transform(val_f)
                    for name, model in self.models.items():
                        if hasattr(model, "predict_proba"):
                            probs = model.predict_proba(old_val_scaled)
                            if probs.ndim == 2 and probs.shape[1] >= 2:
                                old_brier_scores.append(brier_score_loss(val_l, probs[:, 1]))
                except Exception as e:
                    logger.debug("Old model Brier comparison failed (promoting new): %s", e)

                if old_brier_scores:
                    old_brier = float(np.mean(old_brier_scores))
                    # Allow a small tolerance (0.02) so marginal fluctuations don't block promotion
                    tolerance = getattr(settings, "MODEL_ROLLBACK_BRIER_TOLERANCE", 0.02)
                    if new_brier > old_brier + tolerance:
                        logger.warning(
                            "Model rollback: new ensemble Brier %.4f worse than old %.4f "
                            "(tolerance=%.3f). Keeping previous models.",
                            new_brier, old_brier, tolerance,
                        )
                        return  # Don't update self.models — keep old
                    logger.info(
                        "Model promoted: new Brier=%.4f vs old Brier=%.4f (improvement=%.4f)",
                        new_brier, old_brier, old_brier - new_brier,
                    )
                else:
                    logger.info("No old models to compare — promoting new (Brier=%.4f)", new_brier)
            else:
                logger.info("Validation passed", val_accuracy=median_acc if 'median_acc' in dir() else min(val_accs), n_val=len(val_l))

        self.models = models_new
        self.scaler = scaler_new
        self.last_trained_at = datetime.now(timezone.utc)
        # L2 FIX: Store per-model validation accuracy for MetaLearner
        # All models kept (T9 downweights, never drops) — map accuracy to model names
        _model_names = model_names if 'model_names' in dir() else list(models_new.keys())
        if val_accs and _model_names:
            _acc_by_name = dict(zip(_model_names, val_accs))
            self._per_model_val_accuracy = {n: _acc_by_name[n] for n in models_new if n in _acc_by_name}
        else:
            self._per_model_val_accuracy = {}

        # Set drift baseline from training data
        try:
            all_probs = []
            for name, model in self.models.items():
                if hasattr(model, "predict_proba"):
                    probs = model.predict_proba(train_scaled)
                    if probs.ndim == 2 and probs.shape[1] >= 2:
                        all_probs.extend(probs[:, 1].tolist())
            if all_probs:
                self._drift_tracker.set_baseline(
                    mean=float(np.mean(all_probs)),
                    std=float(np.std(all_probs)),
                )
        except Exception as e:
            logger.debug("Could not set drift baseline: %s", e)

        # PSI feature baselines: save decile distributions for drift detection (Item 21)
        try:
            percentiles = list(range(0, 101, 10))  # 0,10,20,...,100
            self._feature_baselines = {}
            for i, col in enumerate(self.feature_columns):
                self._feature_baselines[col] = np.percentile(train_scaled[:, i], percentiles).tolist()
            self._psi_prediction_count = 0
            self._recent_features = deque(maxlen=500)
            logger.info("PSI baselines computed for %d features", len(self._feature_baselines))
        except Exception as e:
            logger.debug("Could not compute PSI baselines: %s", e)

        logger.info("Models trained successfully")

        # Session 50: Log feature importance after training to identify dead/noisy features
        try:
            fi = self.get_feature_scores()
            if fi:
                sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)
                top_10 = sorted_fi[:10]
                bottom_5 = sorted_fi[-5:] if len(sorted_fi) > 5 else []
                logger.info(
                    "Feature importance (top 10): %s",
                    ", ".join(f"{k}={v:.4f}" for k, v in top_10),
                )
                if bottom_5:
                    logger.info(
                        "Feature importance (bottom 5): %s",
                        ", ".join(f"{k}={v:.4f}" for k, v in bottom_5),
                    )
                # Log features with near-zero importance (candidates for removal)
                dead_features = [k for k, v in fi.items() if v < 0.001]
                if dead_features:
                    logger.warning("Near-zero importance features (%d): %s", len(dead_features), dead_features)
        except Exception as e:
            logger.debug("Feature importance logging failed: %s", e)

    def get_feature_scores(self) -> Dict[str, float]:
        """Build feature name -> importance from tree models (for MetaLearner feature selection). MDI can overweight high-cardinality features."""
        if not self.models or not self.feature_columns:
            return {}
        scores: Dict[str, float] = {}
        n_contributors = 0
        for name, model in self.models.items():
            est = model
            if hasattr(model, "calibrated_classifiers_"):
                est = model.calibrated_classifiers_[0].estimator if model.calibrated_classifiers_ else model
            if not hasattr(est, "feature_importances_"):
                continue
            imp = getattr(est, "feature_importances_", None)
            if imp is None or len(imp) != len(self.feature_columns):
                continue
            n_contributors += 1
            for i, col in enumerate(self.feature_columns):
                scores[col] = scores.get(col, 0.0) + float(imp[i])
        if n_contributors > 0:
            scores = {k: v / n_contributors for k, v in scores.items()}
        return scores

    def get_feature_importance(self) -> Dict[str, float]:
        """Return blended feature importance scores (MDI + FeatureEngineer).
        Returns empty dict if no importance data available yet."""
        scores = dict(self._feature_importance_scores) if self._feature_importance_scores else self.get_feature_scores()
        if not scores:
            return {}
        # Blend with FeatureEngineer's own importance if available
        if self._feature_engineer:
            fe_importance = self._feature_engineer.get_feature_importance()
            if fe_importance:
                for name, score in fe_importance.items():
                    if name in scores:
                        scores[name] = (scores[name] + score) / 2.0
        return scores

    def check_model_drift(self) -> Dict[str, Any]:
        """Check for model prediction drift. Call periodically from monitoring."""
        return self._drift_tracker.check_drift()

    def _run_psi_check(self, recent_features: list, baselines: dict) -> None:
        """Compare recent scaled features against training baselines using PSI.

        Correct PSI: compute proportion of data in each decile bin (not percentile values),
        then sum (P_actual - P_expected) * ln(P_actual / P_expected) across bins.
        Baselines store decile edge thresholds; we use them to assign observations to bins.
        """
        threshold = getattr(settings, "PSI_DRIFT_THRESHOLD", 0.2)
        recent_arr = np.array(recent_features)
        n_recent = len(recent_arr)
        if n_recent == 0:
            return
        drifted = []
        n_bins = 10  # 10 decile buckets (0-10%, 10-20%, ..., 90-100%)
        expected_prop = 1.0 / n_bins  # Uniform by construction (decile edges)
        for i, col in enumerate(self.feature_columns):
            if col not in baselines:
                continue
            edges = baselines[col]  # 11 decile thresholds (0th through 100th percentile)
            if len(edges) < 2:
                continue
            actual_vals = recent_arr[:, i]
            # Count observations per bin using the training decile edges as boundaries
            psi = 0.0
            for b in range(n_bins):
                lo = edges[b]
                hi = edges[b + 1] if b + 1 < len(edges) else float("inf")
                if b == 0:
                    count = np.sum(actual_vals <= hi)
                elif b == n_bins - 1:
                    count = np.sum(actual_vals > lo)
                else:
                    count = np.sum((actual_vals > lo) & (actual_vals <= hi))
                actual_prop = max(count / n_recent, 1e-8)
                psi += (actual_prop - expected_prop) * np.log(actual_prop / expected_prop)
            if psi > threshold:
                drifted.append((col, round(psi, 4)))
        if drifted:
            logger.warning("PSI feature drift detected", drifted_features=drifted[:5], count=len(drifted))

    async def retrain(self) -> None:
        """Retrain models from current DB data and save if persistence enabled. Skips if trained recently.
        Backs up current model cache before retrain so rollback has a file to fall back to."""
        interval_hours = getattr(settings, "RETRAIN_INTERVAL_HOURS", 6)
        now = datetime.now(timezone.utc)
        if self.last_trained_at is not None and (now - self.last_trained_at) < timedelta(hours=max(1, interval_hours // 2)):
            logger.info("Skipping retrain (trained recently at %s)", self.last_trained_at.isoformat())
            return
        # Backup current model cache before retrain (for manual rollback)
        self._backup_model_cache()
        try:
            await self._train_models()
            self._save_models_to_file()  # Always cache locally for fast restart
            if getattr(settings, "LEARNING_PERSISTENCE", False) and self.db.session_factory:
                await self.save_models_to_db()
        except RuntimeError as e:
            if "Insufficient" in str(e) or "No training" in str(e):
                logger.warning("Skipping retrain (insufficient data): %s", e)
                return
            raise
    
    async def save_models_to_db(self) -> None:
        """Persist models and scaler to ml_models table. Version increments per model_name (Phase 3 versioning). Learned weights/blend stored in first model's metrics."""
        if not self.db.session_factory or not self.models:
            return
        import pickle
        from base_engine.data.database import MLModel
        from sqlalchemy import update, select, func
        now_iso = datetime.now(timezone.utc).isoformat()
        learned_metrics = {
            "model_weights": self.model_weights,
            "ensemble_blend": self.ensemble_blend,
            "weights_updated_at": now_iso,
        }
        if self.best_feature_names is not None:
            learned_metrics["best_feature_names"] = self.best_feature_names
        _saved_versions: dict = {}  # name → (version, model_type, metrics)
        async with self.db.get_session() as session:
            await session.execute(update(MLModel).values(is_active=False))
            scaler_blob = pickle.dumps(self.scaler) if self.scaler else None
            skv = getattr(sklearn, "__version__", None) or ""
            first = True
            for name, model in self.models.items():
                r = await session.execute(
                    select(func.coalesce(func.max(MLModel.version), 0)).where(MLModel.model_name == name)
                )
                next_version = (r.scalar() or 0) + 1
                metrics = {"sklearn_version": skv}
                if first:
                    metrics.update(learned_metrics)
                    first = False
                rec = MLModel(
                    model_name=name,
                    model_type=type(model).__name__,
                    model_data=pickle.dumps(model),
                    scaler_data=scaler_blob,
                    is_active=True,
                    version=next_version,
                    metrics=metrics,
                )
                session.add(rec)
                _saved_versions[name] = (next_version, type(model).__name__, metrics)
            await session.commit()
        logger.info("Models saved to database")
        # model_registry registration removed — migration 052 drops table

    async def load_models_from_db(self) -> None:
        """Load active models and scaler from ml_models table. Validates sklearn version and runs test predict."""
        if not self.db.session_factory:
            return
        import pickle
        from base_engine.data.database import MLModel
        from sqlalchemy import select
        async with self.db.get_session() as session:
            result = await session.execute(
                select(MLModel).where(MLModel.is_active == True).order_by(MLModel.version.desc())
            )
            rows = result.scalars().all()
        if not rows:
            raise RuntimeError("No active models in database")
        current_skv = getattr(sklearn, "__version__", None) or ""
        for r in rows:
            stored_skv = (r.metrics or {}).get("sklearn_version") if getattr(r, "metrics", None) else None
            if stored_skv not in (None, "") and stored_skv != current_skv:
                raise RuntimeError(
                    "Model sklearn version mismatch: stored=%s current=%s; will retrain" % (stored_skv, current_skv)
                )
        self.models = {}
        scaler_loaded = False
        for r in rows:
            if r.model_data is None:
                logger.warning("Model %s has NULL model_data, skipping", r.model_name)
                continue
            obj = pickle.loads(r.model_data)
            self.models[r.model_name] = obj
            # Only load scaler once (from first model that has it) to prevent overwrite bug
            if not scaler_loaded and r.scaler_data:
                self.scaler = pickle.loads(r.scaler_data)
                scaler_loaded = True
            if hasattr(obj, "predict"):
                try:
                    n_feat = getattr(obj, "n_features_in_", 1) or 1
                    test_in = np.zeros((1, n_feat), dtype=np.float32)
                    obj.predict(test_in)
                except Exception as e:
                    logger.warning("Model validation predict failed for %s: %s", r.model_name, e)
                    raise RuntimeError("Model validation failed; will retrain") from e
        first_row = rows[0] if rows else None
        metrics = getattr(first_row, "metrics", None) or {} if first_row else {}
        if metrics and (metrics.get("model_weights") is not None or metrics.get("ensemble_blend") is not None):
            updated_at = metrics.get("weights_updated_at")
            if updated_at:
                try:
                    dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                    if getattr(dt, "tzinfo", None) is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if age_hours > MAX_WEIGHT_AGE_HOURS:
                        logger.warning(
                            "Learned weights older than %s hours (%.1f), resetting to equal weights",
                            MAX_WEIGHT_AGE_HOURS, age_hours,
                        )
                        self.model_weights = {}
                        # Session 50: respect ENSEMBLE_BLEND env var instead of hardcoded 0.6
                        _env_blend = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
                        self.ensemble_blend = max(BLEND_MIN, min(BLEND_MAX, _env_blend))
                    else:
                        self.model_weights = metrics.get("model_weights") or {}
                        _env_blend = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
                        self.ensemble_blend = max(BLEND_MIN, min(BLEND_MAX, _env_blend))
                    self.best_feature_names = metrics.get("best_feature_names")
                except Exception as e:
                    logger.debug("weights_updated_at parse failed, using stored weights: %s", e)
                    self.model_weights = metrics.get("model_weights") or {}
                    _env_blend = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
                    self.ensemble_blend = max(BLEND_MIN, min(BLEND_MAX, _env_blend))
                    self.best_feature_names = metrics.get("best_feature_names")
            else:
                self.model_weights = metrics.get("model_weights") or {}
                _env_blend = float(os.getenv("ENSEMBLE_BLEND", "1.0"))
                self.ensemble_blend = max(BLEND_MIN, min(BLEND_MAX, _env_blend))
                self.best_feature_names = metrics.get("best_feature_names")
        # I09: Renormalize weights to include all current models.
        # Models added since the last cache save get 1/N default weight so they're included
        # immediately rather than stuck at zero weight until the next retrain.
        if self.models and self.model_weights:
            _n = max(1, len(self.models))
            _w = self.model_weights
            self.model_weights = {k: _w.get(k, 1.0 / _n) for k in self.models}
            _total = sum(self.model_weights.values())
            if _total > 0:
                self.model_weights = {k: v / _total for k, v in self.model_weights.items()}
            logger.debug(
                "I09 model_weights renormalized: %d models, sum=%.4f",
                len(self.model_weights), sum(self.model_weights.values()),
            )
        self.initialized = True
        logger.info("Models loaded from database")
    
    async def _fallback_training_from_prices(self, session) -> Optional[pd.DataFrame]:
        """
        Build training data from market_prices + resolved markets when no trades exist.
        Uses last price per (market_id, token_id) before resolution; outcome from resolution (YES=1, NO=0).
        """
        from sqlalchemy import text
        min_vol = getattr(settings, "TRAINING_MIN_VOLUME", 500)
        price_lookback_days = getattr(settings, "TRAINING_PRICE_LOOKBACK_DAYS", 180)
        # Optimized: inner subquery scopes to resolved markets only, avoiding full 1M+ row scan.
        query = text(f"""
            SELECT
                mp.market_id, mp.token_id, mp.price, 1.0 as size,
                COALESCE(m.liquidity, 0) as liquidity,
                COALESCE(m.volume, 0) as volume,
                m.resolved, m.resolution, m.resolved_at,
                0.5 as user_win_rate, 0.0 as user_profit,
                CASE WHEN (mp.token_id = m.yes_token_id AND m.resolution = 'YES')
                          OR (mp.token_id = m.no_token_id AND m.resolution = 'NO') THEN 1
                     WHEN (mp.token_id = m.yes_token_id AND m.resolution = 'NO')
                          OR (mp.token_id = m.no_token_id AND m.resolution = 'YES') THEN 0
                     ELSE NULL END as outcome,
                mp.timestamp as trade_ts
            FROM market_prices mp
            JOIN markets m ON (mp.market_id = m.id OR mp.market_id = m.condition_id)
            JOIN (
                SELECT mp2.market_id, mp2.token_id, MAX(mp2.timestamp) as max_ts
                FROM market_prices mp2
                JOIN markets m2 ON (mp2.market_id = m2.id OR mp2.market_id = m2.condition_id)
                WHERE m2.resolved = TRUE AND m2.resolution IN ('YES', 'NO')
                AND mp2.token_id IS NOT NULL
                AND mp2.timestamp >= NOW() - INTERVAL '{price_lookback_days} days'
                GROUP BY mp2.market_id, mp2.token_id
            ) latest ON mp.market_id = latest.market_id
                     AND mp.token_id = latest.token_id
                     AND mp.timestamp = latest.max_ts
            WHERE m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
            AND mp.token_id IS NOT NULL
            AND COALESCE(m.volume, 0) >= {min_vol}
            LIMIT 10000
        """)
        result = await session.execute(query)
        rows = result.fetchall()
        if not rows:
            return None
        return pd.DataFrame([dict(row._mapping) for row in rows])

    async def _get_paper_trade_training_rows(self, session) -> Optional[pd.DataFrame]:
        """Fetch resolved paper trades as training data (feedback loop)."""
        max_rows = getattr(settings, "PAPER_TRADE_TRAINING_MAX_ROWS", 5000)
        try:
            from sqlalchemy import text
            result = await session.execute(text(f"""
                SELECT pt.market_id, pt.token_id, pt.price, pt.size,
                       pt.created_at as trade_ts,
                       m.category, COALESCE(m.liquidity, 0) as liquidity,
                       COALESCE(m.volume, 0) as volume,
                       0.5 as user_win_rate, 0.0 as user_profit,
                       pt.realized_pnl,
                       COALESCE(ts.signal_confidence, 0.5) as signal_confidence,
                       CASE
                           WHEN ts.signal_direction = 'YES' AND LOWER(pt.side) = 'yes' THEN 1.0
                           WHEN ts.signal_direction = 'NO' AND LOWER(pt.side) IN ('no', 'sell') THEN 1.0
                           WHEN ts.signal_direction IS NOT NULL THEN 0.0
                           ELSE 0.5
                       END as signal_direction_encoded,
                       CASE
                           WHEN m.resolution = 'YES' AND LOWER(pt.side) = 'yes' THEN 1
                           WHEN m.resolution = 'YES' AND LOWER(pt.side) IN ('no', 'sell') THEN 0
                           WHEN m.resolution = 'NO' AND LOWER(pt.side) = 'yes' THEN 0
                           WHEN m.resolution = 'NO' AND LOWER(pt.side) IN ('no', 'sell') THEN 1
                           ELSE NULL
                       END as outcome
                FROM paper_trades pt
                JOIN markets m ON pt.market_id = CAST(m.id AS TEXT)
                LEFT JOIN trade_signals ts ON ts.trade_id = pt.id
                WHERE m.resolution IN ('YES', 'NO')
                  AND pt.resolution IS NOT NULL
                LIMIT {max_rows}
            """))
            rows = result.fetchall()
            if not rows:
                return None
            df = pd.DataFrame([dict(row._mapping) for row in rows])
            df["_source"] = "paper"
            return df
        except Exception as e:
            logger.debug("Paper trade training data fetch failed: %s", e)
            return None

    async def _get_prediction_log_training_rows(self, session) -> Optional[pd.DataFrame]:
        """Fetch resolved prediction log entries as training data with 'why wrong' signal."""
        max_rows = getattr(settings, "PREDICTION_LOG_TRAINING_MAX_ROWS", 10000)
        try:
            from sqlalchemy import text
            result = await session.execute(text(f"""
                SELECT pl.market_id, pl.token_id, pl.market_price as price,
                       COALESCE(pl.trade_size, 1.0) as size,
                       pl.prediction_time as trade_ts,
                       m.category, COALESCE(m.liquidity, 0) as liquidity,
                       COALESCE(m.volume, 0) as volume,
                       0.5 as user_win_rate, 0.0 as user_profit,
                       pl.predicted_prob,
                       pl.confidence as pred_confidence,
                       pl.edge as pred_edge,
                       pl.was_correct,
                       CASE
                           WHEN m.resolution = 'YES' THEN 1
                           WHEN m.resolution = 'NO' THEN 0
                           ELSE NULL
                       END as outcome
                FROM prediction_log pl
                JOIN markets m ON (pl.market_id = CAST(m.id AS TEXT) OR pl.market_id = m.condition_id)
                WHERE m.resolution IN ('YES', 'NO')
                LIMIT {max_rows}
            """))
            rows = result.fetchall()
            if not rows:
                return None
            df = pd.DataFrame([dict(row._mapping) for row in rows])
            df["_source"] = "prediction_log"
            return df
        except Exception as e:
            logger.debug("Prediction log training data fetch failed: %s", e)
            return None

    async def _prepare_training_data(self) -> tuple:
        """
        Build (features, labels, sample_weights) for model training.

        Temporal leakage safeguards:
        1. Resolution-cutoff: only trades BEFORE resolution (m.resolved_at) are used
        2. Convergence-zone exclusion: trades within 6h of resolution are dropped (line 651)
           — prices converge to outcome near resolution; training on those is hindsight bias
        3. Walk-forward split: train on oldest 80%, validate on newest 20% (line 250)
           — prevents future data leaking into model fitting
        4. user_win_rate / user_profit: aggregate stats that include future trades (accepted
           trade-off, documented inline at lines 603-607)
        """
        if self.db.session_factory is None:
            raise RuntimeError("Database not initialized. Set DATABASE_URL and initialize.")
        db_url = getattr(settings, "DATABASE_URL", "") or ""
        on_pooler = False  # Direct local PG — no external pooler
        use_resolution = getattr(settings, "USE_RESOLUTION_LABEL", True)
        use_path = getattr(settings, "USE_PATH_SUMMARY", True)
        use_regime = getattr(settings, "USE_REGIME_FEATURES", True)
        path_max_rows = getattr(settings, "PATH_SUMMARY_MAX_ROWS", 5000)
        from sqlalchemy import text, select, and_, or_
        from base_engine.data.database import MarketPrice
        from base_engine.learning.path_summary import (
            get_path_summary_from_prices,
            get_regime_features_from_prices,
            path_summary_to_feature_list,
            regime_features_to_list,
        )

        # FIX 8: Primary query runs in its own session block that closes BEFORE
        # sub-queries (paper_trade, prediction_log, bulk price) open separate sessions.
        # Previously the primary session stayed open for ~90s during feature computation,
        # holding a pool slot while sub-queries grabbed more → pool exhaustion (10 slots).
        async with self.db.get_session() as session:
            # Set statement timeout for training query — long enough for any model + data size
            # Local PG training can take 3-5 minutes on large datasets
            try:
                await session.execute(text("SET LOCAL statement_timeout = '600s'"))
            except Exception:
                pass  # non-critical if SET fails

            # FIX NEW-1: Handle NULL t.side and NULL t.token_id correctly.
            # Previous: NULL t.side fell through to ELSE → 0, giving wrong label.
            # Now: for resolved markets, use resolution as ground truth (token_id match or side match).
            # For unresolved, use COALESCE(t.side, '') to avoid NULL → 0 mislabeling.
            # Rows where we can't determine outcome get NULL → filtered out by valid_mask below.
            outcome_expr = (
                "CASE "
                "WHEN m.resolved = TRUE AND m.resolution = 'YES' THEN "
                "  CASE WHEN (t.token_id = m.yes_token_id) OR (COALESCE(t.side, '') = 'YES') THEN 1 "
                "       WHEN (t.token_id = m.no_token_id) OR (COALESCE(t.side, '') = 'NO') THEN 0 "
                "       ELSE NULL END "
                "WHEN m.resolved = TRUE AND m.resolution = 'NO' THEN "
                "  CASE WHEN (t.token_id = m.yes_token_id) OR (COALESCE(t.side, '') = 'YES') THEN 0 "
                "       WHEN (t.token_id = m.no_token_id) OR (COALESCE(t.side, '') = 'NO') THEN 1 "
                "       ELSE NULL END "
                "ELSE NULL END"
            )
            if not use_resolution:
                outcome_expr = (
                    "CASE WHEN COALESCE(t.side, '') = 'YES' THEN 1 "
                    "WHEN COALESCE(t.side, '') = 'NO' THEN 0 "
                    "ELSE NULL END"
                )

            # JOIN on id OR condition_id: trades.market_id can be either (Data API uses condition_id)
            # Temporal safeguard: if resolved_at is populated, exclude trades within 6h of resolution
            # (convergence-zone prices are hindsight bias). If resolved_at is NULL, include all trades.
            min_vol = getattr(settings, "TRAINING_MIN_VOLUME", 500)
            # NOTE NEW-2: user_win_rate and user_profit are aggregate stats that include trades after
            # this training row's timestamp (temporal leakage). This is accepted because:
            # NOTE NEW-5: user_win_rate / user_profit were previously cumulative lifetime stats from
            # the users table (temporal leakage: a Jan 2024 trade saw the user's Feb 2026 win rate).
            # Fixed via LATERAL JOIN (uts alias) that computes point-in-time stats from prior resolved
            # trades. At inference, the cumulative users table is still used (correct — we want the
            # most current view when predicting). USE_TEMPORAL_USER_STATS controls the training fix.
            use_elite_net = getattr(settings, "USE_ELITE_NET_DIRECTION", True)
            elite_join = ""
            elite_select = ""
            if use_elite_net:
                # B16 FIX: LATERAL JOIN so elite direction is computed relative to each
                # training row's timestamp (t.timestamp), not NOW(). Prevents temporal leakage
                # where a 3-month-old trade would see today's elite activity.
                elite_join = """
                LEFT JOIN LATERAL (
                    SELECT
                        SUM(CASE WHEN t2.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u2.win_rate, 0.5))
                            / NULLIF(SUM(COALESCE(u2.win_rate, 0.5)), 0) as elite_net_direction,
                        SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '1 hour' THEN
                            CASE WHEN t2.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u2.win_rate, 0.5)
                            ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '1 hour' THEN COALESCE(u2.win_rate, 0.5) ELSE 0 END), 0) as elite_direction_1h,
                        SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '6 hours' THEN
                            CASE WHEN t2.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u2.win_rate, 0.5)
                            ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '6 hours' THEN COALESCE(u2.win_rate, 0.5) ELSE 0 END), 0) as elite_direction_6h,
                        SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '24 hours' THEN
                            CASE WHEN t2.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u2.win_rate, 0.5)
                            ELSE 0 END)
                            / NULLIF(SUM(CASE WHEN t2.timestamp >= t.timestamp - INTERVAL '24 hours' THEN COALESCE(u2.win_rate, 0.5) ELSE 0 END), 0) as elite_direction_24h
                    FROM trades t2 JOIN users u2 ON t2.user_address = u2.address
                    WHERE u2.is_elite = TRUE AND COALESCE(u2.is_likely_market_maker, false) = false
                    AND t2.market_id = t.market_id
                    AND t2.timestamp < t.timestamp
                    AND t2.timestamp >= t.timestamp - INTERVAL '90 days'
                ) ea ON true
                """
                elite_select = ", COALESCE(ea.elite_net_direction, 0) as elite_net_direction, COALESCE(ea.elite_direction_1h, 0) as elite_direction_1h, COALESCE(ea.elite_direction_6h, 0) as elite_direction_6h, COALESCE(ea.elite_direction_24h, 0) as elite_direction_24h"

            # Temporal user stats: compute win_rate / profit as-of each trade's timestamp.
            # Replaces cumulative lifetime stats from the users table (which encoded future performance).
            # Uses LATERAL JOIN — same pattern as elite_join above. Falls back to 0.5/0.0 for users
            # with no prior resolved trades in the 365-day window (same as global-stats default).
            use_temporal_user = getattr(settings, "USE_TEMPORAL_USER_STATS", True)
            if use_temporal_user:
                user_temporal_join = """
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(
                            SUM(CASE WHEN m2.resolution = t2.side THEN 1.0 ELSE 0.0 END)::float
                            / NULLIF(COUNT(*), 0),
                        0.5) as win_rate_at_trade,
                        COALESCE(SUM(COALESCE(t2.pnl, 0)), 0.0) as profit_at_trade
                    FROM trades t2
                    JOIN markets m2 ON (t2.market_id = CAST(m2.id AS TEXT) OR t2.market_id = m2.condition_id)
                    WHERE t2.user_address = t.user_address
                      AND t2.timestamp < t.timestamp
                      AND m2.resolved = TRUE
                      AND m2.resolution IN ('YES', 'NO')
                      AND t2.timestamp >= t.timestamp - INTERVAL '365 days'
                ) uts ON true"""
                user_win_rate_col = "COALESCE(uts.win_rate_at_trade, 0.5)"
                user_profit_col = "COALESCE(uts.profit_at_trade, 0.0)"
            else:
                user_temporal_join = ""
                user_win_rate_col = "COALESCE(u.win_rate, 0.5)"
                user_profit_col = "COALESCE(u.total_profit, 0)"

            # Primary path: ALL trades on resolved markets (elite weighting via sample_weight downstream)
            # FIX: Use UNION ALL instead of OR join — OR in JOIN is slow on large tables
            # Path A: trades.market_id = markets.id (normalized trades)
            # Path B: trades.market_id = markets.condition_id (Data API trades)
            # Elite features preserved in both paths
            # FIX: Handle NULL resolved_at gracefully (skip 6h cutoff when unavailable)
            _base_select = f"""
                SELECT
                    t.market_id, t.token_id, t.price, t.size, t.timestamp as trade_ts,
                    m.category,
                    COALESCE(m.liquidity, 0) as liquidity,
                    COALESCE(m.volume, 0) as volume,
                    m.resolved, m.resolution, m.resolved_at,
                    m.created_at, m.end_date_iso,
                    COALESCE(m.resolution_source, '') as resolution_source,
                    {user_win_rate_col} as user_win_rate,
                    {user_profit_col} as user_profit
                    {elite_select},
                    {outcome_expr} as outcome"""
            _base_where = f"""
                WHERE t.market_id IS NOT NULL
                AND t.timestamp >= NOW() - INTERVAL '365 days'
                AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                AND (
                    CASE
                        WHEN m.resolved_at IS NOT NULL THEN t.timestamp < m.resolved_at - INTERVAL '6 hours'
                        WHEN m.end_date_iso IS NOT NULL THEN t.timestamp < m.end_date_iso - INTERVAL '6 hours'
                        ELSE TRUE
                    END
                )
                AND COALESCE(m.volume, 0) >= {min_vol}"""

            # Path A: trades matched by numeric id; Path B: trades matched by condition_id
            # Exclude Path B rows already matched by Path A to avoid duplicate training samples
            _max_training = int(getattr(settings, "MAX_TRAINING_SAMPLES", 50000))
            _per_path_limit = _max_training  # Each path limited; outer also limited
            query = text(f"""
                SELECT * FROM (
                    (
                    {_base_select}
                    FROM trades t
                    JOIN markets m ON t.market_id = CAST(m.id AS TEXT)
                    LEFT JOIN users u ON t.user_address = u.address
                    {elite_join}
                    {user_temporal_join}
                    {_base_where}
                    LIMIT {_per_path_limit}
                    )
                UNION ALL
                    (
                    {_base_select}
                    FROM trades t
                    JOIN markets m ON t.market_id = m.condition_id
                        AND t.market_id != CAST(m.id AS TEXT)
                    LEFT JOIN users u ON t.user_address = u.address
                    {elite_join}
                    {user_temporal_join}
                    {_base_where}
                    LIMIT {_per_path_limit}
                    )
                ) combined
                LIMIT {_max_training}
            """)
            result = await session.execute(query)
            rows = result.fetchall()
            if rows:
                logger.info("Training data: all trades on resolved markets", sample_count=len(rows))
            if not rows and getattr(settings, "USE_PRICE_HISTORY_TRAINING_FALLBACK", True):
                try:
                    df = await self._fallback_training_from_prices(session)
                    if df is None or df.empty:
                        raise RuntimeError("No training data found in database. Ingest market data and trades first.")
                    logger.info("Training data: price-history fallback (%d rows from resolved markets)", len(df))
                except Exception as e:
                    logger.warning("Price-history training fallback failed: %s", e)
                    raise RuntimeError("No training data found in database. Ingest market data and trades first.")
            elif not rows:
                raise RuntimeError("No training data found in database. Ingest market data and trades first.")
            else:
                df = pd.DataFrame([dict(row._mapping) for row in rows])
        # --- Primary session released here (FIX 8) ---
        # df is now in memory; no further need for the primary DB connection.
        # Sub-queries below each open their own short-lived sessions.

        # Mark external trade rows as source "external"
        if "_source" not in df.columns:
            df["_source"] = "external"

        # --- Training Feedback Loop: append paper trades + prediction log ---
        # FIX: Use SEPARATE sessions for optional sub-queries.
        # A failed sub-query inside the same session poisons ALL subsequent queries
        # with "current transaction is aborted" (InFailedSQLTransactionError).
        # Using independent sessions isolates failures completely.
        _initial_count = len(df)
        if getattr(settings, "TRAIN_ON_PAPER_TRADES", True):
            try:
                async with self.db.get_session() as _paper_session:
                    paper_df = await self._get_paper_trade_training_rows(_paper_session)
                if paper_df is not None and not paper_df.empty:
                    df = pd.concat([df, paper_df], ignore_index=True)
                    logger.info("Training data: appended %d paper trade rows (weight=%.2f)",
                                len(paper_df), getattr(settings, "PAPER_TRADE_TRAINING_WEIGHT", 0.5))
            except Exception as e:
                logger.debug("Paper trade training append failed (non-fatal): %s", e)

        if getattr(settings, "TRAIN_ON_PREDICTION_LOG", True):
            try:
                async with self.db.get_session() as _pred_session:
                    pred_df = await self._get_prediction_log_training_rows(_pred_session)
                if pred_df is not None and not pred_df.empty:
                    df = pd.concat([df, pred_df], ignore_index=True)
                    logger.info("Training data: appended %d prediction_log rows (weight=%.2f)",
                                len(pred_df), getattr(settings, "PREDICTION_LOG_TRAINING_WEIGHT", 0.3))
            except Exception as e:
                logger.debug("Prediction log training append failed (non-fatal): %s", e)

        if len(df) > _initial_count:
            logger.info("Training feedback loop: %d external + %d feedback = %d total rows",
                        _initial_count, len(df) - _initial_count, len(df))

        required = ["price", "size", "liquidity", "volume", "user_win_rate", "user_profit", "outcome"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise RuntimeError(f"Missing required columns in training data: {missing}")
        if df.empty:
            raise RuntimeError("Training DataFrame is empty")

        # Temporal order: sort by trade_ts for walk-forward split (train on past, validate on future)
        df["trade_ts"] = pd.to_datetime(df["trade_ts"], utc=True)
        df = df.sort_values("trade_ts", na_position="last").reset_index(drop=True)

        # DEEPEN 1: purge/embargo — drop rows whose resolution falls in boundary window
        purge_days = getattr(settings, "TRAINING_PURGE_DAYS", 0)
        embargo_days = getattr(settings, "TRAINING_EMBARGO_DAYS", 0)
        if (purge_days or embargo_days) and len(df) > 10 and "resolved_at" in df.columns:
            boundary_idx = max(int(len(df) * 0.8), 1)
            boundary_time = df.iloc[boundary_idx]["trade_ts"]
            if hasattr(boundary_time, "to_pydatetime"):
                boundary_time = boundary_time.to_pydatetime()
            resolved = pd.to_datetime(df["resolved_at"], utc=True, errors="coerce")
            keep = (resolved < boundary_time - pd.Timedelta(days=purge_days)) | (resolved > boundary_time + pd.Timedelta(days=embargo_days))
            keep = keep.fillna(True)
            dropped = (~keep).sum()
            if dropped > 0:
                df = df.loc[keep].reset_index(drop=True)
                logger.info("DEEPEN 1: dropped %d rows in purge/embargo window (boundary at 80%%)", dropped)

        # B6 FIX: Removed "size" from features. At inference, size is always 1.0
        # (circular: actual size depends on confidence which depends on features).
        # In training, size varies (actual trade sizes), creating train-serve skew.
        # D6 FIX: Encode category as a numeric feature (label-encode top categories)
        _top_categories = ["politics", "crypto", "sports", "science", "pop-culture", "business", "world-news"]
        if "category" in df.columns:
            cat_lower = df["category"].fillna("other").str.lower().str.strip()
            df["category_encoded"] = cat_lower.map(
                {c: i + 1 for i, c in enumerate(_top_categories)}
            ).fillna(0).astype(float)
        else:
            df["category_encoded"] = 0.0
        # F2 FIX: resolution_source_encoded removed (temporal leakage — not available at inference time)
        base_cols = ["price", "liquidity", "volume", "user_win_rate", "user_profit", "category_encoded"]
        if use_elite_net:
            base_cols.append("elite_net_direction")
            if "elite_net_direction" not in df.columns:
                df["elite_net_direction"] = 0.0
            # DEEPEN 3: Time-decomposed elite direction (1h, 6h, 24h)
            for _td_col in ("elite_direction_1h", "elite_direction_6h", "elite_direction_24h"):
                base_cols.append(_td_col)
                if _td_col not in df.columns:
                    df[_td_col] = 0.0
        # R2c: Signal features can now be included for paper trade rows, which have
        # signal context stored in the trade_signals table (added during R2 implementation).
        # Historical trade rows (from the main trades table) still lack signal data and
        # receive a neutral default of 0.5. We use 0.5 as "unknown" rather than 0.0 so the
        # model doesn't learn to associate "no signal" with negative outcomes.
        # Signal columns appear in df only when trade_signals JOIN succeeds (paper rows).
        use_signal_features = getattr(settings, "USE_SIGNAL_FEATURES_IN_ML", True)
        if use_signal_features:
            for _sig_col in ("signal_confidence", "signal_direction_encoded"):
                if _sig_col not in df.columns:
                    df[_sig_col] = 0.5  # Neutral/unknown default for non-paper rows
                else:
                    df[_sig_col] = pd.to_numeric(df[_sig_col], errors="coerce").fillna(0.5)
            base_cols.extend(["signal_confidence", "signal_direction_encoded"])
        # Tier 2 #16: Resolution clarity score (0=ambiguous, 1=crystal clear)
        # Default 0.7 for historical rows without a score yet (neutral-positive assumption).
        use_clarity = getattr(settings, "RESOLUTION_CLARITY_ENABLED", True)
        if use_clarity:
            if "clarity_score" not in df.columns:
                df["clarity_score"] = 0.5  # Session 50: was 0.7 (positive bias); 0.5 = neutral
            else:
                df["clarity_score"] = pd.to_numeric(df["clarity_score"], errors="coerce").fillna(0.5)
            base_cols.append("clarity_score")
        for col in base_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            df[col] = df[col].replace([np.inf, -np.inf], [1e6, -1e6])

        # Optional: path + regime from market_prices (bulk fetch then slice per row)
        prices_by_key: Dict[tuple, List[tuple]] = {}
        # Batched price fetch for path/regime features (smaller batches of market_ids)
        if use_path or use_regime:
            try:
                df["trade_ts"] = pd.to_datetime(df["trade_ts"], utc=True)
                end_ts = df["trade_ts"]
                min_ts = (df["trade_ts"].min() - pd.Timedelta(days=30)).to_pydatetime()
                max_ts = end_ts.max().to_pydatetime()
                keys = list(zip(df["market_id"].tolist(), df["token_id"].tolist()))
                unique_keys = list(dict.fromkeys(k for k in keys if k[1]))
                # D5 FIX: Was 50 — caused 95% of training rows to get zero path features.
                # Raised to 500 so most training rows get real path/regime features.
                max_price_keys = getattr(settings, "TRAINING_MAX_PRICE_KEYS", 500)
                if len(unique_keys) > max_price_keys:
                    logger.info("Capping price fetch keys from %d to %d", len(unique_keys), max_price_keys)
                    unique_keys = unique_keys[:max_price_keys]
                if unique_keys:
                    key_cond = or_(and_(MarketPrice.market_id == m, MarketPrice.token_id == t) for m, t in unique_keys)
                    stmt = select(MarketPrice.market_id, MarketPrice.token_id, MarketPrice.timestamp, MarketPrice.price).where(
                        and_(
                            MarketPrice.timestamp >= min_ts,
                            MarketPrice.timestamp <= max_ts,
                            key_cond,
                        )
                    )
                    # Use separate session to isolate bulk fetch from main transaction.
                    async with self.db.get_session() as _mp_session:
                        mp_result = await _mp_session.execute(stmt)
                        mp_rows = mp_result.fetchall()
                    for r in mp_rows:
                        k = (r.market_id, r.token_id)
                        if k not in prices_by_key:
                            prices_by_key[k] = []
                        # Normalize naive timestamps to UTC-aware (DB stores naive, trade_ts is UTC)
                        _ts = r.timestamp
                        if _ts is not None and getattr(_ts, 'tzinfo', None) is None:
                            from datetime import timezone as _tz
                            _ts = _ts.replace(tzinfo=_tz.utc)
                        prices_by_key[k].append((_ts, r.price))
                    for k in prices_by_key:
                        prices_by_key[k].sort(key=lambda x: x[0])
            except Exception as e:
                logger.warning("Bulk fetch market_prices for path/regime failed, using defaults: %s", e)
                use_path = use_regime = False

        feature_rows = []
        n = min(len(df), path_max_rows) if (use_path or use_regime) else len(df)
        for i in range(len(df)):
            row = df.iloc[i]
            base = [float(row.get(c, 0)) for c in base_cols if c not in ("signal_confidence", "signal_direction", "signal_direction_encoded")]
            if use_signal_features:
                base.extend([
                    float(row.get("signal_confidence", 0.5)),
                    float(row.get("signal_direction_encoded", 0.5)),
                ])
            if (use_path or use_regime) and i < n:
                key = (row["market_id"], row["token_id"])
                trade_ts = row["trade_ts"].to_pydatetime() if hasattr(row["trade_ts"], "to_pydatetime") else row["trade_ts"]
                series = prices_by_key.get(key, [])
                if use_path:
                    # FIX NEW-4: Use trade_ts as end boundary, not resolved_at.
                    # Path features represent what was known AT trade time, not after.
                    end_dt = trade_ts
                    path_prices = [p for ts, p in series if ts <= end_dt]
                    path_sum = get_path_summary_from_prices(path_prices, float(row["price"])) if len(path_prices) >= 2 else None
                    base.extend(path_summary_to_feature_list(path_sum))
                else:
                    base.extend(path_summary_to_feature_list(None))
                if use_regime:
                    reg_start = trade_ts - timedelta(days=30)
                    reg_prices = [p for ts, p in series if reg_start <= ts <= trade_ts]
                    regime = get_regime_features_from_prices(reg_prices) if len(reg_prices) >= 2 else {}
                    base.extend(regime_features_to_list(regime))
                else:
                    base.extend(regime_features_to_list({}))
            else:
                if use_path:
                    base.extend(path_summary_to_feature_list(None))
                if use_regime:
                    base.extend(regime_features_to_list({}))
            # FeatureEngineer augmentation — generate advanced features from price series
            if self._feature_engineer and getattr(settings, "USE_FEATURE_ENGINEER", True):
                try:
                    key = (row["market_id"], row.get("token_id", ""))
                    # D1 FIX: Filter prices to BEFORE trade timestamp to prevent temporal leakage.
                    # Without this, FeatureEngineer sees future prices (MA, volatility, percentile).
                    _row_ts = row.get("trade_ts")
                    fe_prices = [p for ts, p in prices_by_key.get(key, []) if _row_ts is None or ts <= _row_ts]
                    fe_market = {"liquidity": float(row.get("liquidity", 0)), "volume": float(row.get("volume", 0)), "category": ""}
                    fe = self._feature_engineer.generate_features(fe_market, fe_prices)
                    base.extend([
                        fe.get("current_price", 0.0), fe.get("price_change", 0.0),
                        fe.get("price_change_pct", 0.0), fe.get("volatility", 0.0),
                        fe.get("mean_return", 0.0), fe.get("ma_5", 0.0),
                        fe.get("ma_10", 0.0), fe.get("ma_20", 0.0),
                        fe.get("price_percentile", 0.0), fe.get("price_vs_high", 0.0),
                        fe.get("price_vs_low", 0.0), fe.get("liquidity", 0.0),
                        fe.get("volume", 0.0), fe.get("has_category", 0.0),
                    ])
                except Exception:
                    base.extend([0.0] * 14)

            # L7: Time-based features during training (cyclical sin/cos encoding)
            try:
                import math as _math
                _row_ts_l7 = row.get("trade_ts")
                if _row_ts_l7 is not None and hasattr(_row_ts_l7, "weekday"):
                    # Cyclical day-of-week: Mon=0, Sun=6 → continuous circle, no edge discontinuity
                    _l7_dow_sin = _math.sin(2 * _math.pi * _row_ts_l7.weekday() / 7.0)
                    _l7_dow_cos = _math.cos(2 * _math.pi * _row_ts_l7.weekday() / 7.0)
                    # Cyclical hour-of-day: 0-23 → continuous circle (midnight joins evening)
                    _l7_hod_sin = _math.sin(2 * _math.pi * _row_ts_l7.hour / 24.0) if hasattr(_row_ts_l7, "hour") else 0.0
                    _l7_hod_cos = _math.cos(2 * _math.pi * _row_ts_l7.hour / 24.0) if hasattr(_row_ts_l7, "hour") else 1.0
                else:
                    _l7_dow_sin, _l7_dow_cos = 0.0, 1.0  # Center of unit circle (neutral default)
                    _l7_hod_sin, _l7_hod_cos = 0.0, 1.0
                # Market age at trade time
                _l7_age_t = 0.5
                try:
                    _m_created = row.get("created_at")
                    if _m_created is not None and _row_ts_l7 is not None:
                        if hasattr(_m_created, "tzinfo") and _m_created.tzinfo is None:
                            _m_created = _m_created.replace(tzinfo=timezone.utc)
                        if hasattr(_row_ts_l7, "tzinfo") and _row_ts_l7.tzinfo is None:
                            _row_ts_l7_tz = _row_ts_l7.replace(tzinfo=timezone.utc)
                        else:
                            _row_ts_l7_tz = _row_ts_l7
                        _l7_age_t = min(1.0, max(0.0, (_row_ts_l7_tz - _m_created).days / 365.0))
                except Exception:
                    pass
                # Time to expiry at trade time
                _l7_tte_t = 0.5
                try:
                    _m_end = row.get("end_date_iso")
                    if _m_end is not None and _row_ts_l7 is not None:
                        if isinstance(_m_end, str):
                            from dateutil.parser import parse as _dp
                            _m_end = _dp(_m_end)
                        if hasattr(_m_end, "tzinfo") and _m_end.tzinfo is None:
                            _m_end = _m_end.replace(tzinfo=timezone.utc)
                        if hasattr(_row_ts_l7, "tzinfo") and _row_ts_l7.tzinfo is None:
                            _row_ts_l7_tz2 = _row_ts_l7.replace(tzinfo=timezone.utc)
                        else:
                            _row_ts_l7_tz2 = _row_ts_l7
                        _tte = (_m_end - _row_ts_l7_tz2).total_seconds() / 86400.0
                        _l7_tte_t = max(0.0, min(1.0, _tte / 365.0))
                except Exception:
                    pass
                base.extend([_l7_dow_sin, _l7_dow_cos, _l7_hod_sin, _l7_hod_cos, _l7_age_t, _l7_tte_t])
            except Exception:
                base.extend([0.0, 1.0, 0.0, 1.0, 0.5, 0.5])

            # L8: TA features during training (RSI, Bollinger, ATR)
            try:
                from base_engine.learning.feature_engineering import compute_rsi, compute_bollinger_position, compute_atr_normalized
                key_l8 = (row["market_id"], row.get("token_id", ""))
                _row_ts_l8 = row.get("trade_ts")
                l8_prices = [p for ts, p in prices_by_key.get(key_l8, []) if _row_ts_l8 is None or ts <= _row_ts_l8]
                if len(l8_prices) >= 5:
                    base.extend([
                        compute_rsi(l8_prices),
                        compute_bollinger_position(l8_prices),
                        compute_atr_normalized(l8_prices),
                    ])
                else:
                    base.extend([0.5, 0.5, 0.0])
            except Exception:
                base.extend([0.5, 0.5, 0.0])

            feature_rows.append(base)

        self.feature_columns = base_cols.copy()
        if use_path:
            self.feature_columns += ["path_min", "path_max", "path_final", "path_vol", "path_drawdown", "time_above_entry", "max_run_up", "max_run_down"]
        if use_regime:
            self.feature_columns += ["regime_trend", "regime_vol"]
        if self._feature_engineer and getattr(settings, "USE_FEATURE_ENGINEER", True):
            self.feature_columns += [
                "fe_current_price", "fe_price_change", "fe_price_change_pct", "fe_volatility",
                "fe_mean_return", "fe_ma_5", "fe_ma_10", "fe_ma_20",
                "fe_price_percentile", "fe_price_vs_high", "fe_price_vs_low",
                "fe_liquidity", "fe_volume", "fe_has_category",
            ]
        # L7: Time-based features (always included; cyclical sin/cos encoding)
        self.feature_columns += ["time_dow_sin", "time_dow_cos", "time_hour_sin", "time_hour_cos", "time_market_age", "time_to_expiry"]
        # L8: Technical Analysis features (always included)
        self.feature_columns += ["ta_rsi", "ta_bollinger_pos", "ta_atr_normalized"]

        full_cols = list(self.feature_columns)
        min_resolved_for_feature_selection = getattr(settings, "MIN_RESOLVED_FOR_FEATURE_SELECTION", 200)
        if self.best_feature_names and len(feature_rows) >= min_resolved_for_feature_selection:
            filtered_cols = [c for c in self.best_feature_names if c in full_cols]
            if filtered_cols:
                self.feature_columns = filtered_cols
                features_arr = np.array(feature_rows, dtype=float)
                col_idx = [full_cols.index(c) for c in self.feature_columns]
                features_arr = features_arr[:, col_idx]
            else:
                features_arr = np.array(feature_rows, dtype=float)
        else:
            features_arr = np.array(feature_rows, dtype=float)
        features_arr = np.nan_to_num(features_arr, nan=0.0, posinf=1e6, neginf=-1e6)
        features_arr = np.clip(features_arr, -1e6, 1e6)

        # Validate outcome column - remove rows with invalid outcomes
        outcome_col = pd.to_numeric(df["outcome"], errors="coerce")
        valid_mask = outcome_col.notna() & outcome_col.isin([0, 1, 0.0, 1.0])
        if valid_mask.sum() == 0:
            logger.warning("No valid outcome values found in training data")
            return features_arr[:0], np.array([], dtype=int), np.array([], dtype=float)
        if valid_mask.sum() < len(df):
            logger.info("Filtered %d rows with invalid outcome values", len(df) - valid_mask.sum())
            features_arr = features_arr[valid_mask.values]
            outcome_col = outcome_col[valid_mask]
        labels = outcome_col.astype(int).values
        # Sample weights: volume base + elite multiplier (higher for high vol+return users)
        volume_col = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        sample_weights = np.log1p(volume_col.values[valid_mask.values]).astype(float)
        sample_weights = np.clip(sample_weights, 0.1, 10.0)  # Cap extremes
        # Apply elite multiplier: moderate boost for all elite, extra for high-volume high-return
        elite_w = getattr(settings, "ELITE_LEARNING_WEIGHT", 1.35)
        high_w = getattr(settings, "ELITE_HIGH_VOL_RETURN_WEIGHT", 1.55)
        user_profit = pd.to_numeric(df["user_profit"], errors="coerce").fillna(0).values[valid_mask.values]
        user_wr = pd.to_numeric(df["user_win_rate"], errors="coerce").fillna(0.5).values[valid_mask.values]
        elite_mult = np.where((user_profit > 1000) & (user_wr > 0.6), high_w, elite_w)
        sample_weights = sample_weights * elite_mult
        sample_weights = np.clip(sample_weights, 0.1, 15.0)  # Cap with higher ceiling for elite

        # --- Feedback loop weight adjustments ---
        source_col = df.get("_source")
        if source_col is not None:
            source_vals = source_col.values[valid_mask.values]
            # Paper trade rows: apply discount weight
            paper_mask = source_vals == "paper"
            if paper_mask.any():
                paper_w = getattr(settings, "PAPER_TRADE_TRAINING_WEIGHT", 0.5)
                sample_weights[paper_mask] *= paper_w

                # R4: Realized P&L weighting for paper trades.
                # The model should learn that "right + high P&L" is more important than
                # "right + tiny P&L" (bad entry timing). Conversely, "wrong + big loss" should
                # be penalised more. P&L weight modifier applied on top of paper_w.
                if "realized_pnl" in df.columns:
                    pnl_vals = pd.to_numeric(
                        df["realized_pnl"], errors="coerce"
                    ).fillna(0.0).values[valid_mask.values]
                    pnl_paper = pnl_vals[paper_mask]
                    paper_labels = labels[paper_mask]

                    # Compute per-trade P&L weight modifier (capped at ±50% adjustment)
                    pnl_weight_mod = np.ones(paper_mask.sum(), dtype=float)

                    # Correct + high P&L → up to 1.5x (reinforce profitable correct predictions)
                    correct_high_pnl = (paper_labels == 1) & (pnl_paper > 0)
                    if correct_high_pnl.any():
                        pnl_boost = np.clip(1.0 + pnl_paper[correct_high_pnl] / 200.0, 1.0, 1.5)
                        pnl_weight_mod[correct_high_pnl] = pnl_boost

                    # Correct + near-zero or negative P&L → 0.8x (bad timing, reduce reinforcement)
                    correct_low_pnl = (paper_labels == 1) & (pnl_paper <= 0)
                    if correct_low_pnl.any():
                        pnl_weight_mod[correct_low_pnl] = 0.8

                    # Wrong + large loss → up to 1.3x (strongly avoid these situations)
                    wrong_big_loss = (paper_labels == 0) & (pnl_paper < -50)
                    if wrong_big_loss.any():
                        loss_pen = np.clip(1.0 + abs(pnl_paper[wrong_big_loss]) / 200.0, 1.0, 1.3)
                        pnl_weight_mod[wrong_big_loss] = loss_pen

                    sample_weights[paper_mask] *= pnl_weight_mod
                    n_pnl_adjusted = int((pnl_weight_mod != 1.0).sum())
                    if n_pnl_adjusted > 0:
                        logger.info(
                            "R4 P&L weight: %d paper trades adjusted (avg mod=%.3f)",
                            n_pnl_adjusted, float(pnl_weight_mod.mean()),
                        )

            # Prediction log rows: apply base discount + "why wrong" correction boost
            pred_mask = source_vals == "prediction_log"
            if pred_mask.any():
                pred_w = getattr(settings, "PREDICTION_LOG_TRAINING_WEIGHT", 0.3)
                sample_weights[pred_mask] *= pred_w

                # "Why wrong" signal: boost weight on wrong predictions proportional to confidence * |edge|
                wrong_pred_mask = pred_mask & (labels == 0)
                if wrong_pred_mask.any() and "pred_confidence" in df.columns and "pred_edge" in df.columns:
                    conf_vals = pd.to_numeric(df["pred_confidence"], errors="coerce").fillna(0.5).values[valid_mask.values]
                    edge_vals = pd.to_numeric(df["pred_edge"], errors="coerce").fillna(0.0).values[valid_mask.values]
                    abs_edge = np.clip(np.abs(edge_vals[wrong_pred_mask]), 0, 1)
                    conf_clip = np.clip(conf_vals[wrong_pred_mask], 0.5, 1.0)
                    # Wrong + high confidence + high edge = corrective weight boost (1.0 to 1.3x).
                    # Cap at 1.3x (was 2.0x) to avoid amplifying noise from black swans / disputed
                    # resolutions where the prediction was correct but labeled wrong.
                    correction_boost = np.clip(1.0 + (abs_edge * conf_clip * 0.6), 1.0, 1.3)
                    sample_weights[wrong_pred_mask] *= correction_boost
                    n_wrong = wrong_pred_mask.sum()
                    avg_boost = correction_boost.mean() if n_wrong > 0 else 1.0
                    logger.info("'Why wrong' correction: %d wrong predictions, avg boost=%.2f", n_wrong, avg_boost)

        # B2: Recency-weighted training — exponential decay so recent trades matter more.
        # w_i = exp(-lambda * (T - i) / T): newest row gets 1.0, oldest gets exp(-lambda).
        # TRAINING_RECENCY_LAMBDA=1.0 means oldest row gets ~37% of newest's weight.
        _recency_lambda = getattr(settings, "TRAINING_RECENCY_LAMBDA", 1.0)
        if _recency_lambda > 0.0:
            _n = len(sample_weights)
            if _n > 1:
                _indices = np.arange(_n, dtype=float)
                _recency_decay = np.exp(-_recency_lambda * (_n - 1 - _indices) / (_n - 1))
                sample_weights = sample_weights * _recency_decay
                logger.debug(
                    "B2 recency decay applied: lambda=%.2f oldest_scale=%.3f newest_scale=1.000",
                    _recency_lambda, float(_recency_decay[0]),
                )

        sample_weights = np.clip(sample_weights, 0.1, 15.0)  # Final cap
        return features_arr, labels, sample_weights
    
    async def predict(
        self,
        market_id: str,
        token_id: str,
        price: float,
        user_address: Optional[str] = None,
        correlation_id: Optional[str] = None,
        bot_name: Optional[str] = None,
        aia_mode: bool = False,
    ) -> Dict:
        if not self.initialized or not self.models:
            raise RuntimeError("Prediction engine not initialized or models not trained. Train models first.")

        # Phase 10: prediction result cache (key = market_id + full-precision price)
        # B4: Check Redis first (30s TTL), then local in-memory cache (300s TTL)
        ttl = getattr(settings, "CACHE_TTL_PREDICTIONS", 300)
        # T4 FIX: Include token_id in cache key to prevent YES/NO cross-contamination.
        # Round price to 4 decimal places to avoid spurious cache misses from float repr drift
        # (e.g. 0.453 vs 0.4530000000001 would be different keys with repr()).
        cache_key = f"{market_id}:{token_id}:{price:.4f}"
        now = time.monotonic()
        # Redis cache (L1: shared across components, 30s TTL)
        _redis = getattr(self, "_redis_cache", None)
        if _redis is not None:
            try:
                _redis_key = f"pred:{cache_key}"
                _redis_val = await _redis.get(_redis_key)
                if _redis_val is not None and isinstance(_redis_val, dict):
                    return dict(_redis_val)
            except Exception as e:
                logger.debug("cache_write_failed", error=str(e))
        # Local in-memory cache (L2: per-process, 300s TTL)
        cached_val = self._prediction_cache.get(cache_key)
        if cached_val is not None:
            # L10 FIX: Feed DriftTracker even on cache hits so it has enough data
            if hasattr(self, '_drift_tracker') and self._drift_tracker is not None:
                try:
                    _cp = cached_val.get("prediction")
                    if _cp is not None:
                        self._drift_tracker.record_prediction(float(_cp))
                except Exception as e:
                    logger.debug("drift_tracker_record_failed", error=str(e))
            return dict(cached_val)

        # FAST PATH: Use pre-computed feature vector if available (populated by
        # batch_precompute_all_features() background job or previous _extract_features() calls).
        # This skips ALL DB queries — pure in-memory lookup → model inference.
        _fv_key = f"fv:{market_id}:{token_id}"
        _cached_fv = self._feature_vector_cache.get(_fv_key)
        if _cached_fv is not None:
            features = list(_cached_fv)  # copy to avoid mutation
            # Update the price feature in the cached vector (price changes with every tick)
            # Also persist the updated vector back so the NEXT predict() call reads fresh price
            if self.feature_columns:
                try:
                    _price_idx = self.feature_columns.index("price")
                    _cached_price = features[_price_idx]
                    _price_move = abs(price - _cached_price)
                    _fv_inval_thresh = getattr(settings, "FV_CACHE_INVALIDATE_PRICE_MOVE", 0.03)
                    if _price_move >= _fv_inval_thresh:
                        # Significant move: RSI/elite direction/regime features are now stale
                        self._feature_vector_cache.delete(_fv_key)
                        logger.debug("FV cache invalidated (price move %.3f): market=%s", _price_move, market_id)
                        features = await self._extract_features(market_id, price, user_address)
                    else:
                        features[_price_idx] = price
                        self._feature_vector_cache.set(_fv_key, features, ttl=_FV_CACHE_TTL)  # persist live price
                except ValueError:
                    pass  # "price" not in feature_columns (shouldn't happen)
        else:
            features = await self._extract_features(market_id, price, user_address)

        if features is None:
            raise RuntimeError(f"Could not extract features for market {market_id}. Market data required in database.")

        # SF-20: Validate feature inputs before inference
        if any(math.isnan(f) or math.isinf(f) for f in features):
            _nan_ct = sum(1 for f in features if math.isnan(f) or math.isinf(f))
            logger.warning("predict_nan_input", market_id=market_id, nan_count=_nan_ct, total=len(features))
            features = [0.0 if (math.isnan(f) or math.isinf(f)) else f for f in features]

        # F6: Run scaler + all model.predict_proba in thread pool to avoid blocking event loop
        predictions, features_scaled, _degraded_models = await asyncio.to_thread(
            _predict_models_sync, self.models, self.model_weights, self.scaler, features
        )
        if _degraded_models:
            logger.warning("predict_degraded_models", market_id=market_id, models=_degraded_models)

        if len(features_scaled) == 0 or len(features_scaled[0]) == 0:
            raise RuntimeError(f"Feature scaling produced empty result for market {market_id}")

        # PSI drift accumulation (Item 21): collect recent scaled features, check periodically
        if self._feature_baselines:
            self._recent_features.append(features_scaled[0].tolist())  # deque(maxlen=500) — O(1)
            self._psi_prediction_count += 1
            _psi_interval = getattr(settings, "PSI_CHECK_INTERVAL", 1000)
            if self._psi_prediction_count % _psi_interval == 0 and len(self._recent_features) >= 100:
                try:
                    self._run_psi_check(list(self._recent_features), self._feature_baselines)
                except Exception:
                    pass  # non-critical

        if not predictions:
            raise RuntimeError("All models failed to generate predictions")

        # Alert when majority of models degraded — likely a feature extraction issue
        if _degraded_models and len(_degraded_models) >= len(self.models) * 0.5:
            logger.warning(
                "Majority of models degraded to 0.5 fallback",
                degraded=len(_degraded_models),
                total=len(self.models),
                degraded_models=_degraded_models,
                market_id=market_id,
            )

        if self.model_weights:
            default_weight = 1.0 / max(len(predictions), 1)
            weights = {
                name: max(0.0, min(1.0, self.model_weights.get(name, default_weight)))
                for name in predictions
            }
            total_w = sum(weights.values())
            if total_w > 0:
                weights = {k: v / total_w for k, v in weights.items()}
            else:
                weights = {k: default_weight for k in predictions}
            # Geometric mean of odds aggregation (Satopaa et al. 2014).
            # Weighted sum in log-odds space = weighted geometric mean of odds ratios.
            # Satisfies external Bayesianity — mathematically superior to arithmetic mean.
            _eps = 1e-6
            _log_odds_sum = 0.0
            for name in predictions:
                _p = max(_eps, min(1 - _eps, predictions[name]))
                _log_odds_sum += weights[name] * math.log(_p / (1 - _p))
            ensemble_prediction = 1.0 / (1.0 + math.exp(-_log_odds_sum))
        else:
            # Fallback: unweighted geometric mean of odds
            _eps = 1e-6
            _preds = list(predictions.values())
            _n = len(_preds)
            if _n > 0:
                _log_odds_sum = sum(
                    math.log(max(_eps, min(1 - _eps, p)) / max(_eps, 1 - min(1 - _eps, p)))
                    for p in _preds
                ) / _n
                ensemble_prediction = 1.0 / (1.0 + math.exp(-_log_odds_sum))
            else:
                ensemble_prediction = 0.5
        if math.isnan(ensemble_prediction) or math.isinf(ensemble_prediction):
            logger.warning("Ensemble prediction is NaN/Infinity, using 0.5")
            ensemble_prediction = 0.5
        
        # I45: Fetch market object NOW so category and time_to_res are available
        # for get_bet_type_confidence() per-category/per-horizon win rate stats.
        try:
            from sqlalchemy import select
            from base_engine.data.database import Market
            _mkt_ck = f"mkt:{market_id}"
            market = self._market_cache.get(_mkt_ck)
            if market is None:
                async with self.db.get_session() as session:
                    _mkt_result = await session.execute(
                        select(Market).where(Market.id == market_id)
                    )
                    market = _mkt_result.scalar_one_or_none()
                    if market:
                        session.expunge(market)
                        self._market_cache.set(_mkt_ck, market)
        except Exception:
            market = None  # fallback: learning_conf uses global bucket
        _mkt_category = (market.category or "") if market else ""
        _mkt_end_date = market.end_date_iso if market else None
        _time_to_res = (
            self.learning_engine._calculate_time_to_resolution(
                _mkt_end_date, datetime.now(timezone.utc)
            )
            if _mkt_end_date is not None
            else "unknown"
        )
        learning_conf = await self.learning_engine.calculate_combined_confidence(
            user_address or "",
            price,
            _mkt_category,
            _time_to_res,
        )
        
        # Calibration chain: FocalTemp -> HorizonBias -> isotonic -> extremization
        # Each stage is a no-op if not fitted (insufficient data).

        # Step 1: Focal Temperature Scaling (scale + shape miscalibration correction)
        if getattr(self, '_focal_temp_calibrator', None) and self._focal_temp_calibrator.is_fitted:
            ensemble_prediction = self._focal_temp_calibrator.calibrate(ensemble_prediction)

        # Step 2: Le (2026) horizon bias correction (domain x TTR power-law)
        if getattr(self, '_horizon_calibrator', None) and self._horizon_calibrator.is_fitted:
            _ttr_days = None
            if _mkt_end_date is not None:
                try:
                    from datetime import datetime, timezone
                    _ttr_td = _mkt_end_date - datetime.now(timezone.utc)
                    _ttr_days = max(0.0, _ttr_td.total_seconds() / 86400.0)
                except Exception:
                    pass
            ensemble_prediction = self._horizon_calibrator.calibrate(
                ensemble_prediction, category=_mkt_category, ttr_days=_ttr_days,
            )

        # Step 3: P3-06 isotonic regression (favorite-longshot bias)
        if self._calibrator and self._calibrator.is_fitted:
            ensemble_prediction = self._calibrator.calibrate(ensemble_prediction)

        # SF-1/F5: Extremization applied to ensemble_prediction BEFORE blending with learning_conf.
        # Previously applied post-blend to final_confidence, which diluted the effect.
        # Pushes diverse-ensemble predictions away from 0.5 toward 0/1 via log-odds scaling.
        # d=2.0 is typical for well-calibrated ensembles (tune via EXTREMIZATION_FACTOR env var).
        _ext_d = float(getattr(settings, "EXTREMIZATION_FACTOR", 0.0))
        if _ext_d > 0:
            try:
                _p = max(1e-6, min(1 - 1e-6, ensemble_prediction))
                _logit = math.log(_p / (1 - _p))
                ensemble_prediction = 1.0 / (1.0 + math.exp(-_ext_d * _logit))
            except Exception as e:
                logger.warning("extremization_failed", market_id=market_id, error=str(e))

        blend = max(BLEND_MIN, min(BLEND_MAX, self.ensemble_blend))
        final_confidence = (ensemble_prediction * blend + learning_conf * (1.0 - blend))

        # Platt scaling (deferred item §9.3 — gated on 200+ resolved predictions)
        _platt_enabled = getattr(settings, "PLATT_SCALING_ENABLED", False)
        if _platt_enabled and hasattr(self, 'db') and self.db:
            try:
                _min_resolved = int(getattr(settings, "PLATT_MIN_RESOLVED", 200))
                _recent_brier = await self.db.get_recent_brier_from_prediction_log()
                if _recent_brier is not None and _recent_brier.get("count", 0) >= _min_resolved:
                    # Platt = sigmoid fit. Simple approach: use rolling Brier to estimate calibration offset
                    _brier = _recent_brier.get("brier", 0.25)
                    if _brier > 0.15:
                        # Under-calibrated: shrink toward 0.5
                        _platt_alpha = max(0.8, 1.0 - (_brier - 0.15) * 2)
                        final_confidence = 0.5 + (final_confidence - 0.5) * _platt_alpha
            except Exception:
                pass  # Platt scaling is non-critical; fail silently

        # L1 FIX: Feed DriftTracker with every prediction so it can detect concept drift
        if hasattr(self, '_drift_tracker') and self._drift_tracker is not None:
            try:
                self._drift_tracker.record_prediction(float(ensemble_prediction))
            except Exception as e:
                logger.warning("drift_tracker_record_failed", error=str(e))

        if getattr(settings, "PREDICTION_LOG_ENABLED", True) and self.db and self._feature_cache_warmed:
            # Build feature snapshot dict for offline analysis (Tier 1 #6)
            _feat_snap = None
            _pred_ts_iso = None
            try:
                _pred_ts_iso = datetime.now(timezone.utc).isoformat()
                if features is not None and self.feature_columns:
                    _feat_snap = {col: round(float(v), 6) for col, v in zip(self.feature_columns, features)}
                    # Temporal integrity: embed prediction timestamp in snapshot.
                    # At label-attachment time we assert label_timestamp > _pred_ts to catch
                    # any clock skew or out-of-order label writes that would poison the model.
                    _feat_snap["_pred_ts"] = _pred_ts_iso
                    # Feature vector hash: SHA-256 of sorted feature values (rounded to 6dp).
                    # At label-attachment time we re-hash and compare to detect silent mutations.
                    import hashlib, json as _json
                    _fv_str = _json.dumps(
                        {k: v for k, v in _feat_snap.items() if not k.startswith("_")},
                        sort_keys=True,
                    )
                    _feat_snap["_fv_hash"] = hashlib.sha256(_fv_str.encode()).hexdigest()[:16]
            except Exception as e:
                logger.warning("feature_snapshot_hash_failed", market_id=market_id, error=str(e))
            # Fire-and-forget: don't block the reactive path for a non-critical DB write
            _pp = float(ensemble_prediction)
            if not (0.0 <= _pp <= 1.0):
                logger.warning("predicted_prob out of range", value=_pp, market_id=market_id)
                _pp = max(0.0, min(1.0, _pp))
            if _pp > 0.95 or _pp < 0.05:
                logger.warning("predicted_prob extreme — possible inversion", value=_pp, market_id=market_id)
            async def _bg_log(_snap=_feat_snap, _cid=correlation_id, _pp=_pp, _bn=bot_name):
                try:
                    await self.db.insert_prediction_log(
                        market_id=market_id,
                        predicted_prob=_pp,
                        market_price=price,
                        model_name="ensemble",
                        token_id=token_id,
                        confidence=float(final_confidence),
                        ensemble_pred=float(ensemble_prediction),
                        learning_conf=float(learning_conf),
                        feature_snapshot=_snap,
                        correlation_id=_cid,
                        bot_name=_bn,
                    )
                except Exception as e:
                    logger.debug("Prediction log insert failed: %s", e)
            _task = asyncio.create_task(_bg_log())
            _task.add_done_callback(_on_prediction_log_done)

        # Phase 11: run calibration_quality, cross_market_features, llm_estimate in parallel
        async def _get_calibration() -> Optional[Dict]:
            if not self.db:
                return None
            try:
                perf = await self.db.get_recent_brier_from_prediction_log(
                    getattr(settings, "AUTO_RETRAIN_RECENT_N", 50)
                )
                if perf and perf.get("count", 0) >= getattr(settings, "AUTO_RETRAIN_MIN_SAMPLES", 20):
                    return {"brier": perf.get("brier"), "accuracy": perf.get("accuracy"), "count": perf.get("count")}
            except Exception as e:
                logger.debug("calibration quality fetch failed: %s", e)
            return None

        async def _get_cross_market() -> Optional[Any]:
            if not self._cross_market:
                return None
            try:
                return await self._cross_market.get_features(market_id)
            except Exception as e:
                logger.debug("cross market feature fetch failed: %s", e)
                return None

        async def _get_llm() -> Optional[Any]:
            if not self._llm_estimator or not self._llm_estimator.is_available:
                return None
            try:
                question_text, category_text, time_to_res = market_id, "", ""
                if self.db and self.db.session_factory:
                    try:
                        from sqlalchemy import select
                        from base_engine.data.database import Market
                        async with self.db.get_session() as sess:
                            row = (await sess.execute(
                                select(Market.question, Market.category, Market.end_date_iso).where(Market.id == market_id)
                            )).first()
                            if row:
                                question_text = row[0] or market_id
                                category_text = row[1] or ""
                                if row[2]:
                                    try:
                                        from datetime import datetime as _dt
                                        end = _dt.fromisoformat(str(row[2]).replace("Z", "+00:00"))
                                        if end.tzinfo is None:
                                            end = end.replace(tzinfo=timezone.utc)
                                        days_left = (end - datetime.now(timezone.utc)).days
                                        time_to_res = f"{days_left} days" if days_left > 0 else "imminent"
                                    except Exception as e:
                                        logger.debug("LLM time_to_resolution parsing failed: %s", e)
                    except Exception as e:
                        logger.debug("LLM market metadata fetch failed: %s", e)
                # AIA ensemble (5 CoT variants) — only on trade candidates (aia_mode=True)
                if aia_mode and getattr(settings, "LLM_AIA_ENSEMBLE", False):
                    aia_result = await self._llm_estimator.estimate_aia_ensemble(
                        market_question=question_text,
                        current_price=price,
                        category=category_text,
                        time_to_resolution=time_to_res,
                    )
                    if aia_result:
                        return aia_result

                return await self._llm_estimator.estimate_probability(
                    market_question=question_text,
                    current_price=price,
                    category=category_text,
                    time_to_resolution=time_to_res,
                )
            except Exception as e:
                logger.debug("LLM probability estimation failed: %s", e)
                return None

        calibration_quality, cross_market_features, llm_estimate = await asyncio.gather(
            _get_calibration(), _get_cross_market(), _get_llm()
        )

        # A/B test: log both LLM prompts to prediction_log as llm_standard / llm_superforecaster
        if getattr(settings, "LLM_AB_TEST_PROMPTS", False):
            try:
                await self._log_llm_ab_predictions(market_id, token_id, price, correlation_id=correlation_id)
            except Exception as e:
                logger.debug("LLM A/B log failed (non-critical): %s", e)

        # Phase 7.3: include regime_vol in result so callers can scale position size
        _regime_for_result = self._regime_cache.get(f"regime:{market_id}") or {}
        result = {
            "confidence": float(final_confidence),
            "prediction": float(ensemble_prediction),
            "prediction_timestamp": datetime.now(timezone.utc).isoformat(),
            "model_predictions": {k: float(v) for k, v in predictions.items()},
            "learning_confidence": float(learning_conf),
            "calibration_quality": calibration_quality,
            "suggested_model_weights": dict(self.model_weights) if self.model_weights else None,
            "cross_market_features": cross_market_features,
            "llm_estimate": llm_estimate,
            "ensemble_degraded": len(_degraded_models) > 0,
            "degraded_models": _degraded_models,
            "active_model_count": len(predictions) - len(_degraded_models),
            "feature_importance": dict(self._feature_importance_scores) if self._feature_importance_scores else None,
            "regime_vol": float(_regime_for_result.get("regime_vol", 0.0)),
        }
        # Phase 10: store in bounded local cache (auto-evicts when full)
        self._prediction_cache.set(cache_key, dict(result), ttl=float(ttl))
        # B4: Also store in Redis (30s TTL, shared across components)
        if _redis is not None:
            try:
                await _redis.set(f"pred:{cache_key}", dict(result), ttl=30)
            except Exception as e:
                logger.debug("cache_write_failed", error=str(e))
        return result

    async def _log_llm_ab_predictions(self, market_id: str, token_id: str, price: float, correlation_id: Optional[str] = None) -> None:
        """When LLM_AB_TEST_PROMPTS=true, run both standard and superforecaster prompts and log to prediction_log."""
        if not self._llm_estimator or not self._llm_estimator.is_available or not self.db or not self.db.session_factory:
            return
        question_text, category_text, time_to_res = market_id, "", ""
        try:
            from sqlalchemy import select
            from base_engine.data.database import Market
            async with self.db.get_session() as sess:
                row = (await sess.execute(
                    select(Market.question, Market.category, Market.end_date_iso).where(Market.id == market_id)
                )).first()
                if row:
                    question_text = row[0] or market_id
                    category_text = row[1] or ""
                    if row[2]:
                        try:
                            end = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
                            if end.tzinfo is None:
                                end = end.replace(tzinfo=timezone.utc)
                            days_left = (end - datetime.now(timezone.utc)).days
                            time_to_res = f"{days_left} days" if days_left > 0 else "imminent"
                        except Exception as e:
                            logger.debug("A/B test time_to_resolution parsing failed: %s", e)
        except Exception as e:
            logger.debug("A/B test market metadata fetch failed: %s", e)
            return
        standard_res, super_res = await asyncio.gather(
            self._llm_estimator.estimate_probability(
                market_question=question_text,
                current_price=price,
                category=category_text,
                time_to_resolution=time_to_res,
                prompt_type="standard",
            ),
            self._llm_estimator.estimate_probability(
                market_question=question_text,
                current_price=price,
                category=category_text,
                time_to_resolution=time_to_res,
                prompt_type="superforecaster",
            ),
        )
        if standard_res and "probability" in standard_res and self._feature_cache_warmed:
            try:
                await self.db.insert_prediction_log(
                    market_id=market_id,
                    predicted_prob=float(standard_res["probability"]),
                    market_price=price,
                    model_name="llm_standard",
                    token_id=token_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.debug("A/B test llm_standard prediction log insert failed: %s", e)
        if super_res and "probability" in super_res and self._feature_cache_warmed:
            try:
                await self.db.insert_prediction_log(
                    market_id=market_id,
                    predicted_prob=float(super_res["probability"]),
                    market_price=price,
                    model_name="llm_superforecaster",
                    token_id=token_id,
                    correlation_id=correlation_id,
                )
            except Exception as e:
                logger.debug("A/B test llm_superforecaster prediction log insert failed: %s", e)

    async def _extract_features(
        self,
        market_id: str,
        price: float,
        user_address: Optional[str]
    ) -> Optional[List[float]]:
        from base_engine.utils.validation import validate_price, validate_market_id
        
        try:
            market_id = validate_market_id(market_id)
            price = validate_price(price, "price")
        except ValueError as e:
            logger.error(f"Invalid input for feature extraction: {str(e)}", exc_info=True)
            raise RuntimeError(f"Feature extraction failed: {str(e)}")
        
        if self.db.session_factory is None:
            raise RuntimeError("Database required for feature extraction. Cannot proceed without database connection.")

        # Optional: use pre-computed ML features from FeatureStore when present, recent, and key-compatible
        if getattr(settings, "USE_ML_FEATURES_STORE", False) and hasattr(self.db, "get_ml_features"):
            try:
                stored = await self.db.get_ml_features(market_id)
                if stored and stored.get("features") and self.feature_columns:
                    # datetime/timezone/timedelta already imported at module level
                    computed_at = stored.get("computed_at")
                    if computed_at:
                        if hasattr(computed_at, "tzinfo") and computed_at.tzinfo is None:
                            computed_at = computed_at.replace(tzinfo=timezone.utc)
                        age_hours = (datetime.now(timezone.utc) - computed_at).total_seconds() / 3600
                        if age_hours <= 24.0:
                            feats = stored["features"] if isinstance(stored["features"], dict) else {}
                            if all(k in feats for k in self.feature_columns):
                                vec = [float(feats[k]) for k in self.feature_columns]
                                if not any(math.isnan(x) or math.isinf(x) for x in vec):
                                    return vec
            except Exception as e:
                logger.debug("ML features store fallback: %s", e)

        try:
            from sqlalchemy import select, text
            from base_engine.data.database import Market, User

            # --- Market object: check cache first (populated by prefetch_markets) ---
            _mkt_ck = f"mkt:{market_id}"
            market = self._market_cache.get(_mkt_ck)
            if market is None:
                async with self.db.get_session() as session:
                    market_result = await session.execute(
                        select(Market).where(Market.id == market_id)
                    )
                    market = market_result.scalar_one_or_none()
                    if market:
                        session.expunge(market)
                        self._market_cache.set(_mkt_ck, market)

            if not market:
                raise RuntimeError(f"Market {market_id} not found in database. Ingest market data first.")

            # --- User stats: check cache first ---
            if user_address:
                try:
                    user_address = user_address.strip()
                    if not user_address:
                        raise ValueError("user_address cannot be empty")
                except (AttributeError, ValueError) as e:
                    logger.warning(f"Invalid user_address format: {str(e)}, using defaults")
                    user_address = None

            user_win_rate = 0.5
            user_profit = 0.0
            if user_address:
                _usr_ck = f"usr:{user_address}"
                _usr_cached = self._user_cache.get(_usr_ck)
                if _usr_cached is not None:
                    user_win_rate, user_profit = _usr_cached
                else:
                    async with self.db.get_session() as session:
                        user_result = await session.execute(
                            select(User).where(User.address == user_address)
                        )
                        user = user_result.scalar_one_or_none()
                        # I07: Replace lifetime user_win_rate with 30-day rolling window.
                        # Training uses a point-in-time LATERAL join (365-day window); using the
                        # lifetime aggregate at inference creates covariate shift as the portfolio
                        # matures. 30-day window keeps inference aligned with recent market regime.
                        # Min 5 resolved trades in window required; falls back to lifetime if sparse.
                        _win_rate_365d: Optional[float] = None
                        _profit_365d: Optional[float] = None
                        _n_resolved_365d: int = 0
                        try:
                            from sqlalchemy import text as _sql_text
                            # Session 50: aligned with training's 365-day LATERAL JOIN
                            # (was 30-day win_rate only, profit was lifetime — train-serve skew)
                            _wr_row = (await session.execute(
                                _sql_text("""
                                    SELECT
                                        SUM(CASE WHEN m.resolution =
                                            CASE WHEN t.token_id = m.yes_token_id THEN 'YES' ELSE 'NO' END
                                            THEN 1.0 ELSE 0.0 END
                                        ) / NULLIF(COUNT(*), 0),
                                        COUNT(*),
                                        COALESCE(SUM(t.pnl), 0.0)
                                    FROM trades t
                                    JOIN markets m ON t.market_id = m.id
                                    WHERE t.user_address = :addr
                                      AND m.resolution IN ('YES', 'NO')
                                      AND t.timestamp >= NOW() - INTERVAL '365 days'
                                """),
                                {"addr": user_address},
                            )).fetchone()
                            if _wr_row and _wr_row[0] is not None:
                                _win_rate_365d = float(_wr_row[0])
                                _n_resolved_365d = int(_wr_row[1] or 0)
                                _profit_365d = float(_wr_row[2] or 0.0)
                        except Exception as _e:
                            logger.debug("S50 365d user stats query failed, falling back to lifetime: %s", _e)
                    if not user:
                        logger.warning(f"User {user_address} not found in database, using defaults")
                    else:
                        # Session 50: prefer 365-day window (matches training LATERAL JOIN)
                        if (
                            _win_rate_365d is not None
                            and _n_resolved_365d >= 5
                            and not (math.isnan(_win_rate_365d) or math.isinf(_win_rate_365d))
                        ):
                            user_win_rate = _win_rate_365d
                            user_profit = _profit_365d if _profit_365d is not None else 0.0
                        else:
                            user_win_rate = float(user.win_rate) if user.win_rate is not None else 0.5
                            user_profit = float(user.total_profit) if user.total_profit is not None else 0.0
                        if math.isnan(user_win_rate) or math.isinf(user_win_rate):
                            logger.warning(f"Invalid user_win_rate {user_win_rate}, using 0.5")
                            user_win_rate = 0.5
                        if math.isnan(user_profit) or math.isinf(user_profit):
                            logger.warning(f"Invalid user_profit {user_profit}, using 0.0")
                            user_profit = 0.0
                    self._user_cache.set(_usr_ck, (user_win_rate, user_profit))

            # BUG FIX: Add error handling for market data conversions
            try:
                liquidity = float(market.liquidity) if market.liquidity is not None else 0.0
                if math.isnan(liquidity) or math.isinf(liquidity) or liquidity < 0:
                    liquidity = 0.0
            except (ValueError, TypeError):
                liquidity = 0.0

            try:
                volume = float(market.volume) if market.volume is not None else 0.0
                if math.isnan(volume) or math.isinf(volume) or volume < 0:
                    volume = 0.0
            except (ValueError, TypeError):
                volume = 0.0

            if math.isnan(liquidity) or math.isinf(liquidity):
                logger.warning(f"Invalid liquidity {liquidity} for market {market_id}, using 0.0")
                liquidity = 0.0
            if math.isnan(volume) or math.isinf(volume):
                logger.warning(f"Invalid volume {volume} for market {market_id}, using 0.0")
                volume = 0.0

            # --- Determine what needs DB queries (check caches first) ---
            elite_net = 0.0
            elite_1h, elite_6h, elite_24h = 0.0, 0.0, 0.0
            use_elite = getattr(settings, "USE_ELITE_NET_DIRECTION", True)
            _need_elite = False
            if use_elite:
                ek = f"elite:{market_id}"
                cached_elite = self._elite_cache.get(ek)
                if cached_elite is not None and isinstance(cached_elite, dict):
                    elite_net = cached_elite.get("net", 0.0)
                    elite_1h = cached_elite.get("1h", 0.0)
                    elite_6h = cached_elite.get("6h", 0.0)
                    elite_24h = cached_elite.get("24h", 0.0)
                elif cached_elite is not None:
                    elite_net = cached_elite
                else:
                    _need_elite = True

            use_path = getattr(settings, "USE_PATH_SUMMARY", True)
            use_regime = getattr(settings, "USE_REGIME_FEATURES", True) and "regime_trend" in (self.feature_columns or [])
            _need_path = use_path and self._path_cache.get(f"path:{market_id}") is None
            _need_regime = use_regime and self._regime_cache.get(f"regime:{market_id}") is None
            _need_fe = (self._feature_engineer and getattr(settings, "USE_FEATURE_ENGINEER", True)
                        and self._fe_cache.get(f"fe:{market_id}") is None)
            _path_feats = None
            _fe_vals = None
            # P1-1: L8 TA features — include in shared session to avoid 2nd get_session() per market
            _l8_ck_pre = f"l8:{market_id}"
            _need_l8 = self._l8_cache.get(_l8_ck_pre) is None and bool(self.db.session_factory)
            _l8_prices_shared: list = []  # populated inside shared session, used below

            # --- SINGLE SESSION for ALL uncached DB queries ---
            # Critical for parallel scans: prevents N×4 session explosion.
            # One market extraction uses at most 1 DB session (for all uncached queries).
            if _need_elite or _need_path or _need_regime or _need_fe or _need_l8:
                try:
                    from sqlalchemy import text as sa_text
                    async with self.db.get_session() as _s:
                        # Elite direction (combined query — replaces 2 separate DB method calls)
                        # BIAS FIX: Use as_of timestamp instead of NOW() to match training temporal scoping.
                        # All cutoff timestamps are computed in Python and passed as explicit parameters.
                        # DO NOT use `:param - INTERVAL '...'` in the SQL — asyncpg binds naive datetimes
                        # in a way that makes `param - INTERVAL` produce an `interval` type (not a timestamp),
                        # which PostgreSQL then cannot compare with `timestamp without time zone` columns,
                        # raising: "operator does not exist: timestamp without time zone >= interval".
                        _as_of = datetime.now(timezone.utc).replace(tzinfo=None)
                        _c1h  = (_as_of - timedelta(hours=1))
                        _c6h  = (_as_of - timedelta(hours=6))
                        _c24h = (_as_of - timedelta(hours=24))
                        _c90d = (_as_of - timedelta(days=90))
                        if _need_elite:
                            try:
                                r_elite = await _s.execute(sa_text("""
                                    SELECT
                                        COALESCE(SUM(
                                            CASE WHEN t.side IN ('YES', 'BUY') THEN 1.0 ELSE -1.0 END
                                            * COALESCE(u.win_rate, 0.5)
                                        ) / NULLIF(SUM(COALESCE(u.win_rate, 0.5)), 0), 0) as net,
                                        COALESCE(
                                            SUM(CASE WHEN t.timestamp >= :c1h THEN
                                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                                            / NULLIF(SUM(CASE WHEN t.timestamp >= :c1h THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0) as d1h,
                                        COALESCE(
                                            SUM(CASE WHEN t.timestamp >= :c6h THEN
                                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                                            / NULLIF(SUM(CASE WHEN t.timestamp >= :c6h THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0) as d6h,
                                        COALESCE(
                                            SUM(CASE WHEN t.timestamp >= :c24h THEN
                                                CASE WHEN t.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END * COALESCE(u.win_rate, 0.5) ELSE 0 END)
                                            / NULLIF(SUM(CASE WHEN t.timestamp >= :c24h THEN COALESCE(u.win_rate, 0.5) ELSE 0 END), 0), 0) as d24h
                                    FROM trades t
                                    JOIN users u ON t.user_address = u.address
                                    WHERE (t.market_id = :market_id OR t.market_id IN (
                                        SELECT condition_id FROM markets WHERE CAST(id AS TEXT) = :market_id
                                        UNION SELECT CAST(id AS TEXT) FROM markets WHERE condition_id = :market_id
                                    ))
                                    AND u.is_elite = TRUE
                                    AND COALESCE(u.is_likely_market_maker, false) = false
                                    AND t.timestamp >= :c90d
                                """), {"market_id": market_id, "c1h": _c1h, "c6h": _c6h, "c24h": _c24h, "c90d": _c90d})
                                erow = r_elite.fetchone()
                                if erow:
                                    elite_net = max(-1.0, min(1.0, float(erow[0] or 0)))
                                    elite_1h = max(-1.0, min(1.0, float(erow[1] or 0)))
                                    elite_6h = max(-1.0, min(1.0, float(erow[2] or 0)))
                                    elite_24h = max(-1.0, min(1.0, float(erow[3] or 0)))
                                self._elite_cache.set(f"elite:{market_id}", {
                                    "net": elite_net, "1h": elite_1h, "6h": elite_6h, "24h": elite_24h
                                })
                            except Exception as e:
                                logger.warning("Elite query failed (using zeros): %s", e)
                                # ROLLBACK aborted transaction so subsequent queries in this session work
                                try:
                                    await _s.rollback()
                                except Exception:
                                    pass

                        # Path summary
                        if _need_path:
                            try:
                                from base_engine.learning.path_summary import get_path_summary, path_summary_to_feature_list
                                end_dt = datetime.now(timezone.utc)
                                # Session 50: was 7 days — training uses 30 days, causing train-serve skew
                                start_dt = end_dt - timedelta(days=30)
                                token_id = getattr(market, "yes_token_id", None) or ""
                                path_sum = await get_path_summary(_s, market_id, token_id, start_dt, end_dt, price)
                                _path_feats = path_summary_to_feature_list(path_sum)
                                self._path_cache.set(f"path:{market_id}", _path_feats)
                            except Exception as e:
                                logger.warning("Path summary at inference failed, using defaults: %s", e)
                                try:
                                    await _s.rollback()
                                except Exception:
                                    pass

                        # Regime detection (inline instead of calling detect_regime which opens own session)
                        if _need_regime:
                            try:
                                cutoff_naive = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)
                                r_regime = await _s.execute(sa_text("""
                                    SELECT price, timestamp FROM market_prices
                                    WHERE market_id = :market_id AND timestamp >= :cutoff
                                    ORDER BY timestamp ASC
                                """), {"market_id": market_id, "cutoff": cutoff_naive})
                                regime_rows = r_regime.fetchall()
                                if len(regime_rows) >= 10:
                                    prices_arr = [float(r[0]) for r in regime_rows if r[0] is not None]
                                    if len(prices_arr) >= 10:
                                        import numpy as np
                                        pa = np.array(prices_arr)
                                        returns = np.diff(pa) / (pa[:-1] + 1e-10)
                                        trend_strength = float(np.mean(returns))
                                        volatility = float(np.std(returns))
                                        regime = {
                                            "regime_trend": max(-1.0, min(1.0, trend_strength * 2 - 1)),
                                            "regime_vol": min(1.0, volatility * 10)
                                        }
                                    else:
                                        regime = {"regime_trend": 0.0, "regime_vol": 0.0}
                                else:
                                    regime = {"regime_trend": 0.0, "regime_vol": 0.0}
                                self._regime_cache.set(f"regime:{market_id}", regime)
                            except Exception as e:
                                logger.warning("Regime at inference failed, using defaults: %s", e)
                                try:
                                    await _s.rollback()
                                except Exception:
                                    pass

                        # FeatureEngineer prices
                        if _need_fe:
                            try:
                                fe_end = datetime.now(timezone.utc)
                                fe_start = fe_end - timedelta(days=30)
                                fe_start_naive = fe_start.replace(tzinfo=None)
                                fe_cond_id = getattr(market, "condition_id", None) or ""
                                fe_m_id = str(market_id)
                                r_fe = await _s.execute(
                                    sa_text(
                                        "SELECT price FROM market_prices "
                                        "WHERE (market_id = :mid OR market_id = :cid) AND timestamp >= :since "
                                        "ORDER BY timestamp ASC LIMIT 500"
                                    ),
                                    {"mid": fe_m_id, "cid": fe_cond_id, "since": fe_start_naive},
                                )
                                fe_rows = r_fe.fetchall()
                                fe_prices = [float(r[0]) for r in fe_rows if r[0] is not None]
                                fe_market = {"liquidity": liquidity, "volume": volume, "category": getattr(market, "category", "") or ""}
                                fe = self._feature_engineer.generate_features(fe_market, fe_prices)
                                _fe_vals = [
                                    fe.get("current_price", 0.0), fe.get("price_change", 0.0),
                                    fe.get("price_change_pct", 0.0), fe.get("volatility", 0.0),
                                    fe.get("mean_return", 0.0), fe.get("ma_5", 0.0),
                                    fe.get("ma_10", 0.0), fe.get("ma_20", 0.0),
                                    fe.get("price_percentile", 0.0), fe.get("price_vs_high", 0.0),
                                    fe.get("price_vs_low", 0.0), fe.get("liquidity", 0.0),
                                    fe.get("volume", 0.0), fe.get("has_category", 0.0),
                                ]
                                self._fe_cache.set(f"fe:{market_id}", _fe_vals)
                            except Exception as e:
                                logger.warning("FeatureEngineer at inference failed, using defaults: %s", e)

                        # P1-1: L8 TA — fetch last 30 prices in same session (avoids 2nd get_session() call)
                        if _need_l8:
                            try:
                                _cid_l8 = getattr(market, "condition_id", None) or ""
                                _l8_r = await _s.execute(
                                    sa_text(
                                        "SELECT price FROM market_prices "
                                        "WHERE (market_id = :mid OR market_id = :cid) "
                                        "ORDER BY timestamp DESC LIMIT 30"
                                    ),
                                    {"mid": str(market_id), "cid": _cid_l8},
                                )
                                _l8_rows_shared = _l8_r.fetchall()
                                if _l8_rows_shared and len(_l8_rows_shared) >= 5:
                                    _l8_prices_shared = [float(r[0]) for r in reversed(_l8_rows_shared) if r[0] is not None]
                            except Exception as _l8_shared_err:
                                logger.debug("L8 TA query in shared session failed: %s", _l8_shared_err)
                except Exception as e:
                    logger.warning("Shared DB session for features failed: %s", e)

            # D6 FIX: Encode category at inference (matching training pipeline)
            _top_categories = ["politics", "crypto", "sports", "science", "pop-culture", "business", "world-news"]
            _cat = (getattr(market, "category", None) or "").lower().strip()
            _cat_map = {c: i + 1 for i, c in enumerate(_top_categories)}
            category_encoded = float(_cat_map.get(_cat, 0))

            # B6 FIX: "size" removed (was always 1.0 at inference, varies in training)
            # F2 FIX: resolution_source_encoded removed (temporal leakage — not available at inference time)
            features = [
                price,
                liquidity,
                volume,
                user_win_rate,
                user_profit,
                category_encoded,
            ]
            feature_names = ["price", "liquidity", "volume", "user_win_rate", "user_profit", "category_encoded"]
            if use_elite:
                features.append(elite_net)
                feature_names.append("elite_net_direction")
                features.extend([elite_1h, elite_6h, elite_24h])
                feature_names.extend(["elite_direction_1h", "elite_direction_6h", "elite_direction_24h"])
            # R2c: Signal features are now re-enabled (B3 FIX resolved).
            # Historical trades use 0.5/0.5 neutral default; paper trades have real values
            # from trade_signals table via the training query JOIN. At inference we fetch
            # the current best signal for this market from signal_ingestion (same source as
            # the multipliers in apply_signal_enhancements, ensuring train-serve parity).
            if getattr(settings, "USE_SIGNAL_FEATURES_IN_ML", True) and "signal_confidence" in (self.feature_columns or []):
                sig_conf, sig_dir_enc = 0.5, 0.5  # Neutral defaults (unknown/no signal)
                _sig_ck = f"sig:{market_id}"
                _sig_cached = self._signal_cache.get(_sig_ck)
                if _sig_cached is not None:
                    sig_conf, sig_dir_enc = _sig_cached
                elif self.db and getattr(self.db, "session_factory", None):
                    try:
                        from sqlalchemy import text as _text
                        async with self.db.get_session() as _sig_sess:
                            _sig_res = await _sig_sess.execute(_text("""
                                SELECT signal_confidence, signal_direction
                                FROM trade_signals
                                WHERE market_id = :mid
                                  AND created_at >= NOW() - INTERVAL '30 minutes'
                                ORDER BY created_at DESC LIMIT 1
                            """), {"mid": str(market_id)})
                            _sig_row = _sig_res.fetchone()
                        if _sig_row:
                            sig_conf = float(_sig_row[0] or 0.5)
                            _raw_dir = str(_sig_row[1] or "")
                            sig_dir_enc = 1.0 if _raw_dir == "YES" else (0.0 if _raw_dir == "NO" else 0.5)
                    except Exception as _sig_err:
                        logger.debug("Signal feature lookup failed (neutral default): %s", _sig_err)
                self._signal_cache.set(_sig_ck, (sig_conf, sig_dir_enc), ttl=30.0)
                features.extend([sig_conf, sig_dir_enc])
                feature_names.extend(["signal_confidence", "signal_direction_encoded"])
            # Assemble path features
            if use_path:
                from base_engine.learning.path_summary import path_summary_to_feature_list
                _path_cached = self._path_cache.get(f"path:{market_id}")
                if _path_cached is not None:
                    features.extend(_path_cached)
                elif _path_feats is not None:
                    features.extend(_path_feats)
                else:
                    features.extend(path_summary_to_feature_list(None))
                feature_names.extend(["path_min", "path_max", "path_final", "path_vol", "path_drawdown", "time_above_entry", "max_run_up", "max_run_down"])
            # Assemble regime features
            if use_regime:
                from base_engine.learning.path_summary import regime_features_to_list, REGIME_DEFAULTS
                regime = self._regime_cache.get(f"regime:{market_id}")
                if regime is not None:
                    features.extend(regime_features_to_list(regime))
                else:
                    features.extend(regime_features_to_list(REGIME_DEFAULTS))
                feature_names.extend(["regime_trend", "regime_vol"])
            # Assemble FE features
            if self._feature_engineer and getattr(settings, "USE_FEATURE_ENGINEER", True):
                _fe_cached = self._fe_cache.get(f"fe:{market_id}")
                if _fe_cached is not None:
                    features.extend(_fe_cached)
                elif _fe_vals is not None:
                    features.extend(_fe_vals)
                else:
                    features.extend([0.0] * 14)
                feature_names.extend([
                    "fe_current_price", "fe_price_change", "fe_price_change_pct", "fe_volatility",
                    "fe_mean_return", "fe_ma_5", "fe_ma_10", "fe_ma_20",
                    "fe_price_percentile", "fe_price_vs_high", "fe_price_vs_low",
                    "fe_liquidity", "fe_volume", "fe_has_category",
                ])
            # L7: Time-based features (cyclical sin/cos encoding; matches training pipeline)
            import math as _inf_math
            _now_utc = datetime.now(timezone.utc)
            _l7_dow_sin = _inf_math.sin(2 * _inf_math.pi * _now_utc.weekday() / 7.0)
            _l7_dow_cos = _inf_math.cos(2 * _inf_math.pi * _now_utc.weekday() / 7.0)
            _l7_hod_sin = _inf_math.sin(2 * _inf_math.pi * _now_utc.hour / 24.0)
            _l7_hod_cos = _inf_math.cos(2 * _inf_math.pi * _now_utc.hour / 24.0)
            _l7_age = 0.5  # neutral default
            _l7_tte = 0.5  # neutral default
            try:
                _created = getattr(market, "created_at", None)
                if _created:
                    if hasattr(_created, "tzinfo") and _created.tzinfo is None:
                        _created = _created.replace(tzinfo=timezone.utc)
                    _l7_age = min(1.0, (_now_utc - _created).days / 365.0)
            except Exception:
                pass
            try:
                _end_date = getattr(market, "end_date_iso", None)
                if _end_date:
                    if isinstance(_end_date, str):
                        from dateutil.parser import parse as _date_parse
                        _end_date = _date_parse(_end_date)
                    if hasattr(_end_date, "tzinfo") and _end_date.tzinfo is None:
                        _end_date = _end_date.replace(tzinfo=timezone.utc)
                    _tte_days = (_end_date - _now_utc).total_seconds() / 86400.0
                    _l7_tte = max(0.0, min(1.0, _tte_days / 365.0))
            except Exception:
                pass
            features.extend([_l7_dow_sin, _l7_dow_cos, _l7_hod_sin, _l7_hod_cos, _l7_age, _l7_tte])
            feature_names.extend(["time_dow_sin", "time_dow_cos", "time_hour_sin", "time_hour_cos", "time_market_age", "time_to_expiry"])

            # L8: Technical Analysis features (RSI, Bollinger position, ATR normalized)
            from base_engine.learning.feature_engineering import compute_rsi, compute_bollinger_position, compute_atr_normalized
            _l8_prices = None
            _fe_ck = f"fe:{market_id}"
            # Reuse FE price data if available (already fetched above), otherwise use empty
            if _need_fe and _fe_vals is not None:
                # Prices were fetched for FE — use the same session's cached prices
                pass  # _l8_prices computed below from regime cache or FE cache
            # Try to get prices from regime cache (already fetched)
            try:
                if _need_regime:
                    _regime_cached = self._regime_cache.get(f"regime:{market_id}")
                    # Regime computation used prices_arr but we don't have it cached separately
                    # Fall back to FE prices
                    pass
            except Exception:
                pass
            # Quick path: use the same prices that FE used (already in the session)
            # The FE generate_features was called with fe_prices, which is the price history
            # We stored _fe_vals (14 features) but not the raw prices. For TA indicators,
            # we need raw prices. Use a quick query or approximate from FE features.
            _l8_rsi = 0.5
            _l8_boll = 0.5
            _l8_atr = 0.0
            try:
                # P1-1: L8 TA features — use shared session result or cache (no 2nd get_session())
                _l8_ck = f"l8:{market_id}"
                _l8_cached = self._l8_cache.get(_l8_ck)
                if _l8_cached is not None:
                    _l8_rsi, _l8_boll, _l8_atr = _l8_cached
                elif _l8_prices_shared:
                    # Prices fetched in the shared session above — compute TA indicators
                    _l8_rsi = compute_rsi(_l8_prices_shared)
                    _l8_boll = compute_bollinger_position(_l8_prices_shared)
                    _l8_atr = compute_atr_normalized(_l8_prices_shared)
                    self._l8_cache.set(_l8_ck, (_l8_rsi, _l8_boll, _l8_atr))
                # else: no prices available — keep defaults (0.5/0.5/0.0)
            except Exception as e:
                logger.debug("L8 TA features failed (using defaults): %s", e)

            features.extend([_l8_rsi, _l8_boll, _l8_atr])
            feature_names.extend(["ta_rsi", "ta_bollinger_pos", "ta_atr_normalized"])

            # Tier 2 #16: Resolution clarity score (only if this feature is in the trained model)
            if getattr(settings, "RESOLUTION_CLARITY_ENABLED", True) and "clarity_score" in (self.feature_columns or []):
                _clarity_score = 0.5  # Session 50: was 0.7; 0.5 = truly neutral
                _rra = getattr(self, "_resolution_risk_analyzer", None)
                if _rra is not None:
                    _cached_clarity = _rra._clarity_cache.get(str(market_id))
                    if _cached_clarity is not None:
                        import os as _os_c
                        from datetime import timezone as _tz_c
                        _cscore, _cts = _cached_clarity
                        _cttl = float(_os_c.getenv("RESOLUTION_CLARITY_CACHE_TTL_HOURS", "24")) * 3600
                        if (datetime.now(timezone.utc) - _cts).total_seconds() < _cttl:
                            _clarity_score = _cscore
                features.append(_clarity_score)
                feature_names.append("clarity_score")

            # Section 21: Polling-derived features (if polling_client available)
            _polling = getattr(self, '_polling_client', None)
            if _polling:
                try:
                    _poll_data = _polling.get_latest_aggregate(market_id) if hasattr(_polling, 'get_latest_aggregate') else None
                    features.append(_poll_data.get("probability", 0.5) if _poll_data else 0.5)
                    feature_names.append("polling_model_prob")
                    features.append(abs(features[-1] - price) if _poll_data else 0.0)
                    feature_names.append("poll_market_divergence")
                except Exception:
                    features.extend([0.5, 0.0])
                    feature_names.extend(["polling_model_prob", "poll_market_divergence"])

            # Section 21: Cross-market features (if logical_arbitrage available)
            _logical = getattr(self, '_logical_arbitrage', None)
            if _logical:
                try:
                    _cache_stats = _logical.get_cache_stats()
                    features.append(_cache_stats.get("relationship_cache_size", 0))
                    feature_names.append("cross_market_constraint_count")
                except Exception:
                    features.append(0)
                    feature_names.append("cross_market_constraint_count")

            # Sanitize NaN/Inf → 0.0 instead of raising (one bad sub-query shouldn't block the whole market)
            _bad_count = 0
            for i in range(len(features)):
                if math.isnan(features[i]) or math.isinf(features[i]):
                    features[i] = 0.0
                    _bad_count += 1
            if _bad_count > 0:
                logger.debug("Sanitized %d NaN/Inf features for market %s", _bad_count, market_id)
            if len(feature_names) != len(features):
                logger.warning(
                    "Feature name/value length mismatch for market %s: %d names vs %d values. "
                    "Using fallback feature_names (may indicate a conditional feature bug).",
                    market_id, len(feature_names), len(features),
                )
                feature_names = ["price", "liquidity", "volume", "user_win_rate", "user_profit", "category_encoded"]
                if getattr(settings, "USE_ELITE_NET_DIRECTION", True):
                    feature_names.append("elite_net_direction")
                    feature_names.extend(["elite_direction_1h", "elite_direction_6h", "elite_direction_24h"])
                if getattr(settings, "USE_SIGNAL_FEATURES_IN_ML", True) and "signal_confidence" in (self.feature_columns or []):
                    feature_names.extend(["signal_confidence", "signal_direction_encoded"])
                if use_path:
                    feature_names.extend(["path_min", "path_max", "path_final", "path_vol", "path_drawdown", "time_above_entry", "max_run_up", "max_run_down"])
                if use_regime:
                    feature_names.extend(["regime_trend", "regime_vol"])
                if self._feature_engineer and getattr(settings, "USE_FEATURE_ENGINEER", True):
                    feature_names.extend([
                        "fe_current_price", "fe_price_change", "fe_price_change_pct", "fe_volatility",
                        "fe_mean_return", "fe_ma_5", "fe_ma_10", "fe_ma_20",
                        "fe_price_percentile", "fe_price_vs_high", "fe_price_vs_low",
                        "fe_liquidity", "fe_volume", "fe_has_category",
                    ])
                # L7 time features + L8 TA features (always present in normal path)
                feature_names.extend(["time_dow_sin", "time_dow_cos", "time_hour_sin", "time_hour_cos", "time_market_age", "time_to_expiry"])
                feature_names.extend(["ta_rsi", "ta_bollinger_pos", "ta_atr_normalized"])
                if getattr(settings, "RESOLUTION_CLARITY_ENABLED", True) and "clarity_score" in (self.feature_columns or []):
                    feature_names.append("clarity_score")
                # Section 21 fallback: polling + cross-market features (conditional, mirrors normal path)
                if getattr(self, '_polling_client', None):
                    feature_names.extend(["polling_model_prob", "poll_market_divergence"])
                if getattr(self, '_logical_arbitrage', None):
                    feature_names.append("cross_market_constraint_count")
            feat_dict = dict(zip(feature_names, features))
            result = [float(feat_dict.get(c, 0.0)) for c in self.feature_columns]
            # Cache the computed feature vector for fast-path reuse in predict()
            _mkt_obj = market
            _yes_tid = getattr(_mkt_obj, "yes_token_id", None) or ""
            _no_tid = getattr(_mkt_obj, "no_token_id", None) or ""
            # Store for both YES and NO token IDs (same features, different price slot)
            for _tid in [_yes_tid, _no_tid]:
                if _tid:
                    self._feature_vector_cache.set(f"fv:{market_id}:{_tid}", list(result), ttl=_FV_CACHE_TTL)
            return result
        except Exception as e:
            logger.error(f"Feature extraction failed for market {market_id}: {str(e)}", exc_info=True)
            raise
