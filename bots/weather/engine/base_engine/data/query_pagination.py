"""
Query Pagination - Paginate large queries to prevent memory exhaustion.

Provides:
- Cursor-based pagination (faster than offset-based)
- Offset-based pagination (for compatibility)
- Automatic pagination for large result sets
"""
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, func
from structlog import get_logger

logger = get_logger()


class PaginatedQuery:
    """Helper for paginated database queries."""
    
    @staticmethod
    async def get_paginated_results(
        session: AsyncSession,
        query: str,
        params: Dict[str, Any],
        page: int = 1,
        page_size: int = 1000,
        order_by: str = "timestamp DESC"
    ) -> Dict[str, Any]:
        """
        Execute a paginated query.
        
        Args:
            session: Database session
            query: SQL query (without LIMIT/OFFSET)
            params: Query parameters
            page: Page number (1-indexed)
            page_size: Number of records per page
            order_by: ORDER BY clause
        
        Returns:
            Dictionary with results and pagination metadata
        """
        offset = (page - 1) * page_size
        
        # Get total count
        count_query = f"SELECT COUNT(*) as total FROM ({query}) as subquery"
        count_result = await session.execute(text(count_query), params)
        total = count_result.scalar() or 0
        
        # Sanitize order_by — only allow simple column+direction patterns
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*(\s+(ASC|DESC))?$', order_by.strip(), re.IGNORECASE):
            order_by = "timestamp DESC"
        # Get paginated results
        paginated_query = f"{query} ORDER BY {order_by} LIMIT :limit OFFSET :offset"
        result = await session.execute(
            text(paginated_query),
            {**params, "limit": page_size, "offset": offset}
        )
        rows = result.fetchall()
        data = [dict(row._mapping) for row in rows]
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        return {
            "data": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1
            }
        }
    
    @staticmethod
    async def get_cursor_paginated_results(
        session: AsyncSession,
        query: str,
        params: Dict[str, Any],
        cursor: Optional[str] = None,
        page_size: int = 1000,
        cursor_column: str = "id",
        order_direction: str = "DESC"
    ) -> Dict[str, Any]:
        """
        Execute a cursor-based paginated query (faster than offset-based).
        
        Args:
            session: Database session
            query: SQL query
            params: Query parameters
            cursor: Cursor value (last value from previous page)
            page_size: Number of records per page
            cursor_column: Column to use for cursor
            order_direction: ASC or DESC
        
        Returns:
            Dictionary with results and cursor for next page
        """
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cursor_column.strip()):
            cursor_column = "id"
        if order_direction.strip().upper() not in ("ASC", "DESC"):
            order_direction = "DESC"

        conditions = []
        query_params = params.copy()

        if cursor:
            if order_direction == "DESC":
                conditions.append(f"{cursor_column} < :cursor")
            else:
                conditions.append(f"{cursor_column} > :cursor")
            query_params["cursor"] = cursor
        
        where_clause = ""
        if conditions:
            if "WHERE" in query.upper():
                where_clause = f" AND {' AND '.join(conditions)}"
            else:
                where_clause = f" WHERE {' AND '.join(conditions)}"
        
        paginated_query = f"{query}{where_clause} ORDER BY {cursor_column} {order_direction} LIMIT :limit"
        query_params["limit"] = page_size + 1  # Fetch one extra to check if there's more
        
        result = await session.execute(text(paginated_query), query_params)
        rows = result.fetchall()
        
        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:-1]  # Remove the extra row
        
        data = [dict(row._mapping) for row in rows]
        
        next_cursor = None
        if data and has_next:
            next_cursor = str(data[-1].get(cursor_column))
        
        return {
            "data": data,
            "pagination": {
                "page_size": page_size,
                "has_next": has_next,
                "next_cursor": next_cursor,
                "count": len(data)
            }
        }
    
    @staticmethod
    async def iterate_large_query(
        session: AsyncSession,
        query: str,
        params: Dict[str, Any],
        batch_size: int = 1000,
        order_by: str = "timestamp ASC"
    ):
        """
        Iterate over a large query in batches (generator).
        
        Args:
            session: Database session
            query: SQL query
            params: Query parameters
            batch_size: Number of records per batch
            order_by: ORDER BY clause
        
        Yields:
            Batches of records
        """
        page = 1
        while True:
            result = await PaginatedQuery.get_paginated_results(
                session=session,
                query=query,
                params=params,
                page=page,
                page_size=batch_size,
                order_by=order_by
            )
            
            if not result["data"]:
                break
            
            yield result["data"]
            
            if not result["pagination"]["has_next"]:
                break
            
            page += 1


class QueryOptimizer:
    """Optimize queries for large datasets."""
    
    @staticmethod
    def optimize_query_for_partitions(
        query: str,
        start_date: datetime,
        end_date: datetime,
        partition_column: str = "partition_month"
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Add partition filtering to a query for better performance.
        
        Args:
            query: SQL query
            start_date: Start date
            end_date: End date
            partition_column: Name of partition column
        
        Returns:
            Tuple of (optimized query, additional parameters)
        """
        from bots.weather.engine.base_engine.data.database_partitioning import get_partition_key
        
        # Calculate partitions
        partitions = []
        current = start_date.replace(day=1)
        while current <= end_date:
            partitions.append(get_partition_key(current))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        
        if not partitions:
            return query, {}
        
        # Add partition filter
        partition_placeholders = ','.join([f':partition_{i}' for i in range(len(partitions))])
        
        if "WHERE" in query.upper():
            partition_filter = f" AND {partition_column} IN ({partition_placeholders})"
            # Insert before ORDER BY or LIMIT
            query_upper = query.upper()
            if "ORDER BY" in query_upper:
                idx = query_upper.index("ORDER BY")
                query = query[:idx] + partition_filter + " " + query[idx:]
            elif "LIMIT" in query_upper:
                idx = query_upper.index("LIMIT")
                query = query[:idx] + partition_filter + " " + query[idx:]
            else:
                query = query + partition_filter
        else:
            query = query + f" WHERE {partition_column} IN ({partition_placeholders})"
        
        params = {f'partition_{i}': p for i, p in enumerate(partitions)}
        
        return query, params
