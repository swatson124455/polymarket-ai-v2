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
    StationHealthMonitor,
    US_CITY_NAMES,
    WeatherStation,
)
from config.settings import settings

logger = get_logger()


class WeatherBot(BaseBot):
    def __init__(self, base_engine: BaseEngine):
        super().__init__("WeatherBot", base_engine)

        # Sub-components
        self._forecast_client = WeatherForecastClient(
            cache_ttl=float(getattr(settings, "WEATHER_FORECAST_CACHE_TTL", 900)),
        )
        # Phase 1: inject Redis cache so 429 cooldowns survive restarts
        redis_cache = getattr(base_engine, "cache", None)
        if redis_cache:
            self._forecast_client.set_redis_cache(redis_cache)
        self._metar_client = MetarClient()
        self._prob_engine = WeatherProbabilityEngine()
        self._precip_engine = PrecipitationProbabilityEngine()
        self._market_mapper = WeatherMarketMapper()
        self._station_health = StationHealthMonitor()

        # Config
        self._min_edge = float(getattr(settings, "WEATHER_MIN_EDGE", 0.08))
        self._max_per_group = float(getattr(settings, "WEATHER_MAX_PER_GROUP_USD", 200.0))
        self._daily_loss_limit = float(getattr(settings, "WEATHER_DAILY_LOSS_LIMIT", 500.0))
        self._max_correlated = float(getattr(settings, "WEATHER_MAX_CORRELATED_EXPOSURE", 500.0))
        self._kelly_mult = float(getattr(settings, "WEATHER_KELLY_FRACTION", 0.25))
        self._default_size = float(getattr(settings, "WEATHER_DEFAULT_SIZE", 100.0))
        self._max_lead_time = float(getattr(settings, "WEATHER_MAX_LEAD_TIME_HOURS", 168.0))

        # Risk state (P2: restored from DB on day boundary)
        self._daily_pnl = 0.0
        self._daily_pnl_date: Optional[str] = None
        self._group_exposure: Dict[str, float] = {}   # "city:date" → USD deployed
        self._city_exposure: Dict[str, float] = {}     # city → total USD deployed
        self._recently_exited: Dict[str, float] = {}   # market_id → mono time
        self._known_open_markets: Set[str] = set()     # snapshot for PM exit detection

        # P1: calibration state
        self._calibration_last_loaded: float = 0.0
        self._calibration_reload_interval: float = 3600.0 * 6  # 6 hours

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

    def _get_min_edge(self, market_type: str) -> float:
        """Return per-market-type min_edge, falling back to global setting."""
        params = self._category_params.get(market_type, {})
        return params.get("min_edge", self._min_edge)

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
                    "AND created_at >= NOW() - INTERVAL '7 days' "
                    "ORDER BY created_at"
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
        3. Corresponding paper_trade has realized_pnl (market already settled)

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

            # Step 4: Age fallback (20h) + resolved paper_trade for remaining
            async with db.get_session() as session:
                result = await session.execute(sa_text(
                    "UPDATE positions SET status = 'closed' "
                    "WHERE (bot_id = 'WeatherBot' OR source_bot = 'WeatherBot') "
                    "AND status = 'open' "
                    "AND ("
                    "  opened_at < NOW() - INTERVAL '20 hours' "
                    "  OR market_id IN ("
                    "    SELECT pt.market_id FROM paper_trades pt "
                    "    WHERE pt.realized_pnl IS NOT NULL"
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
        except Exception as exc:
            logger.debug("weatherbot_stale_position_cleanup_failed", error=str(exc))

    # ── Adaptive scan interval ─────────────────────────────────────────────

    def _in_model_window(self) -> bool:
        """Check if current time falls within an NWP model update window."""
        now_utc = datetime.now(timezone.utc)
        h, m = now_utc.hour, now_utc.minute
        for wh, wm, eh, em in self._MODEL_WINDOWS:
            if (h, m) >= (wh, wm) and (h, m) < (eh, em):
                return True
        return False

    # NWP model availability windows (UTC):
    #   07:00-08:00  ECMWF 00Z ENS (highest-alpha)
    #   18:00-19:00  ECMWF 12Z ENS
    #   05:15-06:00  GFS 00Z (~05:30)
    #   17:15-18:00  GFS 12Z (~17:30)
    _MODEL_WINDOWS = [
        (7, 0, 8, 0), (18, 0, 19, 0),   # ECMWF
        (5, 15, 6, 0), (17, 15, 18, 0),  # GFS
    ]

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
        return super()._get_scan_interval_seconds()

    # ── Main scan loop ────────────────────────────────────────────────────

    async def scan_and_trade(self) -> None:
        self._scan_count += 1

        # P1+P2: handle day boundary (must run first — resets exposure on new day)
        await self._handle_daily_boundary()

        # Calibration + category params are independent — run in parallel
        await asyncio.gather(
            self._maybe_reload_calibration(),
            self._load_category_params(),
            self._restore_daily_pnl_from_db(),
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
                logger.debug("weatherbot_pm_exit_detected", market_id=mid)
            self._known_open_markets = set(current_open)

        # Reset per-scan climate normal computation limiter (T3B)
        self._forecast_client.reset_climate_cycle()

        # One-time startup: restore 429 cooldowns from Redis + warm forecast cache from DB
        if not self._cache_warmed:
            await self._forecast_client.restore_state()
            db = getattr(self.base_engine, "db", None)
            await self._forecast_client.warm_cache_from_db(db)
            await self._restore_exits_from_redis()
            await self._restore_exposure_from_db()
            await self._close_stale_positions()
            self._cache_warmed = True

        # One-time startup observability check (logs DB state + Gamma API probe)
        if not self._startup_check_done:
            await self._check_weather_market_availability()

        # Phase timing — track where scan time is spent
        _t0 = time.monotonic()

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
            now_mono = time.monotonic()
            if now_mono - self._last_direct_probe >= self._direct_probe_interval:
                self._last_direct_probe = now_mono
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

        _t_discovery = time.monotonic()

        # Pre-fetch NWS severe weather alerts for all US stations in one pass
        await self._prefetch_severe_weather_alerts(groups)

        _t_alerts = time.monotonic()

        # Phase 1: Analyze all groups (fetch forecasts, compute edges)
        # Parallel with bounded concurrency — 5 concurrent Open-Meteo/NWS requests.
        _group_sem = asyncio.Semaphore(5)

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
                logger.debug(
                    "weatherbot_group_error",
                    city=group.city,
                    date=group.target_date.isoformat(),
                    error=str(result),
                )
            else:
                opps, model_probs = result
                analyzed.append((opps, group, model_probs))

        _t_analysis = time.monotonic()

        # Phase 2: Cross-city regime detection → regime_boost factor
        regime_boost = self._compute_regime_boost(analyzed)
        if regime_boost > 1.0:
            logger.info("weatherbot_regime_boost", boost=regime_boost)

        # Phase 3: Execute trades — W3+W5 laddered via Smoczynski-Tomkins.
        # Groups with >=2 buckets showing edge use S-T multi-bucket allocation.
        # Single-bucket groups fall through to independent Kelly sizing.
        _traded = 0
        _groups_with_edge = 0
        _best_edge = 0.0

        for opps, group, _probs in analyzed:
            if opps:
                _groups_with_edge += 1
            for opp in opps:
                if abs(opp["edge"]) > abs(_best_edge):
                    _best_edge = opp["edge"]
            if len(opps) >= 2:
                # W3+W5: Multi-bucket laddering with S-T sizing
                _traded += await self._execute_group_trades(opps, group, regime_boost)
            else:
                # Single bucket — standard independent sizing
                for opp in opps:
                    opp["regime_boost"] = regime_boost
                    if await self._execute_weather_trade(opp, group):
                        _traded += 1

        # Phase 4: Re-evaluate open positions with fresh model probabilities
        # Feeds position_manager's model-reversal exit logic with current forecasts.
        await self._reevaluate_open_positions(analyzed)

        # Phase 4b: Outcome backfill + drift detection + cleanup — every 10 scans
        if self._scan_count % 10 == 0:
            await self._backfill_weather_outcomes()
            await self._check_emos_drift()
            await self._close_stale_positions()

        _t_trades = time.monotonic()

        # Phases 5-7: Precip/Snow/Wind — independent market types, run in parallel
        _precip_traded, _snow_traded, _wind_traded = await asyncio.gather(
            self._scan_precipitation_markets(),
            self._scan_snowfall_markets(),
            self._scan_wind_markets(),
        )

        # Wire Session 51 heartbeat counters so watchdog can detect silent WeatherBot
        self._last_scan_markets = len(weather_markets)
        self._last_scan_opportunities = _groups_with_edge
        self._last_scan_trades = _traded + _precip_traded + _snow_traded + _wind_traded

        _t_end = time.monotonic()

        logger.info(
            "weatherbot_scan_done",
            weather_markets=len(weather_markets),
            groups=len(groups),
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
        )

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

        if abs(edge) < self._min_edge:
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

    # ── Precipitation scanning ───────────────────────────────────────────

    async def _scan_precipitation_markets(self) -> int:
        """M1: Scan and trade precipitation markets.

        Uses Gamma API tag_slug=precipitation to discover markets,
        parses into PrecipitationMarketGroup, fetches ensemble precip
        data, and executes trades with edge.

        Returns number of trades executed.
        """
        import httpx

        # Discover precipitation markets via tag_slug
        try:
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "tag_slug": "precipitation",
                "limit": "100",
            }
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
                if resp.status_code != 200:
                    return 0
                events = resp.json()
        except Exception as exc:
            logger.debug("weatherbot_precip_tag_fetch_error", error=str(exc))
            return 0

        if not isinstance(events, list):
            return 0

        # Extract markets from events (same pattern as temperature)
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

        # Group by (city, date/period)
        precip_groups = self._market_mapper.group_precipitation_markets(markets)
        if not precip_groups:
            logger.debug("weatherbot_precip_no_groups", markets=len(markets))
            return 0

        # Analyze and trade each group
        traded = 0
        for group in precip_groups:
            try:
                opps = await self._analyze_precipitation_group(group)
                for opp in opps:
                    if await self._execute_weather_trade(opp, self._precip_to_temp_group(group)):
                        traded += 1
            except Exception as exc:
                logger.debug(
                    "weatherbot_precip_group_error",
                    city=group.city, error=str(exc),
                )

        if traded > 0 or precip_groups:
            logger.info(
                "weatherbot_precip_scan_done",
                markets=len(markets),
                groups=len(precip_groups),
                trades=traded,
            )
        return traded

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
            model_probs, engine_buckets, min_edge=self._get_min_edge("precipitation"),
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
        """M2: Scan and trade snowfall markets.

        Uses Gamma API tag_slug=snowfall to discover markets.
        Reuses PrecipitationProbabilityEngine (Gamma distribution works for snowfall).
        Returns number of trades executed.
        """
        import httpx

        try:
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "tag_slug": "snowfall",
                "limit": "100",
            }
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
                if resp.status_code != 200:
                    return 0
                events = resp.json()
        except Exception as exc:
            logger.debug("weatherbot_snow_tag_fetch_error", error=str(exc))
            return 0

        if not isinstance(events, list):
            return 0

        # Extract markets from events (same pattern as precipitation)
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

        snow_groups = self._market_mapper.group_snowfall_markets(markets)
        if not snow_groups:
            logger.debug("weatherbot_snow_no_groups", markets=len(markets))
            return 0

        traded = 0
        for group in snow_groups:
            try:
                opps = await self._analyze_snowfall_group(group)
                for opp in opps:
                    if await self._execute_weather_trade(opp, self._precip_to_temp_group(group)):
                        traded += 1
            except Exception as exc:
                logger.debug(
                    "weatherbot_snow_group_error",
                    city=group.city, error=str(exc),
                )

        if traded > 0 or snow_groups:
            logger.info(
                "weatherbot_snow_scan_done",
                markets=len(markets),
                groups=len(snow_groups),
                trades=traded,
            )
        return traded

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
            model_probs, engine_buckets, min_edge=self._get_min_edge("snowfall"),
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
        """M3: Scan and trade wind gust markets.

        Uses Gamma API tag_slug=wind to discover markets.
        Uses normal CDF for bucket probabilities (wind gusts are ~normally distributed).
        Returns number of trades executed.
        """
        import httpx

        try:
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "tag_slug": "wind",
                "limit": "100",
            }
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
                if resp.status_code != 200:
                    return 0
                events = resp.json()
        except Exception as exc:
            logger.debug("weatherbot_wind_tag_fetch_error", error=str(exc))
            return 0

        if not isinstance(events, list):
            return 0

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

        wind_groups = self._market_mapper.group_wind_markets(markets)
        if not wind_groups:
            logger.debug("weatherbot_wind_no_groups", markets=len(markets))
            return 0

        traded = 0
        for group in wind_groups:
            try:
                opps = await self._analyze_wind_group(group)
                for opp in opps:
                    if await self._execute_weather_trade(opp, self._precip_to_temp_group(group)):
                        traded += 1
            except Exception as exc:
                logger.debug(
                    "weatherbot_wind_group_error",
                    city=group.city, error=str(exc),
                )

        if traded > 0 or wind_groups:
            logger.info(
                "weatherbot_wind_scan_done",
                markets=len(markets),
                groups=len(wind_groups),
                trades=traded,
            )
        return traded

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
            variance = sum((x - mean_wind) ** 2 for x in ensemble) / len(ensemble)
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

            if abs(yes_edge) >= self._get_min_edge("wind"):
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

        # Fit distribution
        try:
            loc, scale, shape = self._prob_engine.fit_distribution(
                forecast.ensemble_members,
                forecast.lead_time_hours,
                group.station.station_id,
            )
        except ValueError as exc:
            logger.debug("weatherbot_fit_failed", error=str(exc))
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

        # Resolution-day METAR override: if within 6h of resolution, fetch the
        # running daily max from METAR T-groups and override model probabilities
        # for buckets that are already definitively ruled in or out.
        if lead_time < 6.0:
            model_probs = await self._apply_metar_resolution_day_override(
                group, model_probs, lead_time,
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
                edges_above_min=[round(e["abs_edge"], 4) for e in edges if e["abs_edge"] >= self._min_edge][:5],
            )

        # Filter to tradeable
        tradeable = []
        bucket_map = {b.market_id: b for b in group.buckets}

        for e in edges:
            if e["abs_edge"] < self._get_min_edge("temperature"):
                continue

            # Lead-time-graduated edge cap: at short lead times, NOAA ensemble
            # convergence and METAR data produce legitimately large edges.
            if lead_time < 6.0:
                _max_edge = 0.70
            elif lead_time < 12.0:
                _max_edge = 0.50
            elif lead_time < 24.0:
                _max_edge = 0.40
            elif lead_time < 48.0:
                _max_edge = 0.30
            else:
                _max_edge = 0.25
            if e["abs_edge"] > _max_edge:
                logger.debug("weatherbot_edge_cap", market_id=e["market_id"], edge=round(e["abs_edge"], 4), max_edge=_max_edge, lead_time_h=round(lead_time, 1))
                continue

            # Skip recently exited markets
            mono_now = time.monotonic()
            exited_at = self._recently_exited.get(e["market_id"])
            if exited_at and mono_now - exited_at < 900.0:  # 15 min cooldown
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
            # Penny-bet filter: skip markets below 5¢ (deep tail buckets).
            # At <5¢, spreads are 50-90% of position value and position_manager
            # exits destroy capital. Hold-to-resolution is the only viable strategy
            # at these prices, but PM exits mid-trade. Better to skip entirely.
            if price <= 0.05 or price >= 0.95:
                continue

            # Check position already open
            gw = self.base_engine.order_gateway
            if gw and hasattr(gw, "_open_position_markets"):
                bot_positions = gw._open_position_markets.get("WeatherBot", set())
                if str(e["market_id"]) in bot_positions:
                    continue

            # WU vs NWS resolution-source uncertainty:
            # When the ensemble mean (loc) is within 0.5°F/°C of a bucket boundary,
            # a small discrepancy between WU hourly max and NWS official daily high
            # can flip the resolution outcome. Reduce confidence by 50% in these cases.
            boundary_risk = WeatherBot._near_boundary(loc, bucket)
            # YES: confidence = model_prob (P of outcome)
            # NO:  confidence = 1 - model_prob (P of NOT outcome) — correct for Kelly + risk manager
            _raw_conf = e["model_prob"] if side == "YES" else (1.0 - e["model_prob"])
            base_confidence = min(0.95, _raw_conf)
            effective_confidence = base_confidence * 0.5 if boundary_risk else base_confidence

            if boundary_risk:
                logger.debug(
                    "weatherbot_boundary_risk",
                    market_id=e["market_id"],
                    loc=round(loc, 2),
                    bucket_type=bucket.bucket_type,
                    high_bound=bucket.high_bound,
                    low_bound=bucket.low_bound,
                )

            tradeable.append({
                "market_id": e["market_id"],
                "token_id": token_id,
                "side": side,
                "price": price,
                "confidence": effective_confidence,
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
        # Skip if position already open (prevents re-entry on same market every scan)
        gw = self.base_engine.order_gateway
        if gw and hasattr(gw, "_open_position_markets"):
            bot_positions = gw._open_position_markets.get("WeatherBot", set())
            if str(opp.get("market_id", "")) in bot_positions:
                return False

        # Skip recently exited markets (15-min cooldown)
        _mono_now = time.monotonic()
        _exited_at = self._recently_exited.get(opp.get("market_id", ""))
        if _exited_at and _mono_now - _exited_at < 900.0:
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

        # Per-group exposure limit
        group_key = f"{group.city}:{group.target_date.isoformat()}"
        current_group_exp = self._group_exposure.get(group_key, 0.0)
        if current_group_exp >= self._max_per_group:
            return False

        # Correlated city exposure limit
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
        if lead_time < 12.0:
            expiry_boost = 2.0   # NOAA final-call: maximum certainty
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

        # Severe weather boost (hurricane/tornado/blizzard near station)
        severe_boost = await self._get_severe_weather_boost(group.station)

        # C4: Combined boost — additive with diminishing returns to prevent
        # multiplicative stacking (was 2.0×1.2×2.0=4.8→cap 3.0 = 0.75 Kelly).
        # New: each boost contributes its excess independently; capped at 2.0×
        # to keep effective Kelly ≤ 0.5 (quarter-Kelly × 2.0).
        combined_boost = 1.0 + (expiry_boost - 1.0) + (regime_boost - 1.0) * 0.5 + (severe_boost - 1.0) * 0.5
        combined_boost = min(combined_boost, 2.0)

        # W7: Baker-McHale uncertainty-scaled sizing.
        # When ensemble members agree (low spread), k* ≈ 1.0 → full size.
        # When members disagree (high spread), k* < 1.0 → reduce size.
        # k* = 1 / (1 + sigma²) where sigma = model_spread / typical_spread.
        # Typical spread: ~3°F (1.7°C). Values below get boosted, above get shrunk.
        _spread = opp.get("model_spread", 3.0)
        _typical_spread = 3.0  # °F baseline
        _sigma_norm = _spread / _typical_spread  # normalized: 1.0 = average
        _bm_factor = 1.0 / (1.0 + _sigma_norm ** 2)  # 0.5 at sigma=1, 0.8 at sigma=0.5
        # Scale combined_boost by Baker-McHale factor
        combined_boost *= _bm_factor

        # Drawdown compression: reduce sizing during losing streaks per market type
        _mtype = opp.get("market_type", "temperature")
        _dd_factor = self._compute_weather_drawdown_factor(_mtype)
        if _dd_factor < 1.0:
            combined_boost *= _dd_factor

        # Per-station reliability: well-calibrated stations get larger size
        _station_id = getattr(getattr(group, "station", None), "station_id", None)
        if _station_id:
            _station_factor = await self._get_station_reliability_factor(_station_id)
            if _station_factor != 1.0:
                combined_boost *= _station_factor

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
                    if _effective_edge < self._min_edge:
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
                logger.debug("weatherbot_liquidity_check_failed", error=str(exc))

        # W3+W5: Use Smoczynski-Tomkins group-level allocation when available.
        # S-T sizes are pre-computed by _execute_group_trades() and passed via
        # _st_size_override. Fall back to independent Kelly if not set.
        _st_override = opp.pop("_st_size_override", None)
        if _st_override is not None:
            size = max(1.0, _st_override * combined_boost)
        else:
            # Size via central risk_manager Kelly (same as all other bots)
            try:
                kelly_shares = await self.calculate_bot_position_size(
                    opp["confidence"], opp["price"],
                )
                size = max(1.0, kelly_shares * opp["price"] * combined_boost)
            except Exception as exc:
                logger.warning("weatherbot_kelly_sizing_failed", error=str(exc))
                size = max(1.0, self._default_size)

        # Cap to remaining group/city budget + liquidity cap
        remaining_group = self._max_per_group - current_group_exp
        remaining_city = self._max_correlated - current_city_exp
        size = min(size, remaining_group, remaining_city, _slippage_size_cap)

        if size < 1.0:
            return False

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
        )

        # M4: Pre-update exposure trackers BEFORE placing order to prevent
        # race condition where multiple orders pass the exposure check
        # simultaneously. Revert on failure.
        self._group_exposure[group_key] = current_group_exp + size
        self._city_exposure[group.city] = current_city_exp + size

        result = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=size,
            price=opp["price"],
            confidence=opp["confidence"],
        )

        if result.get("success"):
            logger.info(
                "weatherbot_trade_filled",
                market_id=opp["market_id"],
                side=opp["side"],
                size=round(size, 2),
            )
            # Log prediction for accuracy tracking at trade execution time
            await self._log_weather_prediction(
                opp["market_id"], opp["model_prob"], opp["price"],
                opp.get("confidence", opp["model_prob"]),
                opp.get("market_type", "temperature"),
            )
            # Cooldown guard: prevent re-entry on same market within 15 min.
            # _recently_exited is checked in _analyze_group(); must be populated here
            # because the position_manager (not weather_bot) triggers SELL exits,
            # so without this the dict stays empty and the cooldown never fires.
            self._recently_exited[opp["market_id"]] = time.monotonic()
            await self._save_exit_to_redis(opp["market_id"])
            return True
        else:
            # Revert exposure trackers on failure
            self._group_exposure[group_key] = current_group_exp
            self._city_exposure[group.city] = current_city_exp
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
        """Detect broad warm/cold front across ≥3 US cities → 1.2x Kelly boost.

        If ≥3 US cities all show their best edge in the same direction (YES = warm,
        NO = cold), a regime signal is present and all positions get a 1.2x boost.
        Returns 1.0 if no regime detected.
        """
        warm_cities: Set[str] = set()
        cold_cities: Set[str] = set()

        for opps, group, _probs in analyzed:
            if not opps or group.city not in US_CITY_NAMES:
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
            import httpx
            url = f"{client.gamma_api}/events"
            params = {
                "active": "true",
                "closed": "false",
                "tag_slug": "temperature",
                "limit": "100",
            }
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("weatherbot_tag_fetch_failed", status=resp.status_code)
                    # B4: Alert on tag API failure
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
                events = resp.json()
        except Exception as exc:
            logger.warning("weatherbot_tag_fetch_error", error=str(exc))
            return []

        if not isinstance(events, list):
            return []

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

    async def _save_exit_to_redis(self, market_id: str) -> None:
        """Persist a recent-exit event to Redis with 15-min TTL so it survives restarts."""
        try:
            cache = getattr(getattr(self, "base_engine", None), "cache", None)
            if cache is None or not getattr(cache, "redis", None):
                return
            expire_at = time.time() + 900.0
            await cache.set(f"weatherbot:exit:{market_id}", expire_at, ttl=900)
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
                elapsed = 900.0 - (expire_at - now_wall)
                mid = key.split("weatherbot:exit:", 1)[-1]
                self._recently_exited[mid] = now_mono - elapsed
                count += 1
            if count:
                logger.info("weatherbot_exits_restored", count=count)
        except Exception as exc:
            logger.warning("weatherbot_restore_exits_failed", error=str(exc))

    async def _restore_exposure_from_db(self) -> None:
        """Rebuild _group_exposure and _city_exposure from today's open paper_trades.

        Called once on startup (inside the _cache_warmed block). Prevents the
        per-group and per-city USD soft caps from resetting to $0 after a mid-day
        restart, which would allow the bot to double-invest in the same city/group.

        Uses the same today_start UTC alignment as _restore_daily_pnl_from_db().
        The JOIN to markets works because the resolution_backfill (commit d3c8a0f)
        ensures all WeatherBot market_ids are now present in the markets table.
        Fail-open: any error logs at debug level and continues with empty dicts.
        """
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_start = datetime.strptime(today_str, "%Y-%m-%d")
            async with db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT m.question, SUM(pt.size * pt.price) AS total_size
                    FROM paper_trades pt
                    JOIN markets m
                      ON (pt.market_id = m.condition_id OR pt.market_id = CAST(m.id AS TEXT))
                    WHERE pt.bot_name = 'WeatherBot'
                      AND pt.created_at >= :today_start
                      AND pt.side IN ('YES', 'NO')
                      AND pt.resolution IS NULL
                    GROUP BY m.question
                """), {"today_start": today_start})
                rows = result.fetchall()
            rebuilt_groups = 0
            for question, total_size in rows:
                if not question or not total_size:
                    continue
                city_text, target_date = self._market_mapper._extract_city_and_date(question)
                if not city_text or not target_date:
                    continue
                group_key = f"{city_text}:{target_date.isoformat()}"
                self._group_exposure[group_key] = self._group_exposure.get(group_key, 0.0) + float(total_size)
                self._city_exposure[city_text] = self._city_exposure.get(city_text, 0.0) + float(total_size)
                rebuilt_groups += 1
            if rebuilt_groups:
                logger.info(
                    "weatherbot_exposure_restored",
                    groups=rebuilt_groups,
                    cities=len(self._city_exposure),
                )
        except Exception as exc:
            logger.debug("weatherbot_exposure_restore_failed", error=str(exc))

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
                result = await session.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0.0)
                    FROM trade_events
                    WHERE bot_name = 'WeatherBot'
                      AND event_type IN ('EXIT', 'RESOLUTION')
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

            async def _fetch_alert(sid: str, station: WeatherStation) -> Tuple[str, float]:
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
                            return sid, 1.0
                        data = await resp.json(content_type=None)
                    boost = 1.0
                    for feat in data.get("features", []):
                        event = feat.get("properties", {}).get("event", "")
                        if event in _HIGH_IMPACT:
                            boost = max(boost, 2.0)
                        elif event in _MED_IMPACT:
                            boost = max(boost, 1.5)
                    if boost > 1.0:
                        logger.info(
                            "weatherbot_severe_weather_boost",
                            station=sid, boost=boost,
                        )
                    return sid, boost
                except Exception:
                    return sid, 1.0

            _alert_sem = asyncio.Semaphore(5)

            async def _bounded_fetch(sid: str, st: WeatherStation) -> Tuple[str, float]:
                async with _alert_sem:
                    return await _fetch_alert(sid, st)

            results = await asyncio.gather(
                *[_bounded_fetch(sid, st) for sid, st in us_stations.items()],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    continue
                batch[r[0]] = r[1]
        except Exception as exc:
            logger.debug("nws_alerts_batch_failed", error=str(exc))

        self._severe_weather_batch = batch
        self._severe_weather_batch_time = now_mono

    async def _get_severe_weather_boost(self, station: WeatherStation) -> float:
        """Return cached severe weather boost for a station.

        Uses batch-prefetched data from _prefetch_severe_weather_alerts().
        Falls back to 1.0 for non-US stations or missing cache.
        """
        if station.temp_unit.upper() != "F":
            return 1.0
        return self._severe_weather_batch.get(station.station_id, 1.0)

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
                rows = await session.execute(text("""
                    SELECT station_id, lead_time_hours, bias, forecast_temp, actual_temp, regime
                    FROM weather_calibration
                    WHERE bias IS NOT NULL AND actual_temp IS NOT NULL
                """))
                all_rows = rows.fetchall()

            if not all_rows:
                self._calibration_last_loaded = now_mono
                return

            # Aggregate: station_id → {lead_bucket → {"biases": [...], "pairs": [(x, y)]}}
            # pairs = (forecast_temp, actual_temp) for EMOS OLS fitting
            raw: Dict[str, Dict[int, Dict[str, Any]]] = {}
            # Regime-aware aggregation: (station_id, regime) → {lead_bucket → {"pairs": [...]}}
            raw_regime: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
            current_regime = await self._get_enso_regime()

            for station_id, lt_hours, bias, forecast_temp, actual_temp, regime in all_rows:
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

            self._calibration_last_loaded = now_mono
            _sc: Dict[str, int] = {
                sid: sum(len(data["pairs"]) for data in buckets.values())
                for sid, buckets in raw.items()
            }
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

                    if brier_proxy > 25.0:  # MSE > 25 = avg error > 5°F
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
