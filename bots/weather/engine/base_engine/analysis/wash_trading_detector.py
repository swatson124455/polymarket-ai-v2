"""
Wash trading detector — bid-ask spread vs volume analysis for liquidity quality.

Genuine markets: high volume correlates with tight spreads.
Wash traded markets: high volume with persistent wide spreads (fake liquidity).

Used by risk_manager to flag low-quality markets.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class WashTradingDetector:
    """
    Detect potential wash trading by analyzing spread-volume relationship.

    A market with high volume but consistently wide bid-ask spreads is
    suspicious — genuine high-volume markets should have tight spreads.
    """

    def __init__(self, db=None):
        self._db = db

    async def analyze_market(self, market_id: str) -> Dict[str, Any]:
        """
        Analyze a market for wash trading signals.

        Returns:
            Dict with keys: wash_score (0-1), spread_volume_ratio,
            volume_24h, avg_spread, is_suspicious.
        """
        result = {
            "market_id": market_id,
            "wash_score": 0.0,
            "spread_volume_ratio": 0.0,
            "volume_24h": 0.0,
            "avg_spread": 0.0,
            "is_suspicious": False,
        }

        if not self._db or not getattr(self._db, "session_factory", None):
            return result

        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                # Get 24h volume
                r = await session.execute(text("""
                    SELECT COALESCE(SUM(size * entry_price), 0) as volume_24h,
                           COUNT(*) as trade_count
                    FROM positions
                    WHERE market_id = :mid
                    AND created_at >= NOW() - INTERVAL '24 hours'
                """), {"mid": market_id})
                row = r.fetchone()
                volume_24h = float(row[0]) if row else 0
                trade_count = int(row[1]) if row else 0
                result["volume_24h"] = volume_24h

                # Get average spread from price history
                r2 = await session.execute(text("""
                    SELECT AVG(ABS(high_price - low_price)) as avg_spread,
                           STDDEV(ABS(high_price - low_price)) as spread_std
                    FROM market_prices
                    WHERE market_id = :mid
                    AND timestamp >= NOW() - INTERVAL '24 hours'
                    AND high_price IS NOT NULL AND low_price IS NOT NULL
                """), {"mid": market_id})
                row2 = r2.fetchone()
                avg_spread = float(row2[0]) if row2 and row2[0] else 0
                spread_std = float(row2[1]) if row2 and row2[1] else 0
                result["avg_spread"] = avg_spread

        except Exception as e:
            logger.debug("WashTradingDetector query failed for %s: %s", market_id, e)
            return result

        # Compute wash trading score
        if volume_24h > 0 and avg_spread > 0:
            # High volume + wide spread = suspicious
            # Normal: volume increases → spread decreases (inverse relationship)
            spread_volume_ratio = avg_spread / max(0.001, volume_24h / 10000)
            result["spread_volume_ratio"] = spread_volume_ratio

            # Score components
            score = 0.0

            # Wide spread for the volume level (main signal)
            if spread_volume_ratio > 0.1:
                score += min(0.4, spread_volume_ratio)

            # Very regular trade sizes (bots washing)
            # Low spread variance suggests automated pattern
            if spread_std > 0 and avg_spread > 0:
                cv = spread_std / avg_spread  # Coefficient of variation
                if cv < 0.1:  # Very consistent spreads = suspicious
                    score += 0.2

            # High trade count but small total volume = many small wash trades
            if trade_count > 0 and volume_24h > 0:
                avg_trade_size = volume_24h / trade_count
                if avg_trade_size < 1.0 and trade_count > 50:
                    score += 0.2

            result["wash_score"] = min(1.0, score)
            result["is_suspicious"] = result["wash_score"] > 0.5

        return result

    async def analyze_batch(self, market_ids: List[str]) -> Dict[str, Dict]:
        """Analyze multiple markets for wash trading."""
        results = {}
        for mid in market_ids:
            results[mid] = await self.analyze_market(mid)
        return results
