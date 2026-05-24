"""
SmartDataFetcher - Predict which markets are likely to have activity and limit fetches to those.
Reduces API calls and rate-limit risk by only updating predicted-active markets.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, List

from structlog import get_logger

logger = get_logger()


class SmartDataFetcher:
    """
    Scores active markets by predicted activity (recent trades, time to close, volume).
    Use predict_active_market_ids(top_n) and pass result to ingestion for price updates.
    """

    def __init__(self, db: Any):
        self.db = db
        self._activity_threshold = 0.5

    async def predict_active_market_ids(self, top_n: int = 200) -> List[str]:
        """
        Return market IDs most likely to have activity in the next period.
        Uses heuristics: recent trade count, days until close, volume.
        """
        if not self.db or not self.db.session_factory:
            return []
        try:
            markets = await self.db.get_active_markets_for_activity(limit=top_n * 3)
            if not markets:
                return []
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            trade_counts = await self.db.get_trade_counts_since(since)
            scored: List[tuple] = []
            for m in markets:
                score = self._calculate_activity_score(m, trade_counts.get(m["id"], 0))
                if score >= self._activity_threshold:
                    scored.append((m["id"], score))
            scored.sort(key=lambda x: x[1], reverse=True)
            out = [mid for mid, _ in scored[:top_n]]
            logger.info(
                "SmartDataFetcher predicted active markets",
                total_active=len(markets),
                above_threshold=len(scored),
                returning=len(out),
            )
            return out
        except Exception as e:
            logger.warning("SmartDataFetcher failed, caller should fall back to top-N", error=str(e))
            return []

    def _calculate_activity_score(self, market: dict, recent_trade_count: int) -> float:
        """
        Higher score = more likely to have trades/price changes.
        Factors: recent volume (last 1h trades), days until close, overall volume.
        """
        score = 0.0
        if recent_trade_count > 0:
            score += min(recent_trade_count / 10.0, 0.5)
        volume = float(market.get("volume") or 0)
        if volume > 100_000:
            score += 0.2
        end_date = market.get("end_date_iso")
        if end_date:
            if hasattr(end_date, "tzinfo") and end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = end_date - now if hasattr(end_date, "__sub__") else None
            if delta is not None:
                days_left = delta.total_seconds() / 86400
                if days_left < 1:
                    score += 0.2
                elif days_left < 7:
                    score += 0.3
        return min(score, 1.0)
