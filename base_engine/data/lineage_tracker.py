"""
Data Lineage Tracking (#38) - trace data from source to destination.

Log lineage edges (source_type, source_id, target_type, target_id, operation)
for audit and debugging.
"""
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class LineageTracker:
    """
    Record data lineage: API -> DB, DB -> ML, etc.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    async def log(
        self,
        source_type: str,
        source_id: Optional[str],
        target_type: str,
        target_id: Optional[str],
        operation: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one lineage record."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            import json
            async with self.db.get_session() as session:
                await session.execute(
                    text(
                        "INSERT INTO data_lineage (source_type, source_id, target_type, target_id, operation, metadata, created_at) "
                        "VALUES (:source_type, :source_id, :target_type, :target_id, :operation, CAST(:metadata AS jsonb), :created_at)"
                    ),
                    {
                        "source_type": source_type,
                        "source_id": source_id,
                        "target_type": target_type,
                        "target_id": target_id,
                        "operation": operation,
                        "metadata": json.dumps(metadata) if metadata else None,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
                await session.commit()
        except Exception as e:
            logger.debug("lineage_tracker log failed (table may not exist): %s", e)
