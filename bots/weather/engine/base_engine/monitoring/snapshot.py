"""
Snapshot manager for pre-operation stats and post-operation verification.
Use before risky bulk updates; verify_operation checks for count drops or suspicious growth.
Real rollback = restore from backup (see docs/deployment/RECOVERY.md).
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from structlog import get_logger

logger = get_logger()


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class SnapshotManager:
    """Create snapshots (table row counts) before risky ops; verify after to detect corruption."""

    def __init__(self, db: Any):
        self.db = db

    async def _get_statistics(self) -> Dict[str, int]:
        """Current row counts for markets, trades, market_prices."""
        if not self.db or not self.db.session_factory:
            return {}
        try:
            from sqlalchemy import select, func
            from bots.weather.engine.base_engine.data.database import Market, Trade, MarketPrice

            async with self.db.get_session() as session:
                stats = {}
                for name, model in [("markets", Market), ("trades", Trade), ("market_prices", MarketPrice)]:
                    r = await session.execute(select(func.count(model.id)))
                    stats[f"{name}_count"] = r.scalar_one() or 0
                return stats
        except Exception as e:
            logger.error("Error getting snapshot statistics", error=str(e), exc_info=True)
            return {}

    async def create_snapshot(self, description: str) -> Optional[str]:
        """
        Record current table counts before a risky operation.
        Returns snapshot_id (e.g. snapshot_YYYYMMDD_HHMMSS) or None on error.
        """
        if not self.db or not self.db.session_factory:
            return None
        snapshot_id = f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        try:
            stats = await self._get_statistics()
            from bots.weather.engine.base_engine.data.database import Snapshot
            created = _naive_utc(datetime.now(timezone.utc))
            async with self.db.get_session() as session:
                session.add(Snapshot(
                    id=snapshot_id,
                    description=description,
                    created_at=created,
                    statistics=stats,
                ))
                await session.commit()
            logger.info("Created snapshot", snapshot_id=snapshot_id, description=description, stats=stats)
            return snapshot_id
        except Exception as e:
            logger.error("Error creating snapshot", snapshot_id=snapshot_id, error=str(e), exc_info=True)
            return None

    async def verify_operation(self, snapshot_id: str) -> bool:
        """
        Compare current counts to snapshot. Returns False if >10% drop or >2x growth (suspicious).
        """
        if not self.db or not self.db.session_factory:
            return False
        try:
            from sqlalchemy import select
            from bots.weather.engine.base_engine.data.database import Snapshot

            async with self.db.get_session() as session:
                r = await session.execute(select(Snapshot).where(Snapshot.id == snapshot_id).limit(1))
                row = r.scalar_one_or_none()
            if not row:
                logger.error("Snapshot not found", snapshot_id=snapshot_id)
                return False
            old_stats = row.statistics or {}
            new_stats = await self._get_statistics()
            issues = []
            for key in old_stats:
                old_count = old_stats.get(key, 0)
                new_count = new_stats.get(key, 0)
                if new_count < old_count * 0.9:
                    issues.append(f"{key}: dropped from {old_count} to {new_count}")
                if new_count > old_count * 2:
                    issues.append(f"{key}: suspicious growth from {old_count} to {new_count}")
            if issues:
                logger.error("Verification failed", snapshot_id=snapshot_id, issues=issues)
                return False
            logger.info("Verification passed", snapshot_id=snapshot_id)
            return True
        except Exception as e:
            logger.error("Error verifying snapshot", snapshot_id=snapshot_id, error=str(e), exc_info=True)
            return False

    async def rollback_if_needed(self, snapshot_id: str) -> bool:
        """
        If verify_operation fails, log critical and return False.
        Real rollback = restore from backup (run disaster_recovery or pg_restore).
        """
        if not await self.verify_operation(snapshot_id):
            logger.critical("Rollback needed", snapshot_id=snapshot_id, action="Restore from backup")
            return False
        return True
