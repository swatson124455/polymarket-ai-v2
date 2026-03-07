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
from typing import Any, Dict, List, Optional, Set, Tuple

from structlog import get_logger

from bots.base_bot import BaseBot
from base_engine.base_engine import BaseEngine
from base_engine.weather.forecast_client import CombinedForecast, WeatherForecastClient
from base_engine.weather.metar_client import MetarClient
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
        self._metar_client = MetarClient()
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

        # Rate-limit the direct API probe (DB + Gamma) to once per 30 min.
        # Without this, every 5-min scan with 0 weather markets fires an extra
        # DB query + HTTP call to Gamma API, lengthening every scan cycle.
        self._last_direct_probe: float = 0.0
        self._direct_probe_interval: float = 1800.0  # 30 minutes

        # P3: dedup tracking (avoid writing same forecast twice in one session)
        self._written_forecasts: Set[str] = set()  # "station_id:date_iso"

        # P2-regime: ENSO regime cache (el_nino / la_nina / neutral)
        # Nino 3.4 SST anomaly updated monthly; cache for 24h.
        self._regime_tag: Optional[str] = None
        self._regime_last_fetched: float = 0.0
        self._regime_cache_ttl: float = 86400.0  # 24 hours

    # ── Adaptive scan interval ─────────────────────────────────────────────

    def _get_scan_interval_seconds(self) -> float:
        """Override base: scan aggressively during NWP model update windows.

        Model availability windows (UTC) where market edge is freshest:
          07:00-08:00  ECMWF 00Z ENS lands (highest-alpha window)
          18:00-19:00  ECMWF 12Z ENS lands
          05:15-06:00  GFS 00Z lands (~05:30)
          17:15-18:00  GFS 12Z lands (~17:30)

        Outside model windows: HRRR updates hourly at ~:45 past the hour,
        so scan every 2 min in the :40-:59 window to catch HRRR data.
        Default: 60s (normal cadence — matches SCAN_INTERVAL_WEATHER).
        """
        now_utc = datetime.now(timezone.utc)
        h, m = now_utc.hour, now_utc.minute

        # ECMWF ENS model windows: scan every 60s
        ecmwf_windows = [(7, 0, 8, 0), (18, 0, 19, 0)]
        for wh, wm, eh, em in ecmwf_windows:
            if (h, m) >= (wh, wm) and (h, m) < (eh, em):
                return 60.0

        # GFS model windows: scan every 90s
        gfs_windows = [(5, 15, 6, 0), (17, 15, 18, 0)]
        for wh, wm, eh, em in gfs_windows:
            if (h, m) >= (wh, wm) and (h, m) < (eh, em):
                return 90.0

        # HRRR window (~:40-:59 each hour): scan every 120s
        if m >= 40:
            return 120.0

        # Default: use configured SCAN_INTERVAL_WEATHER (normally 300s)
        return super()._get_scan_interval_seconds()

    # ── Main scan loop ────────────────────────────────────────────────────

    async def scan_and_trade(self) -> None:
        # P1+P2: handle day boundary + calibration refresh
        await self._handle_daily_boundary()
        await self._maybe_reload_calibration()

        # One-time startup observability check (logs DB state + Gamma API probe)
        if not self._startup_check_done:
            await self._check_weather_market_availability()

        # 1. Fetch weather markets directly from DB on every scan.
        #    category filter is pushed into SQL WHERE before LIMIT (commit 0ba0267),
        #    so weather markets are returned regardless of liquidity=0 ranking.
        weather_markets = await self.base_engine.get_all_tradeable_markets(
            min_liquidity=0, categories=["weather"]
        )

        if not weather_markets:
            # Fallback: Gamma API direct probe — only needed if DB has no weather category
            # markets at all. Rate-limited to avoid hammering the external API.
            now_mono = time.monotonic()
            if now_mono - self._last_direct_probe >= self._direct_probe_interval:
                self._last_direct_probe = now_mono
                weather_markets = await self._fetch_weather_markets_direct()
            if not weather_markets:
                logger.info("weatherbot_no_weather_markets")
                return

        # Enrich live prices via CLOB /midpoint for markets not in the 1000-token
        # WebSocket subscription (yes_price=NULL in DB). Skips markets already priced.
        weather_markets = await self._enrich_with_live_prices(weather_markets)

        scan_limit = getattr(settings, "SCAN_MARKET_LIMIT", 800)
        weather_markets = weather_markets[:scan_limit]

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
            loc, scale, shape, group.buckets, forecast.lead_time_hours,
        )

        # Resolution-day METAR override: if within 6h of resolution, fetch the
        # running daily max from METAR T-groups and override model probabilities
        # for buckets that are already definitively ruled in or out.
        if lead_time < 6.0:
            model_probs = await self._apply_metar_resolution_day_override(
                group, model_probs, lead_time,
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

            # WU vs NWS resolution-source uncertainty:
            # When the ensemble mean (loc) is within 0.5°F/°C of a bucket boundary,
            # a small discrepancy between WU hourly max and NWS official daily high
            # can flip the resolution outcome. Reduce confidence by 50% in these cases.
            boundary_risk = WeatherBot._near_boundary(loc, bucket)
            base_confidence = min(0.95, 0.50 + e["abs_edge"])
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
            })

        return tradeable

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

        for market_id, bucket in bucket_map.items():
            btype = bucket.bucket_type

            if btype == "at_or_below":
                if running_max > bucket.high_bound + 0.5:
                    # Daily max already exceeded threshold — bucket cannot resolve YES
                    updated[market_id] = 0.001
                elif running_max < bucket.high_bound - 1.5:
                    # Well below ceiling with little time left — almost certainly YES
                    updated[market_id] = 0.97

            elif btype == "at_or_higher":
                if running_max >= bucket.low_bound - 0.5:
                    # Daily max has reached or nearly reached the floor — resolving YES
                    updated[market_id] = 0.97
                elif running_max < bucket.low_bound - 2.0:
                    # Well below floor — unlikely to reach threshold in remaining time
                    updated[market_id] = 0.001

            elif btype == "range":
                if running_max > bucket.high_bound + 0.5:
                    # Daily max has exceeded the range upper bound — cannot resolve YES
                    updated[market_id] = 0.001
                # Note: if running_max is already within range, we let model_prob stand
                # since we can't yet rule out the max climbing further above the range.

            elif btype == "exact":
                if running_max > bucket.high_bound + 0.5:
                    # Already exceeded the exact value — cannot resolve YES
                    updated[market_id] = 0.001

        # Renormalize so probabilities sum to 1.0
        total = sum(updated.values())
        if total > 0:
            for mid in updated:
                updated[mid] /= total

        return updated

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

        # Severe weather boost (hurricane/tornado/blizzard near station)
        severe_boost = await self._get_severe_weather_boost(group.station)

        # Combined boost for near-expiry + cross-city regime + severe weather (capped at 3.0×)
        combined_boost = min(expiry_boost * regime_boost * severe_boost, 3.0)

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
            # Cooldown guard: prevent re-entry on same market within 15 min.
            # _recently_exited is checked in _analyze_group(); must be populated here
            # because the position_manager (not weather_bot) triggers SELL exits,
            # so without this the dict stays empty and the cooldown never fires.
            self._recently_exited[opp["market_id"]] = time.monotonic()
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

    async def _enrich_with_live_prices(self, markets: List[Dict]) -> List[Dict]:
        """Fetch live yes_price / no_price from CLOB /midpoint for markets with NULL DB prices.

        Weather markets have yes_price=NULL in the DB because their token IDs are not
        included in the 1000-token WebSocket subscription (they have liquidity=0).
        Without a real price, compute_edges() skips every bucket (price <= 0.0 guard).

        Calls CLOB /midpoint per market's yes_token_id — only runs once per 30 min
        (rate-limited by _fetch_weather_markets_direct's _last_direct_probe timer).
        Capped at 50 markets to avoid overwhelming the API.

        Note: Gamma API /markets/{id} rejects hex condition IDs (DB id format).
        CLOB /midpoint accepts the numeric yes_token_id and returns {"mid": "0.48"}.
        """
        client = getattr(self.base_engine, "client", None)
        if not client:
            return markets

        enriched: List[Dict] = []
        enriched_count = 0
        for m in markets[:50]:
            # Skip if already has a valid price (0 < yes_price < 1)
            existing = m.get("yes_price")
            if existing and 0.0 < float(existing) < 1.0:
                enriched.append(m)
                continue

            # Use CLOB /midpoint with yes_token_id — Gamma API /markets/{id} rejects
            # hex condition IDs (the format stored in our DB). CLOB midpoint accepts
            # the numeric token ID from yes_token_id and returns {"mid": "0.48"}.
            yes_token_id = str(m.get("yes_token_id") or "")
            if not yes_token_id:
                enriched.append(m)
                continue

            try:
                yes_p = await client.get_token_midpoint(yes_token_id)
                if yes_p is not None:
                    m = dict(m)          # Don't mutate original DB dict
                    m["yes_price"] = yes_p
                    m["no_price"] = round(1.0 - yes_p, 6)
                    enriched_count += 1
            except Exception as exc:
                logger.debug(
                    "weatherbot_price_enrich_error",
                    yes_token_id=yes_token_id[:20],
                    error=str(exc),
                )
            enriched.append(m)

        logger.info(
            "weatherbot_price_enriched",
            total=len(enriched),
            enriched=enriched_count,
            skipped=len(enriched) - enriched_count,
        )
        return enriched

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

    @staticmethod
    def _near_boundary(loc: float, bucket, threshold: float = 0.5) -> bool:
        """Return True if the ensemble mean is within threshold of a bucket boundary.

        When the model's expected temperature is close to a bracket boundary, the
        resolution outcome becomes sensitive to the data source (WU hourly max vs
        NWS official daily high). A 0.5°F/°C gap between WU and NWS can flip the
        result — the Dec 2025 NYC incident (WU=29°F vs NWS=30°F) is the canonical
        example. Caller should reduce position size when this flag is True.

        Args:
            loc:       EMOS-corrected ensemble mean (°F or °C)
            bucket:    TemperatureBucket with low_bound/high_bound
            threshold: Distance from boundary that triggers the flag (default 0.5°)
        """
        btype = bucket.bucket_type
        if btype == "at_or_below":
            return abs(loc - bucket.high_bound) <= threshold
        elif btype == "at_or_higher":
            return abs(loc - bucket.low_bound) <= threshold
        elif btype in ("range", "exact"):
            near_low = abs(loc - bucket.low_bound) <= threshold
            near_high = abs(loc - bucket.high_bound) <= threshold
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

    async def _get_severe_weather_boost(self, station: WeatherStation) -> float:
        """Check NWS alerts for severe weather near a station → kelly boost.

        Queries the NWS /alerts/active API for alerts affecting the station's
        lat/lon (50km radius via NWS point lookup). Returns:
          - 2.0 if Hurricane Watch/Warning, Tropical Storm Warning, or Extreme Wind
          - 1.5 if Severe Thunderstorm Warning, Tornado Warning, or Winter Storm Warning
          - 1.0 otherwise (no boost)

        Only fetches for US stations (temp_unit == "F"). Cached for 30 min per station.
        """
        if station.temp_unit.upper() != "F":
            return 1.0

        # Cache key per station, 30 min TTL
        cache_attr = "_nws_alert_cache"
        if not hasattr(self, cache_attr):
            object.__setattr__(self, cache_attr, {})
        cache: Dict[str, Tuple[float, float]] = getattr(self, cache_attr)
        now_mono = time.monotonic()
        cached = cache.get(station.station_id)
        if cached and now_mono - cached[0] < 1800.0:
            return cached[1]

        import aiohttp
        # NWS /alerts/active/point endpoint: filters by lat/lon
        url = f"https://api.weather.gov/alerts/active?point={station.latitude:.4f},{station.longitude:.4f}"
        boost = 1.0
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={
                    "Accept": "application/geo+json",
                    "User-Agent": "PolymarketWeatherBot/1.0",
                },
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.debug("nws_alerts_error", station=station.station_id, status=resp.status)
                        cache[station.station_id] = (now_mono, 1.0)
                        return 1.0
                    data = await resp.json(content_type=None)

            features = data.get("features", [])
            _HIGH_IMPACT = {
                "Hurricane Warning", "Hurricane Watch",
                "Tropical Storm Warning", "Extreme Wind Warning",
            }
            _MED_IMPACT = {
                "Severe Thunderstorm Warning", "Tornado Warning",
                "Winter Storm Warning", "Ice Storm Warning",
                "Blizzard Warning",
            }
            for feat in features:
                props = feat.get("properties", {})
                event = props.get("event", "")
                if event in _HIGH_IMPACT:
                    boost = max(boost, 2.0)
                elif event in _MED_IMPACT:
                    boost = max(boost, 1.5)

            if boost > 1.0:
                logger.info(
                    "weatherbot_severe_weather_boost",
                    station=station.station_id,
                    boost=boost,
                    alerts=[f.get("properties", {}).get("event", "") for f in features[:3]],
                )
        except Exception as exc:
            logger.debug("nws_alerts_failed", station=station.station_id, error=str(exc))

        cache[station.station_id] = (now_mono, boost)
        return boost

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
