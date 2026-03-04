"""
Tunable Config Store (P5-01).

DB-backed key-value store for self-tuning parameters. Read by risk_manager
and bots with fallback to config/settings.py defaults. Updated by config_tuner.

Parameters tuned: MIN_CONFIDENCE_THRESHOLD, KELLY_FRACTION, MIN_EDGE,
SCAN_INTERVAL, MAX_POSITION_PER_MARKET, MIRROR_MAX_DELAY_MINUTES, etc.
"""
from typing import Optional, Any, Dict
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class TunableConfigStore:
    """
    In-memory dict backed by DB table `tunable_config`.
    Read by risk_manager and bots with fallback to settings defaults.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._cache: Dict[str, float] = {}
        self._loaded = False

    async def load(self) -> None:
        """Load all tunable config from DB into cache."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("SELECT key, value FROM tunable_config"))
                for row in r.fetchall():
                    self._cache[row[0]] = float(row[1])
            self._loaded = True
            logger.info("Loaded %d tunable config entries", len(self._cache))
        except Exception as e:
            logger.debug("Failed to load tunable config: %s", e)

    def get(self, key: str, default: float = 0.0) -> float:
        """Get a tunable parameter. Falls back to default if not in DB."""
        return self._cache.get(key, default)

    async def set(self, key: str, value: float, source: str = "system") -> None:
        """Set a tunable parameter in DB and cache."""
        self._cache[key] = value
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            now = datetime.now(timezone.utc)
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO tunable_config (key, value, updated_at, updated_by)
                    VALUES (:k, :v, :t, :s)
                    ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = :t, updated_by = :s
                """), {"k": key, "v": value, "t": now, "s": source})
                await session.commit()
            logger.info("Tunable config updated", key=key, value=value, source=source)
        except Exception as e:
            logger.debug("Failed to save tunable config %s: %s", key, e)

    def get_all(self) -> Dict[str, float]:
        """Return all cached config values."""
        return dict(self._cache)
