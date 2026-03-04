"""
Historical Replay (#44) - replay past market conditions for backtesting.

Feed historical prices into strategy in chronological order for testing.
"""
import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()


class ReplayEngine:
    """
    Replay historical prices market-by-market or tick-by-tick for backtesting.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def get_price_stream(
        self,
        market_id: str,
        since: datetime,
        until: Optional[datetime] = None,
        batch_size: int = 500,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """
        Yield batches of price records for market_id in chronological order.
        """
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        from sqlalchemy import select
        from base_engine.data.database import MarketPrice
        until = until or datetime.now(timezone.utc)
        offset = 0
        while True:
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(MarketPrice)
                    .where(
                        MarketPrice.market_id == market_id,
                        MarketPrice.timestamp >= since,
                        MarketPrice.timestamp <= until,
                    )
                    .order_by(MarketPrice.timestamp.asc())
                    .offset(offset)
                    .limit(batch_size)
                )
                rows = result.scalars().all()
            if not rows:
                break
            batch = [
                {
                    "market_id": r.market_id,
                    "token_id": r.token_id,
                    "price": float(r.price) if r.price else 0,
                    "side": r.side,
                    "timestamp": r.timestamp,
                }
                for r in rows
            ]
            yield batch
            offset += len(rows)
            if len(rows) < batch_size:
                break

    async def replay_market(
        self,
        market_id: str,
        since: datetime,
        until: Optional[datetime] = None,
        on_batch: Optional[Any] = None,
    ) -> int:
        """
        Replay all price batches for a market; call on_batch(batch) for each batch.
        Returns total records replayed.
        """
        total = 0
        async for batch in self.get_price_stream(market_id, since, until):
            total += len(batch)
            if on_batch:
                try:
                    if asyncio.iscoroutinefunction(on_batch):
                        await on_batch(batch)
                    else:
                        on_batch(batch)
                except Exception as e:
                    logger.warning("replay_engine on_batch error: %s", e)
        return total
