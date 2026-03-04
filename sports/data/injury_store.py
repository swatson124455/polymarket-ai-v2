"""
Injury Store — persistence layer for detected injury/status-change events.

Phase 1: functional stub — methods exist but DB ops are no-ops when db=None.
Phase 2: full save() / is_duplicate() / mark_bet_triggered() using the
         sports_injury_events ORM table.

Deduplication window: same (player_id, detected_status, source) within
INJURY_DEDUP_WINDOW_MINUTES (default 60) is treated as a duplicate.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from structlog import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# InjuryEvent dataclass (canonical signal from news pipeline)
# ---------------------------------------------------------------------------

@dataclass
class InjuryEvent:
    """
    Resolved injury / player-status event emitted by the news pipeline.

    Attributes:
        id:              Row ID in sports_injury_events after save(); None before.
        player_raw:      Raw player name from news text.
        player_id:       Resolved sports_players.id (None if unresolvable).
        game_id:         Nearest upcoming sports_games.id (None if not found).
        sport:           nba / nfl / mlb / nhl / soccer / tennis
        detected_status: out / doubtful / questionable / day-to-day / free-agent-move
        severity:        season_ending / multi_week / day-to-day / offseason_move
        confidence:      0.0–1.0 from NLP classifier
        nlp_tier:        regex / spacy / llm
        source:          twitter / rss / reddit / discord / telegram / manual
        source_url:      Original URL or tweet URL
        raw_text:        Full text snippet that triggered the detection
        detected_at:     Naive UTC datetime of detection
    """
    player_raw: str
    sport: str
    detected_status: str
    confidence: float
    source: str
    raw_text: str
    id: Optional[int] = None
    player_id: Optional[int] = None
    game_id: Optional[int] = None
    severity: Optional[str] = None
    nlp_tier: str = "regex"
    source_url: Optional[str] = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


# ---------------------------------------------------------------------------
# In-memory dedup cache (avoids DB hit on every event)
# ---------------------------------------------------------------------------

class _DedupCache:
    """
    Recent-seen cache: (player_raw_lower, detected_status, source) → monotonic_ts.
    TTL = INJURY_DEDUP_WINDOW_MINUTES (default 60min) converted to seconds.
    """
    def __init__(self) -> None:
        self._seen: Dict[Tuple[str, str, str], float] = {}
        self._lock = asyncio.Lock()

    async def is_duplicate(
        self,
        player_raw: str,
        detected_status: str,
        source: str,
        window_seconds: int = 3600,
    ) -> bool:
        key = (player_raw.lower(), detected_status.lower(), source.lower())
        now = time.monotonic()
        async with self._lock:
            if key in self._seen:
                if now - self._seen[key] < window_seconds:
                    return True
                del self._seen[key]
            self._seen[key] = now
            # Prune entries older than 2× window to prevent unbounded growth
            cutoff = now - window_seconds * 2
            stale = [k for k, ts in self._seen.items() if ts < cutoff]
            for k in stale:
                del self._seen[k]
        return False


_dedup = _DedupCache()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def is_duplicate(
    event: InjuryEvent,
    window_minutes: int = 60,
    db=None,
) -> bool:
    """
    Return True if an equivalent event was already processed within the window.

    Uses in-memory cache for speed. Phase 2 can augment with a DB check
    for events that survived a restart.
    """
    from config.settings import settings
    window_sec = int(getattr(settings, "INJURY_DEDUP_WINDOW_MINUTES", window_minutes)) * 60
    return await _dedup.is_duplicate(
        event.player_raw,
        event.detected_status,
        event.source,
        window_seconds=window_sec,
    )


async def save(event: InjuryEvent, db=None) -> Optional[int]:
    """
    Persist event to sports_injury_events. Returns the new row id.

    Phase 1: no-op when db is None — returns None (dedup cache still records it).
    Phase 2: INSERT into sports_injury_events using ORM.
    """
    if db is None:
        logger.debug(
            "injury_store.save: no DB, skipping persistence",
            player_raw=event.player_raw,
            sport=event.sport,
            status=event.detected_status,
        )
        return None

    try:
        from sports.data.sports_db import SportsInjuryEvent
        detected_at = event.detected_at
        if getattr(detected_at, "tzinfo", None) is not None:
            detected_at = detected_at.replace(tzinfo=None)

        async with db.get_session() as session:
            row = SportsInjuryEvent(
                player_id=event.player_id,
                game_id=event.game_id,
                source=event.source,
                source_url=event.source_url,
                raw_text=event.raw_text,
                player_raw=event.player_raw,
                detected_status=event.detected_status,
                severity=event.severity,
                confidence=event.confidence,
                nlp_tier=event.nlp_tier,
                detected_at=detected_at,
                bet_triggered=False,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            event.id = row.id
            logger.info(
                "injury_store.save: persisted",
                id=row.id,
                player_raw=event.player_raw,
                status=event.detected_status,
                confidence=event.confidence,
            )
            return row.id
    except Exception as e:
        logger.warning("injury_store.save failed", error=str(e), exc_info=True)
        return None


async def mark_bet_triggered(
    event_id: int,
    market_id: str,
    db=None,
) -> None:
    """
    Set bet_triggered=True and bet_market_id on the stored injury event.

    Phase 1: no-op when db is None.
    """
    if db is None or event_id is None:
        return
    try:
        from sports.data.sports_db import SportsInjuryEvent
        from sqlalchemy import select
        async with db.get_session() as session:
            result = await session.execute(
                select(SportsInjuryEvent).where(SportsInjuryEvent.id == event_id)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                row.bet_triggered = True
                row.bet_market_id = market_id
                await session.commit()
                logger.debug(
                    "injury_store.mark_bet_triggered",
                    event_id=event_id,
                    market_id=market_id,
                )
    except Exception as e:
        logger.warning(
            "injury_store.mark_bet_triggered failed",
            event_id=event_id,
            error=str(e),
        )
