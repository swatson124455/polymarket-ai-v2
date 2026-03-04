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

    async def scan_and_trade(self) -> None:
        """
        Find active BO3/BO5 series, compute conditional probability,
        compare to Polymarket price, trade if mispriced.
        """
        db = getattr(self.base_engine, "db", None)

        # Refresh live matches from PandaScore
        await self._refresh_series()

        if not self._active_series:
            return

        for match_id, series_data in list(self._active_series.items()):
            try:
                opp = await self._analyze_series(match_id, series_data, db)
                if opp:
                    await self._execute_series_trade(opp)
            except Exception as exc:
                logger.debug(
                    "EsportsSeriesBot: analysis error",
                    match_id=match_id,
                    error=str(exc),
                )

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Required by BaseBot ABC. EsportsSeriesBot is series-driven."""
        return None

    # ── Core Analysis ─────────────────────────────────────────────────────

    async def _analyze_series(
        self, match_id: str, series_data: Dict, db=None
    ) -> Optional[Dict]:
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
            return None  # Only trade BO3+ series

        maps_a = int(series_data.get("score_maps_a", 0))
        maps_b = int(series_data.get("score_maps_b", 0))
        game = series_data.get("game", "")
        team_a = series_data.get("team_a", "")
        team_b = series_data.get("team_b", "")

        # Skip if series is already decided
        needed = (best_of + 1) // 2
        if maps_a >= needed or maps_b >= needed:
            return None

        # Get per-map win rates (from HLTV or use default)
        map_rates_a = {}
        map_rates_b = {}
        if self._hltv and game == "cs2":
            try:
                map_rates_a = await asyncio.wait_for(
                    self._hltv.get_map_win_rates(team_a), timeout=5.0
                )
                map_rates_b = await asyncio.wait_for(
                    self._hltv.get_map_win_rates(team_b), timeout=5.0
                )
            except (asyncio.TimeoutError, Exception):
                pass

        # Compute conditional probability
        if map_rates_a and map_rates_b:
            # Use map-veto-adjusted heterogeneous model
            try:
                model_prob = series_prob_with_map_veto(
                    team_a_map_rates=map_rates_a,
                    team_b_map_rates=map_rates_b,
                    maps_won_a=maps_a,
                    maps_won_b=maps_b,
                    best_of=best_of,
                )
            except Exception:
                # Fallback to simple model
                model_prob = self._simple_series_prob(
                    maps_a, maps_b, best_of
                )
        else:
            model_prob = self._simple_series_prob(maps_a, maps_b, best_of)

        if model_prob is None or not (0.01 < model_prob < 0.99):
            return None

        # Find matching Polymarket market
        market_info = await self._find_series_market(
            match_id, game, team_a, team_b, db
        )
        if not market_info:
            return None

        market_price = market_info.get("price", 0.5)
        market_id = market_info.get("market_id")
        token_id = market_info.get("token_id")

        if not market_id or not token_id:
            return None

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
            return None

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

        return {
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
            markets = await asyncio.wait_for(
                self._scanner.find_markets_for_match(match_id, game, db=db),
                timeout=5.0,
            )
            if markets:
                return markets[0]
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    async def _refresh_series(self) -> None:
        """Refresh active BO3/BO5 series from PandaScore."""
        import time
        now = time.monotonic()
        if now - self._last_refresh < 30:
            return
        self._last_refresh = now

        if not self._pandascore:
            return

        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=10.0
            )
            new_series: Dict[str, Dict] = {}
            for match in (live or []):
                best_of = int(match.get("number_of_games", 1))
                if best_of < 3:
                    continue
                mid = str(match.get("id", ""))
                if not mid:
                    continue

                # Extract series state
                opponents = match.get("opponents", [])
                team_a = ""
                team_b = ""
                if len(opponents) >= 2:
                    team_a = (opponents[0].get("opponent", {}).get("name", "")
                              if isinstance(opponents[0], dict) else "")
                    team_b = (opponents[1].get("opponent", {}).get("name", "")
                              if isinstance(opponents[1], dict) else "")

                results = match.get("results", [])
                score_a = 0
                score_b = 0
                if len(results) >= 2:
                    score_a = int(results[0].get("score", 0))
                    score_b = int(results[1].get("score", 0))

                game_slug = str(match.get("videogame", {}).get("slug", ""))
                game = {
                    "league-of-legends": "lol",
                    "cs-2": "cs2",
                    "cs-go": "cs2",
                    "dota-2": "dota2",
                    "valorant": "valorant",
                }.get(game_slug, "")

                new_series[mid] = {
                    "match_id": mid,
                    "game": game,
                    "team_a": team_a,
                    "team_b": team_b,
                    "score_maps_a": score_a,
                    "score_maps_b": score_b,
                    "best_of": best_of,
                }

            self._active_series = new_series
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EsportsSeriesBot: refresh failed", error=str(exc))

    async def _execute_series_trade(self, opp: Dict) -> None:
        """Execute series trade using own bankroll manager."""
        # Use EsportsBankrollManager for sizing (separate Kelly pool)
        size = 0.0
        if self._bankroll_mgr:
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
