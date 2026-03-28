"""
WeatherBot — trades Polymarket temperature-bucket markets using NOAA ensemble forecasts.

Strategy:
  1. Fetch GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no key)
  2. Fit skew-normal distribution to ensemble spread
  3. Integrate CDF across each temperature bucket's bounds → model probabilities
  4. Compare model probs vs market-implied probs (YES prices)
  5. Trade when edge ≥ 15% (configurable), sized by fractional Kelly criterion

Multi-outcome awareness: each city+date has ~7 bucket markets. We analyze all
buckets as a group and trade the ones with the widest mispricing.

SWOT upgrades applied:
  P1 - load_calibration() wired to weather_calibration DB table (6h refresh)
  P2 - daily P&L restored from paper_trades DB on day boundary (survives restarts)
  P3 - ensemble forecasts persisted to weather_forecasts DB table
  P5 - ECMWF ENS 51 members combined with GEFS 31 members (in forecast_client)
  Near-expiry - Kelly boosted 1.5x when < 24h to resolution
  Cross-city  - regime boost 1.2x when ≥3 US cities show unanimous warm/cold edge
"""

import asyncio
import copy
import json
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from base_engine.monitoring.alerting import AlertSeverity
from base_engine.weather.forecast_client import CombinedForecast, WeatherForecastClient
from base_engine.weather.metar_client import MetarClient
from base_engine.weather.market_mapper import (
    PrecipitationMarketGroup,
    TemperatureBucket,
    WeatherMarketGroup,
    WeatherMarketMapper,
)
from base_engine.weather.precipitation_engine import PrecipitationProbabilityEngine
from base_engine.weather.probability_engine import WeatherProbabilityEngine
from base_engine.weather.station_registry import (
    STATION_REGISTRY,
    StationHealthMonitor,
    WeatherStation,
)
from base_engine.weather.model_run_monitor import ModelRunMonitor
from base_engine.weather.metar_monitor import MetarMonitor
from base_engine.data.daily_counter import increment_counter as _inc_daily, restore_counters as _restore_daily
from config.settings import settings

logger = get_logger()


# ---------------------------------------------------------------------------
# Confidence calibrator — Logistic Regression (S135: replaces Platt+Isotonic)
# ---------------------------------------------------------------------------
_CONF_CAL_MIN_SAMPLES = 200  # minimum resolved trades to fit


class WeatherConfidenceCalibrator:
    """Logistic regression calibrator with 6 features.

    Fits on WeatherBot trade_events: (confidence, side, lead_time_hours, price,
    bucket_type_enc, ensemble_spread) → win/loss.
    S135: Replaced Platt+Isotonic. S136: Added bucket_type and ensemble_spread.
    bucket_type_enc: at_or_higher=1, range=2, else=0.
    """

    def __init__(self) -> None:
        self._model: Any = None     # sklearn LogisticRegression
        self._scaler: Any = None    # sklearn StandardScaler
        self._fitted: bool = False
        self._n_samples: int = 0
        self._cal_brier: Optional[float] = None  # calibrated Brier score — fed to BankrollManager
        self._coef_confidence: float = 0.0  # for .temperature property compat
        self._coefficients: dict = {}       # for logging/inspection

    # -- fitting -------------------------------------------------------------

    async def fit_from_trade_events(
        self, db: Any, window_days: int = 30, min_samples: int = _CONF_CAL_MIN_SAMPLES,
    ) -> bool:
        """Fit LogisticRegression from WeatherBot resolved trade_events.

        Features: raw_confidence, side (YES=1/NO=0), lead_time_hours, entry_price.
        Returns True if fitted, False if insufficient data (identity passthrough).
        """
        if not db:
            return False
        try:
            import numpy as np
            from sqlalchemy import text

            async with db.get_session() as session:
                result = await session.execute(text("""
                    WITH entries AS (
                        SELECT DISTINCT ON (market_id) market_id, confidence, side, price,
                               COALESCE((event_data->>'lead_time_hours')::float, 48.0) AS lead_time_hours,
                               CASE event_data->>'bucket_type'
                                   WHEN 'at_or_higher' THEN 1.0
                                   WHEN 'range' THEN 2.0
                                   ELSE 0.0
                               END AS bucket_type_enc,
                               COALESCE((event_data->>'ensemble_spread')::float, 3.0) AS ensemble_spread
                        FROM trade_events
                        WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
                          AND event_time >= NOW() - INTERVAL '1 day' * :window_days
                          AND confidence IS NOT NULL
                          AND price >= 0.08
                        ORDER BY market_id, event_time
                    )
                    SELECT e.confidence, e.side, e.lead_time_hours, e.price,
                           e.bucket_type_enc, e.ensemble_spread,
                           CASE WHEN r.realized_pnl > 0 THEN 1.0 ELSE 0.0 END AS outcome
                    FROM trade_events r
                    JOIN entries e ON e.market_id = r.market_id
                    WHERE r.bot_name = 'WeatherBot' AND r.event_type = 'RESOLUTION'
                      AND r.realized_pnl IS NOT NULL
                """), {"window_days": window_days})
                rows = result.fetchall()

            if len(rows) < min_samples:
                logger.info(
                    "weatherbot_confidence_cal_insufficient_data",
                    n=len(rows), need=min_samples,
                )
                return False

            confidences = np.array([float(r[0]) for r in rows], dtype=np.float64)
            sides = np.array([1.0 if r[1] == "YES" else 0.0 for r in rows], dtype=np.float64)
            lead_times = np.array([float(r[2]) for r in rows], dtype=np.float64)
            prices = np.array([float(r[3]) for r in rows], dtype=np.float64)
            bucket_encs = np.array([float(r[4]) for r in rows], dtype=np.float64)
            spreads = np.array([float(r[5]) for r in rows], dtype=np.float64)
            outcomes = np.array([float(r[6]) for r in rows], dtype=np.float64)

            X = np.column_stack([confidences, sides, lead_times, prices, bucket_encs, spreads])

            from sklearn.preprocessing import StandardScaler
            from sklearn.linear_model import LogisticRegression

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
            model.fit(X_scaled, outcomes)

            # Validate: calibrated Brier should not be worse than raw
            raw_brier = float(np.mean((confidences - outcomes) ** 2))
            cal_probs = model.predict_proba(X_scaled)[:, 1]
            cal_brier = float(np.mean((cal_probs - outcomes) ** 2))

            if cal_brier > raw_brier + 0.005:
                logger.warning(
                    "weatherbot_confidence_cal_rejected",
                    raw_brier=round(raw_brier, 4),
                    cal_brier=round(cal_brier, 4),
                    n=len(rows),
                )
                self._fitted = False
                return False

            self._model = model
            self._scaler = scaler
            self._fitted = True
            self._n_samples = len(rows)
            self._cal_brier = cal_brier
            self._coef_confidence = float(model.coef_[0][0])
            self._coefficients = {
                "confidence": round(float(model.coef_[0][0]), 4),
                "side_yes": round(float(model.coef_[0][1]), 4),
                "lead_time": round(float(model.coef_[0][2]), 4),
                "entry_price": round(float(model.coef_[0][3]), 4),
                "bucket_type": round(float(model.coef_[0][4]), 4),
                "ensemble_spread": round(float(model.coef_[0][5]), 4),
                "intercept": round(float(model.intercept_[0]), 4),
            }

            logger.info(
                "weatherbot_confidence_cal_fitted",
                model_type="logistic_regression",
                n_samples=len(rows),
                raw_brier=round(raw_brier, 4),
                cal_brier=round(cal_brier, 4),
                brier_improvement=round(raw_brier - cal_brier, 4),
                coef_confidence=self._coefficients["confidence"],
                coef_side_yes=self._coefficients["side_yes"],
                coef_lead_time=self._coefficients["lead_time"],
                coef_entry_price=self._coefficients["entry_price"],
                coef_bucket_type=self._coefficients["bucket_type"],
                coef_ensemble_spread=self._coefficients["ensemble_spread"],
                intercept=self._coefficients["intercept"],
                window_days=window_days,
            )
            return True

        except Exception as exc:
            logger.debug("weatherbot_confidence_cal_fit_failed", error=str(exc))
            return False

    # -- inference -----------------------------------------------------------

    def calibrate(self, raw_confidence: float, side: str = "YES",
                  lead_time_hours: float = 48.0, entry_price: float = 0.50,
                  bucket_type: str = "unknown", ensemble_spread: float = 3.0) -> float:
        """Apply logistic regression calibration. Identity if not fitted.

        bucket_type: 'at_or_higher'=1, 'range'=2, else=0.
        ensemble_spread: std dev of ensemble members (degrees).
        """
        if not self._fitted:
            return raw_confidence
        try:
            import numpy as np
            side_enc = 1.0 if side == "YES" else 0.0
            bucket_enc = 1.0 if bucket_type == "at_or_higher" else (2.0 if bucket_type == "range" else 0.0)
            X = np.array([[raw_confidence, side_enc, lead_time_hours, entry_price,
                           bucket_enc, ensemble_spread]])
            X_scaled = self._scaler.transform(X)
            result = self._model.predict_proba(X_scaled)[0, 1]
            return float(np.clip(result, 0.01, 0.99))
        except Exception:
            return raw_confidence

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def temperature(self) -> float:
        """Backward compat: returns confidence coefficient (diagnostic proxy for T)."""
        return self._coef_confidence

    @property
    def n_samples(self) -> int:
        return self._n_samples


class WeatherBot(BaseBot):
    def __init__(self, base_engine: BaseEngine):
        super().__init__("WeatherBot", base_engine)

        # Sub-components
        self._forecast_client = WeatherForecastClient(
            cache_ttl=float(getattr(settings, "WEATHER_FORECAST_CACHE_TTL", 900)),
            rate_limit_per_min=int(getattr(settings, "WEATHER_RATE_LIMIT_PER_MIN", 120)),
        )
        # Phase 1: inject Redis cache so 429 cooldowns survive restarts
        redis_cache = getattr(base_engine, "cache", None)
        if redis_cache:
            self._forecast_client.set_redis_cache(redis_cache)
        self._metar_client = MetarClient()
        if getattr(settings, "ASOS_1MIN_ENABLED", False):
            from base_engine.weather.asos_onemin_client import AsosOneMinClient
            self._metar_client.set_asos_client(AsosOneMinClient())
        self._prob_engine = WeatherProbabilityEngine()
        self._precip_engine = PrecipitationProbabilityEngine()
        self._market_mapper = WeatherMarketMapper()
        self._station_health = StationHealthMonitor()

        # Config
        self._min_edge = float(getattr(settings, "WEATHER_MIN_EDGE", 0.08))
        self._intl_min_edge = float(getattr(settings, "WEATHER_INTL_MIN_EDGE", 0.12))
        self._max_per_group = float(getattr(settings, "WEATHER_MAX_PER_GROUP_USD", 10000.0))  # S122: 1000→10000
        self._daily_loss_limit = float(getattr(settings, "WEATHER_DAILY_LOSS_LIMIT", 10000.0))
        self._max_correlated = float(getattr(settings, "WEATHER_MAX_CORRELATED_EXPOSURE", 5000.0))  # S122: 2000→5000
        self._kelly_mult = float(getattr(settings, "WEATHER_KELLY_FRACTION", 0.25))
        self._default_size = float(getattr(settings, "WEATHER_DEFAULT_SIZE", 100.0))
        self._max_lead_time = float(getattr(settings, "WEATHER_MAX_LEAD_TIME_HOURS", 168.0))

        # Risk state (P2: restored from DB on day boundary)
        self._daily_pnl = 0.0
        self._daily_pnl_date: Optional[str] = None
        self._group_exposure: Dict[str, float] = {}   # "city:date" → USD deployed
        self._city_exposure: Dict[str, float] = {}     # city → total USD deployed
        self._recently_exited: Dict[str, float] = {}   # market_id → mono time
        self._exit_cooldown_secs = float(getattr(settings, "WEATHER_EXIT_COOLDOWN_SECS", 14400.0))
        self._known_open_markets: Set[str] = set()     # snapshot for PM exit detection
        # S104: market_id → (group_key, city, cost_usd) — survives cache expiry, used for exit exposure decrement
        self._market_group_cache: Dict[str, Tuple[str, str, float]] = {}

        # S97: Liquidity cache (3-min TTL) — fail-closed on check_liquidity() exception
        self._liquidity_cache: Dict[str, Tuple[float, Dict]] = {}  # market_id → (mono_time, result)
        self._liquidity_cache_ttl: float = 180.0  # 3 minutes

        # S97: Discovery cache (5-min TTL) — avoid Gamma API call every scan
        self._discovery_cache: Optional[Tuple[float, List, List]] = None  # (mono_time, markets, groups)
        self._discovery_cache_ttl: float = 300.0  # 5 minutes

        # S99: Fill-failure cooldown — market_id → (consecutive_fails, last_attempt_mono)
        self._fill_fail_tracker: Dict[str, Tuple[int, float]] = {}
        self._fill_fail_max_consec = int(getattr(settings, "WEATHER_FILL_FAIL_COOLDOWN_SCANS", 2))
        self._fill_fail_cooldown_secs = float(getattr(settings, "WEATHER_FILL_FAIL_COOLDOWN_SECS", 900.0))

        # S99: Fill probability floor — skip trades where price-depth alone predicts <threshold
        self._min_fill_prob_estimate = float(getattr(settings, "WEATHER_MIN_FILL_PROB_ESTIMATE", 0.25))

        # S99: PSW every-other-scan divisor
        self._psw_scan_divisor = int(getattr(settings, "WEATHER_PSW_SCAN_DIVISOR", 2))

        # S99: Adaptive scan backoff
        self._consecutive_no_edge: int = 0
        self._backoff_threshold = int(getattr(settings, "WEATHER_ADAPTIVE_BACKOFF_THRESHOLD", 6))
        self._max_scan_interval = float(getattr(settings, "WEATHER_MAX_SCAN_INTERVAL", 600.0))

        # S99/S115: Unified discovery cache for precip/snow/wind (10-20 min TTL)
        # key → (monotonic_time, events_list)
        self._psw_discovery_cache: Dict[str, Tuple[float, list]] = {}

        # S97: Exposure lock for parallel trade execution
        self._exposure_lock = asyncio.Lock()


        # S97: Priority queue for jump detection + METAR boundary events
        self._priority_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # S97: Model-run monitor + METAR monitor (started on first scan)
        _all_stations = list(STATION_REGISTRY.values())
        self._model_run_monitor = ModelRunMonitor(
            forecast_client=self._forecast_client,
            stations=_all_stations,
            priority_queue=self._priority_queue,
        )
        # S102: Pass Redis cache to MetarMonitor for daily max persistence
        _redis_cache = getattr(base_engine, "cache", None) if base_engine else None
        self._metar_monitor = MetarMonitor(
            stations=_all_stations,
            priority_queue=self._priority_queue,
            redis_cache=_redis_cache,
        )
        self._monitors_started = False

        # P1: calibration state
        self._calibration_last_loaded: float = 0.0
        self._calibration_reload_interval: float = float(getattr(settings, "WEATHER_CALIBRATION_RELOAD_SECS", 21600.0))

        # S135: Logistic regression confidence calibrator (4-feature: conf, side, lead_time, price)
        self._confidence_calibrator = WeatherConfidenceCalibrator()
        self._cal_fitted: bool = False

        # W4: Monitoring thresholds — structured Brier/drawdown alerts
        self._monitoring_halt: bool = False  # True = stop trading until Brier improves
        self._monitoring_last_check: float = 0.0
        self._monitoring_check_interval: float = 600.0  # 10 minutes

        # Startup observability flag — runs market availability check once on first scan
        self._startup_check_done: bool = False
        # Phase 1+2: one-time cache restore from Redis/DB on first scan
        self._cache_warmed: bool = False

        # Rate-limit the direct API probe (DB + Gamma) to once per 30 min.
        # Without this, every 5-min scan with 0 weather markets fires an extra
        # DB query + HTTP call to Gamma API, lengthening every scan cycle.
        self._last_direct_probe: float = 0.0
        self._direct_probe_interval: float = 1800.0  # 30 minutes

        # P3: dedup tracking (avoid writing same forecast twice in one session)
        self._written_forecasts: Set[str] = set()  # "station_id:date_iso"

        # Cross-bot transfer: prediction logging dedup cache
        # market_id → (predicted_prob, monotonic_ts); skip if delta < 0.01 within 600s
        self._prediction_log_cache: Dict[str, Tuple[float, float]] = {}
        self._scan_count: int = 0

        # S101b: City discovery tracking
        self._alerted_unmatched_cities: Set[str] = set()  # dedup alerts per session
        self._last_city_digest_date: Optional[str] = None  # "YYYY-MM-DD" for daily digest

        # Cross-bot transfer: per-market-type consecutive loss tracking (from EsportsBot)
        self._consecutive_losses: Dict[str, int] = {}  # market_type → streak count

        # Cross-bot transfer: per-market-type adaptive parameters (from MirrorBot)
        self._category_params: Dict[str, Dict[str, float]] = {}
        self._category_params_loaded: bool = False

        # Cross-bot transfer: per-station reliability-weighted sizing (from MirrorBot)
        # station_id → (mse, monotonic_ts); 1-hour TTL
        self._station_mse_cache: Dict[str, Tuple[float, float]] = {}

        # Cross-bot transfer: EMOS drift detection (DDM/EDDM per station)
        self._drift_detectors: Dict[str, Any] = {}  # station_id → DriftDetector

        # P2-regime: ENSO regime cache (el_nino / la_nina / neutral)
        # Nino 3.4 SST anomaly updated monthly; cache for 24h.
        self._regime_tag: Optional[str] = None
        self._regime_last_fetched: float = 0.0
        self._regime_cache_ttl: float = 86400.0  # 24 hours

        # T3C: AFD (Area Forecast Discussion) spread adjustment cache
        # station_id → (expiry_mono, spread_factor)
        self._afd_cache: Dict[str, Tuple[float, float]] = {}
        # NWS WFO cache: station_id → Optional[wfo_code] (never expires — WFOs are static)
        self._wfo_cache: Dict[str, Optional[str]] = {}

        # Batch NWS severe weather alerts cache (prefetched once per scan)
        # station_id → boost_factor
        self._severe_weather_batch: Dict[str, float] = {}
        self._severe_weather_batch_time: float = 0.0
        # S115: Also store alert event names for hard halt check
        self._severe_weather_events: Dict[str, List[str]] = {}  # station_id → [event_names]

        # S114: Cold-start mitigation
        # Item 1 — Spread confidence gate: rolling 14-day model_spread per station
        from collections import deque
        self._spread_history: Dict[str, deque] = {}  # station_id → deque(maxlen=14)
        # Item 2 — Bühlmann sizing ramp: resolved pairs per station (refreshed with calibration)
        self._station_n_resolved: Dict[str, int] = {}
        self._buhlmann_kappa: float = float(getattr(settings, "WEATHER_BUHLMANN_KAPPA", 30.0))
        # Item 4 — Historical bias bootstrap: stations already bootstrapped this session
        self._bootstrapped_stations: Set[str] = set()

    # ── Prediction logging (cross-bot transfer from EsportsBot) ────────────

    async def _log_weather_prediction(
        self,
        market_id: str,
        model_prob: float,
        market_price: float,
        confidence: float,
        market_type: str,
    ) -> None:
        """Log prediction to prediction_log for accuracy tracking + drift detection.

        Dedup: skip if same market_id has |delta_prob| < 0.01 within 600s.
        """
        now_mono = time.monotonic()
        cached = self._prediction_log_cache.get(market_id)
        if cached and abs(model_prob - cached[0]) < 0.01 and now_mono - cached[1] < 600:
            return
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            await db.insert_prediction_log(
                market_id=market_id,
                predicted_prob=model_prob,
                market_price=market_price,
                model_name=f"weather_{market_type}",
                bot_name="WeatherBot",
                confidence=confidence,
            )
            self._prediction_log_cache[market_id] = (model_prob, now_mono)
        except Exception:
            pass  # insert_prediction_log already logs internally

    async def _backfill_weather_outcomes(self) -> None:
        """Resolve WeatherBot predictions against settled markets.

        Runs every 10 scans (~50 min). Calls shared backfill_prediction_log_resolution()
        which propagates market resolutions to prediction_log rows.
        Also feeds consecutive loss tracker with newly resolved outcomes.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            n = await db.backfill_prediction_log_resolution()
            if n:
                logger.info("weatherbot_prediction_backfill_resolved", count=n)
        except Exception as exc:
            logger.debug("weatherbot_prediction_backfill_failed", error=str(exc))

        # Feed consecutive loss tracker with recently resolved predictions
        try:
            from sqlalchemy import text as sa_text
            async with db.get_session() as session:
                result = await session.execute(sa_text(
                    "SELECT model_name, was_correct FROM prediction_log "
                    "WHERE bot_name = 'WeatherBot' "
                    "AND was_correct IS NOT NULL "
                    "AND resolved_at > NOW() - INTERVAL '1 hour'"
                ))
                for row in result.fetchall():
                    mtype = str(row[0]).replace("weather_", "")
                    self._record_weather_outcome(mtype, bool(row[1]))
        except Exception as exc:
            logger.debug("weatherbot_outcome_feed_failed", error=str(exc))

    # ── Drawdown compression (cross-bot transfer from EsportsBot) ────────

    # (min_consecutive_losses, kelly_factor) — first matching threshold wins
    _DRAWDOWN_SCHEDULE = [(8, 0.25), (5, 0.50), (3, 0.75)]

    def _compute_weather_drawdown_factor(self, market_type: str) -> float:
        """Return Kelly compression factor based on consecutive losses for this market type."""
        streak = self._consecutive_losses.get(market_type, 0)
        for threshold, factor in self._DRAWDOWN_SCHEDULE:
            if streak >= threshold:
                return factor
        return 1.0

    def _record_weather_outcome(self, market_type: str, won: bool) -> None:
        """Update consecutive loss counter for a market type."""
        if won:
            prev = self._consecutive_losses.get(market_type, 0)
            if prev >= 3:
                logger.info("weatherbot_drawdown_reset", market_type=market_type, was_streak=prev)
            self._consecutive_losses[market_type] = 0
        else:
            self._consecutive_losses[market_type] = self._consecutive_losses.get(market_type, 0) + 1
            streak = self._consecutive_losses[market_type]
            if streak >= 3:
                logger.warning(
                    "weatherbot_losing_streak",
                    market_type=market_type,
                    consecutive_losses=streak,
                    kelly_factor=self._compute_weather_drawdown_factor(market_type),
                )

    # ── Per-market-type adaptive parameters (cross-bot from MirrorBot) ─────

    async def _load_category_params(self) -> None:
        """Load per-market-type parameters from bot_category_params table.

        Runs once on first scan. Falls back to global settings when no DB overrides exist.
        """
        if self._category_params_loaded:
            return
        self._category_params_loaded = True
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            from sqlalchemy import text as sa_text
            async with db.get_session() as session:
                result = await session.execute(sa_text(
                    "SELECT category, param_name, param_value "
                    "FROM bot_category_params "
                    "WHERE bot_name = 'WeatherBot'"
                ))
                for row in result.fetchall():
                    cat = str(row[0])
                    if cat not in self._category_params:
                        self._category_params[cat] = {}
                    self._category_params[cat][str(row[1])] = float(row[2])
            if self._category_params:
                logger.info("weatherbot_category_params_loaded", types=list(self._category_params.keys()))
        except Exception as exc:
            logger.debug("weatherbot_category_params_load_failed", error=str(exc))

    def _get_min_edge(
        self, market_type: str, station: Optional[WeatherStation] = None,
        model_spread: Optional[float] = None,
    ) -> float:
        """Return per-market-type min_edge, falling back to global setting.

        International cities without a local hi-res model have a data handicap
        and use a higher floor (WEATHER_INTL_MIN_EDGE, default 0.12).
        Cities WITH local_model (Paris, London, Berlin, etc.) trade at data parity.

        S114 Item 1: Spread confidence gate — scale min_edge by spread_ratio
        when model_spread is provided. Tight spread → lower edge required,
        wide spread → higher edge required. Clamped to [0.7, 1.5].
        """
        params = self._category_params.get(market_type, {})
        base = params.get("min_edge", self._min_edge)
        if station and station.temp_unit.upper() == "C" and station.local_model is None:
            base = max(base, self._intl_min_edge)

        # S132: Spread confidence gate REMOVED — double-counted spread
        # already captured in the probability distribution width.

        return base

    # ── S114: Cold-start calibration confidence ─────────────────────────

    def _calibration_confidence(self, station_id: str) -> float:
        """Bühlmann credibility weight: w = n / (n + κ), κ=30.

        Returns 0.0–1.0 scaling factor for position sizing.
        Hard floor: n < 5 → 0.0 (no trading).
        At n=15: 0.33, n=30: 0.50, n=120: 0.80.

        Returns 1.0 (full confidence) if calibration hasn't been loaded yet
        (pre-first-reload), to avoid blocking trades before we have DB state.
        """
        if not self._station_n_resolved:
            # Calibration not loaded yet — don't gate on missing data
            return 1.0
        n = self._station_n_resolved.get(station_id, 0)
        if n < 5:
            return 0.0
        return n / (n + self._buhlmann_kappa)

    async def _maybe_bootstrap_cold_station(self, station: WeatherStation) -> None:
        """S114 Item 4: Pre-compute EMOS for cold stations via historical forecast+actuals.

        Fetches 90 days of GFS deterministic forecasts and ERA5 reanalysis actuals
        from Open-Meteo, computes (forecast, actual) pairs, and inserts them into
        weather_calibration so that EMOS can activate on next calibration reload.
        Only runs once per station per session.
        """
        if station.station_id in self._bootstrapped_stations:
            return
        # Only bootstrap if station has < 5 resolved pairs (truly cold)
        n = self._station_n_resolved.get(station.station_id, 0)
        if n >= 5:
            self._bootstrapped_stations.add(station.station_id)
            return

        self._bootstrapped_stations.add(station.station_id)
        logger.info(
            "weatherbot_cold_start_bootstrap",
            station=station.station_id,
            city=station.city_name,
            n_existing=n,
        )

        try:
            pairs = await self._forecast_client.fetch_historical_bias(
                latitude=station.latitude,
                longitude=station.longitude,
                temp_unit=station.temp_unit,
                days=90,
            )
            if not pairs or len(pairs) < 10:
                logger.info("weatherbot_bootstrap_insufficient_data", station=station.station_id, pairs=len(pairs) if pairs else 0)
                return

            # Insert into weather_calibration for EMOS fitting on next reload
            db = getattr(self.base_engine, "db", None)
            if not db:
                return

            # S115: Climatology comes from weather_climatology table (backfill_climatology.py),
            # NOT from bootstrap data. Bootstrap only inserts (forecast, actual) pairs.
            inserted = 0
            async with db.get_session() as session:
                from sqlalchemy import text
                for forecast_temp, actual_temp, target_date_str, lead_hours in pairs:
                    bias = forecast_temp - actual_temp
                    try:
                        await session.execute(text("""
                            INSERT INTO weather_calibration
                                (station_id, target_date, forecast_temp, actual_temp, lead_time_hours,
                                 bias, model_name, created_at)
                            VALUES
                                (:sid, :td, :ft, :at, :lt, :bias, 'bootstrap_gfs', NOW())
                            ON CONFLICT (station_id, target_date, lead_time_hours) DO NOTHING
                        """), {
                            "sid": station.station_id,
                            "td": target_date_str,
                            "ft": forecast_temp,
                            "at": actual_temp,
                            "lt": lead_hours,
                            "bias": round(bias, 2),
                        })
                        inserted += 1
                    except Exception:
                        pass  # ON CONFLICT or other — skip row
                await session.commit()

            if inserted > 0:
                logger.info(
                    "weatherbot_bootstrap_complete",
                    station=station.station_id,
                    inserted=inserted,
                    total_pairs=len(pairs),
                )
                # Force calibration reload on next scan to pick up new data
                self._calibration_last_loaded = 0.0
        except Exception as exc:
            logger.warning("weatherbot_bootstrap_failed", station=station.station_id, error=str(exc))

    # ── Per-station reliability sizing (cross-bot from MirrorBot) ────────

    async def _get_station_reliability_factor(self, station_id: str) -> float:
        """Compute sizing factor from per-station MSE. Well-calibrated → larger, poor → smaller.

        MSE thresholds (°F²):
          < 4  (avg error < 2°F): 1.2x
          4-9  (avg error 2-3°F): 1.0x (baseline)
          9-16 (avg error 3-4°F): 0.8x
          > 16 (avg error > 4°F): 0.5x
        """
        now_mono = time.monotonic()
        cached = self._station_mse_cache.get(station_id)
        if cached and now_mono - cached[1] < 3600:
            mse = cached[0]
        else:
            db = getattr(self.base_engine, "db", None)
            if not db:
                return 1.0
            try:
                from sqlalchemy import text as sa_text
                async with db.get_session() as session:
                    result = await session.execute(sa_text(
                        "SELECT AVG(POWER(forecast_temp - actual_temp, 2)) "
                        "FROM weather_calibration "
                        "WHERE station_id = :sid AND actual_temp IS NOT NULL "
                        "AND created_at >= NOW() - INTERVAL '14 days'"
                    ), {"sid": station_id})
                    row = result.fetchone()
                    if not row or row[0] is None:
                        return 1.0
                    mse = float(row[0])
                self._station_mse_cache[station_id] = (mse, now_mono)
            except Exception:
                return 1.0

        if mse < 4.0:
            return 1.2
        elif mse < 9.0:
            return 1.0
        elif mse < 16.0:
            return 0.8
        else:
            return 0.5

    # ── EMOS drift detection (cross-bot from CalibrationTracker) ───────────

    async def _check_emos_drift(self) -> None:
        """Check per-station EMOS calibration drift using DDM/EDDM.

        Feeds recent forecast errors into per-station DriftDetectors.
        Sends advisory alert on drift — does NOT halt trading.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            from base_engine.learning.calibration_tracker import DriftDetector
            from sqlalchemy import text as sa_text
            async with db.get_session() as session:
                result = await session.execute(sa_text(
                    "SELECT station_id, ABS(forecast_temp - actual_temp) AS abs_error "
                    "FROM weather_calibration "
                    "WHERE actual_temp IS NOT NULL "
                    "AND created_at >= NOW() - INTERVAL '24 hours' "
                    "ORDER BY created_at "
                    "LIMIT 5000"
                ))
                for row in result.fetchall():
                    sid = str(row[0])
                    abs_err = float(row[1])
                    if sid not in self._drift_detectors:
                        self._drift_detectors[sid] = DriftDetector()
                    # Error threshold: > 3°F considered a miss
                    status = self._drift_detectors[sid].update(abs_err > 3.0)
                    if status.get("ddm_drift") or status.get("eddm_drift"):
                        logger.warning(
                            "weatherbot_emos_drift_detected",
                            station_id=sid,
                            ddm_drift=status.get("ddm_drift"),
                            eddm_drift=status.get("eddm_drift"),
                            error_rate=round(status.get("error_rate", 0), 3),
                            n_observations=status.get("n_observations"),
                        )
                        alerting = getattr(self.base_engine, "alerting_system", None)
                        if alerting:
                            await alerting.send_alert(
                                title=f"WeatherBot EMOS drift: {sid}",
                                message=f"DDM/EDDM detected calibration drift for station {sid}. "
                                        f"Error rate: {status.get('error_rate', 0):.1%}",
                                severity=AlertSeverity.WARNING,
                            )
                        # Reset after alerting to avoid repeated drift alerts
                        self._drift_detectors[sid].reset()
        except Exception as exc:
            logger.debug("weatherbot_emos_drift_check_failed", error=str(exc))

    # ── Zombie position cleanup ────────────────────────────────────────────

    async def _close_stale_positions(self) -> None:
        """Close WeatherBot positions that are stale or already resolved.

        Three criteria (OR):
        1. Target date has passed — parsed from market question (date-aware)
        2. Age > 20h — fallback when question parsing fails
        3. Corresponding trade_events EXIT record exists (position was exited)

        Without this, stale 'open' positions block re-entry on the same market_id
        via the position-already-open filter in _execute_weather_trade().
        Also removes them from in-memory _open_position_markets set.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            from sqlalchemy import text as sa_text

            # Step 1: Fetch open positions with their market questions
            async with db.get_session() as session:
                rows = await session.execute(sa_text(
                    "SELECT p.market_id, m.question "
                    "FROM positions p "
                    "LEFT JOIN markets m ON p.market_id = m.id "
                    "WHERE (p.bot_id = 'WeatherBot' OR p.source_bot = 'WeatherBot') "
                    "AND p.status = 'open'"
                ))
                open_positions = [(str(r[0]), r[1]) for r in rows.fetchall()]

            if not open_positions:
                return

            # Step 2: Determine which positions are stale
            today = datetime.now(timezone.utc).date()
            stale_ids: list[str] = []
            date_closed = 0

            for market_id, question in open_positions:
                if question:
                    _, target_date = WeatherMarketMapper._extract_city_and_date(question)
                    if target_date and target_date < today:
                        stale_ids.append(market_id)
                        date_closed += 1
                        continue

            # Step 3: Also close via age fallback + resolved paper_trade
            if stale_ids:
                # Close date-aware stale positions
                async with db.get_session() as session:
                    await session.execute(sa_text(
                        "UPDATE positions SET status = 'closed' "
                        "WHERE market_id = ANY(:ids) AND status = 'open'"
                    ), {"ids": stale_ids})
                    await session.commit()

            # Step 4: Age fallback (20h) + exited via trade_events for remaining
            # S105b: Changed from paper_trades (no SELL records) to trade_events EXIT.
            async with db.get_session() as session:
                result = await session.execute(sa_text(
                    "UPDATE positions SET status = 'closed' "
                    "WHERE (bot_id = 'WeatherBot' OR source_bot = 'WeatherBot') "
                    "AND status = 'open' "
                    "AND ("
                    "  opened_at < NOW() - INTERVAL '20 hours' "
                    "  OR market_id IN ("
                    "    SELECT te.market_id FROM trade_events te "
                    "    WHERE te.bot_name = 'WeatherBot' "
                    "    AND te.event_type = 'EXIT' "
                    "    AND te.event_time > NOW() - INTERVAL '24 hours'"
                    "  )"
                    ") "
                    "RETURNING market_id"
                ))
                fallback_closed = [str(row[0]) for row in result.fetchall()]
                await session.commit()

            all_closed = stale_ids + fallback_closed

            # Compute unrealized_pnl for just-closed positions where market resolved
            if all_closed:
                _fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0
                try:
                    async with db.get_session() as session:
                        await session.execute(sa_text(
                            "UPDATE positions p SET "
                            "  unrealized_pnl = CASE "
                            "    WHEN UPPER(p.side) = m.resolution "
                            "      THEN (1.0 - p.entry_price) * p.size "
                            "    ELSE (0.0 - p.entry_price) * p.size "
                            "  END - (p.entry_price * p.size * :fee_rate) "
                            "FROM markets m "
                            "WHERE p.market_id = m.id "
                            "AND p.market_id = ANY(:ids) "
                            "AND p.status = 'closed' "
                            "AND m.resolution IN ('YES', 'NO') "
                            "AND (p.unrealized_pnl IS NULL OR p.unrealized_pnl = 0.0)"
                        ), {"fee_rate": _fee_rate, "ids": all_closed})
                        await session.commit()
                except Exception as exc:
                    logger.debug("weatherbot_stale_pnl_fill_failed", error=str(exc))

            if all_closed:
                logger.info(
                    "weatherbot_stale_positions_closed",
                    count=len(all_closed),
                    date_aware=date_closed,
                    fallback=len(fallback_closed),
                )
                # Also evict from in-memory set so the filter unblocks immediately
                gw = getattr(self.base_engine, "order_gateway", None)
                if gw and hasattr(gw, "_open_position_markets"):
                    bot_set = gw._open_position_markets.get("WeatherBot", set())
                    for mid in all_closed:
                        bot_set.discard(mid)
                        self._market_group_cache.pop(mid, None)  # S104: clear stale mappings
        except Exception as exc:
            logger.debug("weatherbot_stale_position_cleanup_failed", error=str(exc))

    # ── Adaptive scan interval ─────────────────────────────────────────────

    def _effective_discovery_ttl(self) -> float:
        """S99: Time-of-day aware discovery cache TTL.
        Overnight (00-12 UTC): 900s. Daytime (12-00 UTC): 300s."""
        return 900.0 if datetime.now(timezone.utc).hour < 12 else 300.0

    def _effective_psw_discovery_ttl(self) -> float:
        """S99: Precip/snow/wind discovery cache TTL.
        Overnight: 1200s. Daytime: 600s."""
        return 1200.0 if datetime.now(timezone.utc).hour < 12 else 600.0

    def _get_scan_interval_seconds(self) -> float:
        """Override base: scan aggressively during NWP model update windows.

        During model windows, also invalidates the forecast cache so the next
        fetch picks up fresh model data instead of serving stale cached results.
        """
        now_utc = datetime.now(timezone.utc)
        h, m = now_utc.hour, now_utc.minute

        # ECMWF ENS model windows: scan every 60s + invalidate cache
        ecmwf_windows = [(7, 0, 8, 0), (18, 0, 19, 0)]
        for wh, wm, eh, em in ecmwf_windows:
            if (h, m) >= (wh, wm) and (h, m) < (eh, em):
                self._forecast_client.invalidate_forecast_cache()
                return 60.0

        # GFS model windows: scan every 90s + invalidate cache
        gfs_windows = [(5, 15, 6, 0), (17, 15, 18, 0)]
        for wh, wm, eh, em in gfs_windows:
            if (h, m) >= (wh, wm) and (h, m) < (eh, em):
                self._forecast_client.invalidate_forecast_cache()
                return 90.0

        # HRRR window (~:40-:59 each hour): scan every 120s
        if m >= 40:
            return 120.0

        # Default: use configured SCAN_INTERVAL_WEATHER (normally 300s)
        # S99: Adaptive backoff — extend interval after consecutive zero-edge scans
        base = super()._get_scan_interval_seconds()
        if self._consecutive_no_edge >= self._backoff_threshold * 2:
            return min(base * 2.0, self._max_scan_interval)
        elif self._consecutive_no_edge >= self._backoff_threshold:
            return min(base * 1.5, self._max_scan_interval)
        return base

    # ── Main scan loop ────────────────────────────────────────────────────

    async def scan_and_trade(self) -> None:
        self._scan_start_mono = time.monotonic()  # S100: for alpha decay latency
        self._scan_count += 1

        # P1+P2: handle day boundary (must run first — resets exposure on new day)
        await self._handle_daily_boundary()

        # Calibration + category params are independent — run in parallel
        # S120: _restore_daily_pnl_from_db removed — already called inside
        # _handle_daily_boundary() at L784 which runs first every scan.
        await asyncio.gather(
            self._maybe_reload_calibration(),
            self._load_category_params(),
        )

        # W4: Monitoring thresholds — check Brier/drawdown and halt if needed
        await self._check_monitoring_thresholds()
        if self._monitoring_halt:
            logger.warning("weatherbot_monitoring_halt_active")
            return

        # Detect PM exits: markets open last scan but not now → add to cooldown
        og = getattr(self.base_engine, "order_gateway", None)
        if og:
            current_open = og._open_position_markets.get("WeatherBot", set())
            exited_by_pm = self._known_open_markets - current_open
            for mid in exited_by_pm:
                self._recently_exited[mid] = time.monotonic()
                await self._save_exit_to_redis(mid)
                # S104: Decrement group/city exposure on exit
                cached = self._market_group_cache.pop(mid, None)
                if cached:
                    group_key, city, exit_cost = cached
                    async with self._exposure_lock:
                        self._group_exposure[group_key] = max(0.0, self._group_exposure.get(group_key, 0.0) - exit_cost)
                        self._city_exposure[city] = max(0.0, self._city_exposure.get(city, 0.0) - exit_cost)
                    db = getattr(self.base_engine, "db", None)
                    if db is not None:
                        try:
                            await _inc_daily(db, "WeatherBot", f"group_{group_key}", -exit_cost)
                            await _inc_daily(db, "WeatherBot", f"city_{city}", -exit_cost)
                        except Exception as exc:
                            logger.warning("weatherbot_exposure_db_write_failed", market_id=mid, group_key=group_key, city=city, exc=str(exc))
                    logger.info("weatherbot_exposure_decremented", market_id=mid,
                                group_key=group_key, city=city, cost_usd=round(exit_cost, 2))
                else:
                    # S111: Fallback — reconstruct group/city/cost from DB so
                    # exposure still decrements even after a restart clears cache.
                    _fb_ok = False
                    db = getattr(self.base_engine, "db", None)
                    if db is not None:
                        try:
                            async with db.get_session() as _fb_sess:
                                from sqlalchemy import text as _fb_text
                                _fb_row = (await _fb_sess.execute(_fb_text("""
                                    SELECT m.question, p.size, p.entry_price
                                    FROM positions p
                                    JOIN markets m ON (p.market_id = CAST(m.id AS TEXT) OR p.market_id = m.condition_id)
                                    WHERE p.market_id = :mid
                                      AND (p.source_bot = 'WeatherBot' OR p.bot_id = 'WeatherBot')
                                    LIMIT 1
                                """), {"mid": mid})).first()
                            if _fb_row and _fb_row[0]:
                                _fb_city, _fb_date = self._market_mapper._extract_city_and_date(_fb_row[0])
                                if _fb_city and _fb_date:
                                    _fb_gk = f"{_fb_city}:{_fb_date.isoformat()}"
                                    _fb_cost = float(_fb_row[1] or 0) * float(_fb_row[2] or 0)
                                    async with self._exposure_lock:
                                        self._group_exposure[_fb_gk] = max(0.0, self._group_exposure.get(_fb_gk, 0.0) - _fb_cost)
                                        self._city_exposure[_fb_city] = max(0.0, self._city_exposure.get(_fb_city, 0.0) - _fb_cost)
                                    logger.info("weatherbot_exposure_decremented_fallback", market_id=mid,
                                                group_key=_fb_gk, city=_fb_city, cost_usd=round(_fb_cost, 2))
                                    _fb_ok = True
                        except Exception as exc:
                            logger.warning("weatherbot_exposure_fallback_failed", market_id=mid, exc=str(exc))
                    if not _fb_ok:
                        logger.warning("weatherbot_pm_exit_no_cache", market_id=mid)
            self._known_open_markets = set(current_open)

        # Reset per-scan climate normal computation limiter (T3B)
        self._forecast_client.reset_climate_cycle()

        # One-time startup: restore 429 cooldowns from Redis + warm forecast cache from DB
        if not self._cache_warmed:
            await self._forecast_client.restore_state()
            db = getattr(self.base_engine, "db", None)
            await self._forecast_client.warm_cache_from_db(db)
            await self._restore_exits_from_redis()
            await self._restore_backoff_from_redis()
            await self._metar_monitor.restore_from_redis()
            await self._restore_exposure_from_db()
            await self._rebuild_market_group_cache()
            await self._close_stale_positions()
            self._cache_warmed = True

        # S97: Start background monitors on first scan
        if not self._monitors_started:
            self._model_run_monitor.start()
            self._metar_monitor.start()
            self._monitors_started = True

        # One-time startup observability check (logs DB state + Gamma API probe)
        if not self._startup_check_done:
            await self._check_weather_market_availability()

        # S97: Reset per-scan API call counter
        self._forecast_client.api_calls_this_scan = 0

        # Phase timing — track where scan time is spent
        _t0 = time.monotonic()

        # S97: Check priority queue for jump/METAR events — evaluate immediately
        _priority_items: List[Dict] = []
        while not self._priority_queue.empty():
            try:
                _priority_items.append(self._priority_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if _priority_items:
            logger.info("weatherbot_priority_events", count=len(_priority_items))
            self._consecutive_no_edge = 0  # S99: Priority event resets backoff

        # S97: Discovery cache — reuse markets+groups for 5 min to skip Gamma API call
        _now_mono = time.monotonic()
        if (self._discovery_cache is not None
                and (_now_mono - self._discovery_cache[0]) < self._effective_discovery_ttl()):
            weather_markets, groups = copy.deepcopy(self._discovery_cache[1]), copy.deepcopy(self._discovery_cache[2])
        else:
            # 1. Fetch weather markets via Gamma API tag_slug=temperature (PRIMARY).
            #    The standard ingestion pipeline misses weather events — they have
            #    event IDs > 249000, far beyond the ingestion's pagination reach.
            #    tag_slug=temperature returns all live temperature events with prices
            #    pre-populated from outcomePrices (no CLOB enrichment needed).
            weather_markets = await self._fetch_weather_events_by_tag()

            if not weather_markets:
                # Fallback: DB-based discovery (for markets already ingested)
                weather_markets = await self.base_engine.get_all_tradeable_markets(
                    min_liquidity=0, categories=["weather"]
                )
                if weather_markets:
                    # DB markets lack prices — enrich via CLOB midpoint
                    weather_markets = await self._enrich_with_live_prices(weather_markets)

            if not weather_markets:
                # Last resort: direct Gamma API probe (rate-limited)
                if _now_mono - self._last_direct_probe >= self._direct_probe_interval:
                    self._last_direct_probe = _now_mono
                    weather_markets = await self._fetch_weather_markets_direct()
                if not weather_markets:
                    logger.info("weatherbot_no_weather_markets")
                    return

            scan_limit = getattr(settings, "SCAN_MARKET_LIMIT", 800)
            weather_markets = weather_markets[:scan_limit]

            # 2. Group by (city, date)
            groups = self._market_mapper.group_markets(weather_markets)
            if not groups:
                logger.info("weatherbot_no_groups_parsed", weather_markets=len(weather_markets))
                return

            # Cache discovery result
            self._discovery_cache = (time.monotonic(), weather_markets, groups)

            # S101b: City universe log + unmatched city alert + daily digest
            _active_cities = sorted(set(g.city for g in groups))
            logger.info("weatherbot_city_universe", cities=_active_cities, n=len(_active_cities))

            # Alert on new unmatched cities (deduped per session)
            _unmatched = self._market_mapper._last_unmatched_cities
            _new_unmatched = _unmatched - self._alerted_unmatched_cities
            if _new_unmatched:
                logger.warning(
                    "weatherbot_unmatched_cities",
                    cities=sorted(_new_unmatched),
                    n=len(_new_unmatched),
                )
                _alerting = getattr(self.base_engine, "alerting_system", None)
                if _alerting:
                    try:
                        from base_engine.monitoring.alerting import AlertSeverity
                        await _alerting.send_alert(
                            title="WeatherBot: New Unmatched Cities",
                            message=f"Polymarket has weather markets for cities not in station registry: {sorted(_new_unmatched)}. Add to station_registry.py to trade them.",
                            severity=AlertSeverity.WARNING,
                            source="WeatherBot",
                            metadata={"cities": sorted(_new_unmatched)},
                        )
                    except Exception:
                        pass  # Alert failure is non-fatal
                self._alerted_unmatched_cities.update(_new_unmatched)

            # Daily digest — once per UTC day
            _today_str = date.today().isoformat()
            if self._last_city_digest_date != _today_str:
                self._last_city_digest_date = _today_str
                logger.info(
                    "weatherbot_daily_city_digest",
                    active_cities=_active_cities,
                    active_count=len(_active_cities),
                    unmatched_cities=sorted(_unmatched) if _unmatched else [],
                    unmatched_count=len(_unmatched),
                    registry_size=len(STATION_REGISTRY),
                    total_markets=len(weather_markets),
                )

        _t_discovery = time.monotonic()

        # Pre-fetch NWS severe weather alerts for all US stations in one pass
        await self._prefetch_severe_weather_alerts(groups)

        _t_alerts = time.monotonic()

        # Phase 1: Analyze all groups (fetch forecasts, compute edges)
        # Parallel with bounded concurrency — configurable concurrent Open-Meteo/NWS requests.
        _group_sem = asyncio.Semaphore(int(getattr(settings, "WEATHER_GROUP_CONCURRENCY", 12)))

        async def _analyze_bounded(g: WeatherMarketGroup):
            async with _group_sem:
                return await self._analyze_group(g)

        _results = await asyncio.gather(
            *[_analyze_bounded(g) for g in groups],
            return_exceptions=True,
        )
        analyzed: List[Tuple[List[Dict], WeatherMarketGroup, Dict[str, float]]] = []
        for group, result in zip(groups, _results):
            if isinstance(result, Exception):
                logger.warning(
                    "weatherbot_group_error",
                    city=group.city,
                    date=group.target_date.isoformat(),
                    error=str(result),
                    error_type=type(result).__name__,
                )
            else:
                opps, model_probs = result
                analyzed.append((opps, group, model_probs))

        _t_analysis = time.monotonic()

        # Phase 2: Cross-city regime detection → regime_boost factor
        regime_boost = self._compute_regime_boost(analyzed)
        if regime_boost > 1.0:
            logger.info("weatherbot_regime_boost", boost=regime_boost)

        # Phase 3: Execute trades — S97 parallel with semaphore + exposure lock.
        # Groups with >=2 buckets showing edge use S-T multi-bucket allocation.
        # Single-bucket groups fall through to independent Kelly sizing.
        _traded = 0
        _groups_with_edge = 0
        _best_edge = 0.0

        # Pre-compute read-only aggregation before parallel dispatch
        for opps, group, _probs in analyzed:
            if opps:
                _groups_with_edge += 1
            for opp in opps:
                if abs(opp["edge"]) > abs(_best_edge):
                    _best_edge = opp["edge"]

        # S97: Parallel trade execution with bounded concurrency
        _trade_sem = asyncio.Semaphore(int(getattr(settings, "WEATHER_TRADE_CONCURRENCY", 8)))

        async def _exec_group(opps, group):
            async with _trade_sem:
                if len(opps) >= 2:
                    return await self._execute_group_trades(opps, group, regime_boost)
                else:
                    _count = 0
                    for opp in opps:
                        opp["regime_boost"] = regime_boost
                        if await self._execute_weather_trade(opp, group):
                            _count += 1
                    return _count

        _trade_tasks = [_exec_group(opps, group) for opps, group, _probs in analyzed if opps]
        if _trade_tasks:
            _trade_results = await asyncio.gather(*_trade_tasks, return_exceptions=True)
            for r in _trade_results:
                if isinstance(r, int):
                    _traded += r
                elif isinstance(r, Exception):
                    logger.warning("weatherbot_parallel_trade_error", error=str(r))

        # Phase 4: Re-evaluate open positions with fresh model probabilities
        # Feeds position_manager's model-reversal exit logic with current forecasts.
        await self._reevaluate_open_positions(analyzed)

        # Phase 4b: Outcome backfill + drift detection + cleanup — every 10 scans
        # All three are independent (different tables, no shared mutable state) —
        # run in parallel to cut periodic scan from sum(16-80s) to max(10-50s).
        if self._scan_count % 10 == 0:
            await asyncio.gather(
                self._backfill_weather_outcomes(),
                self._check_emos_drift(),
                self._close_stale_positions(),
                return_exceptions=True,
            )

        _t_trades = time.monotonic()

        # Phases 5-7: Precip/Snow/Wind — independent market types, run in parallel
        # S99: Run every Nth scan to reduce overhead (these markets move slower)
        if self._psw_scan_divisor <= 1 or self._scan_count % self._psw_scan_divisor == 0:
            _precip_traded, _snow_traded, _wind_traded = await asyncio.gather(
                self._scan_precipitation_markets(),
                self._scan_snowfall_markets(),
                self._scan_wind_markets(),
            )
        else:
            _precip_traded = _snow_traded = _wind_traded = 0

        # Wire Session 51 heartbeat counters so watchdog can detect silent WeatherBot
        self._last_scan_markets = len(weather_markets)
        self._last_scan_opportunities = _groups_with_edge
        self._last_scan_trades = _traded + _precip_traded + _snow_traded + _wind_traded

        _t_end = time.monotonic()

        logger.info(
            "weatherbot_scan_done",
            weather_markets=len(weather_markets),
            groups=len(groups),
            active_cities=len(set(g.city for g in groups)),
            groups_with_edge=_groups_with_edge,
            trades=_traded,
            precip_trades=_precip_traded,
            snow_trades=_snow_traded,
            wind_trades=_wind_traded,
            best_edge=round(_best_edge, 4),
            regime_boost=regime_boost,
            ms_discovery=round((_t_discovery - _t0) * 1000),
            ms_alerts=round((_t_alerts - _t_discovery) * 1000),
            ms_analysis=round((_t_analysis - _t_alerts) * 1000),
            ms_trades=round((_t_trades - _t_analysis) * 1000),
            ms_precip_snow_wind=round((_t_end - _t_trades) * 1000),
            api_calls=self._forecast_client.api_calls_this_scan,
            priority_events=len(_priority_items),
        )

        # S99: Track consecutive zero-edge scans for adaptive backoff
        _total_activity = _groups_with_edge + _precip_traded + _snow_traded + _wind_traded
        if _total_activity == 0:
            self._consecutive_no_edge += 1
        else:
            self._consecutive_no_edge = 0
        await self._save_backoff_to_redis()

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot. Analyzes a single market in isolation.

        For weather markets, group-level analysis (in scan_and_trade) is
        preferred because bucket probabilities must sum to 1.0 across the group.
        This method provides a basic single-market analysis as fallback.
        """
        if not self._market_mapper.is_weather_market(market_data):
            return None

        bucket = self._market_mapper.parse_market(market_data)
        if not bucket:
            return None

        q = market_data.get("question") or market_data.get("title") or ""
        city_text, target_date = self._market_mapper._extract_city_and_date(q)
        if not city_text or not target_date:
            return None

        from base_engine.weather.station_registry import lookup_station
        station = lookup_station(city_text)
        if not station:
            return None

        forecast = await self._forecast_client.get_combined_forecast(station, target_date)
        if not forecast:
            return None

        loc, scale, shape = self._prob_engine.fit_distribution(
            forecast.ensemble_members, forecast.lead_time_hours, station.station_id,
        )
        model_probs = self._prob_engine.bucket_probabilities(loc, scale, shape, [bucket])
        model_prob = model_probs.get(bucket.market_id, 0.0)
        edge = model_prob - bucket.yes_price

        if abs(edge) < self._get_min_edge("temperature", station, forecast.model_spread):
            return None

        side = "YES" if edge > 0 else "NO"
        token_id = bucket.token_id if side == "YES" else bucket.no_token_id
        price = bucket.yes_price if side == "YES" else (1.0 - bucket.yes_price)

        return {
            "market_id": bucket.market_id,
            "token_id": token_id,
            "side": side,
            "price": price,
            "confidence": min(0.95, model_prob) if side == "YES" else min(0.95, 1.0 - model_prob),
            "model_prob": model_prob,
            "edge": edge,
            "city": station.city_name,
        }

    # ── Shared PSW (Precip/Snow/Wind) scanning ──────────────────────────

    async def _scan_psw_markets(
        self,
        market_type: str,
        tag_slug: str,
        grouper_func,
        analyzer_func,
    ) -> int:
        """S115 DRY: Shared scan template for precipitation/snowfall/wind markets.

        1. Fetch events from Gamma API (cached per tag_slug)
        2. Extract markets from JSON events
        3. Group with the provided grouper
        4. Analyze each group and execute trades

        Returns number of trades executed.
        """
        import httpx

        # S99/S115: Unified discovery cache (keyed by tag_slug)
        _now_mono = time.monotonic()
        _ttl = self._effective_psw_discovery_ttl()
        _cached = self._psw_discovery_cache.get(tag_slug)
        if _cached is not None and (_now_mono - _cached[0]) < _ttl:
            events = _cached[1]
        else:
            try:
                url = "https://gamma-api.polymarket.com/events"
                params = {
                    "active": "true",
                    "closed": "false",
                    "tag_slug": tag_slug,
                    "limit": "100",
                }
                async with httpx.AsyncClient(timeout=15.0) as http:
                    resp = await http.get(url, params=params)
                    if resp.status_code != 200:
                        return 0
                    events = resp.json()
            except Exception as exc:
                logger.debug(f"weatherbot_{market_type}_tag_fetch_error", error=str(exc))
                return 0

            if not isinstance(events, list):
                return 0
            self._psw_discovery_cache[tag_slug] = (time.monotonic(), events)

        # Extract markets from events
        markets: List[Dict] = []
        for evt in events:
            if not isinstance(evt, dict):
                continue
            for mkt in evt.get("markets", []):
                if not isinstance(mkt, dict):
                    continue
                q = mkt.get("question", "")
                if not q:
                    continue
                # Extract yes_price from outcomePrices
                prices = mkt.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        price_list = json.loads(prices)
                        if isinstance(price_list, list) and len(price_list) >= 1:
                            mkt["yes_price"] = float(price_list[0])
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif isinstance(prices, list) and len(prices) >= 1:
                    try:
                        mkt["yes_price"] = float(prices[0])
                    except (ValueError, TypeError):
                        pass
                # Extract token IDs
                clobTokenIds = mkt.get("clobTokenIds")
                if isinstance(clobTokenIds, str):
                    try:
                        token_list = json.loads(clobTokenIds)
                        if isinstance(token_list, list) and len(token_list) >= 2:
                            mkt["yes_token_id"] = token_list[0]
                            mkt["no_token_id"] = token_list[1]
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif isinstance(clobTokenIds, list) and len(clobTokenIds) >= 2:
                    mkt["yes_token_id"] = clobTokenIds[0]
                    mkt["no_token_id"] = clobTokenIds[1]
                mkt["id"] = mkt.get("id") or mkt.get("conditionId", "")
                markets.append(mkt)

        if not markets:
            return 0

        groups = grouper_func(markets)
        if not groups:
            logger.debug(f"weatherbot_{market_type}_no_groups", markets=len(markets))
            return 0

        traded = 0
        for group in groups:
            try:
                opps = await analyzer_func(group)
                for opp in opps:
                    if await self._execute_weather_trade(opp, self._precip_to_temp_group(group)):
                        traded += 1
            except Exception as exc:
                logger.debug(
                    f"weatherbot_{market_type}_group_error",
                    city=group.city, error=str(exc),
                )

        if traded > 0 or groups:
            logger.info(
                f"weatherbot_{market_type}_scan_done",
                markets=len(markets),
                groups=len(groups),
                trades=traded,
            )
        return traded

    # ── Precipitation scanning ───────────────────────────────────────────

    async def _scan_precipitation_markets(self) -> int:
        """M1: Scan and trade precipitation markets."""
        return await self._scan_psw_markets(
            market_type="precip",
            tag_slug="precipitation",
            grouper_func=self._market_mapper.group_precipitation_markets,
            analyzer_func=self._analyze_precipitation_group,
        )

    async def _analyze_precipitation_group(
        self,
        group: PrecipitationMarketGroup,
    ) -> List[Dict]:
        """Analyze a precipitation market group: fetch ensemble, compute edges."""
        # Fetch precipitation ensemble — monthly or daily
        if getattr(group, "period", "daily") == "monthly":
            ensemble = await self._forecast_client.get_monthly_precipitation_ensemble(
                group.station,
                month=group.target_date.month,
                year=group.target_date.year,
            )
        else:
            ensemble = await self._forecast_client.get_precipitation_ensemble(
                group.station, group.target_date,
            )
        if not ensemble or len(ensemble) < 10:
            return []

        # Fetch NDFD PoP for US stations (blends into rain probability)
        # Only for daily markets — monthly uses pure ensemble CDF.
        ndfd_pop = None
        if getattr(group, "period", "daily") == "daily" and group.station.temp_unit.upper() == "F":
            pop_data = await self._forecast_client.get_ndfd_pop(group.station)
            if pop_data:
                target_iso = group.target_date.isoformat()
                day_pops = [p for _name, p, dt in pop_data if dt == target_iso]
                if day_pops:
                    ndfd_pop = sum(day_pops) / len(day_pops)
                else:
                    # Fallback: use first 2 periods (today day + night)
                    ndfd_pop = sum(p for _, p, _ in pop_data[:2]) / max(len(pop_data[:2]), 1)

        # Convert PrecipitationBucket → PrecipBucket for the engine
        from base_engine.weather.precipitation_engine import PrecipBucket
        engine_buckets = [
            PrecipBucket(
                market_id=b.market_id,
                token_id=b.token_id,
                no_token_id=b.no_token_id,
                yes_price=b.yes_price,
                bucket_type=b.bucket_type,
                low_bound=b.low_bound,
                high_bound=b.high_bound,
                precip_unit=b.precip_unit,
            )
            for b in group.buckets
        ]

        # Compute probabilities
        model_probs = self._precip_engine.compute_bucket_probabilities(
            ensemble, engine_buckets, ndfd_pop=ndfd_pop,
        )
        if not model_probs:
            return []

        # Find edges
        opps = self._precip_engine.compute_edges(
            model_probs, engine_buckets, min_edge=self._get_min_edge("precipitation", group.station),
        )

        # Compute actual lead time
        now_utc = datetime.now(timezone.utc)
        target_noon = datetime.combine(
            group.target_date, datetime.min.time(),
        ).replace(hour=18, tzinfo=timezone.utc)
        _lead_h = max(0.0, (target_noon - now_utc).total_seconds() / 3600.0)

        # Add metadata for trade execution
        for opp in opps:
            opp["city"] = group.city
            opp["target_date"] = group.target_date.isoformat()
            opp["lead_time_hours"] = _lead_h
            opp["model_spread"] = 3.0  # Default
            opp["ensemble_count"] = len(ensemble)
            opp["market_type"] = "precipitation"
            await self._log_weather_prediction(
                opp["market_id"], opp["model_prob"], opp["price"],
                opp.get("confidence", opp["model_prob"]), "precipitation",
            )

        if opps:
            logger.info(
                "weatherbot_precip_edges",
                city=group.city,
                date=group.target_date.isoformat(),
                n_buckets=len(group.buckets),
                n_opps=len(opps),
                best_edge=round(max(o["abs_edge"] for o in opps), 4),
            )

        return opps

    @staticmethod
    def _precip_to_temp_group(group) -> WeatherMarketGroup:
        """Convert Precipitation/SnowfallMarketGroup to WeatherMarketGroup for trade execution.

        _execute_weather_trade expects a WeatherMarketGroup for exposure tracking.
        """
        return WeatherMarketGroup(
            city=group.city,
            target_date=group.target_date,
            station=group.station,
            buckets=[],
            slug_prefix=group.slug_prefix,
            temp_unit=group.station.temp_unit,
        )

    # ── Snowfall scanning ─────────────────────────────────────────────────

    async def _scan_snowfall_markets(self) -> int:
        """M2: Scan and trade snowfall markets."""
        return await self._scan_psw_markets(
            market_type="snow",
            tag_slug="snowfall",
            grouper_func=self._market_mapper.group_snowfall_markets,
            analyzer_func=self._analyze_snowfall_group,
        )

    async def _analyze_snowfall_group(self, group) -> List[Dict]:
        """Analyze a snowfall market group: fetch ensemble, compute edges.

        Reuses PrecipitationProbabilityEngine — Gamma distribution is appropriate
        for snowfall amounts (same zero-inflated positive continuous structure).
        """
        ensemble = await self._forecast_client.get_snowfall_ensemble(
            group.station, group.target_date,
        )
        if not ensemble or len(ensemble) < 10:
            return []

        # Convert SnowfallBucket → PrecipBucket for the engine
        from base_engine.weather.precipitation_engine import PrecipBucket
        engine_buckets = [
            PrecipBucket(
                market_id=b.market_id,
                token_id=b.token_id,
                no_token_id=b.no_token_id,
                yes_price=b.yes_price,
                bucket_type=b.bucket_type,
                low_bound=b.low_bound,
                high_bound=b.high_bound,
                precip_unit=b.snow_unit,
            )
            for b in group.buckets
        ]

        # No NDFD PoP for snowfall — use ensemble-only rain probability
        model_probs = self._precip_engine.compute_bucket_probabilities(
            ensemble, engine_buckets, ndfd_pop=None,
        )
        if not model_probs:
            return []

        opps = self._precip_engine.compute_edges(
            model_probs, engine_buckets, min_edge=self._get_min_edge("snowfall", group.station),
        )

        # Compute lead time
        now_utc = datetime.now(timezone.utc)
        target_noon = datetime.combine(
            group.target_date, datetime.min.time(),
        ).replace(hour=18, tzinfo=timezone.utc)
        _lead_h = max(0.0, (target_noon - now_utc).total_seconds() / 3600.0)

        for opp in opps:
            opp["city"] = group.city
            opp["target_date"] = group.target_date.isoformat()
            opp["lead_time_hours"] = _lead_h
            opp["model_spread"] = 3.0
            opp["ensemble_count"] = len(ensemble)
            opp["market_type"] = "snowfall"
            await self._log_weather_prediction(
                opp["market_id"], opp["model_prob"], opp["price"],
                opp.get("confidence", opp["model_prob"]), "snowfall",
            )

        if opps:
            logger.info(
                "weatherbot_snow_edges",
                city=group.city,
                date=group.target_date.isoformat(),
                n_buckets=len(group.buckets),
                n_opps=len(opps),
                best_edge=round(max(o["abs_edge"] for o in opps), 4),
            )

        return opps

    # ── Wind gust scanning ────────────────────────────────────────────────

    async def _scan_wind_markets(self) -> int:
        """M3: Scan and trade wind gust markets."""
        return await self._scan_psw_markets(
            market_type="wind",
            tag_slug="wind",
            grouper_func=self._market_mapper.group_wind_markets,
            analyzer_func=self._analyze_wind_group,
        )

    async def _analyze_wind_group(self, group) -> List[Dict]:
        """Analyze a wind gust market group: fetch ensemble, compute edges.

        Uses normal distribution (wind gusts are ~normally distributed at daily max).
        Computes CDF across bucket bounds, then finds mispriced buckets.
        """
        import math

        ensemble = await self._forecast_client.get_wind_ensemble(
            group.station, group.target_date,
        )
        if not ensemble or len(ensemble) < 10:
            return []

        # Compute mean and std of ensemble
        mean_wind = sum(ensemble) / len(ensemble)
        if len(ensemble) > 1:
            variance = sum((x - mean_wind) ** 2 for x in ensemble) / (len(ensemble) - 1)
            std_wind = max(variance ** 0.5, 0.5)  # Floor at 0.5 to avoid division by zero
        else:
            std_wind = 5.0  # Conservative default

        # Use normal CDF for bucket probabilities
        # math.erf is available without scipy
        def _norm_cdf(x: float) -> float:
            """Standard normal CDF using erf."""
            return 0.5 * (1.0 + math.erf((x - mean_wind) / (std_wind * math.sqrt(2.0))))

        # Compute model probability for each bucket
        opps: List[Dict] = []
        for b in group.buckets:
            if b.bucket_type == "range" and b.low_bound is not None and b.high_bound is not None:
                model_prob = _norm_cdf(b.high_bound + 0.5) - _norm_cdf(b.low_bound - 0.5)
            elif b.bucket_type == "at_or_below" and b.high_bound is not None:
                model_prob = _norm_cdf(b.high_bound + 0.5)
            elif b.bucket_type == "at_or_higher" and b.low_bound is not None:
                model_prob = 1.0 - _norm_cdf(b.low_bound - 0.5)
            else:
                continue

            model_prob = max(0.001, min(0.999, model_prob))
            market_prob = max(0.001, min(0.999, b.yes_price))

            # Check YES side edge
            yes_edge = model_prob - market_prob
            # Check NO side edge
            no_edge = (1.0 - model_prob) - (1.0 - market_prob)

            if abs(yes_edge) >= self._get_min_edge("wind", group.station):
                if yes_edge > 0:
                    opps.append({
                        "market_id": b.market_id,
                        "token_id": b.token_id,
                        "side": "YES",
                        "edge": round(yes_edge, 4),
                        "price": round(market_prob, 4),
                        "model_prob": round(model_prob, 4),
                        "market_prob": round(market_prob, 4),
                        "abs_edge": round(abs(yes_edge), 4),
                        "confidence": round(model_prob, 4),
                    })
                else:
                    opps.append({
                        "market_id": b.market_id,
                        "token_id": b.no_token_id,
                        "side": "NO",
                        "edge": round(no_edge, 4),
                        "price": round(1.0 - market_prob, 4),
                        "model_prob": round(1.0 - model_prob, 4),
                        "market_prob": round(1.0 - market_prob, 4),
                        "abs_edge": round(abs(no_edge), 4),
                        "confidence": round(1.0 - model_prob, 4),
                    })

        # Compute lead time
        now_utc = datetime.now(timezone.utc)
        target_noon = datetime.combine(
            group.target_date, datetime.min.time(),
        ).replace(hour=18, tzinfo=timezone.utc)
        _lead_h = max(0.0, (target_noon - now_utc).total_seconds() / 3600.0)

        for opp in opps:
            opp["city"] = group.city
            opp["target_date"] = group.target_date.isoformat()
            opp["lead_time_hours"] = _lead_h
            opp["model_spread"] = round(std_wind, 1)
            opp["ensemble_count"] = len(ensemble)
            opp["market_type"] = "wind"
            await self._log_weather_prediction(
                opp["market_id"], opp["model_prob"], opp["price"],
                opp.get("confidence", opp["model_prob"]), "wind",
            )

        if opps:
            logger.info(
                "weatherbot_wind_edges",
                city=group.city,
                date=group.target_date.isoformat(),
                n_buckets=len(group.buckets),
                n_opps=len(opps),
                mean_wind=round(mean_wind, 1),
                std_wind=round(std_wind, 1),
                best_edge=round(max(o["abs_edge"] for o in opps), 4),
            )

        return opps

    # ── Group analysis ────────────────────────────────────────────────────

    async def _analyze_group(
        self, group: WeatherMarketGroup,
    ) -> Tuple[List[Dict], Dict[str, float]]:
        """Analyze all buckets in a city+date group.

        Returns (tradeable_opportunities, model_probs) where model_probs maps
        market_id → model probability for all buckets (used by L4 re-evaluation).
        """
        # Skip if target date is in the past
        today = date.today()
        if group.target_date < today:
            return [], {}

        # Skip if lead time exceeds max
        now_utc = datetime.now(timezone.utc)
        target_noon = datetime(
            group.target_date.year, group.target_date.month, group.target_date.day,
            18, 0, tzinfo=timezone.utc,
        )
        lead_time = max(0.0, (target_noon - now_utc).total_seconds() / 3600.0)
        if lead_time > self._max_lead_time:
            return [], {}

        # Station health check
        if not await self._station_health.is_healthy(group.station):
            logger.warning("weatherbot_station_unhealthy", station=group.station.station_id)
            # B4: Alert on station offline
            _alerting = getattr(self.base_engine, "alerting_system", None)
            if _alerting:
                await _alerting.send_alert(
                    title="WeatherBot Station Unhealthy",
                    message=f"Station {group.station.station_id} ({group.station.city_name}) failed health check.",
                    severity=AlertSeverity.WARNING,
                    source="WeatherBot",
                    metadata={"station": group.station.station_id},
                )
            return [], {}

        # Fetch ensemble forecast
        forecast = await self._forecast_client.get_combined_forecast(
            group.station, group.target_date,
        )
        if not forecast:
            logger.debug(
                "weatherbot_no_forecast",
                station=group.station.station_id,
                date=group.target_date.isoformat(),
            )
            return [], {}

        # P3: Persist forecast to DB (async, non-blocking on failure)
        await self._save_forecast_to_db(group.station, group.target_date, forecast)

        # S114 Item 1: Track model_spread for spread confidence gate
        sid = group.station.station_id
        if forecast.model_spread > 0:
            from collections import deque
            if sid not in self._spread_history:
                self._spread_history[sid] = deque(maxlen=14)
            self._spread_history[sid].append(forecast.model_spread)

        # S114 Item 4: Bootstrap historical bias for cold stations
        await self._maybe_bootstrap_cold_station(group.station)

        # S97: Pre-screen — skip EMOS if raw ensemble mean is too close to market price
        # (no chance of clearing min_edge after calibration) + check exposure caps early
        if forecast.ensemble_members:
            _ens_mean = sum(forecast.ensemble_members) / len(forecast.ensemble_members)
            # Find the bucket closest to ensemble mean and compare to market price
            _best_bucket = None
            _best_dist = float("inf")
            for b in group.buckets:
                _mid = (b.low_bound + b.high_bound) / 2.0 if b.low_bound is not None and b.high_bound is not None and b.high_bound < 999 else (b.high_bound - 5.0 if b.high_bound is not None and b.low_bound is None else (b.low_bound + 5.0 if b.low_bound is not None else 50.0))
                if abs(_ens_mean - _mid) < _best_dist:
                    _best_dist = abs(_ens_mean - _mid)
                    _best_bucket = b
            if _best_bucket:
                # Rough estimate: modal bucket gets ~40-60% probability mass
                # If |0.50 - market_price| < 0.04, no bucket can clear 0.08 min_edge
                _min_edge = float(getattr(settings, "WEATHER_MIN_EDGE", 0.08))
                _rough_delta = abs(0.50 - _best_bucket.yes_price)
                if _rough_delta < _min_edge * 0.5:
                    # Check all buckets — if ALL are within the dead zone, skip
                    _any_edge = False
                    for b in group.buckets:
                        if abs(0.50 - b.yes_price) >= _min_edge * 0.5:
                            _any_edge = True
                            break
                    if not _any_edge:
                        return [], {}

        # S97: Early exposure cap check — skip EMOS if group/city at cap
        _group_key = f"{group.city}:{group.target_date.isoformat()}"
        if self._group_exposure.get(_group_key, 0.0) >= self._max_per_group:
            return [], {}
        if self._city_exposure.get(group.city, 0.0) >= self._max_correlated:
            return [], {}

        # Fit distribution
        try:
            loc, scale, shape = self._prob_engine.fit_distribution(
                forecast.ensemble_members,
                forecast.lead_time_hours,
                group.station.station_id,
            )
        except ValueError as exc:
            logger.debug("weatherbot_fit_failed", station=group.station.station_id, city=group.city, error=str(exc))
            return [], {}

        # T3B: Climate normal Bayesian prior — blend toward climatology at long lead times.
        # At ≤72h the ensemble is skilled; beyond 72h, blend 0-40% toward 10-year climate mean.
        if lead_time > 72.0:
            climate = await self._forecast_client.get_climate_normal(
                group.station.latitude, group.station.longitude,
                group.target_date, group.station.temp_unit,
            )
            if climate:
                clim_mean, clim_std = climate
                loc, scale = WeatherProbabilityEngine.apply_climate_prior(
                    loc, scale, clim_mean, clim_std, lead_time,
                )

        # T3C: AFD uncertainty adjustment — widen/tighten spread based on NWS forecast discussion
        afd_factor = await self._get_afd_spread_factor(group.station)
        if afd_factor != 1.0:
            scale *= afd_factor

        # Compute bucket probabilities
        model_probs = self._prob_engine.bucket_probabilities(
            loc, scale, shape, group.buckets, forecast.lead_time_hours,
        )

        # Resolution-day METAR override: if within 12h of resolution, fetch the
        # running daily max from METAR T-groups and override model probabilities
        # for buckets that are already definitively ruled in or out.
        # S97: Expanded from 6h to 12h — METAR continuous monitor provides fresh data.
        if lead_time < 12.0:
            model_probs = await self._apply_metar_resolution_day_override(
                group, model_probs, lead_time,
            )

        # P2: NBM CDF benchmark — when NBM disagrees with market by ≥15pp,
        # flag as high-conviction. Only available for US stations (NBM in models_used).
        _nbm_signals: Dict[str, Dict] = {}
        if "nbm" in forecast.models_used:
            market_prices_for_nbm = {b.market_id: b.yes_price for b in group.buckets}
            _nbm_disagree = float(getattr(settings, "WEATHER_NBM_DISAGREE_THRESHOLD", 0.15))
            _nbm_signals = self._prob_engine.compute_nbm_benchmark(
                nbm_high=forecast.deterministic_high,
                buckets=group.buckets,
                market_prices=market_prices_for_nbm,
                lead_time_hours=forecast.lead_time_hours,
                disagree_threshold=_nbm_disagree,
            )
            if _nbm_signals:
                logger.info(
                    "weatherbot_nbm_benchmark",
                    city=group.city,
                    date=group.target_date.isoformat(),
                    nbm_high=round(forecast.deterministic_high, 1),
                    signals=len(_nbm_signals),
                    best_nbm_edge=round(max(abs(s["nbm_edge"]) for s in _nbm_signals.values()), 4),
                )

        # M7: Multi-outcome coherence check — reject if >50% of buckets lack prices.
        # Trading on partial data breaks the sum-to-1 probability assumption.
        market_prices = {b.market_id: b.yes_price for b in group.buckets}
        priced_count = sum(1 for p in market_prices.values() if p and 0.0 < float(p) < 1.0)

        # Diagnostic: log per-group price coverage at info level
        if priced_count < len(group.buckets):
            logger.info(
                "weatherbot_group_pricing",
                city=group.city,
                date=group.target_date.isoformat(),
                priced=priced_count,
                total=len(group.buckets),
                m7_rejected=len(group.buckets) >= 4 and priced_count < len(group.buckets) * 0.5,
                model_probs_count=len(model_probs),
            )

        if len(group.buckets) >= 4 and priced_count < len(group.buckets) * 0.5:
            return [], model_probs

        # Compute edges
        edges = self._prob_engine.compute_edges(model_probs, market_prices)

        # Diagnostic: log raw edges before filtering
        if edges:
            best_raw = max(e["abs_edge"] for e in edges)
            logger.info(
                "weatherbot_raw_edges",
                city=group.city,
                date=group.target_date.isoformat(),
                n_edges=len(edges),
                best_raw_edge=round(best_raw, 4),
                edges_above_min=[round(e["abs_edge"], 4) for e in edges if e["abs_edge"] >= self._get_min_edge("temperature", group.station, forecast.model_spread)][:5],
            )

        # Filter to tradeable
        tradeable = []
        bucket_map = {b.market_id: b for b in group.buckets}
        _effective_min = self._get_min_edge("temperature", group.station, forecast.model_spread)

        for e in edges:
            if e["abs_edge"] < _effective_min:
                continue

            # S118: Edge cap REMOVED. Data analysis (7,886 resolved signals) showed
            # 0.70+ edge bucket had HIGHEST win rate (87.3%). Larger edges are more
            # reliable, not less. The cap was blocking the bot's strongest signals.

            # Skip recently exited markets
            mono_now = time.monotonic()
            exited_at = self._recently_exited.get(e["market_id"])
            if exited_at and mono_now - exited_at < self._exit_cooldown_secs:  # S107: 4hr cooldown
                continue

            # Skip if no token ID
            bucket = bucket_map.get(e["market_id"])
            if not bucket:
                continue

            side = e["side"]
            token_id = bucket.token_id if side == "YES" else bucket.no_token_id
            if not token_id:
                continue

            price = bucket.yes_price if side == "YES" else (1.0 - bucket.yes_price)
            # S101: Penny-bet filter widened (0.05→0.04, 0.95→0.97) for tail buckets.
            # At 4¢+, CLOB spreads are 25-50% — fillable with IOC. Below 3¢, spreads
            # exceed price itself. Live-ready at 0.04; verify before lowering to 0.03.
            if price <= 0.04 or price >= 0.97:
                continue

            # S118 Fix 2: NO entry price cap — skip expensive NO tokens.
            # Data: 70-80¢ NO bucket is -$484 (76.4% WR, 0.24x win/loss ratio).
            # At 75¢ entry: risk $75, win $25. Need >75% WR to break even.
            # <60¢ bucket is +$1,836 — that's where real NO edge lives.
            _no_max_price = float(getattr(settings, "WEATHER_NO_MAX_ENTRY_PRICE", 0.65))
            if side == "NO" and price > _no_max_price:
                continue

            # Check position already open — fast path via in-memory set
            gw = self.base_engine.order_gateway
            if gw and hasattr(gw, "_open_position_markets"):
                bot_positions = gw._open_position_markets.get("WeatherBot", set())
                if str(e["market_id"]) in bot_positions:
                    continue

            # S118 Fix 1: DB-backed re-entry guard — prevents position stacking.
            # Data: 55% of markets had 2+ entries, Miami had 12 entries on one market.
            # The in-memory check above misses paper positions. DB is ground truth.
            try:
                db = getattr(self.base_engine, "db", None)
                if db:
                    _existing = await db.fetch_one(
                        "SELECT 1 FROM positions WHERE market_id = :mid AND bot_id = 'WeatherBot' AND status = 'open' LIMIT 1",
                        {"mid": str(e["market_id"])},
                    )
                    if _existing:
                        continue
            except Exception:
                pass  # fail-open: if DB unreachable, fall through to trade

            # WU vs NWS resolution-source uncertainty:
            # S121: boundary_risk tracked for logging but NO LONGER discounts confidence.
            # Let Kelly self-regulate — the 50% discount was crushing profitable trades.
            boundary_risk = WeatherBot._near_boundary(loc, bucket)
            # YES: confidence = model_prob (P of outcome)
            # NO:  confidence = 1 - model_prob (P of NOT outcome) — correct for Kelly + risk manager
            _raw_conf = e["model_prob"] if side == "YES" else (1.0 - e["model_prob"])
            base_confidence = min(0.95, _raw_conf)
            # S135: Logistic regression calibration (6-feature: conf, side, lead_time, price, bucket, spread)
            if self._cal_fitted:
                effective_confidence = self._confidence_calibrator.calibrate(
                    base_confidence, side=side,
                    lead_time_hours=lead_time, entry_price=price,
                    bucket_type=bucket.bucket_type,
                    ensemble_spread=forecast.model_spread,
                )
            else:
                effective_confidence = base_confidence

            # S135 R3: YES confidence floor — data shows YES <0.35 has 6.4% WR, -$3,159
            _yes_min_conf = float(getattr(settings, "WEATHER_YES_MIN_CONFIDENCE", 0.35))
            if side == "YES" and _yes_min_conf > 0 and effective_confidence < _yes_min_conf:
                logger.debug(
                    "weatherbot_yes_conf_gate",
                    market_id=e["market_id"],
                    effective_confidence=round(effective_confidence, 3),
                    floor=_yes_min_conf,
                )
                continue

            if boundary_risk:
                logger.debug(
                    "weatherbot_boundary_risk",
                    market_id=e["market_id"],
                    loc=round(loc, 2),
                    bucket_type=bucket.bucket_type,
                    high_bound=bucket.high_bound,
                    low_bound=bucket.low_bound,
                )

            # S118 Fix 3: Max buckets per group — limit correlated blowup risk.
            # Data: Miami lost -$976 from 12 positions on same city+date resolution.
            # Keep only the top N by edge magnitude; skip the rest.
            _max_buckets = int(getattr(settings, "WEATHER_MAX_BUCKETS_PER_GROUP", 3))
            if len(tradeable) >= _max_buckets:
                break

            # S121: NO confidence discount REMOVED — let Kelly self-regulate.
            # Data showed NO 60-80¢ had 58.8% WR (positive!) but negative P&L because
            # the 0.80x discount shrank bets below break-even threshold. Kelly already
            # accounts for payoff asymmetry via (p*b - q)/b formula.

            tradeable.append({
                "market_id": e["market_id"],
                "token_id": token_id,
                "side": side,
                "price": price,
                "confidence": effective_confidence,
                "raw_confidence": base_confidence,
                "model_prob": e["model_prob"],
                "edge": e["edge"],
                "abs_edge": e["abs_edge"],
                "city": group.city,
                "target_date": group.target_date.isoformat(),
                "lead_time_hours": round(forecast.lead_time_hours, 1),
                "ensemble_mean": round(forecast.deterministic_high, 1),
                "model_spread": round(forecast.model_spread, 2),
                "ensemble_count": len(forecast.ensemble_members),
                "resolution_boundary_risk": boundary_risk,
                "market_type": "temperature",
                "forecast_delta": forecast.forecast_delta,
                "nbm_high_conviction": e["market_id"] in _nbm_signals,
                "bucket_type": bucket.bucket_type,
            })
            await self._log_weather_prediction(
                e["market_id"], e["model_prob"], price,
                effective_confidence, "temperature",
            )

        return tradeable, model_probs

    async def _apply_metar_resolution_day_override(
        self,
        group: WeatherMarketGroup,
        model_probs: Dict[str, float],
        lead_time_hours: float,
    ) -> Dict[str, float]:
        """Override model probabilities with METAR running daily max on resolution day.

        When lead_time_hours < 6 (same calendar day as resolution), the running
        daily maximum from METAR T-groups can definitively rule buckets in or out:
          - running_max > bucket.high_bound + 0.5: range/at_or_below can't resolve YES
          - running_max >= at_or_higher.low_bound - 0.5: threshold already crossed → YES
          - running_max < at_or_below.high_bound - 1.5: well below ceiling → YES
          - running_max < at_or_higher.low_bound - 2.0: far below floor → NO

        Returns updated model_probs dict (original unchanged if METAR unavailable).
        Probabilities are renormalized after overrides to maintain sum ≈ 1.0.
        """
        running_max = await self._metar_client.get_running_daily_max(
            group.station.station_id,
            group.target_date,
            group.station.temp_unit,
        )
        if running_max is None:
            return model_probs

        logger.info(
            "weatherbot_metar_resolution_override",
            station=group.station.station_id,
            date=group.target_date.isoformat(),
            running_max=round(running_max, 1),
            unit=group.station.temp_unit,
            lead_time_hours=round(lead_time_hours, 1),
        )

        updated = dict(model_probs)
        bucket_map = {b.market_id: b for b in group.buckets}

        # At <2h lead time, daily high is nearly established — tighter margins.
        # Unit-aware margin: 0.5°F or 0.3°C (matching boundary_risk logic).
        aggressive = lead_time_hours < 2.0
        unit_margin = 0.3 if group.temp_unit == "C" else 0.5

        for market_id, bucket in bucket_map.items():
            btype = bucket.bucket_type

            if btype == "at_or_below":
                if running_max > bucket.high_bound + unit_margin:
                    # Daily max already exceeded threshold — bucket cannot resolve YES
                    updated[market_id] = 0.001
                elif aggressive and running_max < bucket.high_bound - unit_margin:
                    # <2h: well below ceiling, max essentially established
                    updated[market_id] = 0.98
                elif running_max < bucket.high_bound - 1.5:
                    # Well below ceiling with little time left — almost certainly YES
                    updated[market_id] = 0.97

            elif btype == "at_or_higher":
                if aggressive and running_max >= bucket.low_bound - unit_margin:
                    # <2h: running max at or near floor — resolving YES
                    updated[market_id] = 0.98
                elif running_max >= bucket.low_bound - 0.5:
                    # Daily max has reached or nearly reached the floor — resolving YES
                    updated[market_id] = 0.97
                elif aggressive and running_max < bucket.low_bound - 1.0:
                    # <2h: still well below floor — very unlikely to reach
                    updated[market_id] = 0.001
                elif running_max < bucket.low_bound - 2.0:
                    # Well below floor — unlikely to reach threshold in remaining time
                    updated[market_id] = 0.001

            elif btype == "range":
                if running_max > bucket.high_bound + unit_margin:
                    # Daily max has exceeded the range upper bound — cannot resolve YES
                    updated[market_id] = 0.001
                elif aggressive and bucket.low_bound <= running_max <= bucket.high_bound - unit_margin:
                    # <2h: running max is within range and below upper bound with margin.
                    # Daily high is nearly established — this range is very likely to win.
                    updated[market_id] = 0.92

            elif btype == "exact":
                if running_max > bucket.high_bound + unit_margin:
                    # Already exceeded the exact value — cannot resolve YES
                    updated[market_id] = 0.001

        # S111: Guard against degenerate METAR override — if ALL buckets were
        # set to 0.001 (running_max outside every range), renormalization would
        # give each ~14% and create artificial edges.  Return original probs.
        if updated and max(updated.values()) <= 0.001:
            logger.info("weatherbot_metar_renorm_skip", running_max=running_max,
                        n_buckets=len(updated), reason="all_buckets_outside_range")
            return model_probs

        # Renormalize so probabilities sum to 1.0
        total = sum(updated.values())
        if total > 0:
            for mid in updated:
                updated[mid] /= total

        return updated

    # ── Smoczynski-Tomkins multi-bucket sizing (W3+W5) ───────────────────

    @staticmethod
    def _smoczynski_tomkins_allocate(
        opps: List[Dict], group_budget: float, kelly_mult: float = 0.25,
    ) -> Dict[str, float]:
        """Optimal Kelly allocation for mutually exclusive temperature buckets.

        Standard independent Kelly undersizes by 20-40% because it ignores
        the synthetic hedge: betting 3 of 7 mutually exclusive buckets means
        losing bets partially fund the winner. Smoczynski-Tomkins (2010) gives
        the closed-form solution.

        For mutually exclusive outcomes with positive edge, the allocation is
        proportional to each outcome's Kelly edge, scaled by the total hedge.

        Args:
            opps: Tradeable opportunities from _analyze_group(), each with
                  model_prob, price, abs_edge, side, market_id.
            group_budget: Maximum USD to deploy across this group.
            kelly_mult: Fractional Kelly multiplier (default 0.25).

        Returns:
            Dict mapping market_id → USD allocation.
        """
        if not opps or group_budget <= 0:
            return {}

        # Compute per-bucket Kelly edge: f_i = (p_i * b_i - q_i) / b_i
        # where p_i = confidence in this bucket, b_i = payout odds
        edges: Dict[str, float] = {}
        for opp in opps:
            p = opp["model_prob"] if opp["side"] == "YES" else (1.0 - opp["model_prob"])
            price = opp["price"]
            if price <= 0.02 or price >= 0.98 or p <= price:
                continue
            b = (1.0 - price) / price
            if b <= 0:
                continue
            q = 1.0 - p
            f_i = (p * b - q) / b
            if f_i > 0:
                edges[opp["market_id"]] = f_i

        if not edges:
            return {}

        # S-T hedge factor: sum of all probabilities we're betting on.
        # The hedge arises because exactly ONE bucket wins — losses on N-1
        # bets are offset by the win on 1. The total fraction to deploy is
        # boosted by 1 / (1 - sum_of_losing_probs) ≈ 1 / (1 - hedge).
        #
        # Simplified: allocate proportional to edge magnitude, scaled by
        # fractional Kelly, with the group budget as hard cap.
        total_edge = sum(edges.values())
        if total_edge <= 0:
            return {}

        # Pro-rata allocation: each bucket gets its share of the budget
        # proportional to its Kelly edge. Apply kelly_mult for safety.
        allocations = {}
        for mid, f_i in edges.items():
            share = (f_i / total_edge) * kelly_mult * group_budget
            allocations[mid] = round(max(1.0, share), 2)

        # Ensure total doesn't exceed group budget
        total = sum(allocations.values())
        if total > group_budget:
            scale = group_budget / total
            allocations = {mid: round(v * scale, 2) for mid, v in allocations.items()}

        return allocations

    async def _execute_group_trades(
        self,
        opps: List[Dict],
        group: WeatherMarketGroup,
        regime_boost: float,
    ) -> int:
        """Execute laddered trades across a group using S-T multi-bucket sizing.

        Instead of sizing each bucket independently (undersizing by 20-40%),
        distributes the group budget proportionally by edge magnitude.
        Still respects group/city exposure limits and daily loss limit.

        Returns number of trades executed.
        """
        if not opps:
            return 0

        # Daily loss limit
        if self._daily_pnl <= -self._daily_loss_limit:
            return 0

        group_key = f"{group.city}:{group.target_date.isoformat()}"
        current_group_exp = self._group_exposure.get(group_key, 0.0)
        remaining_group = max(0.0, self._max_per_group - current_group_exp)
        if remaining_group < 1.0:
            return 0

        current_city_exp = self._city_exposure.get(group.city, 0.0)
        remaining_city = max(0.0, self._max_correlated - current_city_exp)
        if remaining_city < 1.0:
            return 0

        group_budget = min(remaining_group, remaining_city)

        # S-T allocation across all buckets with edge
        st_sizes = self._smoczynski_tomkins_allocate(
            opps, group_budget, self._kelly_mult,
        )

        if st_sizes:
            logger.info(
                "weatherbot_st_allocation",
                city=group.city,
                date=group.target_date.isoformat(),
                n_buckets=len(st_sizes),
                total_usd=round(sum(st_sizes.values()), 2),
                group_budget=round(group_budget, 2),
            )

        traded = 0
        for opp in opps:
            opp["regime_boost"] = regime_boost
            st_size = st_sizes.get(opp["market_id"])
            if st_size and st_size >= 1.0:
                opp["_st_size_override"] = st_size
            if await self._execute_weather_trade(opp, group):
                traded += 1

        return traded

    # ── Trade execution ───────────────────────────────────────────────────

    async def _execute_weather_trade(self, opp: Dict, group: WeatherMarketGroup) -> bool:
        """Execute a weather trade with risk checks. Returns True if trade was placed."""
        gw = self.base_engine.order_gateway

        # S107 Fix 4: Same-side dedup — check _position_details (side-aware) instead of
        # _open_position_markets (market_id only). 700 duplicate entries found without side check.
        if gw and hasattr(gw, "_position_details"):
            _key = f"WeatherBot:{opp.get('market_id', '')}"
            _existing = gw._position_details.get(_key)
            if _existing and str(_existing.get("side", "")).upper() == str(opp.get("side", "")).upper():
                return False
        elif gw and hasattr(gw, "_open_position_markets"):
            # Fallback: market_id-only check if _position_details not available
            bot_positions = gw._open_position_markets.get("WeatherBot", set())
            if str(opp.get("market_id", "")) in bot_positions:
                return False

        # Skip recently exited markets (15-min cooldown)
        _mono_now = time.monotonic()
        _mid = opp.get("market_id", "")
        _exited_at = self._recently_exited.get(_mid)
        if _exited_at and _mono_now - _exited_at < self._exit_cooldown_secs:  # S107: 4hr
            return False

        # S99: Fill-failure cooldown — skip markets that failed N consecutive times
        _fail_entry = self._fill_fail_tracker.get(_mid)
        if _fail_entry:
            _consec, _last_mono = _fail_entry
            if _consec >= self._fill_fail_max_consec:
                if _mono_now - _last_mono < self._fill_fail_cooldown_secs:
                    return False
                else:
                    del self._fill_fail_tracker[_mid]

        # S120: bestAsk pre-filter REMOVED. It was redundant with compute_edges()
        # which already ensures model_prob > market_price + min_edge. The market
        # index bestAsk is staler than the prices used in edge computation, so the
        # filter could only reject valid trades, never catch invalid ones.
        # Volume passthrough kept for event_data logging (L2537).
        _clob_volume = 0.0
        if gw:
            try:
                _midx = getattr(gw, "_market_index", None)
                _midx_cid = getattr(gw, "_market_index_by_cid", None)
                _mdata = None
                if _midx and isinstance(_midx, dict):
                    _mdata = _midx.get(str(_mid))
                if not _mdata and _midx_cid and isinstance(_midx_cid, dict):
                    _mdata = _midx_cid.get(str(_mid))
                if _mdata and isinstance(_mdata, dict):
                    _clob_volume = float(_mdata.get("volume") or _mdata.get("volume24hr") or 0)
            except (TypeError, ValueError, AttributeError):
                pass
        if _clob_volume > 0:
            opp["_clob_volume"] = _clob_volume

        # S99: Fill probability floor — skip if price-depth predicts <threshold
        _price = opp.get("price", 0.5)
        _est_fill = 0.3 + 0.7 * 4.0 * _price * (1.0 - _price)
        if _est_fill < self._min_fill_prob_estimate:
            logger.debug("weatherbot_low_fill_prob_skip", market_id=_mid, price=_price, est_fill=round(_est_fill, 3))
            return False

        # Daily loss limit
        if self._daily_pnl <= -self._daily_loss_limit:
            logger.warning("weatherbot_daily_loss_limit_hit", pnl=self._daily_pnl)
            # B4: Alert on daily loss limit hit
            _alerting = getattr(self.base_engine, "alerting_system", None)
            if _alerting:
                await _alerting.send_alert(
                    title="WeatherBot Daily Loss Limit",
                    message=f"Daily P&L ${self._daily_pnl:.2f} hit limit -${self._daily_loss_limit:.0f}. Trades blocked.",
                    severity=AlertSeverity.WARNING,
                    source="WeatherBot",
                    metadata={"daily_pnl": self._daily_pnl, "limit": self._daily_loss_limit},
                )
            return False

        # Per-group + city exposure limit — early check (unlocked, for fast rejection)
        group_key = f"{group.city}:{group.target_date.isoformat()}"
        current_group_exp = self._group_exposure.get(group_key, 0.0)
        if current_group_exp >= self._max_per_group:
            return False
        current_city_exp = self._city_exposure.get(group.city, 0.0)
        if current_city_exp >= self._max_correlated:
            return False

        # Near-expiry Kelly boost — 2h: WEATHER_HOLD_HOURS_BEFORE_RESOLUTION window.
        # NOAA model spread narrows as resolution approaches (more ensemble members converge),
        # yielding 600-700% ROI by holding. Apply progressive boost within the hold window.
        # Boost schedule (from settings-controlled window, default 48h):
        #   <12h to resolution → 2.0× (NOAA final-call, highest certainty)
        #   <24h to resolution → 1.5× (NOAA day-of-event, strong convergence)
        #   <hold_hours to resolution → 1.2× (within hold window, early convergence)
        #   otherwise → 1.0× (standard)
        lead_time = opp.get("lead_time_hours", 48.0)
        _hold_h = getattr(settings, "WEATHER_HOLD_HOURS_BEFORE_RESOLUTION", 48.0)
        # S101: Graduated expiry boost — cap near resolution to avoid compounding
        # with paper_trading.py's 3.0x slippage + 0.5x fill penalty at <30min.
        # 2.0x boost at <1h meets 3.0x penalty = net negative; cap to 1.2x.
        if lead_time < 1.0:
            expiry_boost = 1.2   # <1h: resolution penalty dominates, cap boost
        elif lead_time < 6.0:
            expiry_boost = 1.5   # 1-6h: METAR override window, moderate boost
        elif lead_time < 12.0:
            expiry_boost = 2.0   # 6-12h: NOAA final-call, maximum certainty
        elif lead_time < 24.0:
            expiry_boost = 1.5   # NOAA day-of: strong convergence
        elif lead_time < _hold_h:
            expiry_boost = 1.2   # Within hold window: early convergence signal
        else:
            expiry_boost = 1.0   # Standard (outside hold window)

        if expiry_boost > 1.0:
            logger.debug(
                "weatherbot_expiry_boost: lead_h=%.1f hold_h=%.1f boost=%.1f",
                lead_time, _hold_h, expiry_boost,
            )

        # Cross-city regime boost (P-Opportunity)
        regime_boost = opp.get("regime_boost", 1.0)

        # S115: Severe weather hard halt — skip ALL trades if halt-category alert active
        _halt_event = self._should_halt_severe_weather(group.station)
        if _halt_event:
            logger.info(
                "weatherbot_severe_weather_halt",
                station=getattr(group.station, "station_id", "?"),
                event=_halt_event,
                market_id=opp["market_id"],
            )
            return False

        # Severe weather boost (hurricane/tornado/blizzard near station)
        severe_boost = await self._get_severe_weather_boost(group.station)

        # P1: Model-run jump boost — when ensemble mean shifts ≥ threshold between
        # model runs, markets lag. Scale boost linearly by delta magnitude.
        _forecast_delta = opp.get("forecast_delta")
        _jump_threshold = float(getattr(settings, "WEATHER_JUMP_THRESHOLD_F", 3.0))
        _jump_max_boost = float(getattr(settings, "WEATHER_JUMP_MAX_BOOST", 1.5))
        if _forecast_delta is not None and abs(_forecast_delta) >= _jump_threshold:
            # Linear scale: at threshold → 1.0 extra, at 2× threshold → max_boost extra
            _jump_ratio = min(abs(_forecast_delta) / _jump_threshold, 2.0)
            jump_boost = 1.0 + (_jump_max_boost - 1.0) * (_jump_ratio / 2.0)
            logger.info(
                "weatherbot_jump_boost",
                market_id=opp["market_id"],
                delta=_forecast_delta,
                jump_boost=round(jump_boost, 2),
            )
        else:
            jump_boost = 1.0

        # P2: NBM high-conviction boost — when NBM CDF disagrees with market by ≥15pp
        # for US stations, apply a 1.3× sizing multiplier (calibrated benchmark signal).
        nbm_boost = getattr(settings, "WEATHER_NBM_BOOST", 1.3) if opp.get("nbm_high_conviction") else 1.0

        # S132: Model freshness dampener REMOVED

        # C4: Combined boost — additive with diminishing returns to prevent
        # multiplicative stacking (was 2.0×1.2×2.0=4.8→cap 3.0 = 0.75 Kelly).
        # New: each boost contributes its excess independently; capped at 2.0×
        # to keep effective Kelly ≤ 0.5 (quarter-Kelly × 2.0).
        # S132: model_freshness removed from formula; Baker-McHale removed
        combined_boost = 1.0 + (expiry_boost - 1.0) + (regime_boost - 1.0) * 0.5 + (severe_boost - 1.0) * 0.5 + (jump_boost - 1.0) * 0.5 + (nbm_boost - 1.0) * 0.5

        # S135 R4: Disable boost for YES — amplifies already-bad YES bets (18.8% WR at 0.95+ conf)
        if opp["side"] == "YES" and not getattr(settings, "WEATHER_YES_BOOST_ENABLED", False):
            combined_boost = 1.0

        # S107: Drawdown compression REMOVED from combined_boost — already applied
        # in BotBankrollManager.get_bet_size() via `compress` factor. Keeping both
        # double-counted the penalty (0.50 × 0.50 = 0.25x on a 3-loss streak).
        # _compute_weather_drawdown_factor() retained for monitoring/logging only.

        # Per-station reliability: well-calibrated stations get larger size
        _station_id = getattr(getattr(group, "station", None), "station_id", None)
        if _station_id:
            _station_factor = await self._get_station_reliability_factor(_station_id)
            if _station_factor != 1.0:
                combined_boost *= _station_factor

        # S132: Bühlmann calibration ramp REMOVED — global Platt calibration
        # (T=2.271) handles uncalibrated stations.

        # S132: Combined boost cap REMOVED — guardrails (exposure caps, Kelly,
        # slippage) prevent oversizing.

        # H1: Slippage-adjusted edge — query order book depth and skip if
        # estimated slippage eats the edge. Cap size to max safe fill.
        # Fail-open: if liquidity check fails, proceed at full size.
        _slippage_size_cap = float("inf")
        _liq_guard = getattr(self.base_engine, "liquidity_guardian", None)
        if _liq_guard:
            try:
                _cid = ""
                _midx = getattr(self.base_engine.order_gateway, "_market_index", None)
                if _midx:
                    _mdata = _midx.get(str(opp["market_id"]))
                    if _mdata:
                        _cid = str(_mdata.get("conditionId") or _mdata.get("condition_id") or "")
                liq_check = await _liq_guard.check_liquidity(
                    market_id=opp["market_id"],
                    token_id=opp["token_id"],
                    trade_size=self._default_size / max(opp["price"], 0.01),
                    side="BUY",
                    condition_id=_cid,
                )
                if liq_check:
                    _slippage_pct = liq_check.get("slippage", 0.0)
                    _effective_edge = opp["abs_edge"] - _slippage_pct
                    if _effective_edge < self._get_min_edge(opp.get("market_type", "temperature"), group.station):
                        logger.debug(
                            "weatherbot_slippage_skip",
                            market_id=opp["market_id"],
                            raw_edge=round(opp["abs_edge"], 4),
                            slippage=round(_slippage_pct, 4),
                            effective_edge=round(_effective_edge, 4),
                        )
                        return False
                    # Cap size to what the book can fill within 2% slippage
                    max_safe = await _liq_guard.get_max_safe_size(
                        market_id=opp["market_id"],
                        token_id=opp["token_id"],
                        side="BUY",
                        max_slippage_pct=0.02,
                        condition_id=_cid,
                    )
                    if max_safe > 0:
                        _slippage_size_cap = max_safe * max(opp["price"], 0.01)
            except Exception as exc:
                # S97: Fail-CLOSED — use cached result or conservative default (50 bps slippage)
                logger.warning("weatherbot_liquidity_check_failed", error=str(exc), market_id=opp["market_id"])
                _cached_liq = self._liquidity_cache.get(opp["market_id"])
                if _cached_liq and (time.monotonic() - _cached_liq[0]) < self._liquidity_cache_ttl:
                    liq_check = _cached_liq[1]
                    _slippage_pct = liq_check.get("slippage", 0.0)
                    _effective_edge = opp["abs_edge"] - _slippage_pct
                    if _effective_edge < self._get_min_edge(opp.get("market_type", "temperature"), group.station):
                        return False
                else:
                    # No cache — apply conservative 50 bps slippage penalty
                    _effective_edge = opp["abs_edge"] - 0.005
                    if _effective_edge < self._get_min_edge(opp.get("market_type", "temperature"), group.station):
                        return False
            else:
                # Cache successful liquidity result
                if liq_check:
                    self._liquidity_cache[opp["market_id"]] = (time.monotonic(), liq_check)

        # W3+W5: Use Smoczynski-Tomkins group-level allocation when available.
        # S-T sizes are pre-computed by _execute_group_trades() and passed via
        # _st_size_override. Fall back to independent Kelly if not set.
        _min_trade = float(getattr(settings, "WEATHER_MIN_TRADE_USD", 5.0))
        _st_override = opp.pop("_st_size_override", None)
        if _st_override is not None:
            _raw_size = _st_override * combined_boost
        else:
            # Size via central risk_manager Kelly (same as all other bots)
            try:
                _cal_qual = None
                if (self._confidence_calibrator.is_fitted
                        and self._confidence_calibrator._cal_brier is not None):
                    _cal_qual = {
                        "brier": self._confidence_calibrator._cal_brier,
                        "count": self._confidence_calibrator.n_samples,
                    }
                kelly_shares = await self.calculate_bot_position_size(
                    opp["confidence"], opp["price"],
                    calibration_quality=_cal_qual,
                )
                _raw_size = kelly_shares * opp["price"] * combined_boost
            except Exception as exc:
                logger.warning("weatherbot_kelly_sizing_failed", error=str(exc))
                _raw_size = self._default_size

        # S124: Negative-EV gate — if calibrated confidence < price, the trade is
        # negative EV regardless of sizing path (Kelly or S-T). The S-T allocator
        # distributes budget by edge ratio and doesn't check Kelly's EV signal,
        # so _raw_size can be large even when confidence < price. Block both paths.
        if opp["confidence"] < opp["price"] or _raw_size <= 0:
            logger.info(
                "weatherbot_shadow_entry",
                market_id=opp["market_id"], side=opp["side"],
                price=round(opp["price"], 4),
                confidence=round(opp["confidence"], 4),
                raw_confidence=round(opp.get("raw_confidence", opp["confidence"]), 4),
                edge=round(opp.get("edge", 0), 4),
                raw_size_usd=round(_raw_size, 2), combined_boost=round(combined_boost, 3),
                city=opp.get("city", ""),
                reason="negative_ev" if opp["confidence"] < opp["price"] else "zero_kelly",
            )
            try:
                _shadow_reason = "negative_ev" if opp["confidence"] < opp["price"] else "zero_kelly"
                await self.base_engine.db.insert_trade_event(
                    bot_name=self.bot_name, event_type="SHADOW_ENTRY",
                    market_id=opp["market_id"], side=opp["side"],
                    price=opp["price"], size=_raw_size / max(opp["price"], 0.01),
                    confidence=opp["confidence"],
                    event_data={
                        "city": opp.get("city", ""),
                        "raw_size_usd": round(_raw_size, 2),
                        "raw_confidence": round(opp.get("raw_confidence", opp["confidence"]), 4),
                        "combined_boost": round(combined_boost, 3),
                        "lead_time_hours": round(opp.get("lead_time_hours", 0), 1),
                        "reason": _shadow_reason,
                    },
                )
            except Exception:
                pass  # best-effort
            return False

        size = max(_min_trade, _raw_size)

        # S97: Lock-guarded exposure reservation — re-read under lock for parallel safety
        async with self._exposure_lock:
            current_group_exp = self._group_exposure.get(group_key, 0.0)
            current_city_exp = self._city_exposure.get(group.city, 0.0)
            remaining_group = self._max_per_group - current_group_exp
            remaining_city = self._max_correlated - current_city_exp
            size = min(size, remaining_group, remaining_city, _slippage_size_cap)
            if size < _min_trade:
                # S122: Log shadow entry for sub-$5 trades (data collection)
                if _raw_size > 0:
                    logger.info(
                        "weatherbot_shadow_entry",
                        market_id=opp["market_id"],
                        side=opp["side"],
                        price=round(opp["price"], 4),
                        confidence=round(opp["confidence"], 4),
                        edge=round(opp.get("edge", 0), 4),
                        raw_size_usd=round(_raw_size, 2),
                        combined_boost=round(combined_boost, 3),
                        city=opp.get("city", ""),
                        reason="sub_min_trade" if _raw_size < _min_trade else "exposure_cap",
                    )
                    try:
                        await self.base_engine.db.insert_trade_event(
                            bot_name=self.bot_name,
                            event_type="SHADOW_ENTRY",
                            market_id=opp["market_id"],
                            side=opp["side"],
                            price=opp["price"],
                            size=_raw_size / max(opp["price"], 0.01),
                            confidence=opp["confidence"],
                            event_data={
                                "city": opp.get("city", ""),
                                "raw_size_usd": round(_raw_size, 2),
                                "combined_boost": round(combined_boost, 3),
                                "lead_time_hours": round(opp.get("lead_time_hours", 0), 1),
                                "reason": "sub_min_trade" if _raw_size < _min_trade else "exposure_cap",
                            },
                        )
                    except Exception:
                        pass  # best-effort, don't block
                return False
            # Reserve exposure atomically under lock
            self._group_exposure[group_key] = current_group_exp + size
            self._city_exposure[group.city] = current_city_exp + size

        logger.info(
            "weatherbot_trade_signal",
            market_id=opp["market_id"],
            side=opp["side"],
            edge=opp["edge"],
            model_prob=opp["model_prob"],
            price=opp["price"],
            size=round(size, 2),
            city=opp["city"],
            date=opp.get("target_date"),
            lead_time_h=lead_time,
            expiry_boost=expiry_boost,
            regime_boost=regime_boost,
            ensemble_count=opp.get("ensemble_count", 0),
            jump_boost=jump_boost,
            forecast_delta=_forecast_delta,
            nbm_boost=nbm_boost,
            # S132: model_freshness and model_age_h removed
        )

        # S109: Convert USD to shares for place_order (paper engine expects shares).
        # All upstream sizing, exposure tracking, and floor checks remain in USD.
        _size_shares = size / opp["price"]
        result = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=_size_shares,
            price=opp["price"],
            confidence=opp["confidence"],
            event_data={
                "city": group.city,
                "date": group.target_date.isoformat(),
                "market_type": opp.get("market_type", "temperature"),
                "lead_time_hours": lead_time,
                "boundary_risk": opp.get("resolution_boundary_risk", False),
                "scan_start_mono": getattr(self, "_scan_start_mono", None),
                "alpha_decay_half_life_s": getattr(settings, "WEATHER_ALPHA_DECAY_HALF_LIFE_S", 1800),
                "volume_24h": opp.get("_clob_volume", 0.0),  # S107 Fix 3: pass CLOB volume for fill model
                "bucket_type": opp.get("bucket_type", "unknown"),
                "ensemble_spread": opp.get("model_spread", 3.0),
            },
        )

        if result.get("success"):
            logger.info(
                "weatherbot_trade_filled",
                market_id=opp["market_id"],
                side=opp["side"],
                size_usd=round(size, 2),
                size_shares=round(_size_shares, 2),
            )
            # S104: Cache market→group mapping for exit exposure decrement
            self._market_group_cache[opp["market_id"]] = (group_key, group.city, size)
            # S104: Write-through exposure to daily_counters
            _db = getattr(self.base_engine, "db", None)
            if _db is not None:
                try:
                    await _inc_daily(_db, "WeatherBot", f"group_{group_key}", size)
                    await _inc_daily(_db, "WeatherBot", f"city_{group.city}", size)
                except Exception:
                    pass  # Non-critical: in-memory is authoritative intra-day
            # Log prediction for accuracy tracking at trade execution time
            await self._log_weather_prediction(
                opp["market_id"], opp["model_prob"], opp["price"],
                opp.get("confidence", opp["model_prob"]),
                opp.get("market_type", "temperature"),
            )
            # S127: Entry-side cooldown REMOVED — cooldown must start at EXIT, not
            # ENTRY.  Exit-side set lives at line ~1015 (PM exit detection).  The old
            # entry-side set caused the 4h cooldown to expire before the position
            # actually closed, defeating whipsaw protection.
            # S99: Clear fill-failure tracker on success
            self._fill_fail_tracker.pop(opp["market_id"], None)
            return True
        else:
            # S102 fix: revert under lock with decrement to avoid race condition.
            # Prior code used snapshot assignment outside lock — could overwrite
            # another coroutine's reservation under WEATHER_TRADE_CONCURRENCY=8.
            async with self._exposure_lock:
                self._group_exposure[group_key] = max(0.0, self._group_exposure.get(group_key, 0.0) - size)
                self._city_exposure[group.city] = max(0.0, self._city_exposure.get(group.city, 0.0) - size)
            # S104: Write-through revert to daily_counters
            _db = getattr(self.base_engine, "db", None)
            if _db is not None:
                try:
                    await _inc_daily(_db, "WeatherBot", f"group_{group_key}", -size)
                    await _inc_daily(_db, "WeatherBot", f"city_{group.city}", -size)
                except Exception:
                    pass
            # S99: Track consecutive fill failures
            _prev = self._fill_fail_tracker.get(opp["market_id"])
            _prev_count = _prev[0] if _prev else 0
            self._fill_fail_tracker[opp["market_id"]] = (_prev_count + 1, _mono_now)
            logger.debug(
                "weatherbot_trade_failed",
                market_id=opp["market_id"],
                error=result.get("error", "unknown"),
            )
            return False

    # ── Position re-evaluation (L4) ──────────────────────────────────────

    async def _reevaluate_open_positions(
        self,
        analyzed: List[Tuple[List[Dict], WeatherMarketGroup, Dict[str, float]]],
    ) -> None:
        """Update predicted_prob on open WeatherBot positions using fresh forecast data.

        Feeds position_manager's model-reversal exit logic with current probabilities
        instead of stale entry-time values. Called at end of each scan cycle.
        """
        og = getattr(self.base_engine, "order_gateway", None)
        if not og:
            return

        bot_positions = og._open_position_markets.get("WeatherBot", set())
        if not bot_positions:
            return

        # Build market_id → fresh probability lookup from all analyzed groups
        fresh_probs: Dict[str, float] = {}
        for _opps, _group, _model_probs in analyzed:
            fresh_probs.update(_model_probs)

        if not fresh_probs:
            return

        updated = 0
        for mid in list(bot_positions):
            if mid in fresh_probs:
                new_prob = fresh_probs[mid]
                detail_key = f"WeatherBot:{mid}"
                details = og._position_details.get(detail_key)
                if details:
                    old_prob = details.get("predicted_prob", 0.5)
                    if abs(new_prob - old_prob) > 0.05:
                        details["predicted_prob"] = new_prob
                        updated += 1
                        logger.debug(
                            "weatherbot_position_prob_updated",
                            market_id=mid,
                            old_prob=round(old_prob, 3),
                            new_prob=round(new_prob, 3),
                        )

        if updated:
            logger.info(
                "weatherbot_positions_reevaluated",
                updated=updated,
                total=len(bot_positions),
            )

    # ── Regime detection ─────────────────────────────────────────────────

    @staticmethod
    def _compute_regime_boost(
        analyzed: List[Tuple[List[Dict], "WeatherMarketGroup", Dict[str, float]]],
    ) -> float:
        """Detect broad warm/cold front across ≥3 cities → 1.2x Kelly boost.

        If ≥3 cities all show their best edge in the same direction (YES = warm,
        NO = cold), a regime signal is present and all positions get a 1.2x boost.
        Returns 1.0 if no regime detected.
        """
        warm_cities: Set[str] = set()
        cold_cities: Set[str] = set()

        for opps, group, _probs in analyzed:
            if not opps:
                continue
            # Best opportunity for this city
            best = max(opps, key=lambda o: o["abs_edge"])
            if best["side"] == "YES":
                warm_cities.add(group.city)
            else:
                cold_cities.add(group.city)

        if len(warm_cities) >= 3:
            logger.info("weatherbot_warm_regime_detected", cities=sorted(warm_cities))
            return 1.2
        if len(cold_cities) >= 3:
            logger.info("weatherbot_cold_regime_detected", cities=sorted(cold_cities))
            return 1.2
        return 1.0

    # ── Market discovery fallbacks ────────────────────────────────────────

    async def _fetch_weather_markets_direct(self) -> List[Dict]:
        """Probe DB (no liquidity floor) and Gamma API for weather/temperature markets.

        Used as fallback when the normal DB pipeline returns 0 weather markets.
        This handles two failure modes:
          - Weather markets in DB but below liquidity threshold (min_liquidity=0 bypass)
          - Weather markets on Polymarket but not yet ingested (Gamma API probe)
        """
        found: List[Dict] = []

        # 1. DB query with zero liquidity floor and weather category filter
        try:
            db_weather = await self.base_engine.get_all_tradeable_markets(
                min_liquidity=0, categories=["weather"]
            )
            matched = [m for m in db_weather if self._market_mapper.is_weather_market(m)]
            if matched:
                logger.info(
                    "weatherbot_direct_db_found",
                    count=len(matched),
                    note="bypassed liquidity floor with category=weather",
                )
                found.extend(matched)
        except Exception as exc:
            logger.debug("weatherbot_direct_db_failed", error=str(exc))

        if found:
            # Weather markets in DB have yes_price=NULL (not in 1000-token WS subscription).
            # Fetch live prices from Gamma API so compute_edges() gets real midpoints,
            # not the 0.0 fallback that causes every bucket to be skipped (price <= 0 guard).
            found = await self._enrich_with_live_prices(found)
            return found

        # 2. Gamma API direct probe with category=weather
        try:
            client = getattr(self.base_engine, "client", None)  # PolymarketClient attr name
            if client:
                direct = await client.get_markets(active=True, limit=500, category="weather")
                matched_api = [m for m in direct if self._market_mapper.is_weather_market(m)]
                if matched_api:
                    logger.info(
                        "weatherbot_direct_api_found",
                        count=len(matched_api),
                        note="Gamma API category=weather probe",
                    )
                    found.extend(matched_api)
                elif direct:
                    # API returned weather-category markets but none match our regex — log sample
                    sample = [(m.get("question") or "")[:70] for m in direct[:5]]
                    logger.info(
                        "weatherbot_api_probe_regex_miss",
                        category_markets=len(direct),
                        sample_questions=sample,
                        note="weather markets exist but regex did not match — check market_mapper.py",
                    )
                else:
                    logger.info(
                        "weatherbot_api_probe_empty",
                        note="Gamma API returned 0 weather-category markets — seasonal gap likely",
                    )
        except Exception as exc:
            logger.debug("weatherbot_direct_api_failed", error=str(exc))

        return found

    async def _fetch_weather_events_by_tag(self) -> List[Dict]:
        """Fetch live temperature-bucket markets via Gamma API tag_slug=temperature.

        The standard ingestion pipeline fetches events by ID order and stops after
        ~1000 markets. Temperature events (ID ~249000+) are on page 45+ and never
        reached. This method queries events by tag_slug directly.

        Returns market dicts with yes_price/no_price pre-populated from outcomePrices,
        eliminating the need for per-token CLOB midpoint enrichment.
        """
        client = getattr(self.base_engine, "client", None)
        if not client:
            return []

        try:
            # Gamma API supports tag_slug filter on /events endpoint
            # S101b: Paginate to fetch ALL events (was limit=100, missing overflow)
            import httpx
            url = f"{client.gamma_api}/events"
            _MAX_PAGES = 5  # Safety valve: 500 events max
            events: list = []
            _pages_fetched = 0
            async with httpx.AsyncClient(timeout=15.0) as http:
                for _page in range(_MAX_PAGES):
                    params = {
                        "active": "true",
                        "closed": "false",
                        "tag_slug": "temperature",
                        "limit": "100",
                        "offset": str(_page * 100),
                    }
                    resp = await http.get(url, params=params)
                    if resp.status_code != 200:
                        if _page == 0:
                            logger.warning("weatherbot_tag_fetch_failed", status=resp.status_code)
                            _alerting = getattr(self.base_engine, "alerting_system", None)
                            if _alerting:
                                await _alerting.send_alert(
                                    title="WeatherBot Tag Fetch Failed",
                                    message=f"Gamma API tag_slug=temperature returned {resp.status_code}.",
                                    severity=AlertSeverity.WARNING,
                                    source="WeatherBot",
                                    metadata={"status_code": resp.status_code},
                                )
                            return []
                        break  # Non-first page failure — use what we have
                    page_data = resp.json()
                    if not isinstance(page_data, list) or len(page_data) == 0:
                        break
                    events.extend(page_data)
                    _pages_fetched = _page + 1
                    if len(page_data) < 100:
                        break  # Last page (partial)
        except Exception as exc:
            logger.warning("weatherbot_tag_fetch_error", error=str(exc))
            if not events:
                return []
            # Use partial results if we have any

        markets: List[Dict] = []
        for evt in events:
            if not isinstance(evt, dict):
                continue
            evt_markets = evt.get("markets") or []
            if isinstance(evt_markets, str):
                try:
                    evt_markets = json.loads(evt_markets)
                except (json.JSONDecodeError, ValueError):
                    continue

            for m in evt_markets:
                if not isinstance(m, dict):
                    continue
                # Skip closed/resolved individual markets
                if m.get("closed"):
                    continue

                # Parse outcomePrices: '["0.31", "0.69"]' → yes_price=0.31
                outcome_prices = m.get("outcomePrices")
                yes_price = 0.0
                no_price = 0.0
                if isinstance(outcome_prices, str):
                    try:
                        prices = json.loads(outcome_prices)
                        if isinstance(prices, list) and len(prices) >= 2:
                            yes_price = float(prices[0])
                            no_price = float(prices[1])
                    except (json.JSONDecodeError, ValueError, IndexError):
                        pass
                elif isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                    try:
                        yes_price = float(outcome_prices[0])
                        no_price = float(outcome_prices[1])
                    except (ValueError, TypeError):
                        pass

                # Parse clobTokenIds: '["token1", "token2"]' → yes_token_id, no_token_id
                clob_tokens = m.get("clobTokenIds")
                yes_token_id = ""
                no_token_id = ""
                if isinstance(clob_tokens, str):
                    try:
                        tokens = json.loads(clob_tokens)
                        if isinstance(tokens, list) and len(tokens) >= 2:
                            yes_token_id = str(tokens[0])
                            no_token_id = str(tokens[1])
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif isinstance(clob_tokens, list) and len(clob_tokens) >= 2:
                    yes_token_id = str(clob_tokens[0])
                    no_token_id = str(clob_tokens[1])

                market_dict = {
                    "id": str(m.get("conditionId") or m.get("id", "")),
                    "question": m.get("question") or "",
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "yes_token_id": yes_token_id,
                    "no_token_id": no_token_id,
                    "volume": m.get("volumeNum") or m.get("volume") or 0,
                    "active": True,
                    "category": "weather",
                    "slug": m.get("slug") or "",
                    "condition_id": m.get("conditionId") or "",
                }
                markets.append(market_dict)

        if markets:
            logger.info(
                "weatherbot_tag_discovery",
                events=len(events),
                markets=len(markets),
                priced=sum(1 for m in markets if 0 < m["yes_price"] < 1),
                pages_fetched=_pages_fetched,
            )
        return markets

    async def _enrich_with_live_prices(self, markets: List[Dict]) -> List[Dict]:
        """Fetch live yes_price / no_price from CLOB /midpoint for markets with NULL DB prices.

        Weather markets have yes_price=NULL in the DB because their token IDs are not
        included in the 1000-token WebSocket subscription (they have liquidity=0).
        Without a real price, compute_edges() skips every bucket (price <= 0.0 guard).

        Calls CLOB /midpoint per market's yes_token_id — only runs once per 30 min
        (rate-limited by _fetch_weather_markets_direct's _last_direct_probe timer).
        Capped at 200 markets (was 50) to avoid missing mispriced buckets.

        Note: Gamma API /markets/{id} rejects hex condition IDs (DB id format).
        CLOB /midpoint accepts the numeric yes_token_id and returns {"mid": "0.48"}.
        """
        client = getattr(self.base_engine, "client", None)
        if not client:
            return markets

        # B3: Parallelize CLOB midpoint calls with semaphore (10 concurrent)
        sem = asyncio.Semaphore(10)

        async def _fetch_midpoint(m: Dict) -> Dict:
            existing = m.get("yes_price")
            if existing and 0.0 < float(existing) < 1.0:
                return m
            yes_token_id = str(m.get("yes_token_id") or "")
            if not yes_token_id:
                return m
            async with sem:
                try:
                    yes_p = await client.get_token_midpoint(yes_token_id)
                    if yes_p is not None:
                        m = dict(m)
                        m["yes_price"] = yes_p
                        m["no_price"] = round(1.0 - yes_p, 6)
                        m["_enriched"] = True
                except Exception as exc:
                    logger.debug(
                        "weatherbot_price_enrich_error",
                        yes_token_id=yes_token_id[:20],
                        error=str(exc),
                    )
            return m

        enriched = await asyncio.gather(*[_fetch_midpoint(m) for m in markets[:200]])
        enriched_count = sum(1 for m in enriched if m.get("_enriched"))
        # Clean up transient flag
        for m in enriched:
            m.pop("_enriched", None)

        logger.info(
            "weatherbot_price_enriched",
            total=len(enriched),
            enriched=enriched_count,
            skipped=len(enriched) - enriched_count,
        )
        return list(enriched)

    async def _save_backoff_to_redis(self) -> None:
        """Persist adaptive backoff counter to Redis so it survives restarts."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            await cache.set("weatherbot:consecutive_no_edge", self._consecutive_no_edge, ttl=3600)
        except Exception as exc:
            logger.debug("weatherbot_redis_backoff_save_failed", error=str(exc))

    async def _restore_backoff_from_redis(self) -> None:
        """Reload adaptive backoff counter from Redis on startup."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            val = await cache.get("weatherbot:consecutive_no_edge")
            if val is not None:
                self._consecutive_no_edge = int(val)
                logger.info("weatherbot_backoff_restored", consecutive_no_edge=self._consecutive_no_edge)
        except Exception as exc:
            logger.debug("weatherbot_redis_backoff_restore_failed", error=str(exc))

    async def _save_exit_to_redis(self, market_id: str) -> None:
        """Persist a recent-exit event to Redis with configurable TTL so it survives restarts."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            _ttl = int(self._exit_cooldown_secs)
            expire_at = time.time() + float(_ttl)
            await cache.set(f"weatherbot:exit:{market_id}", expire_at, ttl=_ttl)
        except Exception as exc:
            logger.debug("weatherbot_redis_exit_save_failed", error=str(exc))

    async def _restore_exits_from_redis(self) -> None:
        """Reload _recently_exited cooldowns from Redis on startup."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            keys = await cache.redis.keys("weatherbot:exit:*")
            now_wall = time.time()
            now_mono = time.monotonic()
            count = 0
            for key in keys:
                raw = await cache.get(key)
                if raw is None:
                    continue
                expire_at = float(raw)
                if expire_at <= now_wall:
                    continue  # cooldown already expired
                elapsed = self._exit_cooldown_secs - (expire_at - now_wall)
                mid = key.split("weatherbot:exit:", 1)[-1]
                self._recently_exited[mid] = now_mono - elapsed
                count += 1
            if count:
                logger.info("weatherbot_exits_restored", count=count)
        except Exception as exc:
            logger.warning("weatherbot_restore_exits_failed", error=str(exc))

    async def _restore_exposure_from_db(self) -> None:
        """S104: Rebuild _group_exposure and _city_exposure from daily_counters.

        Replaces the old paper_trades JOIN + question parsing approach with
        EsportsBot's proven daily_counter pattern (<10ms vs 50-200ms).
        Called once on startup (inside the _cache_warmed block).
        S105b: Clamp negative counters to 0 in DB — they occur when exits from
        yesterday's positions decrement today's counters (which start at 0).
        Fail-open: any error logs at debug level and continues with empty dicts.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            counters = await _restore_daily(db, "WeatherBot")
            rebuilt_groups = 0
            negative_clamped = 0
            for name, value in counters.items():
                if value < 0:
                    negative_clamped += 1
                    value = 0.0  # 2E: treat negative as 0 so in-memory dict gets the entry
                if value == 0:
                    continue
                if name.startswith("group_"):
                    self._group_exposure[name[6:]] = value
                    rebuilt_groups += 1
                elif name.startswith("city_"):
                    self._city_exposure[name[5:]] = value
            # S105b: Clamp negative counters in DB so table stays clean for auditing
            if negative_clamped > 0:
                try:
                    from sqlalchemy import text as sa_text
                    async with db.get_session() as session:
                        await session.execute(sa_text(
                            "UPDATE daily_counters SET counter_value = 0 "
                            "WHERE bot_id = 'WeatherBot' AND counter_date = CURRENT_DATE "
                            "AND counter_value < 0"
                        ))
                        await session.commit()
                    logger.info("weatherbot_negative_counters_clamped", count=negative_clamped)
                except Exception as exc:
                    logger.warning("weatherbot_negative_counter_clamp_failed", count=negative_clamped, exc=str(exc))
            if rebuilt_groups or self._city_exposure:
                logger.info(
                    "weatherbot_exposure_restored",
                    groups=rebuilt_groups,
                    cities=len(self._city_exposure),
                )
        except Exception as exc:
            logger.debug("weatherbot_exposure_restore_failed", error=str(exc))

    async def _rebuild_market_group_cache(self) -> None:
        """S104: Rebuild _market_group_cache from open positions on startup.

        Populates market_id → (group_key, city, cost_usd) so exit exposure
        decrements work for positions opened before this session started.
        Fail-open: if query or parsing fails, exits without cache will log
        weatherbot_pm_exit_no_cache (non-fatal, exposure just won't decrement).
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            async with db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT p.market_id, m.question, p.size, p.entry_price
                    FROM positions p
                    JOIN markets m ON (p.market_id = CAST(m.id AS TEXT) OR p.market_id = m.condition_id)
                    WHERE (p.source_bot = 'WeatherBot' OR p.bot_id = 'WeatherBot')
                      AND p.status = 'open'
                """))
                rows = result.fetchall()
            count = 0
            for market_id, question, size, entry_price in rows:
                if not question:
                    continue
                city_text, target_date = self._market_mapper._extract_city_and_date(question)
                if not city_text or not target_date:
                    continue
                group_key = f"{city_text}:{target_date.isoformat()}"
                cost_usd = float(size or 0) * float(entry_price or 0)
                self._market_group_cache[market_id] = (group_key, city_text, cost_usd)
                count += 1
            if count:
                logger.info("weatherbot_market_group_cache_rebuilt", count=count)
        except Exception as exc:
            logger.debug("weatherbot_market_group_cache_rebuild_failed", error=str(exc))

    async def _check_weather_market_availability(self) -> None:
        """One-time startup log of weather market availability across DB and Gamma API.

        Runs once on first scan_and_trade() call. Provides immediate visibility
        into whether the silence is a code issue or a seasonal market gap.
        """
        self._startup_check_done = True
        try:
            # All tradeable markets (no category filter — for total context)
            all_markets = await self.base_engine.get_all_tradeable_markets()
            # Weather-category markets with no liquidity floor (same query as main scan)
            weather_markets = await self.base_engine.get_all_tradeable_markets(
                min_liquidity=0, categories=["weather"]
            )
            # Subset recognised by regex as actual temperature bucket markets
            weather_regex_match = sum(
                1 for m in weather_markets if self._market_mapper.is_weather_market(m)
            )
            logger.info(
                "weatherbot_startup_availability",
                db_total=len(all_markets),
                db_weather_category=len(weather_markets),
                db_weather_regex_match=weather_regex_match,
            )
        except Exception as exc:
            logger.debug("weatherbot_startup_check_failed", error=str(exc))

    # ── DB helpers (P1, P2, P3) ──────────────────────────────────────────

    async def _handle_daily_boundary(self) -> None:
        """Reset daily state at UTC day boundary; restore P&L from DB (P2)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_pnl_date == today:
            return

        # New day — reset in-memory state
        self._daily_pnl = 0.0
        self._daily_pnl_date = today
        self._group_exposure.clear()
        self._city_exposure.clear()
        self._market_group_cache.clear()  # S104: stale date mappings from yesterday

        # P2: Restore today's realized P&L from paper_trades so restarts
        # don't reset the daily loss limit check to $0.
        await self._restore_daily_pnl_from_db()

        # Calibration feedback: fill actual_temp for past forecast rows so
        # bias correction accumulates over time.
        await self._maybe_update_calibration_actuals()

    async def _restore_daily_pnl_from_db(self) -> None:
        """Query today's WeatherBot realized P&L from trade_events DB."""
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_start = datetime.strptime(today_str, "%Y-%m-%d")  # naive UTC midnight
            async with db.get_session() as session:
                from sqlalchemy import text
                # S134: Only use EXIT events for daily P&L — RESOLUTION events
                # are corrupted by Phase 4b paper_trades UPSERT bug.
                result = await session.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0.0)
                    FROM trade_events
                    WHERE bot_name = 'WeatherBot'
                      AND event_type = 'EXIT'
                      AND realized_pnl IS NOT NULL
                      AND event_time >= :today_start
                """), {"today_start": today_start})
                row = result.fetchone()
                if row and row[0] is not None:
                    self._daily_pnl = float(row[0])
                    if self._daily_pnl != 0.0:
                        logger.info(
                            "weatherbot_daily_pnl_restored",
                            pnl=round(self._daily_pnl, 2),
                        )
        except Exception as exc:
            logger.debug("weatherbot_daily_pnl_restore_failed", error=str(exc))

    async def _maybe_update_calibration_actuals(self) -> None:
        """Fetch actual historical temperatures for past forecast rows and update bias.

        Queries weather_calibration for rows where actual_temp IS NULL and
        target_date is in the past, then fetches actual daily-max from Open-Meteo
        archive and stores actual_temp + bias = actual_temp - forecast_temp.

        Called once per UTC day boundary. Non-fatal: errors are logged and skipped.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            from sqlalchemy import text
            from base_engine.weather.station_registry import STATION_REGISTRY

            # Build station_id → station map for coordinate lookup
            station_map = {s.station_id: s for s in STATION_REGISTRY.values()}

            async with db.get_session() as session:
                result = await session.execute(text("""
                    SELECT id, station_id, target_date, forecast_temp, lead_time_hours
                    FROM weather_calibration
                    WHERE actual_temp IS NULL
                      AND target_date < NOW() - INTERVAL '1 day'
                    ORDER BY target_date DESC
                    LIMIT 50
                """))
                rows = result.fetchall()

            if not rows:
                return

            logger.info(
                "weatherbot_calibration_actuals_pending",
                rows=len(rows),
            )

            updated = 0
            for row_id, station_id, target_dt, forecast_temp, lead_time_hours in rows:
                station = station_map.get(station_id)
                if not station:
                    continue

                target_date = target_dt.date() if hasattr(target_dt, "date") else target_dt
                # B1: Prefer Weather Underground (Polymarket resolution source)
                wu_temp = await self._fetch_wu_daily_high(station, target_date)
                om_temp = await self._forecast_client.get_historical_temperature(
                    latitude=station.latitude,
                    longitude=station.longitude,
                    target_date=target_date,
                    temp_unit=station.temp_unit,
                )
                # Use WU when available (resolution source); fall back to Open-Meteo
                # Sanity check: reject WU values that differ from Open-Meteo by
                # more than 10°F/5°C — likely a scraping error, not a real value.
                max_diff = 10.0 if station.temp_unit.upper() == "F" else 5.0
                if wu_temp is not None and om_temp is not None:
                    diff = abs(wu_temp - om_temp)
                    if diff > max_diff:
                        logger.warning(
                            "weatherbot_wu_sanity_rejected",
                            station=station_id,
                            date=str(target_date),
                            wu=wu_temp, om=om_temp, diff=round(diff, 1),
                            threshold=max_diff,
                        )
                        wu_temp = None  # Fall back to Open-Meteo
                    elif diff > 1.0:
                        logger.warning(
                            "weatherbot_wu_om_discrepancy",
                            station=station_id,
                            date=str(target_date),
                            wu=wu_temp, om=om_temp, diff=round(diff, 1),
                        )
                actual_temp = wu_temp if wu_temp is not None else om_temp
                if actual_temp is None:
                    continue

                bias = actual_temp - forecast_temp

                # W8: CRPS scoring — evaluate full ensemble distribution
                crps_val = await self._compute_crps(
                    db, station_id, target_date, actual_temp,
                )

                async with db.get_session() as session:
                    await session.execute(text("""
                        UPDATE weather_calibration
                        SET actual_temp = :actual_temp,
                            bias = :bias,
                            crps = :crps
                        WHERE id = :row_id
                    """), {
                        "actual_temp": actual_temp,
                        "bias": bias,
                        "crps": round(crps_val, 4) if crps_val is not None else None,
                        "row_id": row_id,
                    })
                    await session.commit()

                if crps_val is not None:
                    logger.debug(
                        "weatherbot_crps",
                        station=station_id,
                        date=str(target_date),
                        crps=round(crps_val, 3),
                        bias=round(bias, 2),
                    )
                updated += 1

            if updated:
                logger.info(
                    "weatherbot_calibration_actuals_updated",
                    updated=updated,
                    total_pending=len(rows),
                )
        except Exception as exc:
            logger.debug("weatherbot_calibration_actuals_failed", error=str(exc))

    async def _fetch_wu_daily_high(
        self, station: WeatherStation, target_date,
    ) -> Optional[float]:
        """B1: Scrape Weather Underground history page for daily high temperature.

        WU is the resolution source for Polymarket temperature markets.
        URL: https://www.wunderground.com/history/daily/{ICAO}/date/{YYYY-M-D}
        Parses the daily high from the "Max" row in the history table.
        Returns temperature in station's native unit (F or C), or None on failure.
        """
        import re

        icao = station.station_id
        d = target_date
        url = f"https://www.wunderground.com/history/daily/{icao}/date/{d.isoformat()}"

        try:
            session = await self._forecast_client.get_session()
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.wunderground.com/",
                },
            ) as resp:
                if resp.status != 200:
                    logger.debug("weatherbot_wu_http_error", station=icao, status=resp.status)
                    return None
                html = await resp.text()

            # WU history pages show "Max Temperature" in a summary table.
            # The page uses Angular but embeds observation data in initial HTML.
            # Try 4 patterns in order of reliability.

            # Pattern 1: "Max</span>" or "Max Temperature</span>" … °value
            match = re.search(
                r'Max\s*(?:Temperature)?</span>.*?(-?\d+(?:\.\d+)?)\s*°',
                html, re.DOTALL | re.IGNORECASE,
            )
            if match:
                temp_val = float(match.group(1))
                logger.debug("weatherbot_wu_actual", station=icao, date=str(target_date), temp=temp_val)
                return temp_val

            # Pattern 2: JSON property "maxTemp" or "maxTemperature"
            match2 = re.search(
                r'"maxTemp(?:erature)?"\s*:\s*\{\s*"[^"]*"\s*:\s*(-?\d+(?:\.\d+)?)',
                html, re.DOTALL | re.IGNORECASE,
            )
            if match2:
                temp_val = float(match2.group(1))
                logger.debug("weatherbot_wu_actual_p2", station=icao, date=str(target_date), temp=temp_val)
                return temp_val

            # Pattern 3: Angular state dump — "High": value near temperature context
            match3 = re.search(
                r'"(?:High|Maximum)\s*Temperature[^"]*"\s*[,:].*?(-?\d{2,3}(?:\.\d+)?)',
                html, re.DOTALL | re.IGNORECASE,
            )
            if match3:
                temp_val = float(match3.group(1))
                logger.debug("weatherbot_wu_actual_p3", station=icao, date=str(target_date), temp=temp_val)
                return temp_val

            # Pattern 4: observation summary JSON block (WU server-side state)
            match4 = re.search(
                r'"observationSummary".*?["\s]Max["\s].*?(-?\d{2,3}(?:\.\d+)?)',
                html, re.DOTALL | re.IGNORECASE,
            )
            if match4:
                temp_val = float(match4.group(1))
                logger.debug("weatherbot_wu_actual_p4", station=icao, date=str(target_date), temp=temp_val)
                return temp_val

            logger.debug("weatherbot_wu_no_match", station=icao, date=str(target_date), html_len=len(html))

        except Exception as exc:
            logger.debug("weatherbot_wu_fetch_failed", station=icao, error=str(exc))
        return None

    @staticmethod
    async def _compute_crps(db, station_id: str, target_date, actual_temp: float) -> Optional[float]:
        """W8: Compute CRPS (Continuous Ranked Probability Score) for this forecast.

        CRPS evaluates the full ensemble distribution, not just the point forecast.
        Formula: CRPS = (1/M) * Σ|x_i - y| - (1/(2M²)) * Σ|x_i - x_j|
        where x_i are ensemble members and y is the observed temperature.
        Lower CRPS = better calibrated distribution. Perfect score = 0.0.

        Returns None if ensemble members not found in weather_forecasts table.
        """
        try:
            from sqlalchemy import text
            target_d = target_date.date() if hasattr(target_date, "date") else target_date
            async with db.get_session() as session:
                result = await session.execute(text("""
                    SELECT ensemble_members
                    FROM weather_forecasts
                    WHERE station_id = :station_id
                      AND target_date = :target_date
                    ORDER BY forecast_time DESC
                    LIMIT 1
                """), {
                    "station_id": station_id,
                    "target_date": target_d,
                })
                row = result.fetchone()
                if not row or not row[0]:
                    return None

                members = row[0]  # JSONB → list of floats
                if isinstance(members, str):
                    members = json.loads(members)
                if not members or len(members) < 2:
                    return None

            # CRPS computation (Ferro 2014 fair CRPS for ensemble):
            # CRPS = (1/M) * Σ|x_i - y| - (1/(2M²)) * Σ|x_i - x_j|
            m = len(members)
            abs_diff_obs = sum(abs(x - actual_temp) for x in members) / m

            # Pairwise term: efficient O(M log M) via sorted order
            sorted_members = sorted(members)
            pairwise_sum = 0.0
            for i, xi in enumerate(sorted_members):
                # Contribution of xi to pairwise sum:
                # sum_{j>i} (x_j - x_i) = (M - i - 1) * x_i subtracted from partial sum
                pairwise_sum += (2 * i - m + 1) * xi
            pairwise_term = abs(pairwise_sum) / (m * m)

            crps = abs_diff_obs - pairwise_term
            return max(0.0, crps)

        except Exception:
            return None

    @staticmethod
    def _near_boundary(loc: float, bucket, threshold: float = 0.5) -> bool:
        """Return True if the ensemble mean is within threshold of a bucket boundary.

        When the model's expected temperature is close to a bracket boundary, the
        resolution outcome becomes sensitive to the data source (WU hourly max vs
        NWS official daily high). A 0.5°F/°C gap between WU and NWS can flip the
        result — the Dec 2025 NYC incident (WU=29°F vs NWS=30°F) is the canonical
        example. Caller should reduce position size when this flag is True.

        L1: Threshold scaled by unit — 0.5°F for US, 0.3°C for international.
        0.5°C ≈ 0.9°F was too tight for Celsius markets.

        Args:
            loc:       EMOS-corrected ensemble mean (°F or °C)
            bucket:    TemperatureBucket with low_bound/high_bound
            threshold: Distance from boundary that triggers the flag (default 0.5°)
        """
        # L1: Scale threshold by temperature unit
        t = 0.5 if bucket.temp_unit == "F" else 0.3
        btype = bucket.bucket_type
        if btype == "at_or_below":
            return abs(loc - bucket.high_bound) <= t
        elif btype == "at_or_higher":
            return abs(loc - bucket.low_bound) <= t
        elif btype in ("range", "exact"):
            near_low = abs(loc - bucket.low_bound) <= t
            near_high = abs(loc - bucket.high_bound) <= t
            return near_low or near_high
        return False

    @staticmethod
    def _fit_emos(
        pairs: List[Tuple[float, float]],
    ) -> Tuple[float, float, float]:
        """OLS regression: actual_temp = a + b * forecast_temp.

        Returns (a, b, sigma) where:
            a     — intercept (systematic bias correction)
            b     — slope    (b≠1 corrects under/over-forecast spread)
            sigma — residual std (replaces raw ensemble spread in probability_engine)

        Requires ≥2 pairs. Falls back to identity (a=0, b=1, sigma=2.0) on
        degenerate input (all forecast temps identical → singular OLS system).
        """
        n = len(pairs)
        if n < 2:
            return (0.0, 1.0, 2.0)

        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]
        sx = sum(x_vals)
        sy = sum(y_vals)
        sxx = sum(x * x for x in x_vals)
        sxy = sum(x * y for x, y in pairs)

        denom = n * sxx - sx * sx
        if abs(denom) < 1e-10:
            # Degenerate: all forecast temps identical → simple mean bias
            mean_bias = (sy - sx) / n
            return (mean_bias, 1.0, 2.0)

        b = (n * sxy - sx * sy) / denom
        a = (sy - b * sx) / n

        # Residual std (σ correction for spread underdispersion)
        residuals = [y - (a + b * x) for x, y in pairs]
        mean_res = sum(residuals) / n
        var_res = sum((r - mean_res) ** 2 for r in residuals) / max(n - 1, 1)
        sigma = max(var_res ** 0.5, 0.5)  # Floor at 0.5° to avoid overconfidence

        return (a, b, sigma)

    @staticmethod
    def _fit_samos(
        pairs: List[Tuple[float, float, float, float]],
    ) -> Optional[Tuple[float, float, float]]:
        """S115: SAMOS (Standardized Anomaly MOS) fitting.

        Input: list of (forecast_temp, actual_temp, clim_mean, clim_std) tuples.
        Normalizes: anomaly = (x - clim_mean) / clim_std before OLS.
        Returns (a, b, sigma) in ANOMALY SPACE (caller must de-normalize).

        Returns None if < 2 valid pairs (insufficient data for SAMOS).
        Raw EMOS fallback should be used instead.
        """
        # Filter to pairs with valid climatology (clim_std > 0)
        valid = [(f, a, cm, cs) for f, a, cm, cs in pairs if cs and cs > 0.5]
        if len(valid) < 2:
            return None

        # Normalize to anomalies
        x_anom = [(f - cm) / cs for f, _, cm, cs in valid]
        y_anom = [(a - cm) / cs for _, a, cm, cs in valid]

        n = len(valid)
        sx = sum(x_anom)
        sy = sum(y_anom)
        sxx = sum(x * x for x in x_anom)
        sxy = sum(x * y for x, y in zip(x_anom, y_anom))

        denom = n * sxx - sx * sx
        if abs(denom) < 1e-10:
            mean_bias = (sy - sx) / n
            return (mean_bias, 1.0, 1.0)

        b = (n * sxy - sx * sy) / denom
        a = (sy - b * sx) / n

        residuals = [y - (a + b * x) for x, y in zip(x_anom, y_anom)]
        mean_res = sum(residuals) / n
        var_res = sum((r - mean_res) ** 2 for r in residuals) / max(n - 1, 1)
        sigma = max(var_res ** 0.5, 0.3)  # Floor at 0.3 anomaly units

        return (a, b, sigma)

    async def _get_enso_regime(self) -> str:
        """Fetch current ENSO regime from NOAA PSL Nino 3.4 SST anomaly data.

        Classifies the latest monthly Nino 3.4 SST anomaly:
          - ONI >= +0.5  → "el_nino"
          - ONI <= -0.5  → "la_nina"
          - |ONI| < 0.5  → "neutral"

        Cached for 24h (SST anomalies are monthly). Falls back to "neutral"
        on any fetch error so calibration rows are never left without a tag.
        """
        now_mono = time.monotonic()
        if self._regime_tag and now_mono - self._regime_last_fetched < self._regime_cache_ttl:
            return self._regime_tag

        import aiohttp
        url = "https://psl.noaa.gov/data/correlation/nina34.anom.data"
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.debug("enso_regime_fetch_error", status=resp.status)
                        self._regime_tag = self._regime_tag or "neutral"
                        self._regime_last_fetched = now_mono
                        return self._regime_tag
                    text = await resp.text()

            # Parse: each data line is "YYYY val1 val2 ... val12"
            # Find latest non-missing value (not -99.99)
            latest_oni: Optional[float] = None
            for line in text.strip().splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    int(parts[0])  # year check
                except ValueError:
                    continue
                for val_str in reversed(parts[1:]):
                    try:
                        val = float(val_str)
                        if val > -90.0:  # -99.99 = missing
                            latest_oni = val
                            break
                    except ValueError:
                        continue
                if latest_oni is not None:
                    # Don't break — keep scanning to get the truly latest year's data
                    pass

            if latest_oni is None:
                self._regime_tag = "neutral"
            elif latest_oni >= 0.5:
                self._regime_tag = "el_nino"
            elif latest_oni <= -0.5:
                self._regime_tag = "la_nina"
            else:
                self._regime_tag = "neutral"

            self._regime_last_fetched = now_mono
            logger.info(
                "enso_regime_fetched",
                regime=self._regime_tag,
                oni=latest_oni,
            )
        except Exception as exc:
            logger.debug("enso_regime_fetch_failed", error=str(exc))
            self._regime_tag = self._regime_tag or "neutral"
            self._regime_last_fetched = now_mono

        return self._regime_tag

    async def _prefetch_severe_weather_alerts(
        self, groups: list,
    ) -> None:
        """Batch-fetch NWS severe weather alerts for all US stations in one scan.

        Instead of N individual API calls during trade execution, fetch once
        per scan and cache per-station. 30-min TTL — skip refetch if recent.
        """
        now_mono = time.monotonic()
        if now_mono - self._severe_weather_batch_time < 1800.0:
            return  # batch still fresh

        import aiohttp

        _HIGH_IMPACT = {
            "Hurricane Warning", "Hurricane Watch",
            "Tropical Storm Warning", "Extreme Wind Warning",
        }
        _MED_IMPACT = {
            "Severe Thunderstorm Warning", "Tornado Warning",
            "Winter Storm Warning", "Ice Storm Warning",
            "Blizzard Warning",
        }

        # Collect unique US stations from active groups
        us_stations: Dict[str, WeatherStation] = {}
        for g in groups:
            if g.station and g.station.temp_unit.upper() == "F":
                us_stations[g.station.station_id] = g.station

        if not us_stations:
            self._severe_weather_batch_time = now_mono
            return

        batch: Dict[str, float] = {}
        try:
            session = await self._forecast_client.get_session()

            async def _fetch_alert(sid: str, station: WeatherStation) -> Tuple[str, float, List[str]]:
                url = f"https://api.weather.gov/alerts/active?point={station.latitude:.4f},{station.longitude:.4f}"
                try:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=8),
                        headers={
                            "Accept": "application/geo+json",
                            "User-Agent": "PolymarketWeatherBot/1.0",
                        },
                    ) as resp:
                        if resp.status != 200:
                            return sid, 1.0, []
                        data = await resp.json(content_type=None)
                    boost = 1.0
                    events_found: List[str] = []
                    for feat in data.get("features", []):
                        event = feat.get("properties", {}).get("event", "")
                        if event:
                            events_found.append(event)
                        if event in _HIGH_IMPACT:
                            boost = max(boost, 2.0)
                        elif event in _MED_IMPACT:
                            boost = max(boost, 1.5)
                    if boost > 1.0:
                        logger.info(
                            "weatherbot_severe_weather_boost",
                            station=sid, boost=boost, events=events_found,
                        )
                    return sid, boost, events_found
                except Exception:
                    return sid, 1.0, []

            _alert_sem = asyncio.Semaphore(20)

            async def _bounded_fetch(sid: str, st: WeatherStation) -> Tuple[str, float]:
                async with _alert_sem:
                    return await _fetch_alert(sid, st)

            results = await asyncio.gather(
                *[_bounded_fetch(sid, st) for sid, st in us_stations.items()],
                return_exceptions=True,
            )
            events_map: Dict[str, List[str]] = {}
            for r in results:
                if isinstance(r, Exception):
                    continue
                batch[r[0]] = r[1]
                events_map[r[0]] = r[2]
        except Exception as exc:
            logger.debug("nws_alerts_batch_failed", error=str(exc))
            events_map = {}

        self._severe_weather_batch = batch
        self._severe_weather_events = events_map
        self._severe_weather_batch_time = now_mono

    async def _get_severe_weather_boost(self, station: WeatherStation) -> float:
        """Return cached severe weather boost for a station.

        Uses batch-prefetched data from _prefetch_severe_weather_alerts().
        Falls back to 1.0 for non-US stations or missing cache.
        """
        if station.temp_unit.upper() != "F":
            return 1.0
        return self._severe_weather_batch.get(station.station_id, 1.0)

    def _should_halt_severe_weather(self, station: WeatherStation) -> Optional[str]:
        """S115: Check if active alerts require halting trades for this station.

        Returns the halt-triggering event name if trading should be suspended,
        or None if trading can proceed. Configurable via WEATHER_SEVERE_HALT_EVENTS.

        Default halt events: Hurricane Warning, Tornado Warning, Extreme Wind Warning.
        These events make forecasts unreliable — sizing UP is wrong; halting is correct.
        """
        if station.temp_unit.upper() != "F":
            return None  # NWS alerts are US-only

        _halt_events_str = getattr(
            settings, "WEATHER_SEVERE_HALT_EVENTS",
            "Hurricane Warning,Tornado Warning,Extreme Wind Warning",
        )
        _halt_events = {e.strip() for e in _halt_events_str.split(",") if e.strip()}

        station_events = self._severe_weather_events.get(station.station_id, [])
        for event in station_events:
            if event in _halt_events:
                return event
        return None

    async def _get_afd_spread_factor(self, station: WeatherStation) -> float:
        """Parse latest NWS Area Forecast Discussion for uncertainty signals.

        Returns a spread adjustment factor:
          - > 1.0: AFD indicates above-normal uncertainty (widen spread)
          - < 1.0: AFD indicates high confidence (tighten spread)
          - 1.0: no signal or non-US station

        US stations only. Cached per station for 6 hours.
        """
        if station.temp_unit.upper() != "F":
            return 1.0  # AFDs only available for US stations

        # Check cache
        cached = self._afd_cache.get(station.station_id)
        now_mono = time.monotonic()
        if cached and now_mono < cached[0]:
            return cached[1]

        # Get WFO from NWS /points
        wfo = await self._get_station_wfo(station)
        if not wfo:
            self._afd_cache[station.station_id] = (now_mono + 21600.0, 1.0)
            return 1.0

        # Fetch latest AFD
        import aiohttp
        url = f"https://api.weather.gov/products?type=AFD&location={wfo}&limit=1"
        factor = 1.0
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={
                    "Accept": "application/ld+json",
                    "User-Agent": "PolymarketWeatherBot/1.0",
                },
            ) as session:
                # Get list of recent AFDs
                async with session.get(url) as resp:
                    if resp.status != 200:
                        self._afd_cache[station.station_id] = (now_mono + 3600.0, 1.0)
                        return 1.0
                    data = await resp.json(content_type=None)

                # Get the latest AFD product
                products = data.get("@graph", [])
                if not products:
                    self._afd_cache[station.station_id] = (now_mono + 3600.0, 1.0)
                    return 1.0

                latest_url = products[0].get("@id", "")
                if not latest_url:
                    self._afd_cache[station.station_id] = (now_mono + 3600.0, 1.0)
                    return 1.0

                # Fetch the actual AFD text
                async with session.get(latest_url) as resp2:
                    if resp2.status != 200:
                        self._afd_cache[station.station_id] = (now_mono + 3600.0, 1.0)
                        return 1.0
                    afd_data = await resp2.json(content_type=None)

                afd_text = afd_data.get("productText", "").lower()

                # Parse for uncertainty/confidence signals
                factor = WeatherBot._parse_afd_uncertainty(afd_text)
        except Exception as exc:
            logger.debug("afd_fetch_failed", station=station.station_id, error=str(exc))

        self._afd_cache[station.station_id] = (now_mono + 21600.0, factor)
        if factor != 1.0:
            logger.info(
                "weatherbot_afd_spread_factor",
                station=station.station_id,
                wfo=wfo,
                factor=round(factor, 2),
            )
        return factor

    async def _get_station_wfo(self, station: WeatherStation) -> Optional[str]:
        """Get NWS WFO (Weather Forecast Office) code for a station.

        Calls NWS /points API once per station, caches permanently (WFOs are static).
        US stations only.
        """
        if station.station_id in self._wfo_cache:
            return self._wfo_cache[station.station_id]

        import aiohttp
        url = f"https://api.weather.gov/points/{station.latitude:.4f},{station.longitude:.4f}"
        wfo = None
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={
                    "Accept": "application/geo+json",
                    "User-Agent": "PolymarketWeatherBot/1.0",
                },
            ) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        wfo = data.get("properties", {}).get("gridId")
        except Exception as exc:
            logger.debug("wfo_lookup_failed", station=station.station_id, error=str(exc))

        self._wfo_cache[station.station_id] = wfo
        if wfo:
            logger.debug("wfo_cached", station=station.station_id, wfo=wfo)
        return wfo

    @staticmethod
    def _parse_afd_uncertainty(afd_text: str) -> float:
        """Scan AFD text for uncertainty and confidence keywords.

        Returns spread adjustment factor:
          - 1.3: strong uncertainty signals (≥3 net)
          - 1.15: moderate uncertainty (1-2 net)
          - 0.9: strong confidence signals (≤-2 net)
          - 1.0: neutral or mixed signals
        """
        import re

        # M2: Added negative lookahead to reduce false positives from
        # historical context ("in past data", "previous model runs", etc.)
        _HIST_GUARD = r"(?!\s+(?:in\s+(?:past|previous|historical)|last\s+(?:year|month|week)))"
        _HIGH_UNCERTAINTY = [
            r"model\s+(?:spread|disagreement|divergence)" + _HIST_GUARD,
            r"(?:significant|considerable|large)\s+uncertainty" + _HIST_GUARD,
            r"low\s+confidence",
            r"tricky\s+forecast",
            r"challenging\s+forecast",
            r"difficult\s+to\s+(?:forecast|predict)",
            r"uncertainty\s+(?:remains|persists|exists)",
            r"models?\s+(?:disagree|diverge|differ|split)" + _HIST_GUARD,
            r"wide\s+(?:range|spread)" + _HIST_GUARD,
            r"ensemble\s+spread" + _HIST_GUARD,
            r"bust\s+potential",
        ]

        _HIGH_CONFIDENCE = [
            r"high\s+confidence",
            r"good\s+(?:agreement|consensus)",
            r"models?\s+(?:agree|converge|in\s+agreement)",
            r"strong\s+confidence",
            r"well\s+(?:captured|handled)",
            r"confident\s+in",
        ]

        uncertainty_count = sum(
            len(re.findall(pattern, afd_text)) for pattern in _HIGH_UNCERTAINTY
        )
        confidence_count = sum(
            len(re.findall(pattern, afd_text)) for pattern in _HIGH_CONFIDENCE
        )

        # Net signal: positive = more uncertain, negative = more confident
        net = uncertainty_count - confidence_count

        if net >= 3:
            return 1.3   # Strong uncertainty → widen spread 30%
        elif net >= 1:
            return 1.15  # Moderate uncertainty → widen spread 15%
        elif net <= -2:
            return 0.9   # Strong confidence → tighten spread 10%

        return 1.0  # Neutral

    async def _maybe_reload_calibration(self) -> None:
        """Reload bias calibration from weather_calibration DB every 6 hours (P1)."""
        now_mono = time.monotonic()
        if now_mono - self._calibration_last_loaded < self._calibration_reload_interval:
            return

        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            async with db.get_session() as session:
                from sqlalchemy import text
                # S120: Rolling window — only use recent calibration data so
                # winter EMOS coefficients don't contaminate spring forecasts.
                _emos_window_days = int(getattr(settings, "WEATHER_EMOS_WINDOW_DAYS", 90))
                rows = await session.execute(text(f"""
                    SELECT station_id, lead_time_hours, bias, forecast_temp, actual_temp, regime,
                           target_date
                    FROM weather_calibration
                    WHERE bias IS NOT NULL AND actual_temp IS NOT NULL
                      AND created_at >= NOW() - INTERVAL '{_emos_window_days} days'
                """))
                all_rows = rows.fetchall()

            if not all_rows:
                self._calibration_last_loaded = now_mono
                return

            # S115: Load climatology from weather_climatology table (proper ERA5 normals)
            # for SAMOS normalization. Keyed by (station_id, day_of_year).
            _clim_lookup: Dict[Tuple[str, int], Tuple[float, float]] = {}
            try:
                async with db.get_session() as clim_session:
                    clim_rows = await clim_session.execute(text("""
                        SELECT station_id, day_of_year, clim_mean, clim_std
                        FROM weather_climatology
                    """))
                    for sid, doy, cm, cs in clim_rows.fetchall():
                        _clim_lookup[(sid, int(doy))] = (float(cm), float(cs))
            except Exception as clim_exc:
                logger.debug("weatherbot_climatology_load_failed", error=str(clim_exc))

            # Aggregate: station_id → {lead_bucket → {"biases": [...], "pairs": [(x, y)]}}
            # pairs = (forecast_temp, actual_temp) for EMOS OLS fitting
            raw: Dict[str, Dict[int, Dict[str, Any]]] = {}
            # Regime-aware aggregation: (station_id, regime) → {lead_bucket → {"pairs": [...]}}
            raw_regime: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
            current_regime = await self._get_enso_regime()

            for station_id, lt_hours, bias, forecast_temp, actual_temp, regime, target_date in all_rows:
                bucket = int(float(lt_hours) // 6) * 6
                if station_id not in raw:
                    raw[station_id] = {}
                if bucket not in raw[station_id]:
                    raw[station_id][bucket] = {"biases": [], "pairs": []}
                raw[station_id][bucket]["biases"].append(float(bias))
                if forecast_temp is not None and actual_temp is not None:
                    raw[station_id][bucket]["pairs"].append(
                        (float(forecast_temp), float(actual_temp))
                    )
                    # S115 SAMOS: look up real ERA5 climatology by (station, DOY)
                    if target_date is not None and _clim_lookup:
                        try:
                            _doy = target_date.timetuple().tm_yday
                            _clim = _clim_lookup.get((station_id, _doy))
                            if _clim is not None:
                                _cm, _cs = _clim
                                if "samos_pairs" not in raw[station_id][bucket]:
                                    raw[station_id][bucket]["samos_pairs"] = []
                                raw[station_id][bucket]["samos_pairs"].append(
                                    (float(forecast_temp), float(actual_temp), _cm, _cs)
                                )
                        except (AttributeError, ValueError):
                            pass  # target_date might not have timetuple if string
                    # Regime-conditioned grouping (only rows with regime tag)
                    if regime:
                        rkey = (station_id, regime)
                        if rkey not in raw_regime:
                            raw_regime[rkey] = {}
                        if bucket not in raw_regime[rkey]:
                            raw_regime[rkey][bucket] = {"pairs": []}
                        raw_regime[rkey][bucket]["pairs"].append(
                            (float(forecast_temp), float(actual_temp))
                        )

            # Build simple bias calibration (backward compat)
            cal_avg: Dict[str, Dict[int, float]] = {
                sid: {
                    bucket: sum(data["biases"]) / len(data["biases"])
                    for bucket, data in buckets.items()
                }
                for sid, buckets in raw.items()
            }

            # Build EMOS calibration where ≥20 resolved pairs are available per bucket.
            # Regime-conditioned EMOS: if current regime has ≥20 pairs, use regime-specific
            # params. Otherwise fall back to regime-agnostic EMOS from all data.
            _MIN_EMOS_SAMPLES = 20
            emos_params: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]] = {}

            # Step 1: Try regime-specific EMOS for current regime
            _regime_buckets = 0
            for (sid, regime), buckets in raw_regime.items():
                if regime != current_regime:
                    continue
                for bucket, data in buckets.items():
                    pairs = data["pairs"]
                    if len(pairs) >= _MIN_EMOS_SAMPLES:
                        emos_a, emos_b, emos_sigma = WeatherBot._fit_emos(pairs)
                        if sid not in emos_params:
                            emos_params[sid] = {}
                        emos_params[sid][bucket] = (emos_a, emos_b, emos_sigma)
                        _regime_buckets += 1

            # Step 2: Fill in from regime-agnostic data where regime-specific is unavailable
            _agnostic_buckets = 0
            for sid, buckets in raw.items():
                for bucket, data in buckets.items():
                    # Skip if regime-specific already populated this cell
                    if sid in emos_params and bucket in emos_params[sid]:
                        continue
                    pairs = data["pairs"]
                    if len(pairs) >= _MIN_EMOS_SAMPLES:
                        emos_a, emos_b, emos_sigma = WeatherBot._fit_emos(pairs)
                        if sid not in emos_params:
                            emos_params[sid] = {}
                        emos_params[sid][bucket] = (emos_a, emos_b, emos_sigma)
                        _agnostic_buckets += 1

            self._prob_engine.load_calibration(cal_avg)
            if emos_params:
                self._prob_engine.load_emos_calibration(emos_params)
                logger.info(
                    "weatherbot_emos_calibration_loaded",
                    stations_with_emos=len(emos_params),
                    regime=current_regime,
                    regime_buckets=_regime_buckets,
                    agnostic_buckets=_agnostic_buckets,
                )
            # Load isotonic tail calibration data from weather_tail_calibration table.
            # Groups (model_prob, actual_freq) pairs by (bucket_type, lead_bucket).
            # Falls back to fixed 0.85 tail discount when < 5 data points per cell.
            try:
                async with db.get_session() as session:
                    tail_rows = await session.execute(text("""
                        SELECT bucket_type, lead_time_bucket, model_prob, actual_outcome
                        FROM weather_tail_calibration
                    """))
                    tail_all = tail_rows.fetchall()

                if tail_all:
                    # Bin by (bucket_type, lead_bucket), compute actual_freq in probability bins
                    from collections import defaultdict
                    tail_bins: Dict[Tuple[str, int], List[Tuple[float, int]]] = defaultdict(list)
                    for btype, lt_bucket, mp, outcome in tail_all:
                        tail_bins[(btype, lt_bucket)].append((float(mp), int(outcome)))

                    # Compute (model_prob_bin_center, actual_freq) for each cell
                    tail_data: Dict[Tuple[str, int], List[Tuple[float, float]]] = {}
                    for key, points in tail_bins.items():
                        if len(points) < 5:
                            continue
                        # Sort by model_prob, bin into 10 equal-sized bins
                        points.sort(key=lambda x: x[0])
                        bin_size = max(len(points) // 10, 1)
                        calibrated: List[Tuple[float, float]] = []
                        for i in range(0, len(points), bin_size):
                            chunk = points[i:i + bin_size]
                            avg_mp = sum(p[0] for p in chunk) / len(chunk)
                            avg_freq = sum(p[1] for p in chunk) / len(chunk)
                            if avg_mp > 0.01:
                                calibrated.append((avg_mp, avg_freq))
                        if calibrated:
                            tail_data[key] = calibrated

                    if tail_data:
                        self._prob_engine.load_tail_calibration(tail_data)
            except Exception as tail_exc:
                logger.debug("weatherbot_tail_calibration_failed", error=str(tail_exc))

            # S114/S115: Fit global EMOS from pooled data across all stations.
            # Prefer SAMOS (Standardized Anomaly MOS) when climatology is available:
            # normalizes by ERA5 climate normals before fitting → eliminates station-specific
            # effects (tropical vs polar temperatures become comparable anomalies).
            _all_pairs: List[Tuple[float, float]] = []
            _samos_pairs: List[Tuple[float, float, float, float]] = []
            for sid, buckets_data in raw.items():
                for bucket, data in buckets_data.items():
                    _all_pairs.extend(data["pairs"])
                    _samos_pairs.extend(data.get("samos_pairs", []))

            _computed_global_emos: Optional[Tuple[float, float, float]] = None
            _global_method = "raw_emos"
            if len(_samos_pairs) >= _MIN_EMOS_SAMPLES:
                _samos_result = WeatherBot._fit_samos(_samos_pairs)
                if _samos_result is not None:
                    # Convert SAMOS (anomaly space) back to raw space using avg climatology:
                    # a_raw = μ_c*(1 - b_s) + σ_c*a_s, b_raw = b_s, σ_raw = σ_c * σ_s
                    _clim_means = [p[2] for p in _samos_pairs]
                    _clim_stds = [p[3] for p in _samos_pairs if p[3] > 0.5]
                    _avg_cm = sum(_clim_means) / len(_clim_means)
                    _avg_cs = sum(_clim_stds) / len(_clim_stds) if _clim_stds else 3.0
                    _sa, _sb, _ss = _samos_result
                    _raw_a = _avg_cm * (1.0 - _sb) + _avg_cs * _sa
                    _raw_b = _sb
                    _raw_sigma = _avg_cs * _ss
                    self._prob_engine.load_global_emos((_raw_a, _raw_b, _raw_sigma))
                    _computed_global_emos = (_raw_a, _raw_b, _raw_sigma)
                    _global_method = "samos"
                    logger.info(
                        "weatherbot_global_samos_fitted",
                        n_pairs=len(_samos_pairs),
                        samos_a=round(_sa, 4), samos_b=round(_sb, 4), samos_sigma=round(_ss, 4),
                        raw_a=round(_raw_a, 4), raw_b=round(_raw_b, 4), raw_sigma=round(_raw_sigma, 4),
                        avg_clim_mean=round(_avg_cm, 1), avg_clim_std=round(_avg_cs, 2),
                    )

            if _global_method != "samos" and len(_all_pairs) >= _MIN_EMOS_SAMPLES:
                _global_a, _global_b, _global_sigma = WeatherBot._fit_emos(_all_pairs)
                self._prob_engine.load_global_emos((_global_a, _global_b, _global_sigma))
                _computed_global_emos = (_global_a, _global_b, _global_sigma)
                logger.info(
                    "weatherbot_global_emos_fitted",
                    n_pairs=len(_all_pairs),
                    a=round(_global_a, 4),
                    b=round(_global_b, 4),
                    sigma=round(_global_sigma, 4),
                )

            # T0-C: Bühlmann credibility blending — continuously blends local + global EMOS
            # using w = n/(n+30). Replaces binary 20-pair threshold for cold stations.
            # Feature flag: WEATHER_EMOS_SHRINKAGE_ENABLED (default: false).
            # When enabled, replaces the earlier load_emos_calibration() call with blended params.
            _shrinkage_enabled = getattr(settings, "WEATHER_EMOS_SHRINKAGE_ENABLED", False)
            if _shrinkage_enabled and _computed_global_emos is not None:
                _ga, _gb, _gs = _computed_global_emos
                _KAPPA = 30.0
                _SHRINK_MIN = 3
                blended_emos: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]] = {}
                for sid, buckets_data in raw.items():
                    for bucket, data in buckets_data.items():
                        pairs = data["pairs"]
                        n = len(pairs)
                        if n < _SHRINK_MIN:
                            continue
                        # Use already-fitted local params if available; else fit fresh
                        if sid in emos_params and bucket in emos_params[sid]:
                            a_loc, b_loc, s_loc = emos_params[sid][bucket]
                        else:
                            a_loc, b_loc, s_loc = WeatherBot._fit_emos(pairs)
                        w = n / (n + _KAPPA)
                        a_blend = w * a_loc + (1.0 - w) * _ga
                        b_blend = w * b_loc + (1.0 - w) * _gb
                        s_effective = s_loc if s_loc is not None else _gs
                        s_blend = max(0.5, w * s_effective + (1.0 - w) * _gs)
                        if sid not in blended_emos:
                            blended_emos[sid] = {}
                        blended_emos[sid][bucket] = (a_blend, b_blend, s_blend)
                if blended_emos:
                    self._prob_engine.load_emos_calibration(blended_emos)
                    logger.info(
                        "weatherbot_emos_shrinkage_applied",
                        stations=len(blended_emos),
                        kappa=int(_KAPPA),
                    )

            self._calibration_last_loaded = now_mono
            _sc: Dict[str, int] = {
                sid: sum(len(data["pairs"]) for data in buckets.values())
                for sid, buckets in raw.items()
            }
            # S114 Item 2: Expose resolved pair counts for Bühlmann sizing ramp
            self._station_n_resolved = _sc
            _emos_ready = sorted(s for s, c in _sc.items() if c >= _MIN_EMOS_SAMPLES)
            _emos_pending = {s: c for s, c in _sc.items() if c < _MIN_EMOS_SAMPLES}
            logger.info(
                "weatherbot_calibration_reloaded",
                stations=len(cal_avg),
                total_rows=len(all_rows),
                emos_ready_stations=_emos_ready,
                emos_pending=_emos_pending,
            )
        except Exception as exc:
            logger.debug("weatherbot_calibration_reload_failed", error=str(exc))

        # S135: Refit logistic regression confidence calibrator from trade_events
        _conf_cal_enabled = getattr(settings, "WEATHER_CONFIDENCE_CAL_ENABLED", True)
        if _conf_cal_enabled:
            try:
                _conf_cal_window = int(getattr(settings, "WEATHER_CONFIDENCE_CAL_WINDOW_DAYS", 30))
                _conf_cal_min = int(getattr(settings, "WEATHER_CONFIDENCE_CAL_MIN_SAMPLES", _CONF_CAL_MIN_SAMPLES))
                db = getattr(self.base_engine, "db", None)
                self._cal_fitted = await self._confidence_calibrator.fit_from_trade_events(
                    db, window_days=_conf_cal_window, min_samples=_conf_cal_min,
                )
            except Exception as cal_exc:
                logger.debug("weatherbot_confidence_cal_refit_failed", error=str(cal_exc))

    async def _check_monitoring_thresholds(self) -> None:
        """W4: Check Brier score and drawdown against structured thresholds.

        Thresholds:
          - Brier > 0.25 (7d) → WARNING alert, recalibrate
          - Brier > 0.30 (7d) → CRITICAL alert, halt trading
          - Daily drawdown > 10% of capital → reduce Kelly (handled by bankroll_manager)
          - Daily drawdown > 20% of capital → halt trading

        Checks run every 10 minutes (self._monitoring_check_interval).
        """
        now = time.monotonic()
        if now - self._monitoring_last_check < self._monitoring_check_interval:
            return
        self._monitoring_last_check = now

        alerting = getattr(self.base_engine, "alerting_system", None)
        db = getattr(self.base_engine, "db", None)
        if not db:
            return

        # ── Brier score check (7-day rolling) ──
        try:
            async with db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT COUNT(*), AVG(POWER(forecast_temp - actual_temp, 2))
                    FROM weather_calibration
                    WHERE actual_temp IS NOT NULL
                      AND created_at >= NOW() - INTERVAL '7 days'
                """))
                row = result.fetchone()
                if row and row[0] and row[0] >= 10:
                    mse_7d = float(row[1])
                    # Normalize MSE to 0-1 Brier-like scale: divide by typical temp range squared
                    # For monitoring, use raw MSE as the metric (lower = better)
                    brier_proxy = mse_7d

                    _brier_halt_mse = float(getattr(settings, "WEATHER_BRIER_HALT_MSE", 25.0))
                    if brier_proxy > _brier_halt_mse:  # MSE > threshold = avg error too high
                        self._monitoring_halt = True
                        if alerting:
                            await alerting.send_alert(
                                title="WeatherBot Brier CRITICAL",
                                message=f"7d forecast MSE={brier_proxy:.2f} (>25.0) — trading halted. "
                                        f"Recalibrate EMOS or check data pipeline.",
                                severity=AlertSeverity.CRITICAL,
                                source="WeatherBot",
                                metadata={"mse_7d": brier_proxy, "sample_count": row[0]},
                            )
                        logger.critical(
                            "weatherbot_monitoring_halt",
                            mse_7d=round(brier_proxy, 2),
                            samples=row[0],
                        )
                    elif brier_proxy > 16.0:  # MSE > 16 = avg error > 4°F
                        self._monitoring_halt = False
                        if alerting:
                            await alerting.send_alert(
                                title="WeatherBot Brier WARNING",
                                message=f"7d forecast MSE={brier_proxy:.2f} (>16.0) — "
                                        f"consider forcing EMOS recalibration.",
                                severity=AlertSeverity.WARNING,
                                source="WeatherBot",
                                metadata={"mse_7d": brier_proxy, "sample_count": row[0]},
                            )
                        logger.warning(
                            "weatherbot_monitoring_warning",
                            mse_7d=round(brier_proxy, 2),
                            samples=row[0],
                        )
                    else:
                        # Below thresholds — clear halt if it was set
                        if self._monitoring_halt:
                            logger.info("weatherbot_monitoring_halt_cleared", mse_7d=round(brier_proxy, 2))
                        self._monitoring_halt = False

                        # W6: Dynamic Kelly graduation.
                        # When 100+ resolved AND MSE < 9 (avg error < 3°F), upgrade
                        # Kelly from 0.25 to 0.35. At 200+ resolved AND MSE < 4,
                        # upgrade to 0.50. Downgrades automatically when MSE rises.
                        n_resolved = int(row[0])
                        if n_resolved >= 200 and brier_proxy < 4.0:
                            if self._kelly_mult < 0.50:
                                logger.info(
                                    "weatherbot_kelly_graduation",
                                    old=self._kelly_mult, new=0.50,
                                    mse_7d=round(brier_proxy, 2), n_resolved=n_resolved,
                                )
                            self._kelly_mult = 0.50
                        elif n_resolved >= 100 and brier_proxy < 9.0:
                            if self._kelly_mult < 0.35:
                                logger.info(
                                    "weatherbot_kelly_graduation",
                                    old=self._kelly_mult, new=0.35,
                                    mse_7d=round(brier_proxy, 2), n_resolved=n_resolved,
                                )
                            self._kelly_mult = 0.35
                        else:
                            # Not yet graduated — stay at configured default
                            default_kelly = float(getattr(settings, "WEATHER_KELLY_FRACTION", 0.25))
                            if self._kelly_mult > default_kelly:
                                logger.info(
                                    "weatherbot_kelly_downgrade",
                                    old=self._kelly_mult, new=default_kelly,
                                    mse_7d=round(brier_proxy, 2), n_resolved=n_resolved,
                                )
                                self._kelly_mult = default_kelly
        except Exception as exc:
            logger.debug("weatherbot_monitoring_brier_check_failed", error=str(exc))

        # ── Daily drawdown check ──
        capital = self.bankroll.capital if self.bankroll else 5000.0
        if self._daily_pnl < 0:
            drawdown_pct = abs(self._daily_pnl) / capital
            if drawdown_pct > 0.20:
                self._monitoring_halt = True
                if alerting:
                    await alerting.send_alert(
                        title="WeatherBot Drawdown CRITICAL",
                        message=f"Daily drawdown {drawdown_pct:.1%} (>{20}%) — trading halted.",
                        severity=AlertSeverity.CRITICAL,
                        source="WeatherBot",
                        metadata={"daily_pnl": self._daily_pnl, "drawdown_pct": drawdown_pct},
                    )
                logger.critical(
                    "weatherbot_drawdown_halt",
                    daily_pnl=round(self._daily_pnl, 2),
                    drawdown_pct=round(drawdown_pct, 4),
                )
            elif drawdown_pct > 0.10:
                if alerting:
                    await alerting.send_alert(
                        title="WeatherBot Drawdown WARNING",
                        message=f"Daily drawdown {drawdown_pct:.1%} (>{10}%) — Kelly reduced by bankroll manager.",
                        severity=AlertSeverity.WARNING,
                        source="WeatherBot",
                        metadata={"daily_pnl": self._daily_pnl, "drawdown_pct": drawdown_pct},
                    )

    async def _save_forecast_to_db(
        self,
        station: WeatherStation,
        target_date: date,
        forecast: CombinedForecast,
    ) -> None:
        """Persist ensemble forecast snapshot to weather_forecasts DB (P3).

        Also inserts a weather_calibration row (forecast_temp only; actual_temp
        populated later when the market resolves). Deduplicates within session.
        """
        dedup_key = f"{station.station_id}:{target_date.isoformat()}"
        if dedup_key in self._written_forecasts:
            return

        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            # Round fetch_time to nearest 15-min bucket for the unique constraint
            minute_bucket = (now_utc.minute // 15) * 15
            fetch_time = now_utc.replace(minute=minute_bucket, second=0, microsecond=0)
            target_dt = datetime(target_date.year, target_date.month, target_date.day)

            async with db.get_session() as session:
                from sqlalchemy import text

                # Insert forecast snapshot (ON CONFLICT DO NOTHING for unique constraint)
                await session.execute(text("""
                    INSERT INTO weather_forecasts
                        (station_id, target_date, forecast_time, lead_time_hours,
                         ensemble_members, deterministic_high, model_spread,
                         models_used, created_at)
                    VALUES
                        (:station_id, :target_date, :forecast_time, :lead_time_hours,
                         CAST(:ensemble_members AS jsonb), :deterministic_high, :model_spread,
                         CAST(:models_used AS jsonb), :created_at)
                    ON CONFLICT (station_id, target_date, forecast_time) DO NOTHING
                """), {
                    "station_id": station.station_id,
                    "target_date": target_dt,
                    "forecast_time": fetch_time,
                    "lead_time_hours": round(forecast.lead_time_hours, 1),
                    "ensemble_members": json.dumps(forecast.ensemble_members),
                    "deterministic_high": forecast.deterministic_high,
                    "model_spread": forecast.model_spread,
                    "models_used": json.dumps(forecast.models_used),
                    "created_at": now_utc,
                })

                # Insert calibration row (forecast only; actual_temp filled on resolution)
                regime = await self._get_enso_regime()
                await session.execute(text("""
                    INSERT INTO weather_calibration
                        (station_id, target_date, forecast_temp, actual_temp,
                         lead_time_hours, model_name, regime, created_at)
                    VALUES
                        (:station_id, :target_date, :forecast_temp, NULL,
                         :lead_time_hours, :model_name, :regime, :created_at)
                    ON CONFLICT (station_id, target_date, lead_time_hours) DO NOTHING
                """), {
                    "station_id": station.station_id,
                    "target_date": target_dt,
                    "forecast_temp": forecast.deterministic_high,
                    "lead_time_hours": round(forecast.lead_time_hours, 1),
                    "model_name": ",".join(forecast.models_used),
                    "regime": regime,
                    "created_at": now_utc,
                })

                await session.commit()

            self._written_forecasts.add(dedup_key)
            logger.debug(
                "weatherbot_forecast_saved",
                station=station.station_id,
                date=target_date.isoformat(),
                members=len(forecast.ensemble_members),
            )
        except Exception as exc:
            # Non-fatal — DB write failure doesn't block trading
            logger.debug("weatherbot_forecast_save_failed", error=str(exc))

    # ── Utilities ─────────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Clean up resources."""
        await self._forecast_client.close()
        await self._metar_client.close()
        await super().stop()
