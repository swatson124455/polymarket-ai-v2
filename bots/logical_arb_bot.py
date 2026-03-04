"""
Logical Arbitrage Bot — exploits cross-market logical constraint violations.

Uses LogicalArbitrageDetector to find markets where:
  - Mutual exclusivity: sum of YES prices > 1.0 (sell YES on most overpriced)
  - Subset violation: P(A) > P(B) when A implies B (sell subset, buy superset)
  - Complement violation: P(A) + P(B) != 1.0 when A = NOT B

$40M documented arbitrage profits from Polymarket logical mispricings
(IMDEA study, Apr 2024 - Apr 2025).
"""
import asyncio
from typing import Dict, List, Optional, Any
from structlog import get_logger
from bots.base_bot import BaseBot
from config.settings import settings

logger = get_logger()

# Max opportunities to execute per scan cycle (avoid overexposure)
MAX_OPPS_PER_SCAN = 3


def _get_yes_token_id(market: Dict[str, Any]) -> str:
    """Extract YES token ID from market dict, handling all API naming variants."""
    # Direct fields (unified_market_service format)
    tid = (market.get("yes_token_id") or market.get("yesTokenId") or "").strip()
    if tid:
        return tid
    # tokens array format (raw API)
    tokens = market.get("tokens", [])
    if tokens and isinstance(tokens, list):
        for t in tokens:
            if isinstance(t, dict):
                outcome = str(t.get("outcome", "")).upper()
                if outcome == "YES":
                    return str(t.get("tokenId") or t.get("token_id") or "").strip()
        # Fallback: first token is typically YES
        if isinstance(tokens[0], dict):
            return str(tokens[0].get("tokenId") or tokens[0].get("token_id") or "").strip()
    return ""


class LogicalArbBot(BaseBot):
    """
    Detects and trades logical constraint violations across related markets.

    Scan interval: 5min (LOGICAL_ARB_SCAN_INTERVAL_SECONDS).
    Uses sentence embeddings to group related markets, then checks
    mutual exclusivity, subset, and complement constraints.
    """

    def __init__(self, base_engine):
        super().__init__("LogicalArbBot", base_engine)
        self.min_spread = float(getattr(settings, "LOGICAL_ARB_MIN_SPREAD", 0.025))
        self.max_position_usd = float(getattr(settings, "LOGICAL_ARB_MAX_POSITION_USD", 200))
        self._detector = None  # Lazy init — needs async init()
        # Market cache: id -> full market dict (refreshed each scan)
        self._market_cache: Dict[str, Dict[str, Any]] = {}

    async def _get_detector(self):
        """Lazy-init LogicalArbitrageDetector (needs async init for embeddings)."""
        if self._detector is None:
            from base_engine.analysis.logical_arbitrage import LogicalArbitrageDetector
            db = getattr(self.base_engine, "db", None)
            cache = getattr(self.base_engine, "cache", None)
            self._detector = LogicalArbitrageDetector(db=db, cache=cache)
            await self._detector.init()
            logger.info("LogicalArbitrageDetector initialized",
                        cache_stats=self._detector.get_cache_stats())
        return self._detector

    async def scan_and_trade(self):
        """Fetch active markets, scan for logical constraint violations, execute up to 3."""
        detector = await self._get_detector()

        # Fetch active markets
        try:
            markets = await self.base_engine.get_markets(active=True, limit=500)
        except Exception as e:
            logger.warning("LogicalArbBot: market fetch failed", error=str(e))
            return
        if not markets:
            return

        # Build market cache for lookups during execution
        self._market_cache = {}
        for m in markets:
            if isinstance(m, dict) and m.get("id"):
                self._market_cache[str(m["id"])] = m

        # Scan for opportunities
        try:
            opportunities = await detector.scan_for_opportunities(
                markets, min_spread=self.min_spread,
            )
        except Exception as e:
            logger.warning("LogicalArbBot: scan failed", error=str(e))
            return

        if not opportunities:
            return

        logger.info("LogicalArbBot: opportunities found",
                    count=len(opportunities),
                    types=[o.get("type") for o in opportunities[:5]])

        # Execute top opportunities (sorted by spread, most profitable first)
        executed = 0
        for opp in opportunities:
            if executed >= MAX_OPPS_PER_SCAN:
                break
            try:
                success = await self._execute_logical_arb(opp)
                if success:
                    executed += 1
            except Exception as e:
                logger.warning("LogicalArbBot: execution failed",
                              opp_type=opp.get("type"), error=str(e))

        if executed > 0:
            logger.info("LogicalArbBot: scan cycle complete",
                        executed=executed, total_found=len(opportunities))

    async def _execute_logical_arb(self, opp: Dict[str, Any]) -> bool:
        """Route opportunity to the correct execution strategy. Returns True if a trade was placed."""
        opp_type = opp.get("type", "")
        if opp_type == "mutual_exclusivity":
            return await self._execute_mutual_exclusivity(opp)
        elif opp_type == "subset_violation":
            return await self._execute_subset_violation(opp)
        elif opp_type == "complement_violation":
            return await self._execute_complement_violation(opp)
        else:
            logger.debug("LogicalArbBot: unknown opportunity type", type=opp_type)
            return False

    async def _execute_mutual_exclusivity(self, opp: Dict[str, Any]) -> bool:
        """
        Sum of YES prices > 1.0 for mutually exclusive events.
        Strategy: sell YES on the most overpriced market in the group.
        """
        market_ids = opp.get("markets", [])
        prices = opp.get("prices", [])
        if not market_ids or not prices or len(market_ids) != len(prices):
            return False

        # Find the most overpriced market (highest YES price)
        max_idx = max(range(len(prices)), key=lambda i: prices[i])
        target_id = str(market_ids[max_idx])
        target_price = prices[max_idx]

        market = self._market_cache.get(target_id)
        if not market:
            return False

        token_id = _get_yes_token_id(market)
        if not token_id:
            logger.debug("LogicalArbBot: no token ID for market", market_id=target_id)
            return False

        # Risk check
        risk = await self.base_engine.risk_manager.check_risk_limits(
            bot_name=self.bot_name, market_id=target_id,
            size=self.max_position_usd, price=target_price,
            confidence=min(0.95, 0.5 + opp.get("spread", 0) * 5),
        )
        if not risk.get("allowed"):
            logger.debug("LogicalArbBot: risk check blocked mutual_exclusivity",
                        reasons=risk.get("reasons"))
            return False

        # Size position
        confidence = min(0.95, 0.5 + opp.get("spread", 0) * 5)
        size = await self.calculate_bot_position_size(confidence, target_price)
        size = min(size, self.max_position_usd)
        if size <= 0:
            return False

        result = await self.place_order(
            market_id=target_id, token_id=token_id,
            side="NO", size=size, price=target_price,
            confidence=confidence,
        )
        if result.get("success"):
            logger.info("LogicalArbBot: mutual_exclusivity trade",
                        market_id=target_id, side="NO",
                        spread=opp.get("spread"), size=round(size, 2))
            return True
        return False

    async def _execute_subset_violation(self, opp: Dict[str, Any]) -> bool:
        """
        P(subset) > P(superset) when subset implies superset.
        Strategy: sell YES on subset, buy YES on superset.
        """
        subset_id = str(opp.get("subset_market", ""))
        superset_id = str(opp.get("superset_market", ""))
        subset_price = opp.get("subset_price", 0)
        superset_price = opp.get("superset_price", 0)

        subset_market = self._market_cache.get(subset_id)
        superset_market = self._market_cache.get(superset_id)
        if not subset_market or not superset_market:
            return False

        subset_token = _get_yes_token_id(subset_market)
        superset_token = _get_yes_token_id(superset_market)
        if not subset_token or not superset_token:
            return False

        confidence = min(0.95, 0.5 + opp.get("spread", 0) * 5)
        size = await self.calculate_bot_position_size(confidence, subset_price)
        size = min(size, self.max_position_usd)
        if size <= 0:
            return False

        # Risk check on both legs
        for mid, price in [(subset_id, subset_price), (superset_id, superset_price)]:
            risk = await self.base_engine.risk_manager.check_risk_limits(
                bot_name=self.bot_name, market_id=mid,
                size=size, price=price, confidence=confidence,
            )
            if not risk.get("allowed"):
                logger.debug("LogicalArbBot: risk check blocked subset_violation",
                            market_id=mid, reasons=risk.get("reasons"))
                return False

        # Leg 1: sell YES on overpriced subset (buy NO)
        r1 = await self.place_order(
            market_id=subset_id, token_id=subset_token,
            side="NO", size=size, price=subset_price,
            confidence=confidence,
        )
        if not r1.get("success"):
            return False

        # Leg 2: buy YES on underpriced superset
        r2 = await self.place_order(
            market_id=superset_id, token_id=superset_token,
            side="YES", size=size, price=superset_price,
            confidence=confidence,
        )
        if r2.get("success"):
            logger.info("LogicalArbBot: subset_violation trade",
                        subset=subset_id, superset=superset_id,
                        spread=opp.get("spread"), size=round(size, 2))
            return True

        logger.warning("LogicalArbBot: subset_violation leg 2 failed (leg 1 may be orphaned)",
                       subset=subset_id, superset=superset_id)
        return False

    async def _execute_complement_violation(self, opp: Dict[str, Any]) -> bool:
        """
        P(A) + P(B) != 1.0 when A = NOT B.
        Strategy: if sum < 1.0 buy both YES; if sum > 1.0 sell both YES.
        """
        market_a_id = str(opp.get("market_a", ""))
        market_b_id = str(opp.get("market_b", ""))
        price_a = opp.get("price_a", 0)
        price_b = opp.get("price_b", 0)
        complement_sum = opp.get("sum", price_a + price_b)

        market_a = self._market_cache.get(market_a_id)
        market_b = self._market_cache.get(market_b_id)
        if not market_a or not market_b:
            return False

        token_a = _get_yes_token_id(market_a)
        token_b = _get_yes_token_id(market_b)
        if not token_a or not token_b:
            return False

        # Buy both YES if sum < 1.0 (underpriced), sell both YES if sum > 1.0 (overpriced)
        side = "YES" if complement_sum < 1.0 else "NO"
        avg_price = (price_a + price_b) / 2 if (price_a + price_b) > 0 else 0.5

        confidence = min(0.95, 0.5 + opp.get("spread", 0) * 5)
        size = await self.calculate_bot_position_size(confidence, avg_price)
        size = min(size, self.max_position_usd)
        if size <= 0:
            return False

        # Risk check on both legs
        for mid, price in [(market_a_id, price_a), (market_b_id, price_b)]:
            risk = await self.base_engine.risk_manager.check_risk_limits(
                bot_name=self.bot_name, market_id=mid,
                size=size, price=price, confidence=confidence,
            )
            if not risk.get("allowed"):
                logger.debug("LogicalArbBot: risk check blocked complement_violation",
                            market_id=mid, reasons=risk.get("reasons"))
                return False

        r1 = await self.place_order(
            market_id=market_a_id, token_id=token_a,
            side=side, size=size, price=price_a,
            confidence=confidence,
        )
        r2 = await self.place_order(
            market_id=market_b_id, token_id=token_b,
            side=side, size=size, price=price_b,
            confidence=confidence,
        )
        if r1.get("success") or r2.get("success"):
            logger.info("LogicalArbBot: complement_violation trade",
                        market_a=market_a_id, market_b=market_b_id,
                        side=side, sum=complement_sum,
                        spread=opp.get("spread"), size=round(size, 2))
            return True
        return False

    async def analyze_opportunity(self, market_data: Dict) -> Optional[Dict]:
        """Not used — LogicalArbBot is scan-driven, not opportunity-driven."""
        return None
