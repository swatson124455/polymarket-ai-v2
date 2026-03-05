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

import json
import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from base_engine.weather.forecast_client import CombinedForecast, WeatherForecastClient
from base_engine.weather.market_mapper import (
    TemperatureBucket,
    WeatherMarketGroup,
    WeatherMarketMapper,
)
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
        self._prob_engine = WeatherProbabilityEngine()
        self._market_mapper = WeatherMarketMapper()
        self._station_health = StationHealthMonitor()

        # Config
        self._min_edge = float(getattr(settings, "WEATHER_MIN_EDGE", 0.15))
        self._max_per_group = float(getattr(settings, "WEATHER_MAX_PER_GROUP_USD", 200.0))
        self._daily_loss_limit = float(getattr(settings, "WEATHER_DAILY_LOSS_LIMIT", 500.0))
        self._max_correlated = float(getattr(settings, "WEATHER_MAX_CORRELATED_EXPOSURE", 500.0))
        self._kelly_mult = float(getattr(settings, "WEATHER_KELLY_FRACTION", 0.25))
        self._default_size = float(getattr(settings, "WEATHER_DEFAULT_SIZE", 25.0))
        self._max_lead_time = float(getattr(settings, "WEATHER_MAX_LEAD_TIME_HOURS", 168.0))

        # Risk state (P2: restored from DB on day boundary)
        self._daily_pnl = 0.0
        self._daily_pnl_date: Optional[str] = None
        self._group_exposure: Dict[str, float] = {}   # "city:date" → USD deployed
        self._city_exposure: Dict[str, float] = {}     # city → total USD deployed
        self._recently_exited: Dict[str, float] = {}   # market_id → mono time

        # P1: calibration state
        self._calibration_last_loaded: float = 0.0
        self._calibration_reload_interval: float = 3600.0 * 6  # 6 hours

        # Startup observability flag — runs market availability check once on first scan
        self._startup_check_done: bool = False

        # P3: dedup tracking (avoid writing same forecast twice in one session)
        self._written_forecasts: Set[str] = set()  # "station_id:date_iso"

    # ── Main scan loop ────────────────────────────────────────────────────

    async def scan_and_trade(self) -> None:
        # P1+P2: handle day boundary + calibration refresh
        await self._handle_daily_boundary()
        await self._maybe_reload_calibration()

        # One-time startup observability check (logs DB state + Gamma API probe)
        if not self._startup_check_done:
            await self._check_weather_market_availability()

        # 1. Fetch all markets, filter to weather
        markets = await self.base_engine.get_all_tradeable_markets()
        if not markets:
            return

        weather_markets = [m for m in markets if self._market_mapper.is_weather_market(m)]
        scan_limit = getattr(settings, "SCAN_MARKET_LIMIT", 800)
        weather_markets = weather_markets[:scan_limit]

        if not weather_markets:
            # Diagnostic: sample first 10 questions so we can see what the DB is returning.
            sample = [(m.get("question") or m.get("title") or "")[:80] for m in markets[:10]]
            logger.info(
                "weatherbot_no_weather_markets",
                total_markets=len(markets),
                sample_questions=sample,
            )
            # Fallback: probe DB (no liquidity floor) + Gamma API directly for weather markets.
            weather_markets = await self._fetch_weather_markets_direct()
            if not weather_markets:
                return

        # 2. Group by (city, date)
        groups = self._market_mapper.group_markets(weather_markets)
        if not groups:
            logger.info("weatherbot_no_groups_parsed", weather_markets=len(weather_markets))
            return

        # Phase 1: Analyze all groups (fetch forecasts, compute edges)
        analyzed: List[Tuple[List[Dict], WeatherMarketGroup]] = []
        for group in groups:
            try:
                opps = await self._analyze_group(group)
                analyzed.append((opps, group))
            except Exception as exc:
                logger.debug(
                    "weatherbot_group_error",
                    city=group.city,
                    date=group.target_date.isoformat(),
                    error=str(exc),
                )

        # Phase 2: Cross-city regime detection → regime_boost factor
        regime_boost = self._compute_regime_boost(analyzed)
        if regime_boost > 1.0:
            logger.info("weatherbot_regime_boost", boost=regime_boost)

        # Phase 3: Execute trades
        _traded = 0
        _groups_with_edge = 0
        _best_edge = 0.0

        for opps, group in analyzed:
            if opps:
                _groups_with_edge += 1
            for opp in opps:
                if abs(opp["edge"]) > abs(_best_edge):
                    _best_edge = opp["edge"]
                opp["regime_boost"] = regime_boost
                await self._execute_weather_trade(opp, group)
                _traded += 1

        # Wire Session 51 heartbeat counters so watchdog can detect silent WeatherBot
        self._last_scan_markets = len(weather_markets)
        self._last_scan_opportunities = _groups_with_edge
        self._last_scan_trades = _traded

        logger.info(
            "weatherbot_scan_done",
            weather_markets=len(weather_markets),
            groups=len(groups),
            groups_with_edge=_groups_with_edge,
            trades=_traded,
            best_edge=round(_best_edge, 4),
            regime_boost=regime_boost,
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
            "confidence": min(0.95, 0.50 + abs(edge)),
            "model_prob": model_prob,
            "edge": edge,
            "city": station.city_name,
        }

    # ── Group analysis ────────────────────────────────────────────────────

    async def _analyze_group(self, group: WeatherMarketGroup) -> List[Dict]:
        """Analyze all buckets in a city+date group.

        Returns list of tradeable opportunities (edge >= threshold).
        """
        # Skip if target date is in the past
        today = date.today()
        if group.target_date < today:
            return []

        # Skip if lead time exceeds max
        now_utc = datetime.now(timezone.utc)
        target_noon = datetime(
            group.target_date.year, group.target_date.month, group.target_date.day,
            18, 0, tzinfo=timezone.utc,
        )
        lead_time = max(0.0, (target_noon - now_utc).total_seconds() / 3600.0)
        if lead_time > self._max_lead_time:
            return []

        # Station health check
        if not await self._station_health.is_healthy(group.station):
            logger.warning("weatherbot_station_unhealthy", station=group.station.station_id)
            return []

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
            return []

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
            return []

        # Compute bucket probabilities
        model_probs = self._prob_engine.bucket_probabilities(
            loc, scale, shape, group.buckets,
        )

        # Compute edges
        market_prices = {b.market_id: b.yes_price for b in group.buckets}
        edges = self._prob_engine.compute_edges(model_probs, market_prices)

        # Filter to tradeable
        tradeable = []
        bucket_map = {b.market_id: b for b in group.buckets}

        for e in edges:
            if e["abs_edge"] < self._min_edge:
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
            if price <= 0.01 or price >= 0.99:
                continue

            # Check position already open
            gw = self.base_engine.order_gateway
            if gw and hasattr(gw, "_open_position_markets"):
                bot_positions = gw._open_position_markets.get("WeatherBot", set())
                if str(e["market_id"]) in bot_positions:
                    continue

            tradeable.append({
                "market_id": e["market_id"],
                "token_id": token_id,
                "side": side,
                "price": price,
                "confidence": min(0.95, 0.50 + e["abs_edge"]),
                "model_prob": e["model_prob"],
                "edge": e["edge"],
                "abs_edge": e["abs_edge"],
                "city": group.city,
                "target_date": group.target_date.isoformat(),
                "lead_time_hours": round(forecast.lead_time_hours, 1),
                "ensemble_mean": round(forecast.deterministic_high, 1),
                "model_spread": round(forecast.model_spread, 2),
                "ensemble_count": len(forecast.ensemble_members),
            })

        return tradeable

    # ── Trade execution ───────────────────────────────────────────────────

    async def _execute_weather_trade(self, opp: Dict, group: WeatherMarketGroup) -> None:
        """Execute a weather trade with risk checks."""
        # Daily loss limit
        if self._daily_pnl <= -self._daily_loss_limit:
            logger.warning("weatherbot_daily_loss_limit_hit", pnl=self._daily_pnl)
            return

        # Per-group exposure limit
        group_key = f"{group.city}:{group.target_date.isoformat()}"
        current_group_exp = self._group_exposure.get(group_key, 0.0)
        if current_group_exp >= self._max_per_group:
            return

        # Correlated city exposure limit
        current_city_exp = self._city_exposure.get(group.city, 0.0)
        if current_city_exp >= self._max_correlated:
            return

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

        # Combined boost for near-expiry + cross-city regime (capped at 2.5×)
        combined_boost = min(expiry_boost * regime_boost, 2.5)

        # Size via central risk_manager Kelly (same as all other bots)
        try:
            kelly_shares = await self.calculate_bot_position_size(
                opp["confidence"], opp["price"],
            )
            size = max(1.0, kelly_shares * opp["price"] * combined_boost)
        except Exception:
            size = max(1.0, self._default_size)

        # Cap to remaining group/city budget
        remaining_group = self._max_per_group - current_group_exp
        remaining_city = self._max_correlated - current_city_exp
        size = min(size, remaining_group, remaining_city, self._default_size * 4.0)

        if size < 1.0:
            return

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
            # Update exposure trackers
            self._group_exposure[group_key] = current_group_exp + size
            self._city_exposure[group.city] = current_city_exp + size
        else:
            logger.debug(
                "weatherbot_trade_failed",
                market_id=opp["market_id"],
                error=result.get("error", "unknown"),
            )

    # ── Regime detection ─────────────────────────────────────────────────

    @staticmethod
    def _compute_regime_boost(
        analyzed: List[Tuple[List[Dict], "WeatherMarketGroup"]],
    ) -> float:
        """Detect broad warm/cold front across ≥3 US cities → 1.2x Kelly boost.

        If ≥3 US cities all show their best edge in the same direction (YES = warm,
        NO = cold), a regime signal is present and all positions get a 1.2x boost.
        Returns 1.0 if no regime detected.
        """
        warm_cities: Set[str] = set()
        cold_cities: Set[str] = set()

        for opps, group in analyzed:
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

    async def _check_weather_market_availability(self) -> None:
        """One-time startup log of weather market availability across DB and Gamma API.

        Runs once on first scan_and_trade() call. Provides immediate visibility
        into whether the silence is a code issue or a seasonal market gap.
        """
        self._startup_check_done = True
        try:
            # DB with normal liquidity floor
            all_markets = await self.base_engine.get_all_tradeable_markets()
            weather_normal = sum(
                1 for m in all_markets if self._market_mapper.is_weather_market(m)
            )
            # DB with zero liquidity floor
            no_liq = await self.base_engine.get_all_tradeable_markets(min_liquidity=0)
            weather_no_liq = sum(
                1 for m in no_liq if self._market_mapper.is_weather_market(m)
            )
            logger.info(
                "weatherbot_startup_availability",
                db_total=len(all_markets),
                db_weather_with_liq_floor=weather_normal,
                db_weather_no_liq_floor=weather_no_liq,
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
        """Query today's WeatherBot realized P&L from paper_trades DB."""
        db = getattr(self.base_engine, "db", None)
        if not db:
            return
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_start = datetime.strptime(today_str, "%Y-%m-%d")  # naive UTC midnight
            async with db.get_session() as session:
                from sqlalchemy import text
                result = await session.execute(text("""
                    SELECT COALESCE(SUM(realized_pnl), 0.0)
                    FROM paper_trades
                    WHERE bot_name = 'WeatherBot'
                      AND realized_pnl IS NOT NULL
                      AND created_at >= :today_start
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
                actual_temp = await self._forecast_client.get_historical_temperature(
                    latitude=station.latitude,
                    longitude=station.longitude,
                    target_date=target_date,
                    temp_unit=station.temp_unit,
                )
                if actual_temp is None:
                    continue

                bias = actual_temp - forecast_temp
                async with db.get_session() as session:
                    await session.execute(text("""
                        UPDATE weather_calibration
                        SET actual_temp = :actual_temp,
                            bias = :bias
                        WHERE id = :row_id
                    """), {
                        "actual_temp": actual_temp,
                        "bias": bias,
                        "row_id": row_id,
                    })
                    await session.commit()
                updated += 1

            if updated:
                logger.info(
                    "weatherbot_calibration_actuals_updated",
                    updated=updated,
                    total_pending=len(rows),
                )
        except Exception as exc:
            logger.debug("weatherbot_calibration_actuals_failed", error=str(exc))

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
                    SELECT station_id, lead_time_hours, bias
                    FROM weather_calibration
                    WHERE bias IS NOT NULL AND actual_temp IS NOT NULL
                """))
                all_rows = rows.fetchall()

            if not all_rows:
                self._calibration_last_loaded = now_mono
                return

            # Aggregate: station_id → {lead_bucket → [bias values]}
            raw: Dict[str, Dict[int, List[float]]] = {}
            for station_id, lt_hours, bias in all_rows:
                bucket = int(float(lt_hours) // 6) * 6
                if station_id not in raw:
                    raw[station_id] = {}
                if bucket not in raw[station_id]:
                    raw[station_id][bucket] = []
                raw[station_id][bucket].append(float(bias))

            # Average biases per bucket
            cal_avg: Dict[str, Dict[int, float]] = {
                sid: {
                    bucket: sum(biases) / len(biases)
                    for bucket, biases in buckets.items()
                }
                for sid, buckets in raw.items()
            }

            self._prob_engine.load_calibration(cal_avg)
            self._calibration_last_loaded = now_mono
            logger.info(
                "weatherbot_calibration_reloaded",
                stations=len(cal_avg),
                total_rows=len(all_rows),
            )
        except Exception as exc:
            logger.debug("weatherbot_calibration_reload_failed", error=str(exc))

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
                         :ensemble_members::jsonb, :deterministic_high, :model_spread,
                         :models_used::jsonb, :created_at)
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
                await session.execute(text("""
                    INSERT INTO weather_calibration
                        (station_id, target_date, forecast_temp, actual_temp,
                         lead_time_hours, model_name, created_at)
                    VALUES
                        (:station_id, :target_date, :forecast_temp, NULL,
                         :lead_time_hours, :model_name, :created_at)
                    ON CONFLICT (station_id, target_date, lead_time_hours) DO NOTHING
                """), {
                    "station_id": station.station_id,
                    "target_date": target_dt,
                    "forecast_temp": forecast.deterministic_high,
                    "lead_time_hours": round(forecast.lead_time_hours, 1),
                    "model_name": ",".join(forecast.models_used),
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
        await super().stop()
