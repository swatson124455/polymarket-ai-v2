"""
Check 5G: Prediction accuracy anomaly detection via Brier score analysis.

Thresholds:
- Dynamic: trailing 12-week rolling mean + σ. CRITICAL at mean+2σ, WARNING at mean+1σ.
- Absolute floor (cold-start safe): CRITICAL at Brier > 0.35, WARNING at Brier > 0.28.
  A model at 0.28 is approaching random on binary outcomes.
- Cold-start (n < 30 resolved predictions): skip dynamic thresholds, apply absolute only.
  Log INFO "insufficient history for dynamic calibration baseline (n=N, need 30)".

Also checks:
- coverage rate of was_correct < 80% → WARNING (large fraction unscored)
- NULL predicted_prob on executed trades → WARNING
- last 7d Brier delta > 0.05 vs prior 7d → WARNING (temporal degradation)
"""
import time
from math import sqrt
from typing import List

from sqlalchemy import text
from structlog import get_logger

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

logger = get_logger(__name__)

_BRIER_CRITICAL_FLOOR = 0.35
_BRIER_WARNING_FLOOR  = 0.28
_MIN_N_FOR_DYNAMIC    = 30
_TEMPORAL_DELTA_WARN  = 0.05


class PredictionAccuracyCheck(BaseCheck):
    name = "prediction_accuracy_anomaly"
    tables_queried = ["prediction_log", "markets"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Per-bot trailing 90-day Brier + weekly breakdown for dynamic thresholds
        brier_rows = await session.execute(text("""
            WITH resolved AS (
                SELECT bot_name,
                       CAST(predicted_prob AS DOUBLE PRECISION) AS prob,
                       CAST(actual_outcome AS DOUBLE PRECISION) AS outcome,
                       prediction_time
                FROM prediction_log pl
                WHERE pl.actual_outcome IS NOT NULL
                  AND pl.predicted_prob IS NOT NULL
                  AND pl.prediction_time >= NOW() - INTERVAL '90 days'
            ),
            weekly AS (
                SELECT bot_name,
                       DATE_TRUNC('week', prediction_time) AS week_start,
                       AVG(POWER(prob - outcome, 2))        AS weekly_brier,
                       COUNT(*)                             AS n
                FROM resolved
                GROUP BY bot_name, DATE_TRUNC('week', prediction_time)
            ),
            bot_stats AS (
                SELECT bot_name,
                       AVG(weekly_brier)  AS mean_brier,
                       -- population stddev across weeks
                       SQRT(AVG(POWER(weekly_brier - (SELECT AVG(w2.weekly_brier)
                                                      FROM weekly w2
                                                      WHERE w2.bot_name = weekly.bot_name), 2)))
                           AS stddev_brier,
                       COUNT(*) AS n_weeks,
                       SUM(n)   AS n_total
                FROM weekly
                GROUP BY bot_name
            )
            SELECT bs.bot_name, bs.mean_brier, bs.stddev_brier, bs.n_weeks, bs.n_total
            FROM bot_stats bs
        """))

        for row in brier_rows.fetchall():
            bot_name, mean_brier, stddev_brier, n_weeks, n_total = row
            if bot_name is None:
                continue

            mean_b = float(mean_brier) if mean_brier is not None else None
            std_b  = float(stddev_brier) if stddev_brier is not None else 0.0

            # Cold-start guard
            if (n_total or 0) < _MIN_N_FOR_DYNAMIC or mean_b is None:
                logger.info(
                    "prediction_accuracy_cold_start",
                    bot_name=bot_name,
                    n=n_total,
                    need=_MIN_N_FOR_DYNAMIC,
                )
                # Still apply absolute floor
                if mean_b is not None and mean_b > _BRIER_CRITICAL_FLOOR:
                    violations.append(AuditViolation(
                        recon_type="PREDICTION_ACCURACY_ANOMALY",
                        bot_name=bot_name,
                        market_id=None,
                        severity="CRITICAL",
                        details={
                            "reason": "brier_exceeds_critical_floor_cold_start",
                            "brier_score": round(mean_b, 4),
                            "threshold": _BRIER_CRITICAL_FLOOR,
                            "n_total": int(n_total or 0),
                            "cold_start": True,
                        },
                    ))
                continue

            critical_threshold = mean_b + 2 * std_b
            warning_threshold  = mean_b + 1 * std_b

            # Dynamic threshold check — use max of dynamic and absolute floor
            eff_critical = max(critical_threshold, _BRIER_CRITICAL_FLOOR)
            eff_warning  = max(warning_threshold,  _BRIER_WARNING_FLOOR)

            if mean_b > eff_critical:
                violations.append(AuditViolation(
                    recon_type="PREDICTION_ACCURACY_ANOMALY",
                    bot_name=bot_name,
                    market_id=None,
                    severity="CRITICAL",
                    details={
                        "reason": "brier_critical_regression",
                        "brier_score": round(mean_b, 4),
                        "mean_7w": round(mean_b, 4),
                        "stddev_7w": round(std_b, 4),
                        "threshold": round(eff_critical, 4),
                        "n_total": int(n_total),
                        "n_weeks": int(n_weeks),
                    },
                ))
            elif mean_b > eff_warning:
                violations.append(AuditViolation(
                    recon_type="PREDICTION_ACCURACY_ANOMALY",
                    bot_name=bot_name,
                    market_id=None,
                    severity="WARNING",
                    details={
                        "reason": "brier_warning_regression",
                        "brier_score": round(mean_b, 4),
                        "mean_7w": round(mean_b, 4),
                        "stddev_7w": round(std_b, 4),
                        "threshold": round(eff_warning, 4),
                        "n_total": int(n_total),
                        "n_weeks": int(n_weeks),
                    },
                ))

        # was_correct coverage < 80%
        coverage_rows = await session.execute(text("""
            SELECT bot_name,
                   COUNT(*) AS total,
                   COUNT(was_correct) AS scored,
                   ROUND(100.0 * COUNT(was_correct) / NULLIF(COUNT(*), 0), 1) AS coverage_pct
            FROM prediction_log
            WHERE prediction_time >= NOW() - INTERVAL '7 days'
            GROUP BY bot_name
            HAVING ROUND(100.0 * COUNT(was_correct) / NULLIF(COUNT(*), 0), 1) < 80
              AND COUNT(*) > 10
        """))
        for row in coverage_rows.fetchall():
            bot_name, total, scored, pct = row
            violations.append(AuditViolation(
                recon_type="PREDICTION_ACCURACY_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "low_was_correct_coverage",
                    "total_predictions_7d": int(total),
                    "scored_count": int(scored),
                    "coverage_pct": float(pct) if pct else 0.0,
                },
            ))

        # NULL predicted_prob on executed trades
        null_prob_rows = await session.execute(text("""
            SELECT pl.bot_name, COUNT(*) AS null_count
            FROM prediction_log pl
            WHERE pl.trade_executed = TRUE
              AND pl.predicted_prob IS NULL
              AND pl.prediction_time >= NOW() - INTERVAL '7 days'
            GROUP BY pl.bot_name
            HAVING COUNT(*) > 0
        """))
        for row in null_prob_rows.fetchall():
            bot_name, count = row
            violations.append(AuditViolation(
                recon_type="PREDICTION_ACCURACY_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "null_predicted_prob_on_executed_trade",
                    "null_count_7d": int(count),
                },
            ))

        # Temporal degradation: last 7d vs prior 7d Brier delta > 0.05
        temporal_rows = await session.execute(text("""
            WITH recent AS (
                SELECT bot_name,
                       AVG(POWER(CAST(predicted_prob AS DOUBLE PRECISION)
                               - CAST(actual_outcome AS DOUBLE PRECISION), 2)) AS brier
                FROM prediction_log
                WHERE actual_outcome IS NOT NULL AND predicted_prob IS NOT NULL
                  AND prediction_time >= NOW() - INTERVAL '7 days'
                GROUP BY bot_name
            ),
            prior AS (
                SELECT bot_name,
                       AVG(POWER(CAST(predicted_prob AS DOUBLE PRECISION)
                               - CAST(actual_outcome AS DOUBLE PRECISION), 2)) AS brier
                FROM prediction_log
                WHERE actual_outcome IS NOT NULL AND predicted_prob IS NOT NULL
                  AND prediction_time BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
                GROUP BY bot_name
            )
            SELECT r.bot_name, r.brier AS recent_brier, p.brier AS prior_brier,
                   r.brier - p.brier AS delta
            FROM recent r
            JOIN prior p ON p.bot_name = r.bot_name
            WHERE r.brier - p.brier > 0.05
        """))
        for row in temporal_rows.fetchall():
            bot_name, recent_b, prior_b, delta = row
            violations.append(AuditViolation(
                recon_type="PREDICTION_ACCURACY_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "temporal_brier_degradation",
                    "recent_7d_brier": round(float(recent_b), 4),
                    "prior_7d_brier": round(float(prior_b), 4),
                    "delta": round(float(delta), 4),
                },
            ))

        # prediction_time > resolved_at — impossible timestamps (432-row EsportsBot issue)
        # These rows are silently excluded from calibration labeling on every retrain.
        impossible_ts_rows = await session.execute(text("""
            SELECT pl.bot_name, COUNT(*) AS bad_rows
            FROM prediction_log pl
            JOIN markets m ON m.id = pl.market_id
            WHERE pl.prediction_time > m.resolved_at
              AND m.resolved_at IS NOT NULL
            GROUP BY pl.bot_name
            HAVING COUNT(*) > 0
        """))
        for row in impossible_ts_rows.fetchall():
            bot_name, count = row
            violations.append(AuditViolation(
                recon_type="PREDICTION_ACCURACY_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "prediction_time_after_market_resolution",
                    "bad_row_count": int(count),
                    "impact": "rows silently excluded from calibration labeling on retrain",
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} prediction accuracy anomaly(s)",
        )
