"""
Glosten-Milgrom Spread Decomposition (P3-08).

Decomposes bid-ask spread into information component (adverse selection cost)
and friction component (inventory risk + processing). When information_share
is high, informed traders dominate — dangerous to trade. When low, mostly
friction — safe to provide liquidity or arb.

Uses fill_analysis data for adverse move measurements.
"""
from typing import Dict, Any, Optional, List
from structlog import get_logger

logger = get_logger()


class SpreadDecomposer:
    """Compute information share of spread from fill_analysis adverse moves."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def compute_information_share(self, market_id: str, lookback_fills: int = 50) -> Dict[str, Any]:
        """
        Compute what fraction of the spread is due to informed trading (adverse selection).

        Returns:
            information_share: 0.0-1.0 (fraction of spread from adverse selection)
            recommendation: 'safe_to_trade' | 'caution' | 'avoid'
            n_fills: number of fills used
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return {"information_share": 0.5, "recommendation": "caution", "n_fills": 0}

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT adverse_move_30s, fill_price, fill_side
                    FROM fill_analysis
                    WHERE market_id = :mid
                      AND adverse_move_30s IS NOT NULL
                    ORDER BY fill_time DESC
                    LIMIT :lim
                """), {"mid": market_id, "lim": lookback_fills})
                rows = r.fetchall()

            if len(rows) < 5:
                return {"information_share": 0.5, "recommendation": "caution", "n_fills": len(rows)}

            adverse_moves = [abs(float(r[0])) for r in rows]
            prices = [float(r[1]) for r in rows]

            # Estimate spread as 2 * avg distance from 0.5 (proxy when we don't have orderbook)
            avg_price = sum(prices) / len(prices)
            estimated_half_spread = abs(avg_price - 0.5) * 0.1 + 0.005  # minimum 0.5%

            avg_adverse = sum(adverse_moves) / len(adverse_moves)
            information_share = min(1.0, avg_adverse / max(estimated_half_spread, 0.001))

            if information_share > 0.6:
                rec = "avoid"
            elif information_share > 0.3:
                rec = "caution"
            else:
                rec = "safe_to_trade"

            return {
                "information_share": round(information_share, 4),
                "avg_adverse_move": round(avg_adverse, 6),
                "recommendation": rec,
                "n_fills": len(rows),
            }
        except Exception as e:
            logger.debug("Spread decomposition failed: %s", e)
            return {"information_share": 0.5, "recommendation": "caution", "n_fills": 0}
