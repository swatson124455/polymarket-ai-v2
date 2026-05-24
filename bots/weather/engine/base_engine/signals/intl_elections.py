"""
International Elections Client — IFES ElectionGuide + IDEA databases.

Provides election calendar data for international prediction markets.

Sources:
- IFES ElectionGuide (240 countries, 93 datapoints per election)
- International IDEA (comparative election databases)
"""
import asyncio
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()

ELECTIONGUIDE_BASE = "https://www.electionguide.org/api/v1"
IDEA_BASE = "https://www.idea.int/data-tools/api/v1"

# Major upcoming elections to track (manually curated, updated periodically)
TRACKED_COUNTRIES = [
    "united states", "united kingdom", "france", "germany", "brazil",
    "india", "mexico", "canada", "australia", "japan", "south korea",
    "italy", "spain", "poland", "turkey", "argentina", "colombia",
    "nigeria", "south africa", "kenya", "indonesia", "philippines",
    "taiwan", "israel", "ukraine",
]


class InternationalElectionsClient:
    """
    Tracks international elections for prediction market signals.
    """

    def __init__(self):
        self._client = None
        self._election_cache: Dict[str, Any] = {}
        self._cache_ttl = 86400  # 24 hours
        self._seen_ids: Set[str] = set()
        self._running = False

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None

    async def start(self, poll_interval_seconds: int = 43200) -> None:
        """Start polling loop. Default: every 12 hours (elections don't change fast)."""
        self._running = True
        logger.info("InternationalElectionsClient started", interval=poll_interval_seconds)
        while self._running:
            try:
                elections = await self.fetch_upcoming_elections()
                if elections:
                    logger.info("International elections tracked", count=len(elections))
            except Exception as e:
                logger.warning("IntlElections poll error: %s", e)
            await asyncio.sleep(poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def fetch_upcoming_elections(self, months_ahead: int = 6) -> List[Dict[str, Any]]:
        """
        Fetch upcoming elections from ElectionGuide.

        Returns list of election dicts with country, date, type, and significance.
        """
        import time
        cache_key = f"upcoming:{months_ahead}"
        cached = self._election_cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < self._cache_ttl:
            return cached.get("data", [])

        await self._ensure_client()
        elections = []

        try:
            url = f"{ELECTIONGUIDE_BASE}/elections"
            params = {
                "status": "upcoming",
                "limit": 50,
            }
            resp = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=15.0
            )
            if resp.status_code == 200:
                data = resp.json()
                for election in data.get("elections", data.get("results", [])):
                    elections.append(self._normalize_election(election))
            else:
                logger.debug("ElectionGuide API returned %d", resp.status_code)

        except asyncio.TimeoutError:
            logger.debug("ElectionGuide API timeout")
        except Exception as e:
            logger.debug("ElectionGuide fetch failed: %s", e)

        # Fallback: if API is unavailable, provide known elections from IDEA
        if not elections:
            elections = await self._fetch_idea_elections(months_ahead)

        self._election_cache[cache_key] = {"data": elections, "ts": time.time()}
        return elections

    async def _fetch_idea_elections(self, months_ahead: int = 6) -> List[Dict[str, Any]]:
        """Fallback: fetch from International IDEA database."""
        await self._ensure_client()
        elections = []

        try:
            url = f"{IDEA_BASE}/elections"
            cutoff = (datetime.now(timezone.utc) + timedelta(days=months_ahead * 30)).strftime("%Y-%m-%d")
            params = {
                "date_to": cutoff,
                "status": "upcoming",
                "format": "json",
            }
            resp = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=15.0
            )
            if resp.status_code == 200:
                data = resp.json()
                for election in data.get("data", data.get("results", [])):
                    elections.append(self._normalize_election(election))

        except Exception as e:
            logger.debug("IDEA elections fetch failed: %s", e)

        return elections

    def _normalize_election(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize election data from different sources into common format."""
        country = (
            raw.get("country", "")
            or raw.get("country_name", "")
            or raw.get("name", "")
        )
        election_date = (
            raw.get("election_date", "")
            or raw.get("date", "")
            or raw.get("start_date", "")
        )
        election_type = (
            raw.get("election_type", "")
            or raw.get("type", "")
            or "general"
        )

        return {
            "country": country,
            "election_date": str(election_date),
            "election_type": str(election_type).lower(),
            "description": raw.get("description", "") or raw.get("title", ""),
            "is_tracked": country.lower() in TRACKED_COUNTRIES,
            "source": "electionguide" if "electionguide" in str(raw.get("url", "")) else "idea",
        }

    def get_elections_for_market(self, market_question: str) -> List[Dict[str, Any]]:
        """
        Match cached elections to a market question by country/keyword.

        Returns relevant elections sorted by date.
        """
        question_lower = market_question.lower()
        matches = []

        cache_key = "upcoming:6"
        cached = self._election_cache.get(cache_key, {}).get("data", [])

        for election in cached:
            country = election.get("country", "").lower()
            if country and country in question_lower:
                matches.append(election)
            # Also check election type keywords
            e_type = election.get("election_type", "")
            if e_type in question_lower:
                matches.append(election)

        # Deduplicate and sort by date
        seen = set()
        unique = []
        for m in matches:
            key = f"{m['country']}:{m['election_date']}"
            if key not in seen:
                seen.add(key)
                unique.append(m)

        unique.sort(key=lambda x: x.get("election_date", ""))
        return unique

    async def check_once(self) -> List[Dict[str, Any]]:
        return await self.fetch_upcoming_elections()
