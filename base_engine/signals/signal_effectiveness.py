"""
Signal Effectiveness Tracker — R3 learning fix.

Queries signals.outcome_correct to compute per-source, per-category accuracy.
Replaces the flat 1.2x/0.6x multipliers in base_bot.apply_signal_enhancements()
with accuracy-weighted multipliers that improve as we accumulate resolution data.

Algorithm:
  accuracy = AVG(outcome_correct::int) over last 30 days for source+category
  if accuracy < 0.45 → source performs worse than random → return 1.0 (neutral)
  if direction_matches:
      multiplier = 1.0 + (accuracy - 0.5) * 0.8   → [1.0, 1.4]
  else:
      multiplier = 1.0 - (accuracy - 0.5) * 0.8   → [0.6, 1.0]

Falls back to flat 1.2x/0.6x when < MIN_SAMPLE signals exist (cold start).
Cache TTL: 1 hour (accuracy doesn't change minute-to-minute).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from structlog import get_logger

if TYPE_CHECKING:
    from base_engine.data.database import Database

logger = get_logger()

# Minimum resolved signals required before using accuracy-weighted multiplier.
# Below this threshold fall back to flat 1.2x/0.6x.
MIN_SAMPLE = 10

# How long to cache per-source-category accuracy (seconds).
_CACHE_TTL_SECONDS = 3600  # 1 hour

# Flat fallback multipliers (same as previous hard-coded values).
_FLAT_AGREE = 1.2
_FLAT_DISAGREE = 0.6


class SignalEffectivenessTracker:
    """
    Tracks per-source, per-category signal accuracy from the `signals` table
    and exposes dynamic multipliers for confidence adjustment.

    Usage:
        tracker = SignalEffectivenessTracker(db=engine.db)

        # In _signals_mult() inside base_bot.apply_signal_enhancements():
        mult = await tracker.get_multiplier(source="gdelt", category="politics", direction_matches=True)
        confidence *= mult
    """

    def __init__(self, db: "Database"):
        self._db = db
        # Cache: key = "source:category" → (accuracy: float, sample_n: int, expires_at: datetime)
        self._cache: Dict[str, Tuple[float, int, datetime]] = {}
        self._cache_lock = asyncio.Lock()
        self._initialized = False

    # ── Public API ─────────────────────────────────────────────────────────

    async def get_multiplier(
        self,
        source: str,
        category: str,
        direction_matches: bool,
    ) -> float:
        """
        Return a confidence multiplier for this signal source + market category.

        Args:
            source: Signal source string (e.g. "gdelt", "news", "bluesky", "reddit", "social").
            category: Market category (e.g. "politics", "crypto", "sports") or "" for unknown.
            direction_matches: True if signal direction agrees with our trade direction.

        Returns:
            Float multiplier. 1.0 = neutral (no adjustment).
        """
        try:
            accuracy, sample_n = await self._get_accuracy(source, category)
        except Exception as exc:
            logger.debug("SignalEffectivenessTracker accuracy fetch failed: %s", exc)
            return _FLAT_AGREE if direction_matches else _FLAT_DISAGREE

        if sample_n < MIN_SAMPLE:
            # Cold start: insufficient data → use flat fallback.
            return _FLAT_AGREE if direction_matches else _FLAT_DISAGREE

        if accuracy < 0.45:
            # Source is performing worse than random — do not penalise or boost.
            return 1.0

        if direction_matches:
            # More accurate sources boost confidence more (max 1.4x at 100% accuracy).
            return min(1.4, 1.0 + (accuracy - 0.5) * 0.8)
        else:
            # More accurate sources penalise disagreement more (min 0.6x at 100% accuracy).
            return max(0.6, 1.0 - (accuracy - 0.5) * 0.8)

    async def get_source_accuracy(self, source: str, category: str) -> Tuple[float, int]:
        """
        Public accessor: returns (accuracy, sample_n) for a source+category pair.
        accuracy is in [0.0, 1.0]; sample_n is the count of resolved signals used.
        """
        return await self._get_accuracy(source, category)

    # ── Internal ────────────────────────────────────────────────────────────

    async def _get_accuracy(self, source: str, category: str) -> Tuple[float, int]:
        """
        Returns (accuracy, sample_n) from cache or DB query.
        Thread-safe: uses asyncio.Lock around cache writes.
        """
        key = f"{source.lower()}:{(category or '').lower()}"
        now = datetime.now(timezone.utc)

        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                accuracy, sample_n, expires_at = cached
                if now < expires_at:
                    return accuracy, sample_n

        # Fetch from DB (outside lock to avoid blocking other coroutines).
        accuracy, sample_n = await self._query_accuracy(source, category)

        async with self._cache_lock:
            self._cache[key] = (
                accuracy,
                sample_n,
                now + timedelta(seconds=_CACHE_TTL_SECONDS),
            )

        return accuracy, sample_n

    async def _query_accuracy(self, source: str, category: str) -> Tuple[float, int]:
        """
        Query the `signals` table for resolved signals matching source+category
        over the last 30 days.

        Returns (accuracy, sample_n). Falls back to (0.5, 0) on any error.
        """
        try:
            from sqlalchemy import text

            # Join signals with markets to get category — or use a category column if present.
            # The signals table may not have a category column directly; we join on market_id.
            # If the join is too expensive, we'll query by source only (category='').
            if category:
                sql = text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE s.outcome_correct IS NOT NULL) AS sample_n,
                        AVG(s.outcome_correct::int) FILTER (WHERE s.outcome_correct IS NOT NULL) AS accuracy
                    FROM signals s
                    JOIN markets m ON (s.market_id = m.id::text OR s.market_id = m.condition_id)
                    WHERE
                        s.source ILIKE :source
                        AND m.category ILIKE :category
                        AND s.direction IN ('YES', 'NO')
                        AND s.created_at > NOW() - INTERVAL '30 days'
                    """
                )
                params = {"source": source, "category": category}
            else:
                sql = text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE outcome_correct IS NOT NULL) AS sample_n,
                        AVG(outcome_correct::int) FILTER (WHERE outcome_correct IS NOT NULL) AS accuracy
                    FROM signals
                    WHERE
                        source ILIKE :source
                        AND direction IN ('YES', 'NO')
                        AND created_at > NOW() - INTERVAL '30 days'
                    """
                )
                params = {"source": source}

            async with self._db.get_session() as session:
                result = await session.execute(sql, params)
                row = result.fetchone()

            if row is None or row.sample_n == 0:
                return 0.5, 0

            return float(row.accuracy or 0.5), int(row.sample_n)

        except Exception as exc:
            logger.debug(
                "SignalEffectivenessTracker DB query failed (source=%s category=%s): %s",
                source, category, exc,
            )
            return 0.5, 0

    # ── Diagnostics ─────────────────────────────────────────────────────────

    async def get_all_source_stats(self) -> Dict[str, Dict]:
        """
        Return a summary dict of all cached accuracy values for dashboard display.
        """
        async with self._cache_lock:
            result = {}
            for key, (accuracy, sample_n, expires_at) in self._cache.items():
                source, _, cat = key.partition(":")
                result[key] = {
                    "source": source,
                    "category": cat,
                    "accuracy": round(accuracy, 4),
                    "sample_n": sample_n,
                    "cache_expires": expires_at.isoformat(),
                }
            return result
