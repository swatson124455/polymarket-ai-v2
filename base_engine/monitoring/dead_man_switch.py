"""
Dead Man's Switch — heartbeat writer for external watchdog monitoring.

Bots call write_heartbeat() every 60s (piggybacked on the periodic exposure flush
in BaseEngine). The external watchdog (deploy/dead_man_watchdog.sh) checks these
heartbeat files to detect total system failure and trigger the kill switch.

Uses system_config table (key='heartbeat_{bot_name}', value=epoch_timestamp).
"""
import os
import time
from typing import Optional

from structlog import get_logger

from base_engine.data.database import Database

logger = get_logger()


class DeadManSwitch:
    """Writes heartbeats to a file and DB for external watchdog monitoring."""

    def __init__(self, db: Optional[Database] = None,
                 heartbeat_dir: str = "/tmp/polymarket-heartbeats"):
        self.db = db
        self.heartbeat_dir = heartbeat_dir
        os.makedirs(self.heartbeat_dir, exist_ok=True)

    async def write_heartbeat(self, bot_name: str) -> None:
        """Write current timestamp to heartbeat file and DB.

        File: {heartbeat_dir}/{bot_name}.heartbeat (contains epoch timestamp)
        DB: UPSERT system_config SET value=:ts WHERE key='heartbeat_{bot_name}'
        """
        ts = str(int(time.time()))

        # 1. Write to file (always, even if DB is down)
        hb_path = os.path.join(self.heartbeat_dir, f"{bot_name}.heartbeat")
        try:
            with open(hb_path, "w") as f:
                f.write(ts)
        except OSError as e:
            logger.debug("heartbeat_file_write_failed", bot_name=bot_name, error=str(e))

        # 2. Write to DB (best-effort, non-blocking)
        if self.db and self.db.session_factory:
            hb_key = f"heartbeat_{bot_name}"
            try:
                from sqlalchemy import select
                from base_engine.data.database import SystemConfig

                async with self.db.get_raw_session() as session:
                    r = await session.execute(
                        select(SystemConfig).where(SystemConfig.key == hb_key)
                    )
                    row = r.scalar_one_or_none()
                    if row:
                        row.value = ts
                    else:
                        session.add(SystemConfig(key=hb_key, value=ts))
                    await session.commit()
            except Exception as e:
                logger.debug("heartbeat_db_write_failed", bot_name=bot_name, error=str(e))

    def read_heartbeat(self, bot_name: str) -> Optional[float]:
        """Read last heartbeat timestamp from file. Returns None if missing."""
        hb_path = os.path.join(self.heartbeat_dir, f"{bot_name}.heartbeat")
        try:
            with open(hb_path, "r") as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return None

    def all_heartbeats_stale(self, max_age_seconds: float = 300) -> bool:
        """Return True if ALL bot heartbeats are older than max_age_seconds.

        Returns True if no heartbeat files exist (no bots have ever reported).
        """
        now = time.time()
        found_any = False
        try:
            for fname in os.listdir(self.heartbeat_dir):
                if not fname.endswith(".heartbeat"):
                    continue
                found_any = True
                hb_path = os.path.join(self.heartbeat_dir, fname)
                try:
                    with open(hb_path, "r") as f:
                        ts = float(f.read().strip())
                    if (now - ts) < max_age_seconds:
                        return False  # At least one bot is alive
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        # If no heartbeat files exist, consider all stale
        return True if found_any else True
