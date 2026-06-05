"""
Legislative Intelligence Tracker — Congress.gov + ProPublica APIs.

Monitors congressional activity (bills, votes, committee actions) and generates
trading signals for markets affected by legislative outcomes.

Sources:
- Congress.gov API (free, official Library of Congress)
- ProPublica Congress API (free, 5K requests/day)
"""
import asyncio
import hashlib
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()

# ── Congress.gov API endpoints ────────────────────────────────────────────────
CONGRESS_GOV_BASE = "https://api.congress.gov/v3"

# ── ProPublica Congress API endpoints ─────────────────────────────────────────
PROPUBLICA_BASE = "https://api.propublica.org/congress/v1"

# ── Keywords that map legislative activity to prediction market categories ─────
LEGISLATIVE_KEYWORDS = {
    "crypto": [
        "cryptocurrency", "bitcoin", "digital asset", "stablecoin", "blockchain",
        "crypto", "defi", "web3", "cbdc", "digital currency", "sec crypto",
    ],
    "politics": [
        "impeach", "election", "ballot", "campaign finance", "filibuster",
        "supreme court", "confirmation", "nomination", "executive order",
        "government shutdown", "debt ceiling", "continuing resolution",
    ],
    "finance": [
        "interest rate", "federal reserve", "inflation", "tariff", "trade war",
        "tax", "fiscal", "budget", "deficit", "gdp", "stimulus", "bailout",
    ],
    "science": [
        "climate", "environment", "epa", "nasa", "pandemic", "vaccine",
        "public health", "fda", "drug approval", "ai regulation", "artificial intelligence",
    ],
    "geopolitical": [
        "sanctions", "foreign aid", "nato", "defense", "military",
        "arms", "treaty", "diplomatic", "embargo", "ukraine", "china", "iran",
    ],
}

# ── Bill status signals ───────────────────────────────────────────────────────
BULLISH_STATUSES = {"passed_house", "passed_senate", "signed_into_law", "resolving_differences"}
BEARISH_STATUSES = {"vetoed", "failed_house", "failed_senate", "tabled"}


class LegislativeTracker:
    """
    Tracks congressional activity and generates signals for prediction markets.

    Polls Congress.gov and ProPublica APIs at configurable intervals.
    Emits signal dicts compatible with SignalIngestionService._publish_signal().
    """

    def __init__(
        self,
        congress_api_key: Optional[str] = None,
        propublica_api_key: Optional[str] = None,
    ):
        import os
        self._congress_key = congress_api_key or os.getenv("CONGRESS_GOV_API_KEY", "")
        self._propublica_key = propublica_api_key or os.getenv("PROPUBLICA_API_KEY", "")
        self._seen_bill_ids: Set[str] = set()
        self._seen_vote_ids: Set[str] = set()
        self._running = False
        self._client = None
        self._max_seen = 5000

    @property
    def is_available(self) -> bool:
        return bool(self._congress_key or self._propublica_key)

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=20.0)

    # ── Public lifecycle ──────────────────────────────────────────────────────

    async def start(self, poll_interval_seconds: int = 1800) -> None:
        """Start polling loop. Default: every 30 minutes."""
        self._running = True
        logger.info("LegislativeTracker started", interval=poll_interval_seconds)
        while self._running:
            try:
                signals = await self.poll_all()
                if signals:
                    logger.info("Legislative signals generated", count=len(signals))
            except Exception as e:
                logger.warning("LegislativeTracker poll error: %s", e)
            await asyncio.sleep(poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Core polling ──────────────────────────────────────────────────────────

    async def poll_all(self) -> List[Dict[str, Any]]:
        """Poll all sources and return combined signals."""
        signals: List[Dict[str, Any]] = []

        tasks = []
        if self._congress_key:
            tasks.append(self._poll_congress_gov())
        if self._propublica_key:
            tasks.append(self._poll_propublica_votes())
            tasks.append(self._poll_propublica_bills())

        if not tasks:
            # No API keys — return empty
            return signals

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.debug("Legislative poll sub-task failed: %s", result)
                continue
            if isinstance(result, list):
                signals.extend(result)

        return signals

    # ── Congress.gov API ──────────────────────────────────────────────────────

    async def _poll_congress_gov(self) -> List[Dict[str, Any]]:
        """Fetch recent bills from Congress.gov API."""
        await self._ensure_client()
        signals = []

        try:
            url = f"{CONGRESS_GOV_BASE}/bill"
            params = {
                "api_key": self._congress_key,
                "format": "json",
                "limit": 50,
                "sort": "updateDate+desc",
            }
            resp = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()

            bills = data.get("bills", [])
            for bill in bills:
                bill_id = bill.get("number", "")
                congress = bill.get("congress", "")
                bill_type = bill.get("type", "")
                unique_id = f"{congress}-{bill_type}-{bill_id}"

                if unique_id in self._seen_bill_ids:
                    continue
                self._seen_bill_ids.add(unique_id)

                title = bill.get("title", "")
                latest_action = bill.get("latestAction", {})
                action_text = latest_action.get("text", "")
                action_date = latest_action.get("actionDate", "")

                # Match to prediction market categories
                categories = self._match_categories(f"{title} {action_text}")
                if not categories:
                    continue

                # Determine direction from action text
                direction, confidence = self._action_to_signal(action_text, title)

                signals.append({
                    "source_type": "legislative",
                    "source_name": f"congress_gov:{unique_id}",
                    "direction": direction,
                    "confidence": confidence,
                    "raw_text": f"[Bill {bill_type}{bill_id}] {title} — {action_text}",
                    "categories_matched": categories,
                    "bill_id": unique_id,
                    "action_date": action_date,
                    "time_sensitivity": "hours",
                    "is_breaking": False,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                })

        except asyncio.TimeoutError:
            logger.debug("Congress.gov API timeout")
        except Exception as e:
            logger.debug("Congress.gov poll failed: %s", e)

        self._evict_seen(self._seen_bill_ids)
        return signals

    # ── ProPublica Congress API ───────────────────────────────────────────────

    async def _poll_propublica_votes(self) -> List[Dict[str, Any]]:
        """Fetch recent votes from ProPublica."""
        await self._ensure_client()
        signals = []

        try:
            # Recent Senate votes
            for chamber in ["senate", "house"]:
                url = f"{PROPUBLICA_BASE}/{chamber}/votes/recent.json"
                headers = {"X-API-Key": self._propublica_key}
                resp = await asyncio.wait_for(
                    self._client.get(url, headers=headers), timeout=15.0
                )
                resp.raise_for_status()
                data = resp.json()

                votes = data.get("results", {}).get("votes", [])
                for vote in votes[:20]:  # Last 20 votes
                    vote_id = vote.get("roll_call", "")
                    unique_id = f"{chamber}-{vote.get('congress', '')}-{vote.get('session', '')}-{vote_id}"

                    if unique_id in self._seen_vote_ids:
                        continue
                    self._seen_vote_ids.add(unique_id)

                    question = vote.get("question", "")
                    description = vote.get("description", "")
                    result = vote.get("result", "")
                    bill_info = vote.get("bill", {}) or {}
                    bill_title = bill_info.get("title", "")

                    full_text = f"{question} {description} {bill_title}"
                    categories = self._match_categories(full_text)
                    if not categories:
                        continue

                    # Vote result → signal direction
                    direction = "NEUTRAL"
                    confidence = 0.5
                    result_lower = result.lower()
                    if "passed" in result_lower or "agreed" in result_lower:
                        direction = "YES"
                        confidence = 0.7
                    elif "failed" in result_lower or "rejected" in result_lower:
                        direction = "NO"
                        confidence = 0.7

                    # Party-line votes are more significant
                    dem_yes = int(vote.get("democratic", {}).get("yes", 0) or 0)
                    dem_no = int(vote.get("democratic", {}).get("no", 0) or 0)
                    rep_yes = int(vote.get("republican", {}).get("yes", 0) or 0)
                    rep_no = int(vote.get("republican", {}).get("no", 0) or 0)
                    total = dem_yes + dem_no + rep_yes + rep_no
                    if total > 0:
                        margin = abs((dem_yes + rep_yes) - (dem_no + rep_no)) / total
                        if margin > 0.3:
                            confidence = min(0.85, confidence + 0.1)

                    signals.append({
                        "source_type": "legislative",
                        "source_name": f"propublica_vote:{unique_id}",
                        "direction": direction,
                        "confidence": confidence,
                        "raw_text": f"[{chamber.title()} Vote] {question}: {result} — {description}",
                        "categories_matched": categories,
                        "vote_id": unique_id,
                        "vote_result": result,
                        "time_sensitivity": "immediate",
                        "is_breaking": True,
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
                    })

        except asyncio.TimeoutError:
            logger.debug("ProPublica votes API timeout")
        except Exception as e:
            logger.debug("ProPublica votes poll failed: %s", e)

        self._evict_seen(self._seen_vote_ids)
        return signals

    async def _poll_propublica_bills(self) -> List[Dict[str, Any]]:
        """Fetch recently introduced/updated bills from ProPublica."""
        await self._ensure_client()
        signals = []

        try:
            # Recent bills (both chambers)
            url = f"{PROPUBLICA_BASE}/119/both/bills/introduced.json"
            headers = {"X-API-Key": self._propublica_key}
            resp = await asyncio.wait_for(
                self._client.get(url, headers=headers), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()

            bills = data.get("results", [{}])[0].get("bills", [])
            for bill in bills[:30]:
                bill_id = bill.get("bill_id", "")
                if bill_id in self._seen_bill_ids:
                    continue
                self._seen_bill_ids.add(bill_id)

                title = bill.get("title", "") or bill.get("short_title", "")
                summary = bill.get("summary", "") or ""
                sponsor_party = bill.get("sponsor_party", "")
                cosponsors = int(bill.get("cosponsors", 0) or 0)

                full_text = f"{title} {summary}"
                categories = self._match_categories(full_text)
                if not categories:
                    continue

                # More cosponsors → higher passage probability → stronger signal
                confidence = min(0.7, 0.3 + cosponsors * 0.01)

                signals.append({
                    "source_type": "legislative",
                    "source_name": f"propublica_bill:{bill_id}",
                    "direction": "NEUTRAL",
                    "confidence": confidence,
                    "raw_text": f"[Bill {bill_id}] {title} — {cosponsors} cosponsors ({sponsor_party})",
                    "categories_matched": categories,
                    "bill_id": bill_id,
                    "sponsor_party": sponsor_party,
                    "cosponsors": cosponsors,
                    "time_sensitivity": "days",
                    "is_breaking": False,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                })

        except asyncio.TimeoutError:
            logger.debug("ProPublica bills API timeout")
        except Exception as e:
            logger.debug("ProPublica bills poll failed: %s", e)

        return signals

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _match_categories(self, text: str) -> List[str]:
        """Match text against legislative keyword categories."""
        text_lower = text.lower()
        matched = []
        for category, keywords in LEGISLATIVE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                matched.append(category)
        return matched

    def _action_to_signal(self, action_text: str, title: str = "") -> tuple:
        """Convert a legislative action to signal direction + confidence."""
        action_lower = action_text.lower()

        # Strong positive signals
        if any(phrase in action_lower for phrase in [
            "signed by president", "became public law", "passed house", "passed senate",
            "agreed to in", "cloture invoked",
        ]):
            return "YES", 0.75

        # Moderate positive signals
        if any(phrase in action_lower for phrase in [
            "reported by committee", "ordered to be reported", "placed on calendar",
            "received in", "read twice",
        ]):
            return "YES", 0.55

        # Negative signals
        if any(phrase in action_lower for phrase in [
            "vetoed", "pocket vetoed", "failed", "rejected", "tabled",
            "indefinitely postponed",
        ]):
            return "NO", 0.70

        return "NEUTRAL", 0.4

    def _evict_seen(self, seen_set: Set[str]) -> None:
        """Cap seen set size to prevent unbounded growth."""
        if len(seen_set) > self._max_seen:
            # Remove oldest half (sets are unordered, but this is good enough for dedup)
            to_remove = list(seen_set)[:len(seen_set) // 2]
            for item in to_remove:
                seen_set.discard(item)

    # ── One-shot check for testing ────────────────────────────────────────────

    async def check_once(self) -> List[Dict[str, Any]]:
        """Run a single poll cycle (for testing or manual trigger)."""
        return await self.poll_all()
