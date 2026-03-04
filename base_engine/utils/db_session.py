"""
Database session management utilities to reduce code duplication.
"""
from typing import AsyncContextManager, Optional, Callable, Any
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger
from base_engine.data.database import Database

logger = get_logger()


async def with_db_session(
    db: Database,
    operation_name: str,
    operation: Callable[[AsyncSession, ...], Any],
    *args,
    **kwargs
) -> Any:
    """
    Execute an operation within a database session context.
    
    Args:
        db: Database instance
        operation_name: Name of operation for logging
        operation: Async function that takes session as first argument
        *args: Additional positional arguments for operation
        **kwargs: Additional keyword arguments for operation
    
    Returns:
        Result of operation
    
    Raises:
        RuntimeError: If database not initialized
        Exception: Any exception raised by operation (preserves original context)
    """
    if db.session_factory is None:
        raise RuntimeError(f"Database required for {operation_name}. Cannot proceed without database connection.")
    
    async with db.get_session() as session:
        try:
            return await operation(session, *args, **kwargs)
        except Exception as e:
            logger.error(
                f"Database operation '{operation_name}' failed",
                error=str(e),
                operation=operation_name,
                error_type=type(e).__name__,
                exc_info=True
            )
            raise RuntimeError(f"Database operation '{operation_name}' failed: {str(e)}") from e
