"""
Cross-platform arbitrage scanner.

Polls all enabled ExchangeAdapters in parallel, normalizes prices after
platform-specific fees, and identifies true arbitrage opportunities where
the same event is priced differently across venues.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from structlog import get_logger

from bots.weather.engine.base_engine.exchanges.base_adapter import ExchangeAdapter
from bots.weather.engine.base_engine.exchanges.models import FeeSchedule, MarketSnapshot

logger = get_logger()

# Minimum title similarity ratio for cross-platform market matching
# 0.55→0.40: cross-platform questions are worded differently
# e.g. Polymarket "Will BTC be above $100k?" vs Kalshi "BTC/USD > 100000"
MATCH_THRESHOLD = 0.40


@dataclass
class ArbOpportunity:
    """A detected cross-platform arbitrage opportunity."""
    event_question: str
    platform_a: str
    platform_b: str
    market_id_a: str
    market_id_b: str
    gross_price_a: float
    gross_price_b: float
    net_price_a: float   # After fees
    net_price_b: float   # After fees
    spread: float         # True spread after fees
    profit_pct: float
    side: str             # "buy_a_sell_b" or "buy_b_sell_a"
    question_a: str = ""  # Full question from platform A (for resolution verification)
    question_b: str = ""  # Full question from platform B (for resolution verification)


def _normalize_question(q: str) -> str:
    """Lowercase, strip, truncate for matching."""
    return (q or "").lower().strip()[:200]


def _match_score(q1: str, q2: str) -> float:
    """Similarity ratio between two market questions."""
    n1 = _normalize_question(q1)
    n2 = _normalize_question(q2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    return SequenceMatcher(None, n1, n2).ratio()


class ArbScanner:
    """
    Scans all adapters in parallel and finds cross-platform arbitrage.

    Usage:
        scanner = ArbScanner([poly_adapter, kalshi_adapter, ...])
        opps = await scanner.scan(min_profit_pct=2.0)
    """

    def __init__(self, adapters: List[ExchangeAdapter]):
        self._adapters = [a for a in adapters if a.is_enabled()]

    async def scan(self, min_profit_pct: float = 2.0) -> List[ArbOpportunity]:
        """Poll all adapters in parallel, match markets, find arbitrage."""
        if len(self._adapters) < 2:
            return []

        # Fetch markets from all platforms concurrently
        tasks = [a.get_markets(limit=200) for a in self._adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        platform_markets: Dict[str, List[MarketSnapshot]] = {}
        platform_fees: Dict[str, FeeSchedule] = {}
        for adapter, result in zip(self._adapters, results):
            name = adapter.platform_name()
            if isinstance(result, Exception):
                logger.debug("ArbScanner: %s fetch failed: %s", name, result)
                continue
            platform_markets[name] = result or []
            platform_fees[name] = adapter.fee_schedule()

        if len(platform_markets) < 2:
            return []

        # Cross-match markets between all platform pairs
        opportunities: List[ArbOpportunity] = []
        platforms = list(platform_markets.keys())

        for i in range(len(platforms)):
            for j in range(i + 1, len(platforms)):
                pa, pb = platforms[i], platforms[j]
                fees_a, fees_b = platform_fees[pa], platform_fees[pb]
                opps = self._find_arb_between(
                    platform_markets[pa], platform_markets[pb],
                    fees_a, fees_b, min_profit_pct,
                )
                opportunities.extend(opps)

        opportunities.sort(key=lambda o: o.profit_pct, reverse=True)
        return opportunities

    def _find_arb_between(
        self,
        markets_a: List[MarketSnapshot],
        markets_b: List[MarketSnapshot],
        fees_a: FeeSchedule,
        fees_b: FeeSchedule,
        min_profit_pct: float,
    ) -> List[ArbOpportunity]:
        """Find arbitrage between two platforms' market lists."""
        opps: List[ArbOpportunity] = []

        for ma in markets_a:
            if ma.yes_price is None:
                continue
            for mb in markets_b:
                if mb.yes_price is None:
                    continue
                score = _match_score(ma.question, mb.question)
                if score < MATCH_THRESHOLD:
                    continue

                # Compare prices after fees
                # Strategy: buy cheap YES on one platform, buy cheap NO on other
                net_a = fees_a.net_price_after_fees(ma.yes_price, "BUY")
                net_b = fees_b.net_price_after_fees(mb.yes_price, "BUY")

                # Compute arb profit per contract using actual price-dependent fees.
                # Arb: buy YES on cheap side + buy NO on expensive side → payout $1.
                if ma.yes_price < mb.yes_price:
                    cost = net_a + fees_b.net_price_after_fees(1.0 - mb.yes_price, "BUY")
                else:
                    cost = fees_a.net_price_after_fees(1.0 - ma.yes_price, "BUY") + net_b
                true_spread = 1.0 - cost

                if true_spread <= 0:
                    continue

                profit_pct = true_spread * 100
                if profit_pct < min_profit_pct:
                    continue

                side = "buy_a_sell_b" if ma.yes_price < mb.yes_price else "buy_b_sell_a"

                opps.append(ArbOpportunity(
                    event_question=ma.question[:120],
                    platform_a=ma.platform,
                    platform_b=mb.platform,
                    market_id_a=ma.market_id,
                    market_id_b=mb.market_id,
                    gross_price_a=ma.yes_price,
                    gross_price_b=mb.yes_price,
                    net_price_a=net_a,
                    net_price_b=net_b,
                    spread=true_spread,
                    profit_pct=profit_pct,
                    side=side,
                    question_a=ma.question or "",
                    question_b=mb.question or "",
                ))

        return opps
