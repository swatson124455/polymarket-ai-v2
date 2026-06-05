"""
Streaming Anomaly Detection — River ADWIN + HalfSpaceTrees.

Monitors system metrics in real-time with incremental algorithms that update
on every observation (O(1) per sample, bounded memory).

Metrics tracked:
  - db_semaphore_free    : free DB connection slots (drift = pool stress building)
  - scan_latency_ms      : per-bot scan cycle duration (drift = ML/DB degradation)
  - error_rate           : rolling error fraction per bot (drift = instability)
  - api_response_ms      : Polymarket API latency (drift = external degradation)

ADWIN (Adaptive Windowing): detects when the distribution of a metric shifts.
  Raises alert when mean or variance changes beyond statistical threshold.
  Faster than batch Z-score: catches emerging problems within ~20-50 samples.

HalfSpaceTrees: multivariate anomaly scorer.
  Scores the joint feature vector {db_free, scan_ms, error_rate}.
  High score = unusual combination (e.g., DB fine but scan latency spiking).
"""
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime, timezone
from structlog import get_logger

try:
    from river import drift, anomaly as river_anomaly
    RIVER_AVAILABLE = True
except ImportError:
    RIVER_AVAILABLE = False

logger = get_logger()

# Metrics that get individual ADWIN detectors
_MONITORED_METRICS = [
    "db_semaphore_free",
    "scan_latency_ms",
    "error_rate",
    "api_response_ms",
]

# Multivariate feature vector keys (subset of metrics)
_MULTIVARIATE_KEYS = ["db_semaphore_free", "scan_latency_ms", "error_rate"]


class StreamingAnomalyDetector:
    """
    Streaming anomaly detection on system health metrics.

    Usage::

        detector = StreamingAnomalyDetector(on_anomaly=my_alert_fn)

        # In SLI loop every 10s:
        detector.update("db_semaphore_free", float(free_slots))
        detector.update("scan_latency_ms", float(last_scan_ms))

        # Multivariate score:
        score = detector.score({"db_semaphore_free": 5.0, "scan_latency_ms": 1200.0, "error_rate": 0.1})
    """

    def __init__(
        self,
        on_anomaly: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        adwin_delta: float = 0.002,          # Lower = more sensitive (default: 0.002)
        hst_n_trees: int = 10,
        hst_height: int = 8,
        hst_window_size: int = 50,
    ):
        self._on_anomaly = on_anomaly
        self._detectors: Dict[str, Any] = {}
        self._hst: Optional[Any] = None
        self._drift_counts: Dict[str, int] = {}
        self._last_values: Dict[str, float] = {}
        self._scores: List[float] = []
        self._max_scores = 500
        self._observation_count = 0

        if RIVER_AVAILABLE:
            for metric in _MONITORED_METRICS:
                self._detectors[metric] = drift.ADWIN(delta=adwin_delta)
                self._drift_counts[metric] = 0
            self._hst = river_anomaly.HalfSpaceTrees(
                n_trees=hst_n_trees,
                height=hst_height,
                window_size=hst_window_size,
                seed=42,
            )
            logger.debug("StreamingAnomalyDetector initialized with River %s", "ADWIN+HalfSpaceTrees")
        else:
            logger.warning("river not installed — StreamingAnomalyDetector running in no-op mode")

    # ── Per-metric ADWIN ─────────────────────────────────────────────────────

    def update(self, metric_name: str, value: float) -> bool:
        """
        Feed one metric observation to its ADWIN detector.

        Args:
            metric_name: One of the monitored metrics (or any string for new metrics).
            value: Current numeric value.

        Returns:
            True if ADWIN detected a distributional change (drift alert).
        """
        self._last_values[metric_name] = value
        self._observation_count += 1

        if not RIVER_AVAILABLE:
            return False

        # Auto-create ADWIN for new metric names
        if metric_name not in self._detectors:
            self._detectors[metric_name] = drift.ADWIN(delta=0.002)
            self._drift_counts[metric_name] = 0

        detector = self._detectors[metric_name]
        detector.update(value)

        if detector.drift_detected:
            self._drift_counts[metric_name] = self._drift_counts.get(metric_name, 0) + 1
            details = {
                "value": value,
                "drift_count": self._drift_counts[metric_name],
                "metric": metric_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.warning(
                "ADWIN drift detected: %s=%.2f (drift #%d)",
                metric_name, value, self._drift_counts[metric_name],
            )
            if self._on_anomaly:
                try:
                    self._on_anomaly(metric_name, details)
                except Exception:
                    pass
            return True
        return False

    # ── Multivariate HalfSpaceTrees ──────────────────────────────────────────

    def score(self, features: Dict[str, float]) -> float:
        """
        Compute multivariate anomaly score for a feature vector.

        Higher score = more anomalous. Range is unbounded but typically 0-1
        during normal operation; spikes above 0.7 indicate unusual joint behaviour.

        Args:
            features: {metric_name: value} dict. Missing keys default to last known value.

        Returns:
            Anomaly score (float). Returns 0.0 if river unavailable.
        """
        if not RIVER_AVAILABLE or self._hst is None:
            return 0.0

        # Fill missing keys with last known value (or 0)
        full_vec = {k: self._last_values.get(k, 0.0) for k in _MULTIVARIATE_KEYS}
        full_vec.update({k: v for k, v in features.items() if k in _MULTIVARIATE_KEYS})

        try:
            score_val = self._hst.score_one(full_vec)
            self._hst.learn_one(full_vec)

            self._scores.append(score_val)
            if len(self._scores) > self._max_scores:
                self._scores.pop(0)

            # Alert on unusually high score (top ~5% of history)
            if len(self._scores) >= 20:
                threshold = sorted(self._scores)[-max(1, len(self._scores) // 20)]
                if score_val >= threshold and score_val > 0.5:
                    logger.warning(
                        "HalfSpaceTrees anomaly score=%.4f (threshold=%.4f, features=%s)",
                        score_val, threshold,
                        {k: round(v, 3) for k, v in full_vec.items()},
                    )
            return score_val
        except Exception as e:
            logger.debug("HalfSpaceTrees score error: %s", e)
            return 0.0

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def get_drift_summary(self) -> Dict[str, Any]:
        """Summary of all detected drifts and current anomaly score statistics."""
        recent_score = self._scores[-1] if self._scores else 0.0
        window = self._scores[-20:]
        avg_score = sum(window) / len(window) if window else 0.0

        return {
            "river_available": RIVER_AVAILABLE,
            "observation_count": self._observation_count,
            "drift_counts": dict(self._drift_counts),
            "last_values": {k: round(v, 3) for k, v in self._last_values.items()},
            "recent_anomaly_score": round(recent_score, 4),
            "avg_anomaly_score_20obs": round(avg_score, 4),
            "total_drift_events": sum(self._drift_counts.values()),
        }

    def get_metric_status(self, metric_name: str) -> Dict[str, Any]:
        """Per-metric status snapshot."""
        return {
            "metric": metric_name,
            "last_value": self._last_values.get(metric_name),
            "drift_count": self._drift_counts.get(metric_name, 0),
            "detector_active": metric_name in self._detectors,
        }
