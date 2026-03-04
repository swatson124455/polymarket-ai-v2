"""
Phase Graduation Tracker — evaluates trading performance and recommends phase transitions.

Phases (in order):
  paper       → learning → graduated → production

Promotion criteria:
  paper → learning:    win_rate ≥ 52%, predictions ≥ 100, brier ≤ 0.22
  learning → graduated: win_rate ≥ 55%, predictions ≥ 300, brier ≤ 0.20
  graduated → production: manual only (requires user confirmation)

Demotion: not automatic — only logs warnings when metrics fall below demotion thresholds.

Called by health_runner every PHASE_GRADUATION_CHECK_HOURS (default 24h).
Does NOT modify settings or .env — only logs recommendations and writes to DB.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from structlog import get_logger
from config.settings import settings

logger = get_logger()

_PHASE_ORDER = ["paper", "learning", "graduated", "production"]


@dataclass
class PhaseMetrics:
    """Snapshot of current performance metrics."""
    prediction_count: int = 0
    resolved_count: int = 0
    win_rate: float = 0.0
    brier_score: float = 0.5
    avg_edge_realized: float = 0.0
    current_phase: str = "paper"
    evaluation_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def meets_promotion_criteria(self, target_phase: str) -> bool:
        """Return True if current metrics meet the criteria to enter target_phase."""
        if target_phase == "learning":
            return (
                self.resolved_count >= getattr(settings, "PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS", 100)
                and self.win_rate >= getattr(settings, "PHASE_PAPER_TO_LEARNING_WIN_RATE", 0.52)
                and self.brier_score <= getattr(settings, "PHASE_PAPER_TO_LEARNING_MAX_BRIER", 0.22)
            )
        if target_phase == "graduated":
            return (
                self.resolved_count >= getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS", 300)
                and self.win_rate >= getattr(settings, "PHASE_LEARNING_TO_GRADUATED_WIN_RATE", 0.55)
                and self.brier_score <= getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MAX_BRIER", 0.20)
            )
        return False  # graduated → production is manual only


class PhaseTracker:
    """
    Evaluates trading performance metrics and logs phase transition recommendations.

    Does NOT automatically change settings or environment variables — only logs
    structured recommendations for the operator to action.

    Usage:
        tracker = PhaseTracker(db=db_instance)
        metrics = await tracker.evaluate()
        if metrics.promotion_recommended:
            logger.info("PHASE PROMOTION RECOMMENDED: %s → %s", ...)
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        # Initialize to -inf so a fresh tracker is always ready to evaluate immediately.
        # Using 0.0 would only work if machine uptime > PHASE_GRADUATION_CHECK_HOURS.
        self._last_evaluated: float = float("-inf")

    async def evaluate(self) -> PhaseMetrics:
        """
        Query prediction_log for resolved predictions and compute performance metrics.
        Returns PhaseMetrics with promotion/demotion recommendations.
        Fails gracefully — returns neutral metrics on DB error.
        """
        metrics = PhaseMetrics(current_phase=getattr(settings, "TRADING_PHASE", "paper"))

        if not self.db or not self.db.session_factory:
            return metrics

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                # Set statement timeout to avoid slow query blocking pool
                try:
                    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                except Exception:
                    pass

                # Count resolved predictions with known outcomes
                result = await session.execute(text("""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN was_correct = TRUE THEN 1 ELSE 0 END) AS correct,
                        AVG(
                            CASE WHEN was_correct IS NOT NULL AND predicted_prob IS NOT NULL
                            THEN POWER(predicted_prob - CASE WHEN was_correct THEN 1.0 ELSE 0.0 END, 2)
                            END
                        ) AS brier,
                        AVG(
                            CASE WHEN was_correct IS NOT NULL AND predicted_prob IS NOT NULL
                                 AND market_price IS NOT NULL
                            THEN ABS(predicted_prob - market_price)
                            END
                        ) AS avg_edge
                    FROM prediction_log
                    WHERE was_correct IS NOT NULL
                    AND prediction_time > NOW() - INTERVAL '30 days'
                """))
                row = result.fetchone()

            if row and row[0]:
                metrics.resolved_count = int(row[0])
                correct = int(row[1] or 0)
                metrics.prediction_count = metrics.resolved_count
                metrics.win_rate = correct / max(metrics.resolved_count, 1)
                metrics.brier_score = float(row[2] or 0.5)
                metrics.avg_edge_realized = float(row[3] or 0.0)

            self._last_evaluated = time.monotonic()

        except Exception as e:
            logger.warning("PhaseTracker.evaluate failed (non-fatal): %s", e)
            return metrics

        # Determine promotion recommendation
        current_idx = _PHASE_ORDER.index(metrics.current_phase) if metrics.current_phase in _PHASE_ORDER else 0
        if current_idx + 1 < len(_PHASE_ORDER):
            next_phase = _PHASE_ORDER[current_idx + 1]
            if next_phase != "production" and metrics.meets_promotion_criteria(next_phase):
                logger.warning(
                    "PHASE_PROMOTION_RECOMMENDED",
                    current_phase=metrics.current_phase,
                    recommended_phase=next_phase,
                    resolved_count=metrics.resolved_count,
                    win_rate=round(metrics.win_rate, 4),
                    brier=round(metrics.brier_score, 4),
                    action="Set TRADING_PHASE=%s in .env and restart" % next_phase,
                )
            elif next_phase == "production":
                logger.info(
                    "PHASE_PRODUCTION_GATE",
                    current_phase=metrics.current_phase,
                    note="graduated→production requires manual review",
                    win_rate=round(metrics.win_rate, 4),
                    brier=round(metrics.brier_score, 4),
                )

        # Demotion warning: if significantly underperforming for current phase
        _demotion_thresholds = {
            "learning": (0.48, 0.28),     # win_rate < 48% OR brier > 0.28 for 30d
            "graduated": (0.50, 0.25),    # win_rate < 50% OR brier > 0.25 for 30d
            "production": (0.52, 0.23),   # win_rate < 52% OR brier > 0.23 for 30d
        }
        if metrics.current_phase in _demotion_thresholds and metrics.resolved_count >= 50:
            _d_wr, _d_brier = _demotion_thresholds[metrics.current_phase]
            if metrics.win_rate < _d_wr or metrics.brier_score > _d_brier:
                logger.warning(
                    "PHASE_DEMOTION_WARNING",
                    current_phase=metrics.current_phase,
                    win_rate=round(metrics.win_rate, 4),
                    brier=round(metrics.brier_score, 4),
                    demotion_win_rate_threshold=_d_wr,
                    demotion_brier_threshold=_d_brier,
                    note="Performance below phase threshold — consider reviewing model quality",
                )

        logger.info(
            "phase_tracker_evaluation",
            current_phase=metrics.current_phase,
            resolved_count=metrics.resolved_count,
            win_rate=round(metrics.win_rate, 4),
            brier=round(metrics.brier_score, 4),
            avg_edge_realized=round(metrics.avg_edge_realized, 4),
        )
        return metrics

    def should_evaluate(self) -> bool:
        """Return True if enough time has passed since last evaluation."""
        _interval_h = getattr(settings, "PHASE_GRADUATION_CHECK_HOURS", 24.0)
        _interval_s = _interval_h * 3600.0
        return (time.monotonic() - self._last_evaluated) >= _interval_s
