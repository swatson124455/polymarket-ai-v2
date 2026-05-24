"""
LifecycleManager - Graceful shutdown: persist state, release reservations, close DB.
Call shutdown() from BaseEngine.stop() or when process receives SIGTERM/SIGINT.
"""
from typing import Any, Dict
from structlog import get_logger

logger = get_logger()


class LifecycleManager:
    """Handles graceful shutdown: persist state, release reservations, close DB."""

    def __init__(self, components: Dict[str, Any]):
        self.components = components
        self._shutdown_done = False

    async def shutdown(self) -> None:
        """Persist state, release reservations, close DB. Idempotent."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        logger.info("Graceful shutdown initiated")
        try:
            if "scheduler" in self.components and self.components["scheduler"] is not None:
                s = self.components["scheduler"]
                if hasattr(s, "stop"):
                    await s.stop()
                logger.info("Scheduler stopped")
            if "learning_engine" in self.components and self.components["learning_engine"] is not None:
                le = self.components["learning_engine"]
                if hasattr(le, "save_patterns_to_db"):
                    await le.save_patterns_to_db()
                logger.info("Learning patterns persisted")
            if "prediction_engine" in self.components and self.components["prediction_engine"] is not None:
                pe = self.components["prediction_engine"]
                if hasattr(pe, "save_models_to_db"):
                    await pe.save_models_to_db()
                logger.info("Models persisted")
            if "trade_coordinator" in self.components and self.components["trade_coordinator"] is not None:
                tc = self.components["trade_coordinator"]
                if hasattr(tc, "release_all_reservations"):
                    await tc.release_all_reservations()
                logger.info("Position reservations released")
            if "database" in self.components and self.components["database"] is not None:
                db = self.components["database"]
                if hasattr(db, "close"):
                    await db.close()
                logger.info("Database closed")
        except Exception as e:
            logger.warning("Shutdown step failed: %s", e, exc_info=True)
        logger.info("Shutdown complete")
