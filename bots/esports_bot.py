"""
EsportsBot — Pre-game + live in-play esports trading bot.

All 4 game titles: LoL, CS2, Dota 2, Valorant.
Pre-game: prediction engine + model-vs-market edge validation.
Live: win probability model updates on game events via PandaScore.

Requires PANDASCORE_API_KEY — fails fast if missing.
Scan interval: 120s default, 10s during live matches.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from structlog import get_logger

from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()


class EsportsBot(BaseBot):
    """
    Pre-game + live in-play esports trading bot.

    Covers match_winner, map_winner, tournament_winner, total_maps
    across LoL, CS2, Dota 2, and Valorant on Polymarket.
    """

    def __init__(self, base_engine):
        super().__init__("EsportsBot", base_engine)

        # Fail fast if PandaScore API key not configured
        api_key = getattr(settings, "PANDASCORE_API_KEY", None)
        if not api_key:
            raise ValueError(
                "EsportsBot requires PANDASCORE_API_KEY — set it in .env"
            )

        self._api_key = api_key
        self._pandascore = None      # Lazy init in start()
        self._patch_drift = None     # PatchDriftDetector instance
        self._market_scanner = None  # EsportsMarketScanner instance
        self._lol_model = None       # LoLWinModel
        self._cs2_model = None       # CS2EconomyModel

        # Live match tracking
        self._live_matches: Dict[str, Dict] = {}  # match_id → PandaScore match data
        self._last_live_refresh = 0.0

        # Settings
        self._min_edge = float(getattr(settings, "ESPORTS_MIN_EDGE", 0.08))
        self._min_confidence = float(getattr(settings, "ESPORTS_MIN_CONFIDENCE", 0.55))
        self._maker_timeout = float(
            getattr(settings, "ESPORTS_MAKER_FALLBACK_TIMEOUT_S", 3.0)
        )

    def _get_scan_interval_seconds(self) -> float:
        """10s during live matches, 120s otherwise."""
        if self._live_matches:
            return float(getattr(settings, "SCAN_INTERVAL_ESPORTS_LIVE", 10))
        return float(getattr(settings, "SCAN_INTERVAL_ESPORTS", 120))

    async def start(self) -> None:
        """Initialize data clients and models, then start scan loop."""
        from esports.data.pandascore_client import PandaScoreClient
        from esports.models.patch_drift import PatchDriftDetector
        from esports.markets.esports_market_scanner import EsportsMarketScanner

        self._pandascore = PandaScoreClient(api_key=self._api_key)
        await self._pandascore.init()

        riot_key = getattr(settings, "RIOT_API_KEY", None)
        riot_client = None
        if riot_key:
            from esports.data.riot_api_client import RiotAPIClient
            riot_client = RiotAPIClient(api_key=riot_key)
            await riot_client.init()

        self._patch_drift = PatchDriftDetector(riot_client=riot_client)

        db = getattr(self.base_engine, "db", None)
        self._market_scanner = EsportsMarketScanner(db=db)

        # Load models (non-blocking)
        try:
            from esports.models.lol_win_model import LoLWinModel
            self._lol_model = LoLWinModel()
            self._lol_model.load()
        except Exception:
            logger.debug("EsportsBot: LoL model not loaded (no saved model yet)")

        try:
            from esports.models.cs2_economy_model import CS2EconomyModel
            self._cs2_model = CS2EconomyModel()
        except Exception:
            logger.debug("EsportsBot: CS2 model not loaded")

        logger.info(
            "EsportsBot: initialized",
            pandascore=True,
            riot_api=bool(riot_key),
            lol_model=self._lol_model is not None,
            cs2_model=self._cs2_model is not None,
        )

        await super().start()

    async def stop(self) -> None:
        """Clean up HTTP clients."""
        if self._pandascore:
            await self._pandascore.close()
        await super().stop()

    async def scan_and_trade(self) -> None:
        """
        Main scan loop body.

        1. Check patch observation mode
        2. Refresh live match data
        3. Get esports markets from Polymarket
        4. Analyze each market for edge
        """
        db = getattr(self.base_engine, "db", None)

        # Step 1: Patch drift check — skip live trading during observation mode
        if self._patch_drift:
            try:
                drift_status = await asyncio.wait_for(
                    self._patch_drift.check_all_games(), timeout=10.0
                )
                for game, status in drift_status.items():
                    if status.get("halted"):
                        logger.warning(
                            "EsportsBot: game halted (calibration failure)",
                            game=game,
                        )
            except (asyncio.TimeoutError, Exception) as exc:
                logger.debug("EsportsBot: patch drift check failed", error=str(exc))

        # Step 2: Refresh live match data from PandaScore
        await self._refresh_live_matches()

        # Step 3: Get esports markets from Polymarket
        markets = await self.base_engine.get_markets(active=True, limit=200)
        esports_markets = self.base_engine.filter_markets_for_trading(
            markets, categories=["esports"]
        )
        self._last_scan_markets = len(esports_markets) if esports_markets else 0
        if not esports_markets:
            return

        # Step 4: Analyze each market
        _opps = 0
        _trades = 0
        for market in esports_markets:
            try:
                opp = await self.analyze_opportunity(market)
                if opp:
                    _opps += 1
                    await self._execute_esports_trade(opp)
                    _trades += 1
            except Exception as exc:
                logger.debug("EsportsBot scan error: %s", exc)
        self._last_scan_opportunities = _opps
        self._last_scan_trades = _trades

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """
        Analyze a single esports market for trading opportunity.

        1. Classify market type + detect game
        2. Get model prediction (game-specific or prediction engine)
        3. Validate edge: model_prob - poly_price > ESPORTS_MIN_EDGE
        4. Build trade opportunity if edge exists
        """
        market_id = str(market_data.get("id", ""))
        if not market_id:
            return None

        tokens = market_data.get("tokens", [])
        if not tokens:
            return None

        token = tokens[0]
        price_raw = token.get("outcomePrice") or token.get("price")
        price = self.validate_price(price_raw, market_id)
        if price is None:
            return None

        token_id = token.get("tokenId") or token.get("token_id")
        if not token_id:
            return None

        question = (market_data.get("question") or "").lower()

        # Detect game title
        game = self._detect_game(question)
        if not game:
            return None

        # Check observation mode for this game
        if self._patch_drift and self._patch_drift.is_observation_mode(game):
            logger.debug(
                "EsportsBot: observation mode active (paper only)",
                game=game,
                market_id=market_id,
            )
            return None

        # Check if halted
        if self._patch_drift and self._patch_drift.is_halted(game):
            return None

        market_type = self._classify_market_type(question)

        # Get model prediction
        model_prob = await self._get_model_prediction(
            game, market_type, market_id, token_id, price, market_data
        )
        if model_prob is None:
            return None

        # Validate edge
        # YES side: model thinks YES is more likely than market price
        # NO side: model thinks YES is less likely than market price
        no_token = tokens[1] if len(tokens) > 1 else {}
        no_token_id = no_token.get("tokenId") or no_token.get("token_id")

        edge_yes = model_prob - price
        edge_no = (1.0 - model_prob) - (1.0 - price)  # simplifies to price - model_prob

        if edge_yes >= self._min_edge:
            side = "YES"
            trade_token_id = token_id
            trade_price = price
            edge = edge_yes
            confidence = model_prob
        elif -edge_yes >= self._min_edge and no_token_id:
            side = "NO"
            trade_token_id = no_token_id
            trade_price = 1.0 - price
            edge = -edge_yes
            confidence = 1.0 - model_prob
        else:
            return None

        if confidence < self._min_confidence:
            return None

        return {
            "type": "esports_pregame" if not self._is_live(market_id) else "esports_live",
            "market_id": market_id,
            "token_id": str(trade_token_id),
            "side": side,
            "price": trade_price,
            "confidence": confidence,
            "prediction": model_prob,
            "edge": round(edge, 4),
            "game": game,
            "market_type": market_type,
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _get_model_prediction(
        self,
        game: str,
        market_type: str,
        market_id: str,
        token_id: str,
        price: float,
        market_data: Dict,
    ) -> Optional[float]:
        """
        Get win probability from game-specific model or prediction engine.

        Returns model's estimated probability for YES outcome, or None.
        """
        # Try game-specific model for live matches
        live_data = self._live_matches.get(market_id)

        if game == "lol" and self._lol_model and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    prob = await asyncio.to_thread(
                        self._lol_model.predict, game_state
                    )
                    if 0.0 < prob < 1.0:
                        return prob
            except Exception:
                pass

        if game == "cs2" and self._cs2_model and live_data:
            try:
                game_state = live_data.get("game_state", {})
                if game_state:
                    prob = self._cs2_model.predict_match(
                        maps_won_a=live_data.get("score_maps_a", 0),
                        maps_won_b=live_data.get("score_maps_b", 0),
                        best_of=live_data.get("best_of", 1),
                        map_probs=game_state.get("map_probs"),
                    )
                    if 0.0 < prob < 1.0:
                        return prob
            except Exception:
                pass

        # Fallback: use base prediction engine
        try:
            prediction = await self.base_engine.get_predictions(
                market_id=market_id,
                token_id=token_id,
                price=price,
                correlation_id=getattr(self, "_current_correlation_id", None),
            )
            if prediction:
                pred_value = prediction.get("prediction")
                if pred_value is not None:
                    return float(pred_value)
        except Exception as exc:
            logger.debug("EsportsBot: prediction engine failed", error=str(exc))

        return None

    async def _refresh_live_matches(self) -> None:
        """Fetch live matches from PandaScore (rate-limited to every 15s)."""
        now = time.monotonic()
        if now - self._last_live_refresh < 15:
            return
        self._last_live_refresh = now

        if not self._pandascore:
            return

        try:
            live = await asyncio.wait_for(
                self._pandascore.get_live_matches(), timeout=10.0
            )
            new_live = {}
            for match in (live or []):
                mid = str(match.get("id", ""))
                if mid:
                    new_live[mid] = match
            self._live_matches = new_live
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("EsportsBot: live match refresh failed", error=str(exc))

    def _is_live(self, market_id: str) -> bool:
        """Check if a market has an associated live match."""
        return market_id in self._live_matches

    @staticmethod
    def _detect_game(question: str) -> Optional[str]:
        """Detect game title from market question text."""
        q = question.lower()
        if any(kw in q for kw in ("league of legends", "lol ", "lck", "lec", "lpl", "lcs", "worlds", "msi")):
            return "lol"
        if any(kw in q for kw in ("counter-strike", "cs2", "csgo", "blast premier", "esl", "pgl", "iem")):
            return "cs2"
        if any(kw in q for kw in ("dota", "the international", " ti ", "dpc")):
            return "dota2"
        if any(kw in q for kw in ("valorant", "vct", "champions tour")):
            return "valorant"
        return None

    @staticmethod
    def _classify_market_type(question: str) -> str:
        """Classify market type from question text."""
        q = question.lower()
        if any(kw in q for kw in ("map ", "game 1", "game 2", "game 3", "game 4", "game 5")):
            return "map_winner"
        if any(kw in q for kw in ("tournament", "championship", "champion", "split winner", "season")):
            return "tournament_winner"
        if any(kw in q for kw in ("total maps", "over", "under", "maps played")):
            return "total_maps"
        if any(kw in q for kw in ("first blood", "first kill")):
            return "first_blood"
        if any(kw in q for kw in ("mvp", "kills", "assists")):
            return "props"
        return "match_winner"

    async def _execute_esports_trade(self, opp: Dict) -> None:
        """Execute trade with maker-first, taker-fallback strategy."""
        size = await self.calculate_bot_position_size(opp["confidence"], opp["price"])
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
                "EsportsBot trade executed",
                type=opp.get("type"),
                game=opp.get("game"),
                market_type=opp.get("market_type"),
                market_id=opp["market_id"],
                side=opp["side"],
                price=opp["price"],
                confidence=round(opp["confidence"], 3),
                edge=opp.get("edge"),
                size=round(size, 2),
            )
