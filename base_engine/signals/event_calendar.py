"""
Event Calendar - Manage scheduled events that affect markets.

Events:
- Court dates
- Earnings announcements
- Elections
- Government announcements
- Market-specific events
- Recurring scheduled events (SCOTUS, FOMC, BLS, Congressional)
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from structlog import get_logger
from sqlalchemy import select, and_
from base_engine.data.database import Database, ScheduledEvent, _naive_utc

logger = get_logger()

# ── Recurring Scheduled Events (hardcoded, known schedules) ───────────────

RECURRING_SCHEDULES = {
    "scotus_opinions": {
        "description": "SCOTUS opinions released at 10:00 AM ET on non-argument days",
        "event_type": "court_date",
        "time_et": "10:00",
        "categories": ["politics"],
        "note": "Bulk opinions by mid-June; term ends late June/early July",
    },
    "fomc_statement": {
        "description": "FOMC interest rate decision and statement",
        "event_type": "announcement",
        "time_et": "14:00",
        "categories": ["finance", "crypto"],
        "note": "Press conference at 14:30 ET. 8 meetings/year.",
    },
    "fomc_minutes": {
        "description": "FOMC meeting minutes release",
        "event_type": "announcement",
        "time_et": "14:00",
        "categories": ["finance"],
        "note": "Released 3 weeks after each FOMC meeting.",
    },
    "bls_jobs_report": {
        "description": "BLS Employment Situation (Non-Farm Payrolls)",
        "event_type": "announcement",
        "time_et": "08:30",
        "categories": ["finance"],
        "note": "First Friday of each month.",
    },
    "bls_cpi": {
        "description": "BLS Consumer Price Index (CPI)",
        "event_type": "announcement",
        "time_et": "08:30",
        "categories": ["finance", "crypto"],
        "note": "Usually 10th-14th of each month.",
    },
    "bea_gdp": {
        "description": "BEA GDP advance/preliminary/final estimate",
        "event_type": "announcement",
        "time_et": "08:30",
        "categories": ["finance"],
        "note": "Three estimates per quarter (advance, preliminary, final).",
    },
    "congressional_vote": {
        "description": "Congressional floor vote",
        "event_type": "announcement",
        "time_et": None,  # Variable
        "categories": ["politics"],
        "note": "Check live.house.gov for real-time schedule.",
    },
}

# T-minus alert windows (seconds before event)
T_MINUS_WINDOWS = [3600, 900, 60]  # 1h, 15min, 1min


class EventCalendar:
    """
    Manage scheduled events that may affect markets.
    """
    
    def __init__(self, db: Database):
        self.db = db
    
    async def add_event(
        self,
        event_name: str,
        event_type: str,
        scheduled_time: datetime,
        market_id: Optional[str] = None,
        source_url: Optional[str] = None,
        description: Optional[str] = None
    ) -> ScheduledEvent:
        """
        Add a scheduled event.
        
        Args:
            event_name: Name of the event
            event_type: Type (court_date, earnings, election, announcement, etc.)
            scheduled_time: When the event occurs
            market_id: Optional market ID this affects
            source_url: Source URL
            description: Event description
        
        Returns:
            Created ScheduledEvent
        """
        if not self.db.session_factory:
            raise RuntimeError("Database not available")
        
        async with self.db.get_session() as session:
            event = ScheduledEvent(
                market_id=market_id,
                event_type=event_type,
                event_name=event_name,
                scheduled_time=scheduled_time,
                source_url=source_url,
                description=description
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            
            logger.info(f"Added scheduled event: {event_name}", event_type=event_type, scheduled_time=scheduled_time)
            
            return event
    
    async def get_upcoming_events(
        self,
        hours: int = 24,
        market_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming events.
        
        Args:
            hours: How many hours ahead to look
            market_id: Optional market ID filter
        
        Returns:
            List of upcoming events
        """
        if not self.db.session_factory:
            return []
        
        async with self.db.get_session() as session:
            now_utc = datetime.now(timezone.utc)
            cutoff = _naive_utc(now_utc + timedelta(hours=hours))
            start = _naive_utc(now_utc)
            conditions = [
                ScheduledEvent.scheduled_time <= cutoff,
                ScheduledEvent.scheduled_time >= start
            ]
            
            if market_id:
                conditions.append(ScheduledEvent.market_id == market_id)
            
            result = await session.execute(
                select(ScheduledEvent).where(and_(*conditions)).order_by(ScheduledEvent.scheduled_time.asc())
            )
            events = result.scalars().all()
            
            return [
                {
                    "id": e.id,
                    "market_id": e.market_id,
                    "event_type": e.event_type,
                    "event_name": e.event_name,
                    "scheduled_time": e.scheduled_time.isoformat() if e.scheduled_time else None,
                    "source_url": e.source_url,
                    "description": e.description,
                    "notified": e.notified
                }
                for e in events
            ]
    
    async def mark_notified(self, event_id: int):
        """Mark an event as notified."""
        if not self.db.session_factory:
            return
        
        async with self.db.get_session() as session:
            result = await session.execute(
                select(ScheduledEvent).where(ScheduledEvent.id == event_id)
            )
            event = result.scalar_one_or_none()
            
            if event:
                event.notified = True
                await session.commit()
    
    def get_recurring_schedules(self) -> Dict[str, Any]:
        """Return all known recurring event schedules."""
        return RECURRING_SCHEDULES

    def get_t_minus_alerts(
        self,
        event_time: datetime,
        now: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get T-minus countdown alerts for an upcoming event.

        Returns list of alerts that should fire (window has been reached
        but event hasn't passed yet).
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)

        seconds_until = (event_time - now).total_seconds()
        if seconds_until < 0:
            return []  # Event already passed

        alerts = []
        for window_seconds in T_MINUS_WINDOWS:
            if seconds_until <= window_seconds:
                if window_seconds >= 3600:
                    label = f"T-{window_seconds // 3600}h"
                elif window_seconds >= 60:
                    label = f"T-{window_seconds // 60}min"
                else:
                    label = f"T-{window_seconds}s"

                alerts.append({
                    "window_seconds": window_seconds,
                    "label": label,
                    "seconds_until_event": round(seconds_until),
                    "event_time": event_time.isoformat(),
                })

        return alerts

    async def get_upcoming_scheduled_events_with_alerts(
        self,
        hours: int = 24,
        market_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming events enriched with T-minus countdown alerts.

        Combines DB events with alert windows for pre-positioning.
        """
        events = await self.get_upcoming_events(hours=hours, market_id=market_id)
        now = datetime.now(timezone.utc)

        for event in events:
            scheduled_str = event.get("scheduled_time", "")
            if scheduled_str:
                try:
                    from dateutil.parser import parse as parse_date
                    event_time = parse_date(scheduled_str)
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=timezone.utc)
                    event["t_minus_alerts"] = self.get_t_minus_alerts(event_time, now)
                    event["seconds_until"] = round((event_time - now).total_seconds())
                except Exception:
                    event["t_minus_alerts"] = []
                    event["seconds_until"] = None
            else:
                event["t_minus_alerts"] = []
                event["seconds_until"] = None

        return events

    def match_event_to_market(
        self,
        event_type: str,
        market_question: str,
    ) -> Optional[str]:
        """
        Match a recurring event type to a market question.

        Returns the schedule key if matched, None otherwise.
        """
        q_lower = (market_question or "").lower()

        keyword_map = {
            "scotus_opinions": ["supreme court", "scotus", "court ruling", "court decision"],
            "fomc_statement": ["fed", "fomc", "interest rate", "rate cut", "rate hike", "federal reserve"],
            "fomc_minutes": ["fomc minutes", "fed minutes"],
            "bls_jobs_report": ["jobs report", "non-farm", "nonfarm", "employment", "payroll", "unemployment rate"],
            "bls_cpi": ["cpi", "inflation", "consumer price"],
            "bea_gdp": ["gdp", "gross domestic product"],
            "congressional_vote": ["congress", "senate vote", "house vote", "bill pass"],
        }

        for schedule_key, keywords in keyword_map.items():
            if any(kw in q_lower for kw in keywords):
                return schedule_key

        return None

    async def import_events_from_markets(self):
        """
        Extract scheduled events from market descriptions.
        Looks for dates, deadlines, etc.
        Batches candidate events then adds in one pass to avoid Session.add() during flush (SAWarning).
        """
        if not self.db.session_factory:
            return
        
        try:
            async with self.db.get_session() as session:
                from base_engine.data.database import Market
                import re
                
                # Get all active markets (read-only, no flush)
                result = await session.execute(
                    select(Market).where(Market.active == True)
                )
                markets = result.scalars().all()
                
                # Collect candidate events (no session.add or execute that could trigger flush)
                candidates: List[Dict[str, Any]] = []
                for market in markets:
                    description = getattr(market, 'description', None) or ""
                    question = market.question or ""
                    combined_text = f"{question} {description}".strip()
                    date_patterns = [
                        r"(\w+\s+\d{1,2},?\s+\d{4})",
                        r"(\d{1,2}/\d{1,2}/\d{4})",
                        r"(\d{4}-\d{2}-\d{2})",
                    ]
                    # Strip common prefixes that concatenate with month names (e.g. "byJune" -> "June")
                    # Handle both "by June" (with space) and "byJune" (concatenated - no space)
                    _date_prefixes = re.compile(
                        r"^(by|before|on|after|until)(?:\s+|(?=[A-Z]))",
                        re.IGNORECASE,
                    )

                    for pattern in date_patterns:
                        for match in re.findall(pattern, combined_text):
                            try:
                                from dateutil.parser import parse
                                to_parse = _date_prefixes.sub("", match.strip()).strip()
                                if not to_parse:
                                    continue
                                event_date = parse(to_parse)
                                if event_date.tzinfo is None:
                                    event_date = event_date.replace(tzinfo=timezone.utc)
                                event_type = "announcement"
                                if any(w in combined_text.lower() for w in ["court", "trial", "hearing"]):
                                    event_type = "court_date"
                                elif any(w in combined_text.lower() for w in ["election", "vote", "ballot"]):
                                    event_type = "election"
                                elif any(w in combined_text.lower() for w in ["earnings", "financial", "quarterly"]):
                                    event_type = "earnings"
                                candidates.append({
                                    "market_id": market.id,
                                    "scheduled_time": event_date,
                                    "event_type": event_type,
                                    "event_name": f"{(question or '')[:50]} - {event_type}",
                                    "description": combined_text[:500],
                                })
                            except Exception as e:
                                logger.debug(f"Failed to parse date '{match}': {str(e)}")
                                continue
                
                if not candidates:
                    return
                
                # Batch-check existing (market_id, scheduled_time) in one query
                try:
                    existing_pairs = set()
                    for c in candidates:
                        r = await session.execute(
                            select(ScheduledEvent.id).where(
                                ScheduledEvent.market_id == c["market_id"],
                                ScheduledEvent.scheduled_time == _naive_utc(c["scheduled_time"]),
                            ).limit(1)
                        )
                        if r.scalar_one_or_none():
                            existing_pairs.add((c["market_id"], _naive_utc(c["scheduled_time"])))
                except Exception as db_err:
                    if "cannot switch to a different thread" in str(db_err) or "thread" in str(db_err).lower():
                        logger.debug(f"Skipping event import due to thread context: {str(db_err)}")
                        return
                    raise
                
                # Add only new events in one pass, then single commit (avoids add() during flush)
                imported_count = 0
                for c in candidates:
                    key = (c["market_id"], _naive_utc(c["scheduled_time"]))
                    if key in existing_pairs:
                        continue
                    event = ScheduledEvent(
                        market_id=c["market_id"],
                        event_type=c["event_type"],
                        event_name=c["event_name"],
                        scheduled_time=_naive_utc(c["scheduled_time"]),
                        description=c["description"],
                    )
                    session.add(event)
                    imported_count += 1
                    existing_pairs.add(key)
                
                await session.commit()
                logger.info(f"Imported {imported_count} events from market descriptions")
        except Exception as e:
            if "cannot switch to a different thread" in str(e) or ("thread" in str(e).lower() and "exited" in str(e).lower()):
                logger.warning(f"Event import skipped due to thread context: {str(e)}")
                return
            logger.error(f"Event import failed: {str(e)}", exc_info=True)
            raise
