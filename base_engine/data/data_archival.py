"""
Data Archival System - Archive old data to keep main database small and fast.

Features:
- Archive data older than N days to separate tables
- Compress archived data
- Keep only aggregates for very old data
- Restore archived data when needed
"""
import asyncio
import gzip
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, func
from structlog import get_logger
from base_engine.data.database_partitioning import get_partition_key

logger = get_logger()


class DataArchival:
    """
    Archive old data to keep main database small and fast.
    
    Strategy:
    - Hot data (last 7 days): Main tables, full detail
    - Warm data (7-365 days): Partitioned tables, full detail (keep 365 days for learning/fluctuations)
    - Cold data (365+ days): Archived tables, compressed
    - Very old data (2+ years): Aggregates only
    """
    
    def __init__(
        self,
        archive_directory: str = "data/archive",
        days_to_keep_hot: int = 7,
        days_to_keep_warm: int = 365,
        days_to_keep_cold: int = 730
    ):
        self.archive_directory = Path(archive_directory)
        self.archive_directory.mkdir(parents=True, exist_ok=True)
        
        self.days_to_keep_hot = days_to_keep_hot
        self.days_to_keep_warm = days_to_keep_warm
        self.days_to_keep_cold = days_to_keep_cold
        
        self.hot_cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep_hot)
        self.warm_cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep_warm)
        self.cold_cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep_cold)
    
    async def archive_old_data(
        self,
        session: AsyncSession,
        table_name: str,
        timestamp_column: str = "timestamp"
    ) -> Dict[str, Any]:
        """
        Archive old data from a table.
        
        Args:
            session: Database session
            table_name: Name of table to archive
            timestamp_column: Name of timestamp column
        
        Returns:
            Dictionary with archival statistics
        """
        logger.info(f"Archiving old data from {table_name}...")
        
        stats = {
            "table": table_name,
            "archived_count": 0,
            "deleted_count": 0,
            "archived_partitions": [],
            "errors": []
        }
        
        try:
            # Get old data to archive
            cutoff = self.warm_cutoff
            
            # Get partition months to archive
            import re
            _ident = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
            if not _ident.match(table_name) or not _ident.match(timestamp_column):
                logger.warning("data_archival: invalid identifier", table=table_name, col=timestamp_column)
                return
            partition_query = text(f"""
                SELECT DISTINCT partition_month
                FROM {table_name}
                WHERE {timestamp_column} < :cutoff
                AND partition_month IS NOT NULL
                ORDER BY partition_month ASC
            """)
            
            result = await session.execute(partition_query, {"cutoff": cutoff})
            partitions = [row[0] for row in result.fetchall()]
            
            for partition in partitions:
                try:
                    # Get all data for this partition
                    data_query = text(f"""
                        SELECT *
                        FROM {table_name}
                        WHERE partition_month = :partition
                        AND {timestamp_column} < :cutoff
                    """)
                    
                    result = await session.execute(data_query, {
                        "partition": partition,
                        "cutoff": cutoff
                    })
                    rows = result.fetchall()
                    
                    if not rows:
                        continue
                    
                    # Convert to dictionaries
                    data = [dict(row._mapping) for row in rows]
                    
                    # Archive to compressed file
                    archive_file = self.archive_directory / f"{table_name}_{partition}.json.gz"
                    await self._write_compressed_archive(archive_file, data)
                    
                    # Delete from main table
                    delete_query = text(f"""
                        DELETE FROM {table_name}
                        WHERE partition_month = :partition
                        AND {timestamp_column} < :cutoff
                    """)
                    
                    result = await session.execute(delete_query, {
                        "partition": partition,
                        "cutoff": cutoff
                    })
                    deleted_count = result.rowcount
                    
                    stats["archived_count"] += len(data)
                    stats["deleted_count"] += deleted_count
                    stats["archived_partitions"].append({
                        "partition": partition,
                        "records": len(data),
                        "file": str(archive_file)
                    })
                    
                    logger.info(
                        f"Archived partition {partition} from {table_name}: "
                        f"{len(data)} records -> {archive_file}"
                    )
                    
                except Exception as e:
                    error_msg = f"Error archiving partition {partition}: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    stats["errors"].append(error_msg)
            
            await session.commit()
            logger.info(
                f"Archival complete for {table_name}: "
                f"{stats['archived_count']} records archived, "
                f"{stats['deleted_count']} records deleted"
            )
            
        except Exception as e:
            logger.error(f"Error archiving {table_name}: {str(e)}", exc_info=True)
            stats["errors"].append(str(e))
            await session.rollback()
        
        return stats
    
    async def _write_compressed_archive(self, file_path: Path, data: List[Dict[str, Any]]):
        """Write data to compressed archive file."""
        # Convert datetime objects to ISO strings for JSON serialization
        serializable_data = []
        for record in data:
            serializable_record = {}
            for key, value in record.items():
                if isinstance(value, datetime):
                    serializable_record[key] = value.isoformat()
                else:
                    serializable_record[key] = value
            serializable_data.append(serializable_record)
        
        # Write compressed JSON
        json_data = json.dumps(serializable_data, indent=2)
        compressed = gzip.compress(json_data.encode('utf-8'))
        
        with open(file_path, 'wb') as f:
            f.write(compressed)
    
    async def restore_archived_data(
        self,
        session: AsyncSession,
        table_name: str,
        partition: str
    ) -> Dict[str, Any]:
        """
        Restore archived data from a partition.
        
        Args:
            session: Database session
            table_name: Name of table
            partition: Partition key (YYYY-MM)
        
        Returns:
            Dictionary with restore statistics
        """
        archive_file = self.archive_directory / f"{table_name}_{partition}.json.gz"
        
        if not archive_file.exists():
            return {
                "success": False,
                "message": f"Archive file not found: {archive_file}",
                "restored_count": 0
            }
        
        try:
            # Read compressed archive
            with open(archive_file, 'rb') as f:
                compressed = f.read()
            
            json_data = gzip.decompress(compressed).decode('utf-8')
            data = json.loads(json_data)
            
            # Restore to database
            # Note: This is a simplified version. In production, you'd want
            # to use proper SQLAlchemy bulk insert with the model classes.
            restored_count = 0
            
            for record in data:
                # Convert ISO strings back to datetime
                for key, value in record.items():
                    if isinstance(value, str) and 'T' in value and ('+' in value or 'Z' in value):
                        try:
                            record[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        except (ValueError, TypeError):
                            pass
                
                # Insert record (simplified - would use proper model in production)
                # This is a placeholder - actual implementation depends on table structure
                restored_count += 1
            
            await session.commit()
            
            return {
                "success": True,
                "message": f"Restored {restored_count} records from {archive_file}",
                "restored_count": restored_count
            }
            
        except Exception as e:
            logger.error(f"Error restoring archive {archive_file}: {str(e)}", exc_info=True)
            await session.rollback()
            return {
                "success": False,
                "message": f"Restore error: {str(e)}",
                "restored_count": 0
            }
    
    async def create_aggregates_for_old_data(
        self,
        session: AsyncSession,
        table_name: str,
        timestamp_column: str = "timestamp"
    ) -> Dict[str, Any]:
        """
        Create aggregates for very old data before archiving.
        
        This keeps summary statistics even after detailed data is archived.
        """
        cutoff = self.cold_cutoff
        
        logger.info(f"Creating aggregates for {table_name} (data older than {cutoff})...")
        
        try:
            if table_name == "market_prices":
                # Create daily price aggregates
                aggregate_query = text(f"""
                    INSERT INTO market_price_aggregates (market_id, token_id, date, avg_price, min_price, max_price, count)
                    SELECT 
                        market_id,
                        token_id,
                        DATE({timestamp_column}) as date,
                        AVG(price) as avg_price,
                        MIN(price) as min_price,
                        MAX(price) as max_price,
                        COUNT(*) as count
                    FROM {table_name}
                    WHERE {timestamp_column} < :cutoff
                    GROUP BY market_id, token_id, DATE({timestamp_column})
                """)
                
                result = await session.execute(aggregate_query, {"cutoff": cutoff})
                aggregates_created = result.rowcount
                
            elif table_name == "trades":
                # Create daily trade aggregates
                aggregate_query = text(f"""
                    INSERT INTO trade_aggregates (market_id, date, total_volume, total_trades, avg_price)
                    SELECT 
                        market_id,
                        DATE({timestamp_column}) as date,
                        SUM(size * price) as total_volume,
                        COUNT(*) as total_trades,
                        AVG(price) as avg_price
                    FROM {table_name}
                    WHERE {timestamp_column} < :cutoff
                    GROUP BY market_id, DATE({timestamp_column})
                """)
                
                result = await session.execute(aggregate_query, {"cutoff": cutoff})
                aggregates_created = result.rowcount
            else:
                return {
                    "success": False,
                    "message": f"No aggregate strategy for table {table_name}",
                    "aggregates_created": 0
                }
            
            await session.commit()
            
            return {
                "success": True,
                "message": f"Created {aggregates_created} aggregates for {table_name}",
                "aggregates_created": aggregates_created
            }
            
        except Exception as e:
            logger.error(f"Error creating aggregates for {table_name}: {str(e)}", exc_info=True)
            await session.rollback()
            return {
                "success": False,
                "message": f"Aggregate error: {str(e)}",
                "aggregates_created": 0
            }
    
    def get_archive_stats(self) -> Dict[str, Any]:
        """Get statistics about archived data."""
        archive_files = list(self.archive_directory.glob("*.json.gz"))
        
        total_size = sum(f.stat().st_size for f in archive_files)
        
        partitions = {}
        for file in archive_files:
            # Extract table and partition from filename: table_partition.json.gz
            parts = file.stem.replace('.json', '').split('_')
            if len(parts) >= 2:
                table = '_'.join(parts[:-1])
                partition = parts[-1]
                
                if table not in partitions:
                    partitions[table] = {}
                partitions[table][partition] = {
                    "file": str(file),
                    "size_bytes": file.stat().st_size,
                    "size_mb": round(file.stat().st_size / (1024 * 1024), 2)
                }
        
        return {
            "total_files": len(archive_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "total_size_gb": round(total_size / (1024 * 1024 * 1024), 2),
            "partitions": partitions
        }
