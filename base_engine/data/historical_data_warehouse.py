"""
Historical Data Warehouse
=========================
Comprehensive OHLCV dataset for backtesting and analysis.
Stores Open, High, Low, Close, Volume snapshots.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from base_engine.data.database import Database, MarketPrice

logger = get_logger()


class OHLCVSnapshot:
    """OHLCV data point"""
    def __init__(
        self,
        market_id: str,
        token_id: str,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        interval: str = "1h"
    ):
        self.market_id = market_id
        self.token_id = token_id
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.interval = interval


class HistoricalDataWarehouse:
    """
    Comprehensive OHLCV dataset for backtesting and analysis.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
    
    async def build_ohlcv(
        self,
        market_id: str,
        token_id: str,
        interval: str = "1h",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[OHLCVSnapshot]:
        """
        Build OHLCV data from price history.
        
        Args:
            market_id: Market ID
            token_id: Token ID
            interval: "1m", "5m", "15m", "1h", "4h", "1d"
            start_date: Optional start date
            end_date: Optional end date
        
        Returns:
            List of OHLCV snapshots
        """
        if not self.db or not self.db.session_factory:
            return []
        
        # Parse interval
        interval_seconds = self._parse_interval(interval)
        if not interval_seconds:
            logger.error(f"Invalid interval: {interval}")
            return []
        
        async with self.db.get_session() as session:
            from sqlalchemy import select, func, and_
            
            # Get price history
            query = select(MarketPrice).where(
                and_(
                    MarketPrice.market_id == market_id,
                    MarketPrice.token_id == token_id
                )
            )
            
            if start_date:
                query = query.where(MarketPrice.timestamp >= start_date)
            if end_date:
                query = query.where(MarketPrice.timestamp <= end_date)
            
            query = query.order_by(MarketPrice.timestamp)
            
            result = await session.execute(query)
            prices = result.scalars().all()
            
            if not prices:
                return []
            
            # Group prices by interval
            ohlcv_snapshots = []
            current_bucket_start = None
            current_bucket_prices = []
            
            for price in prices:
                price_time = price.timestamp
                
                # Calculate bucket start time
                bucket_start = self._get_bucket_start(price_time, interval_seconds)
                
                if current_bucket_start is None or bucket_start != current_bucket_start:
                    # Save previous bucket if exists
                    if current_bucket_prices:
                        snapshot = self._create_snapshot(
                            market_id, token_id, current_bucket_start,
                            current_bucket_prices, interval
                        )
                        if snapshot:
                            ohlcv_snapshots.append(snapshot)
                    
                    # Start new bucket
                    current_bucket_start = bucket_start
                    current_bucket_prices = [float(price.price)]
                else:
                    current_bucket_prices.append(float(price.price))
            
            # Save last bucket
            if current_bucket_prices:
                snapshot = self._create_snapshot(
                    market_id, token_id, current_bucket_start,
                    current_bucket_prices, interval
                )
                if snapshot:
                    ohlcv_snapshots.append(snapshot)
            
            logger.info(
                f"Built {len(ohlcv_snapshots)} OHLCV snapshots",
                market_id=market_id,
                interval=interval
            )
            
            return ohlcv_snapshots
    
    def _parse_interval(self, interval: str) -> Optional[int]:
        """Parse interval string to seconds"""
        interval_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400
        }
        return interval_map.get(interval.lower())
    
    def _get_bucket_start(self, timestamp: datetime, interval_seconds: int) -> datetime:
        """Get bucket start time for a timestamp"""
        # Round down to interval
        ts = timestamp.timestamp()
        bucket_ts = int(ts // interval_seconds) * interval_seconds
        return datetime.fromtimestamp(bucket_ts, tz=timezone.utc)
    
    def _create_snapshot(
        self,
        market_id: str,
        token_id: str,
        timestamp: datetime,
        prices: List[float],
        interval: str
    ) -> Optional[OHLCVSnapshot]:
        """Create OHLCV snapshot from prices in bucket"""
        if not prices:
            return None
        
        return OHLCVSnapshot(
            market_id=market_id,
            token_id=token_id,
            timestamp=timestamp,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=len(prices),  # Count as volume proxy
            interval=interval
        )
    
    async def get_ohlcv(
        self,
        market_id: str,
        token_id: str,
        interval: str = "1h",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Get OHLCV data as dicts.
        
        Returns:
            List of dicts with ohlcv data
        """
        snapshots = await self.build_ohlcv(
            market_id, token_id, interval, start_date, end_date
        )
        
        return [
            {
                "market_id": s.market_id,
                "token_id": s.token_id,
                "timestamp": s.timestamp.isoformat(),
                "open": s.open,
                "high": s.high,
                "low": s.low,
                "close": s.close,
                "volume": s.volume,
                "interval": s.interval
            }
            for s in snapshots
        ]
    
    async def export_ohlcv_csv(
        self,
        market_id: str,
        token_id: str,
        output_path: str,
        interval: str = "1h"
    ) -> int:
        """Export OHLCV data to CSV"""
        import csv
        
        ohlcv_data = await self.get_ohlcv(market_id, token_id, interval)
        
        if not ohlcv_data:
            return 0
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'open', 'high', 'low', 'close', 'volume'
            ])
            
            for row in ohlcv_data:
                writer.writerow([
                    row['timestamp'],
                    row['open'],
                    row['high'],
                    row['low'],
                    row['close'],
                    row['volume']
                ])
        
        logger.info(f"Exported {len(ohlcv_data)} OHLCV records to {output_path}")
        return len(ohlcv_data)
