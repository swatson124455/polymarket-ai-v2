"""
EsportsSeriesBot — Series-level market dynamics and conditional probability.

Unique to esports (no direct sports analog). Monitors BO3/BO5 series and
exploits market mispricings caused by:
  1. Momentum fallacy — market overreacts to map score (0-2 ≠ dead)
  2. Map veto ignorance — market ignores team-specific map win rates
  3. Conditional probability errors — market anchors on score, not math

Computes conditional match probability from:
  - Per-map win rates (from HLTV/PandaScore)
  - Current series score
  - Map veto order
  - Binomial race probability

Multi-market correlated entry: match-winner + current-map-winner via
batch orders when edge detected.

Enable: BOT_ENABLED_ESPORTS_SERIES=true (disabled by default).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class EsportsSeriesBot(BaseBot):
    """
    Series-level conditional probability bot for BO3/BO5 esports markets.

    Exploits momentum fallacy and map veto mispricings.
    """

    def __init__(self, base_engine):
        super().__init__("EsportsSeriesBot", base_engine)

        # Fail fast
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            raise ValueError(
                "EsportsSeriesBot requires PANDASCORE_API_KEY — set it in .env"
            )

        self._api_key = api_key
        self._pandascore = None
        self._hltv = None
        self._scanner = None
        self._bankroll_mgr = None

        # Settings
        self._min_edge = float(getattr(settings, "ESPORTS_SERIES_MIN_EDGE", 0.10))
        self._reverse_sweep_floor = float(
            getattr(settings, "ESPORTS_SERIES_REVERSE_SWEEP_FLOOR", 0.05)
        )

        # Track active series to avoid redundant analysis
        self._active_series: Dict[str, Dict] = {}  # match_id → last analyzed state
        # Prediction cache for WS reactive path
        self._series_prediction_cache: Dict[str, Dict] = {}
        self._last_refresh = 0.0

    def _get_scan_interval_seconds(self) -> float:
        """30s during active series, 300s otherwise."""
        if self._active_series:
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_SERIES", 30))
        return 300.0

    async def start(self) -> None:
        """Initialize PandaScore + HLTV clients."""
        db = getattr(self.base_engine, "db", None)
        gw = getattr(self.base_engine, "order_gateway", None)

        from esports.data.pandascore_client import PandaScoreClient
        from esports.markets.esports_market_scanner import EsportsMarketScanner
        from esports.kelly.esports_bankroll_manager import EsportsBankrollManager

        self._pandascore = PandaScoreClient(api_key=self._api_key)
        await self._pandascore.init()

        self._scanner = EsportsMarketScanner(db=db)
        self._bankroll_mgr = EsportsBankrollManager(order_gateway=gw)

        try:
            from esports.data.hltv_scraper import HLTVScraper
            self._hltv = HLTVScraper()
        except Exception:
            logger.debug("EsportsSeriesBot: HLTV scraper not available")

        logger.info("EsportsSeriesBot: initialized")
        await super().start()

    async def stop(self) -> None:
        """Clean up clients."""
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()

    async def on_price_update(self, event: dict) -> None:
        """React to WS price updates — cross-reference cached series prediction."""
        await super().on_price_update(event)
        if not self.running:
            return

        import time as _time

        market_id = event.get("market_id", "")
        token_id = event.get("token_id", "")
        new_price = float(event.get("price", 0))
        if not market_id or new_price <= 0:
            return

        cached = self._series_prediction_cache.get(market_id)
        if not cached:
            return

        # Significance threshold
        threshold = float(getattr(settings, "ESPORTS_SERIES_WS_PRICE_CHANGE_PCT", 0.01))
        if not hasattr(self, "_ws_prev_prices"):
            self._ws_prev_prices: dict = {}
        old_price = self._ws_prev_prices.get(market_id)
        self._ws_prev_prices[market_id] = new_price
        if old_price is None or abs(new_price - old_price) / max(old_price, 0.01) < threshold:
            return

        # Cooldown
        now = _time.monotonic()
        if not hasattr(self, "_ws_cooldowns"):
            self._ws_cooldowns: dict = {}
        cooldown = int(getattr(settings, "ESPORTS_SERIES_WS_COOLDOWN_SECONDS", 10))
        if now - self._ws_cooldowns.get(market_id, 0) < cooldown:
            return
        self._ws_cooldowns[market_id] = now

        model_prob = cached["prob"]
        edge = model_prob - new_price
        if abs(edge) < self._min_edge:
            return

        side = "YES" if edge > 0 else "NO"

        # Position check
        og = getattr(self.base_engine, "order_gateway", None)
        if og is not None and og.has_open_position(self.bot_name, str(market_id)):
            return

        try:
            confidence = model_prob if side == "YES" else (1.0 - model_prob)
            trade_price = new_price if side == "YES" else (1.0 - new_price)
            opp = {
                "type": "esports_series_ws",
                "market_id": market_id,
                "token_id": token_id,
                "side": side,
                "price": trade_price,
                "confidence": confidence,
                "prediction": model_prob,
                "edge": round(abs(edge), 4),
                "game": cached.get("game", ""),
                "market_type": "match_winner",
            }
            logger.info(
                "EsportsSeriesBot WS reactive trade",
                market_id=market_id,
                price_move=f"{old_price:.4f}→{new_price:.4f}",
                edge=round(abs(edge), 4),
            )
            await self._execute_series_trade(opp)
        except Exception as exc:
            logger.debug("EsportsSeriesBot WS reactive failed", error=str(exc))

    async def scan_and_trade(self) -> None:
        """
        Find active BO3/BO5 series, compute conditional probability,
        compare to Polymarket price, trade if mispriced.
        """
        import time as _time

        db = getattr(self.base_engine, "db", None)

        # Prune stale prediction cache entries (>30 min)
        now = _time.monotonic()
        stale = [k for k, v in self._series_prediction_cache.items()
                 if now - v.get("ts", 0) > 1800]
        for k in stale:
            del self._series_prediction_cache[k]

        # Refresh live matches from PandaScore
        await self._refresh_series()

        self._last_scan_markets = len(self._active_series)
        if not self._active_series:
            return

        # Collect all opportunities first, then S-T allocate across series
        all_opps: List[Dict] = []
        for match_id, series_data in list(self._active_series.items()):
            try:
                opps = await self._analyze_series(match_id, series_data, db)
                all_opps.extend(opps)
            except Exception as exc:
                logger.debug(
                    "EsportsSeriesBot: analysis error",
                    match_id=match_id,
                    error=str(exc),
                )

        self._last_scan_opportunities = len(all_opps)
        _trades = 0

        if len(all_opps) >= 2:
            # Multiple series bets are correlated (same capital pool) —
            # use S-T allocation to size them optimally as a group.
            max_daily = float(getattr(settings, "ESPORTS_MAX_DAILY_USD", 500.0))
            daily_spent = 0.0
            if self._bankroll_mgr:
                daily_spent = await self._bankroll_mgr.get_daily_esports_exposure()
            group_budget = max(0.0, max_daily * 0.5 - daily_spent)  # 50% of daily for series

            st_sizes = self._smoczynski_tomkins_allocate(all_opps, group_budget)
            if st_sizes:
                logger.info(
                    "EsportsSeriesBot: S-T allocation",
                    n_opps=len(st_sizes),
                    total_usd=round(sum(st_sizes.values()), 2),
                    budget=round(group_budget, 2),
                )
            for opp in all_opps:
                st_size = st_sizes.get(opp["market_id"])
                if st_size and st_size >= 1.0:
                    opp["_st_size_override"] = st_size
                    await self._execute_series_trade(opp)
                    _trades += 1
        else:
            # Single opportunity — standard independent Kelly sizing
            for opp in all_opps:
                await self._execute_series_trade(opp)
                _trades += 1

        self._last_scan_trades = _trades

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. EsportsSeriesBot is series-driven."""
        return None

    # ── Core Analysis ─────────────────────────────────────────────────────

    async def _analyze_series(
        self, match_id: str, series_data: Dict, db=None
    ) -> List[Dict]:
        """
        Compute conditional match probability and compare to market.

        Steps:
          1. Extract current series score + best_of
          2. Get per-map win rates for both teams
          3. Compute conditional probability using series_model
          4. Find matching Polymarket market
          5. Compare model prob to market price
        """
        from esports.models.series_model import (
            bo3_match_prob,
            bo5_match_prob,
            map_veto_adjusted_prob,
            series_prob_with_map_veto,
        )

        best_of = int(series_data.get("best_of", 1))
        if best_of < 3:
            return []  # Only trade BO3+ series

        maps_a = int(series_data.get("score_maps_a", 0))
        maps_b = int(series_data.get("score_maps_b", 0))
        game = series_data.get("game", "")
        team_a = series_data.get("team_a", "")
        team_b = series_data.get("team_b", "")

        # Skip if series is already decided
        needed = (best_of + 1) // 2
        if maps_a >= needed or maps_b >= needed:
            return []

        # Get per-map win rates: DB first (if map data exists), HLTV fallback.
        # PandaScore free tier does not provide per-game map names, so DB query
        # almost always returns {} unless rows were seeded from a paid source.
        # HLTV scrapes live team map stats from hltv.org (CS2 only).
        map_rates_a = {}
        map_rates_b = {}
        if game == "cs2":
            if db:
                try:
                    from esports.data.esports_db import get_team_map_rates
                    map_rates_a = await get_team_map_rates(db, team_a, game="cs2")
                    map_rates_b = await get_team_map_rates(db, team_b, game="cs2")
                except Exception:
                    pass
            # Fallback to HLTV when DB has no per-map data (free-tier PandaScore gap)
            if not map_rates_a and self._hltv:
                try:
                    map_rates_a = await self._hltv.get_map_win_rates(team_a)
                except Exception:
                    pass
            if not map_rates_b and self._hltv:
                try:
                    map_rates_b = await self._hltv.get_map_win_rates(team_b)
                except Exception:
                    pass

        # Compute conditional probability
        if map_rates_a and map_rates_b:
            # Build veto_order from available map data
            veto_order = self._derive_veto_order(
                map_rates_a, map_rates_b, best_of
            )
            if veto_order:
                try:
                    model_prob = series_prob_with_map_veto(
                        team_a_map_rates=map_rates_a,
                        team_b_map_rates=map_rates_b,
                        veto_order=veto_order,
                        maps_won_a=maps_a,
                        maps_won_b=maps_b,
                    )
                    logger.debug(
                        "EsportsSeriesBot: map_veto model used",
                        match_id=match_id, veto_order=veto_order,
                        model_prob=round(model_prob, 4),
                    )
                except Exception:
                    model_prob = self._simple_series_prob(
                        maps_a, maps_b, best_of
                    )
            else:
                model_prob = self._simple_series_prob(maps_a, maps_b, best_of)
        else:
            model_prob = self._simple_series_prob(maps_a, maps_b, best_of)

        if model_prob is None or not (0.01 < model_prob < 0.99):
            return []

        # Find matching Polymarket market
        market_info = await self._find_series_market(
            match_id, game, team_a, team_b, db
        )
        if not market_info:
            return []

        market_price = market_info.get("price", 0.5)
        market_id = market_info.get("market_id")
        token_id = market_info.get("token_id")

        if not market_id or not token_id:
            return []

        # Determine side and edge
        edge_yes = model_prob - market_price
        edge_no = market_price - model_prob

        side = None
        trade_token_id = token_id
        trade_price = market_price
        edge = 0.0

        if edge_yes >= self._min_edge:
            side = "YES"
            edge = edge_yes
        elif edge_no >= self._min_edge:
            side = "NO"
            trade_price = 1.0 - market_price
            edge = edge_no
            # Use NO token if available
            no_token_id = market_info.get("no_token_id")
            if no_token_id:
                trade_token_id = no_token_id

        if not side:
            return []

        # Don't trade if market already prices in reverse sweep
        if (maps_a > maps_b and side == "NO") or (maps_b > maps_a and side == "YES"):
            trailing_price = market_price if side == "NO" else (1.0 - market_price)
            if trailing_price > self._reverse_sweep_floor:
                logger.debug(
                    "EsportsSeriesBot: reverse sweep already priced in",
                    match_id=match_id,
                    trailing_price=round(trailing_price, 3),
                    floor=self._reverse_sweep_floor,
                )

        confidence = model_prob if side == "YES" else (1.0 - model_prob)

        # Cache prediction for WS reactive path
        import time as _time
        self._series_prediction_cache[market_id] = {
            "prob": model_prob, "ts": _time.monotonic(), "game": game,
        }

        # Log prediction for accuracy tracking
        try:
            from esports.data.esports_db import log_prediction
            await log_prediction(
                db=db,
                match_id=match_id,
                game=game,
                market_id=market_id,
                bot_name="EsportsSeriesBot",
                predicted_prob=model_prob,
                market_price=market_price,
                side=side,
                edge=round(edge, 4),
            )
        except Exception as exc:
            logger.warning("EsportsSeriesBot: prediction logging failed", error=str(exc))

        match_opp = {
            "type": "esports_series",
            "market_id": market_id,
            "token_id": str(trade_token_id),
            "side": side,
            "price": trade_price,
            "confidence": confidence,
            "prediction": model_prob,
            "edge": round(edge, 4),
            "game": game,
            "market_type": "match_winner",
            "series_score": f"{maps_a}-{maps_b}",
            "best_of": best_of,
        }
        result = [match_opp]

        # P6.4: Check for current-map hedge opportunity (correlated entry)
        hedge_enabled = getattr(settings, "ESPORTS_SERIES_HEDGE_ENABLED", True)
        if hedge_enabled:
            current_map = maps_a + maps_b + 1
            map_market = await self._find_current_map_market(
                match_id, game, team_a, team_b, current_map, db
            )
            if map_market:
                map_price = float(map_market.get("price") or 0.5)
                map_edge_yes = model_prob - map_price
                map_edge_no = map_price - model_prob
                map_side = None
                map_trade_price = map_price
                map_edge_val = 0.0
                if side == "YES" and map_edge_yes >= self._min_edge:
                    map_side = "YES"
                    map_edge_val = map_edge_yes
                elif side == "NO" and map_edge_no >= self._min_edge:
                    map_side = "NO"
                    map_trade_price = 1.0 - map_price
                    map_edge_val = map_edge_no
                if map_side:
                    map_conf = model_prob if map_side == "YES" else (1.0 - model_prob)
                    result.append({
                        "type": "esports_series_hedge",
                        "market_id": map_market["market_id"],
                        "token_id": str(map_market["token_id"]),
                        "side": map_side,
                        "price": map_trade_price,
                        "confidence": map_conf,
                        "prediction": model_prob,
                        "edge": round(map_edge_val, 4),
                        "game": game,
                        "market_type": "map_winner",
                        "series_score": f"{maps_a}-{maps_b}",
                        "map_number": current_map,
                    })
                    # P7.2: Log hedge opp for accuracy calibration
                    try:
                        from esports.data.esports_db import log_prediction as _log_pred
                        await _log_pred(
                            db=db,
                            match_id=match_id,
                            game=game,
                            market_id=map_market["market_id"],
                            bot_name="EsportsSeriesBot",
                            predicted_prob=model_prob,
                            market_price=map_price,
                            side=map_side,
                            edge=round(map_edge_val, 4),
                        )
                    except Exception as _exc:
                        logger.warning(
                            "EsportsSeriesBot: hedge prediction logging failed",
                            error=str(_exc),
                        )

        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    def _simple_series_prob(
        self, maps_a: int, maps_b: int, best_of: int
    ) -> Optional[float]:
        """Fallback: use uniform game win rate (0.50) for series probability."""
        from esports.models.series_model import bo3_match_prob, bo5_match_prob

        if best_of == 3:
            return bo3_match_prob(0.50, maps_a, maps_b)
        elif best_of == 5:
            return bo5_match_prob(0.50, maps_a, maps_b)
        return None

    @staticmethod
    def _smoczynski_tomkins_allocate(
        opps: List[Dict], group_budget: float, kelly_mult: float = 0.25,
    ) -> Dict[str, float]:
        """Optimal Kelly allocation for correlated series bets.

        When multiple series are active simultaneously, bets share the same
        capital pool. S-T allocation sizes them proportionally by Kelly edge
        rather than independently, preventing over-deployment.

        Args:
            opps: Opportunities from _analyze_series(), each with
                  confidence, price, edge, market_id.
            group_budget: Maximum USD to deploy across all series.
            kelly_mult: Fractional Kelly multiplier (default 0.25).

        Returns:
            Dict mapping market_id → USD allocation.
        """
        if not opps or group_budget <= 0:
            return {}

        edges: Dict[str, float] = {}
        for opp in opps:
            p = opp.get("confidence", 0.5)
            price = opp.get("price", 0.5)
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

        total_edge = sum(edges.values())
        if total_edge <= 0:
            return {}

        allocations = {}
        for mid, f_i in edges.items():
            share = (f_i / total_edge) * kelly_mult * group_budget
            allocations[mid] = round(max(1.0, share), 2)

        total = sum(allocations.values())
        if total > group_budget:
            scale = group_budget / total
            allocations = {mid: round(v * scale, 2) for mid, v in allocations.items()}

        return allocations

    @staticmethod
    def _derive_veto_order(
        rates_a: Dict[str, float],
        rates_b: Dict[str, float],
        best_of: int,
    ) -> List[str]:
        """Derive plausible map veto order from team map preferences.

        CS2 BO3 veto: team_a picks best map, team_b picks best map,
        decider is the remaining map with highest combined interest.
        BO5: each team picks 2 best maps, decider is remaining best.

        Returns list of map names in play order, or empty if insufficient data.
        """
        pool = set(rates_a.keys()) | set(rates_b.keys())
        if len(pool) < best_of:
            return []

        picks_per_team = 1 if best_of == 3 else 2
        veto_order: List[str] = []
        remaining = set(pool)

        # Team A picks their best maps
        a_sorted = sorted(remaining, key=lambda m: rates_a.get(m, 0.5), reverse=True)
        for m in a_sorted[:picks_per_team]:
            veto_order.append(m)
            remaining.discard(m)

        # Team B picks their best maps
        b_sorted = sorted(remaining, key=lambda m: rates_b.get(m, 0.5), reverse=True)
        for m in b_sorted[:picks_per_team]:
            veto_order.append(m)
            remaining.discard(m)

        # Decider: highest combined win rate from remaining pool
        if remaining:
            decider = max(
                remaining,
                key=lambda m: rates_a.get(m, 0.5) + rates_b.get(m, 0.5),
            )
            veto_order.append(decider)

        return veto_order[:best_of]

    async def _find_current_map_market(
        self,
        match_id: str,
        game: str,
        team_a: str,
        team_b: str,
        current_map: int,
        db=None,
    ) -> Optional[Dict]:
        """Find Polymarket map-winner market for the current map being played.

        Reuses the cached find_markets_for_match() result — no extra API call.
        Filters for market_type=map_winner and checks question for map number.
        """
        if not self._scanner:
            return None
        try:
            team_names = [n for n in (team_a, team_b) if n]
            markets = await asyncio.wait_for(
                self._scanner.find_markets_for_match(
                    match_id, game, db=db, team_names=team_names or None,
                ),
                timeout=5.0,
            )
            map_patterns = [f"map {current_map}", f"game {current_map}"]
            for m in (markets or []):
                if m.get("market_type") != "map_winner":
                    continue
                q = str(m.get("question", "")).lower()
                if any(p in q for p in map_patterns):
                    return m
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _find_series_market(
        self,
        match_id: str,
        game: str,
        team_a: str,
        team_b: str,
        db=None,
    ) -> Optional[Dict]:
        """Find matching Polymarket match-winner market for this series."""
        if not self._scanner:
            return None
        try:
            team_names = [n for n in (team_a, team_b) if n]
            markets = await asyncio.wait_for(
                self._scanner.find_markets_for_match(
                    match_id, game, db=db, team_names=team_names or None,
                ),
                timeout=5.0,
            )
            if markets:
                return markets[0]
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _refresh_series(self) -> None:
        """Refresh active BO3/BO5 series from PandaScore (configurable interval)."""
        import time
        now = time.monotonic()
        refresh_interval = int(getattr(settings, "ESPORTS_SERIES_REFRESH_INTERVAL", 30))
        if now - self._last_refresh < refresh_interval:
            return
        self._last_refresh = now

        if not self._pandascore:
            return

        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=30.0
            )
            new_series: Dict[str, Dict] = {}
            for match in (live or []):
                # match is an EsportsMatch dataclass, not a dict
                best_of = getattr(match, "best_of", 1)
                if best_of < 3:
                    continue
                mid = str(getattr(match, "match_id", ""))
                if not mid:
                    continue

                team_a = getattr(match, "team_a", "")
                team_b = getattr(match, "team_b", "")
                score_a = getattr(match, "score_a", 0)
                score_b = getattr(match, "score_b", 0)
                game = getattr(match, "game", "")

                new_series[mid] = {
                    "match_id": mid,
                    "game": game,
                    "team_a": team_a,
                    "team_b": team_b,
                    "score_maps_a": score_a,
                    "score_maps_b": score_b,
                    "best_of": best_of,
                }

            # Prune ended series (no longer in PandaScore live feed)
            stale = set(self._active_series) - set(new_series)
            if stale:
                logger.debug(
                    "EsportsSeriesBot: pruning ended series",
                    pruned=len(stale),
                    match_ids=list(stale)[:5],
                )

            self._active_series = new_series
        except (asyncio.TimeoutError, Exception) as exc:
            logger.info("EsportsSeriesBot: refresh failed", error=str(exc))

    async def _execute_series_trade(self, opp: Dict) -> None:
        """Execute series trade using own bankroll manager.

        If _st_size_override is set (by S-T allocator), use that directly
        instead of independent Kelly sizing.
        """
        st_override = opp.pop("_st_size_override", None)

        if st_override and st_override >= 1.0:
            size = st_override
        elif self._bankroll_mgr:
            db = getattr(self.base_engine, "db", None)
            try:
                size = await self._bankroll_mgr.get_bet_size(
                    fair_prob=opp["confidence"],
                    market_price=opp["price"],
                    game=opp.get("game", ""),
                    market_type="series",
                    db=db,
                )
            except Exception:
                size = 0.0
        else:
            size = 0.0

        if size <= 0:
            return

        order = await self.place_order(
            market_id=opp["market_id"],
            token_id=opp["token_id"],
            side=opp["side"],
            size=size,
            price=opp["price"],
            confidence=opp["confidence"],
        )

        if order and order.get("success"):
            logger.info(
                "EsportsSeriesBot trade executed",
                game=opp.get("game"),
                series_score=opp.get("series_score"),
                best_of=opp.get("best_of"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(opp["confidence"], 3),
                edge=opp.get("edge"),
                size=round(size, 2),
            )
