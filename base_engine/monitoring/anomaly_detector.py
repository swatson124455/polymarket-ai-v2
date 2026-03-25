"""
Anomaly detector (#35) - flag unusual price/volume activity.

Uses simple z-score or IQR on recent data; writes to data_quality_issues
and optionally calls a webhook or callback.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class AnomalyDetector:
    """
    Detect anomalies in market prices/volumes and record them.

    Writes to data_quality_issues table; optionally invokes webhook or callback.
    """

    def __init__(
        self,
        db: Optional[Any] = None,
        z_threshold: float = 3.0,
        min_samples: int = 10,
        lookback_hours: int = 24,
    ):
        self.db = db
        self.z_threshold = z_threshold
        self.min_samples = min_samples
        self.lookback_hours = lookback_hours

    @staticmethod
    def _mean_std(values: List[float]) -> tuple:
        if len(values) < 2:
            return 0.0, 1.0
        n = len(values)
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1) if n > 1 else 0.0
        std = (var ** 0.5) if var > 0 else 1.0
        return mean, std

    async def _get_recent_prices_by_market(self, limit_markets: int = 50) -> Dict[str, List[float]]:
        """Fetch recent prices per market from DB."""
        if not self.db or not self.db.session_factory:
            return {}
        since = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)
        try:
            from sqlalchemy import select
            from base_engine.data.database import MarketPrice
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(MarketPrice.market_id, MarketPrice.price)
                    .where(MarketPrice.timestamp >= since)
                    .where(MarketPrice.price.isnot(None))
                )
                rows = result.all()
            by_market: Dict[str, List[float]] = {}
            for mid, price in rows:
                if mid and price is not None:
                    by_market.setdefault(mid, []).append(float(price))
            return dict(list(by_market.items())[:limit_markets])
        except Exception as e:
            logger.debug("anomaly_detector price fetch failed: %s", e)
            return {}

    async def _insert_quality_issue(self, market_id: str, issue_type: str, description: str) -> None:
        """Insert one row into data_quality_issues."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from base_engine.data.database import DataQualityIssue
            async with self.db.get_session() as session:
                session.add(
                    DataQualityIssue(
                        market_id=market_id,
                        issue_type=issue_type,
                        description=description,
                        detected_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning("anomaly_detector insert_quality_issue failed: %s", e)

    async def run_detection(
        self,
        on_anomaly: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run one pass: find price anomalies (z-score), write to data_quality_issues, optionally callback.
        Returns list of detected anomalies.
        """
        anomalies: List[Dict[str, Any]] = []
        by_market = await self._get_recent_prices_by_market(limit_markets=100)
        for market_id, prices in by_market.items():
            if len(prices) < self.min_samples:
                continue
            mean, std = self._mean_std(prices)
            if std <= 0:
                continue
            for p in prices:
                z = abs((p - mean) / std) if std else 0
                if z >= self.z_threshold:
                    desc = f"price {p:.3f} z-score {z:.2f} (mean={mean:.3f}, std={std:.3f})"
                    rec = {
                        "market_id": market_id,
                        "issue_type": "price_anomaly",
                        "description": desc,
                        "value": p,
                        "z_score": z,
                        "mean": mean,
                        "std": std,
                    }
                    anomalies.append(rec)
                    await self._insert_quality_issue(market_id, "price_anomaly", desc)
                    if on_anomaly:
                        try:
                            if asyncio.iscoroutinefunction(on_anomaly):
                                await on_anomaly(rec)
                            else:
                                on_anomaly(rec)
                        except Exception as e:
                            logger.warning("anomaly_detector on_anomaly error: %s", e)
                    break
        return anomalies
