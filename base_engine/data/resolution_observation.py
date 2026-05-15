"""Single accessor for resolution-observation timestamp writes.

Resolution-observation columns (`markets.resolved_at`, `paper_trades.resolved_at`,
`prediction_log.resolved_at`, `mirror_rejected_signals.resolved_at`,
`traded_markets.resolved_at`, `trade_events.event_time` for RESOLUTION events)
all record *when we observed the market resolve*, not *when the market was scheduled
to close*. Confusing the two produced a multi-table corruption (~40K rows future-
dated, ~4.7K rows past-dated but stuffed with scheduled close) traceable to three
sites all writing `resolved_at = end_date_iso` as a fallback.

This module enforces the semantic invariant at the write boundary:

  observed_at != scheduled_close (within 1-second tolerance)
  observed_at <= NOW() + 5 minutes (clock-skew tolerance)

Hard-fail on future-dated; soft-warn on schedule conflation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from structlog import get_logger

logger = get_logger()

FUTURE_TOLERANCE = timedelta(minutes=5)
CONFLATION_TOLERANCE = timedelta(seconds=1)


def _to_naive_utc(dt: datetime) -> datetime:
    """Normalize to naive UTC. Accepts tz-aware or naive input."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def record_resolution_observation(
    observed_at: datetime,
    *,
    market_id: str,
    scheduled_close: Optional[datetime] = None,
    source: Optional[str] = None,
) -> datetime:
    """Validate and return a resolution-observation timestamp.

    Args:
        observed_at: when we observed the resolution. Typically `datetime.now(tz.utc)`.
            Callers MUST NOT pass `market.end_date_iso` here — that's scheduled close.
        market_id: for log correlation.
        scheduled_close: market.end_date_iso, if known. Used for soft-warn detection
            of the schedule-conflation bug pattern. Omit if not available.
        source: short identifier of the calling code path (e.g. "resolution_backfill",
            "data_ingestion.ingest_markets"). Surfaces in logs.

    Returns:
        A naive UTC datetime, suitable for writing to any `*.resolved_at` column.

    Raises:
        ValueError: if observed_at is materially in the future (> NOW() + 5 min).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    observed_at = _to_naive_utc(observed_at)

    if observed_at > now + FUTURE_TOLERANCE:
        raise ValueError(
            f"resolution_observation_future_dated: "
            f"market_id={market_id} observed_at={observed_at.isoformat()} "
            f"now={now.isoformat()} source={source}"
        )

    if scheduled_close is not None:
        scheduled_close = _to_naive_utc(scheduled_close)
        delta_seconds = abs((observed_at - scheduled_close).total_seconds())
        if delta_seconds < CONFLATION_TOLERANCE.total_seconds():
            logger.warning(
                "resolution_observation_conflation_suspected",
                market_id=market_id,
                observed_at=observed_at.isoformat(),
                scheduled_close=scheduled_close.isoformat(),
                delta_seconds=delta_seconds,
                source=source,
            )

    return observed_at
