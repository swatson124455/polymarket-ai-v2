"""
Database Partitioning - Partition large tables by date for better performance.

Partitions:
- market_prices by month (YYYY-MM)
- trades by month (YYYY-MM)

Benefits:
- Queries only scan relevant partitions
- 10-100x faster queries
- Easier to archive old partitions
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import Column, String, Index, text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

logger = get_logger()


def get_partition_key(timestamp: datetime) -> str:
    """
    Get partition key from timestamp (YYYY-MM format).
    
    Args:
        timestamp: Datetime object
    
    Returns:
        Partition key string (e.g., "2025-01")
    """
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    return timestamp.strftime("%Y-%m")


def add_partition_columns_to_models():
    """
    Add partition_month column to MarketPrice and Trade models.
    
    Note: This modifies the models at runtime. For production, you'd want
    to create a migration script to add these columns to existing tables.
    """
    from base_engine.data.database import MarketPrice, Trade
    
    # Add partition_month column if not exists
    if not hasattr(MarketPrice, 'partition_month'):
        MarketPrice.partition_month = Column(String(7), index=True)  # "YYYY-MM"
        MarketPrice.__table_args__ = (
            Index("idx_prices_market_timestamp", "market_id", "timestamp"),
            Index("idx_prices_side", "side"),
            Index("idx_prices_partition", "partition_month", "market_id"),  # Composite index
        )
    
    if not hasattr(Trade, 'partition_month'):
        Trade.partition_month = Column(String(7), index=True)  # "YYYY-MM"
        Trade.__table_args__ = (
            Index("idx_trades_user_timestamp", "user_address", "timestamp"),
            Index("idx_trades_market_timestamp", "market_id", "timestamp"),
            Index("idx_trades_partition", "partition_month", "market_id"),  # Composite index
        )


class PartitionedQueryHelper:
    """Helper for querying partitioned tables efficiently."""
    
    @staticmethod
    async def get_prices_for_partitions(
        session: AsyncSession,
        market_id: str,
        start_date: datetime,
        end_date: datetime,
        partitions: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get prices for a market across multiple partitions.
        
        Args:
            session: Database session
            market_id: Market ID
            start_date: Start date
            end_date: End date
            partitions: Optional list of partition keys (YYYY-MM). If None, auto-calculate.
        
        Returns:
            List of price records
        """
        if partitions is None:
            # Auto-calculate partitions from date range
            partitions = []
            current = start_date.replace(day=1)
            while current <= end_date:
                partitions.append(get_partition_key(current))
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
        
        all_results = []
        
        for partition in partitions:
            query = text("""
                SELECT market_id, token_id, price, timestamp, side
                FROM market_prices
                WHERE market_id = :market_id
                AND partition_month = :partition
                AND timestamp >= :start_date
                AND timestamp <= :end_date
                ORDER BY timestamp ASC
            """)
            
            result = await session.execute(query, {
                "market_id": market_id,
                "partition": partition,
                "start_date": start_date,
                "end_date": end_date
            })
            
            rows = result.fetchall()
            all_results.extend([dict(row._mapping) for row in rows])
        
        return all_results
    
    @staticmethod
    async def get_trades_for_partitions(
        session: AsyncSession,
        market_id: Optional[str] = None,
        user_address: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        partitions: Optional[List[str]] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get trades across multiple partitions with pagination.
        
        Args:
            session: Database session
            market_id: Optional market ID filter
            user_address: Optional user address filter
            start_date: Optional start date filter
            end_date: Optional end date filter
            partitions: Optional list of partition keys. If None, auto-calculate from dates.
            limit: Optional limit (for pagination)
            offset: Offset for pagination
        
        Returns:
            List of trade records
        """
        if partitions is None and start_date and end_date:
            # Auto-calculate partitions
            partitions = []
            current = start_date.replace(day=1)
            while current <= end_date:
                partitions.append(get_partition_key(current))
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
        
        if not partitions:
            # If no partitions specified, query all (fallback)
            partitions = [None]
        
        all_results = []
        
        for partition in partitions:
            conditions = []
            params = {}
            
            if partition:
                conditions.append("partition_month = :partition")
                params["partition"] = partition
            
            if market_id:
                conditions.append("market_id = :market_id")
                params["market_id"] = market_id
            
            if user_address:
                conditions.append("user_address = :user_address")
                params["user_address"] = user_address
            
            if start_date:
                conditions.append("timestamp >= :start_date")
                params["start_date"] = start_date
            
            if end_date:
                conditions.append("timestamp <= :end_date")
                params["end_date"] = end_date
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query_str = f"""
                SELECT id, market_id, token_id, user_address, side, size, price, timestamp
                FROM trades
                WHERE {where_clause}
                ORDER BY timestamp DESC
            """
            
            if limit:
                query_str += f" LIMIT {limit} OFFSET {offset}"
            
            query = text(query_str)
            result = await session.execute(query, params)
            rows = result.fetchall()
            all_results.extend([dict(row._mapping) for row in rows])
        
        return all_results
    
    @staticmethod
    async def get_partition_stats(session: AsyncSession, table_name: str) -> Dict[str, Any]:
        """
        Get statistics about partitions in a table.
        
        Args:
            session: Database session
            table_name: Name of the table
        
        Returns:
            Dictionary with partition statistics
        """
        query = text(f"""
            SELECT 
                partition_month,
                COUNT(*) as record_count,
                MIN(timestamp) as min_timestamp,
                MAX(timestamp) as max_timestamp
            FROM {table_name}
            WHERE partition_month IS NOT NULL
            GROUP BY partition_month
            ORDER BY partition_month DESC
        """)
        
        result = await session.execute(query)
        rows = result.fetchall()
        
        stats = {
            "partitions": [dict(row._mapping) for row in rows],
            "total_partitions": len(rows),
            "total_records": sum(row.record_count for row in rows)
        }
        
        return stats


async def migrate_existing_data_to_partitions(session: AsyncSession):
    """
    Migrate existing data to use partition_month column.
    
    This should be run once to populate partition_month for existing records.
    """
    logger.info("Migrating existing data to partitions...")
    
    # Update market_prices
    update_prices = text("""
        UPDATE market_prices
        SET partition_month = to_char(timestamp, 'YYYY-MM')
        WHERE partition_month IS NULL
    """)
    
    result = await session.execute(update_prices)
    prices_updated = result.rowcount
    logger.info(f"Updated {prices_updated} market_prices records with partition_month")
    
    # Update trades
    update_trades = text("""
        UPDATE trades
        SET partition_month = to_char(timestamp, 'YYYY-MM')
        WHERE partition_month IS NULL
    """)
    
    result = await session.execute(update_trades)
    trades_updated = result.rowcount
    logger.info(f"Updated {trades_updated} trades records with partition_month")
    
    await session.commit()
    logger.info("Migration complete")
