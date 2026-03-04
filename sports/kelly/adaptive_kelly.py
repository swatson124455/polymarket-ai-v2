"""
Adaptive Kelly Criterion — Phase 6 implementation.

Reads per-(sport, market_type) Brier score from sports_calibration table
and adjusts the Kelly fraction accordingly:

  Brier > 0.30  →  SPORTS_KELLY_MIN_FRACTION  (0.10×)  — poor calibration
  Brier < 0.20  →  SPORTS_KELLY_MAX_FRACTION  (0.50×)  — excellent calibration
  Otherwise     →  interpolated between min and max

Phase 1–5: stub — returns SPORTS_KELLY_DEFAULT_FRACTION for all inputs.
Phase 6: reads sports_calibration table (written by HealthScheduler job).
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple
from structlog import get_logger

from config.settings import settings  # noqa: F401 — kept at module level for patching in tests

logger = get_logger()

# In-memory cache: (sport, market_type) → (timestamp, fraction)
_FRACTION_CACHE: Dict[Tuple[str, str], Tuple[float, float]] = {}
_CACHE_LOCK = asyncio.Lock()


async def get_kelly_fraction(
    sport: str,
    market_type: str = "moneyline",
    db=None,
) -> float:
    """
    Return the Kelly fraction to apply for this (sport, market_type) pair.

    Phase 6: reads sports_calibration table.
    Falls back to SPORTS_KELLY_DEFAULT_FRACTION if DB unavailable.

    Args:
        sport:       nba / nfl / mlb / nhl / soccer / tennis.
        market_type: moneyline / futures / injury_prop / etc.
        db:          Database instance. If None, returns default.

    Returns:
        Kelly fraction in [SPORTS_KELLY_MIN_FRACTION, SPORTS_KELLY_MAX_FRACTION].
    """
    default: float = float(getattr(settings, "SPORTS_KELLY_DEFAULT_FRACTION", 0.25))
    cache_ttl = int(getattr(settings, "SPORTS_CALIBRATION_UPDATE_INTERVAL", 3600))

    if db is None:
        logger.debug(
            "adaptive_kelly.get_kelly_fraction: no DB — using default",
            sport=sport,
            market_type=market_type,
            returning=default,
        )
        return default

    # Check cache
    cache_key = (sport, market_type)
    async with _CACHE_LOCK:
        cached = _FRACTION_CACHE.get(cache_key)
        if cached:
            ts, fraction = cached
            if time.monotonic() - ts < cache_ttl:
                return fraction

    # Read from DB
    try:
        fraction = await asyncio.wait_for(
            _read_fraction_from_db(sport, market_type, db, default),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "adaptive_kelly.get_kelly_fraction: DB timeout — using default",
            sport=sport,
        )
        return default
    except Exception as exc:
        logger.debug(
            "adaptive_kelly.get_kelly_fraction: DB error",
            sport=sport,
            error=str(exc),
        )
        return default

    # Cache result
    async with _CACHE_LOCK:
        _FRACTION_CACHE[cache_key] = (time.monotonic(), fraction)

    logger.debug(
        "adaptive_kelly.get_kelly_fraction: loaded from DB",
        sport=sport,
        market_type=market_type,
        fraction=fraction,
    )
    return fraction


async def _read_fraction_from_db(
    sport: str,
    market_type: str,
    db,
    default: float,
) -> float:
    """Read kelly_fraction from sports_calibration table."""
    from sqlalchemy import text

    async with db.get_session() as session:
        result = await session.execute(
            text(
                "SELECT kelly_fraction, brier_score "
                "FROM sports_calibration "
                "WHERE sport = :sport AND market_type = :market_type "
                "LIMIT 1"
            ),
            {"sport": sport, "market_type": market_type},
        )
        row = result.fetchone()

    if row is None:
        return default

    kelly_fraction = row[0]
    if kelly_fraction is not None:
        return float(kelly_fraction)

    # If only brier_score is stored, recompute
    brier_score = row[1]
    return compute_kelly_fraction(float(brier_score) if brier_score is not None else None)


def compute_kelly_fraction(brier_score: Optional[float]) -> float:
    """
    Map a Brier score to a Kelly fraction.

    Called by the HealthScheduler calibration job to update sports_calibration.

    Args:
        brier_score: 0.0–0.5 (lower = better predictions).
                     None → return default fraction.
    Returns:
        Kelly fraction in [SPORTS_KELLY_MIN_FRACTION, SPORTS_KELLY_MAX_FRACTION].
    """
    min_f = float(getattr(settings, "SPORTS_KELLY_MIN_FRACTION", 0.10))
    max_f = float(getattr(settings, "SPORTS_KELLY_MAX_FRACTION", 0.50))
    default_f = float(getattr(settings, "SPORTS_KELLY_DEFAULT_FRACTION", 0.25))

    if brier_score is None:
        return default_f
    if brier_score > 0.30:
        return min_f
    if brier_score < 0.20:
        return max_f
    # Linear interpolation between min and max in the 0.20–0.30 band
    t = (0.30 - brier_score) / 0.10  # 0.0 at brier=0.30, 1.0 at brier=0.20
    return round(min_f + t * (max_f - min_f), 4)


async def update_calibration(
    sport: str,
    market_type: str,
    bet_count: int,
    correct_count: int,
    brier_score: float,
    db,
) -> None:
    """
    Update the sports_calibration table with new calibration data.

    Called by the HealthScheduler sports calibration job.

    Args:
        sport:         Sport code.
        market_type:   Market type.
        bet_count:     Total bets resolved.
        correct_count: Number where we were directionally correct.
        brier_score:   Brier score (0.0 = perfect, 0.5 = random).
        db:            Database instance.
    """
    from sqlalchemy import text
    from datetime import datetime, timezone

    kelly_fraction = compute_kelly_fraction(brier_score)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        async with db.get_session() as session:
            await session.execute(
                text(
                    "INSERT INTO sports_calibration "
                    "  (sport, market_type, bet_count, correct_count, "
                    "   brier_score, kelly_fraction, last_updated) "
                    "VALUES (:sport, :market_type, :bet_count, :correct_count, "
                    "        :brier_score, :kelly_fraction, :last_updated) "
                    "ON CONFLICT (sport, market_type) DO UPDATE SET "
                    "  bet_count = EXCLUDED.bet_count, "
                    "  correct_count = EXCLUDED.correct_count, "
                    "  brier_score = EXCLUDED.brier_score, "
                    "  kelly_fraction = EXCLUDED.kelly_fraction, "
                    "  last_updated = EXCLUDED.last_updated"
                ),
                {
                    "sport": sport,
                    "market_type": market_type,
                    "bet_count": bet_count,
                    "correct_count": correct_count,
                    "brier_score": brier_score,
                    "kelly_fraction": kelly_fraction,
                    "last_updated": now,
                },
            )
            await session.commit()

        # Invalidate cache for this (sport, market_type)
        async with _CACHE_LOCK:
            _FRACTION_CACHE.pop((sport, market_type), None)

        logger.info(
            "adaptive_kelly.update_calibration: updated",
            sport=sport,
            market_type=market_type,
            bet_count=bet_count,
            brier_score=round(brier_score, 4),
            kelly_fraction=kelly_fraction,
        )
    except Exception as exc:
        logger.warning(
            "adaptive_kelly.update_calibration: DB error",
            sport=sport,
            error=str(exc),
        )
