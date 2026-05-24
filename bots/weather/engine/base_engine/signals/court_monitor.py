"""
Court & Executive Action Monitor — CourtListener + Federal Register APIs.

Monitors court decisions (especially SCOTUS) and executive orders that
affect prediction market outcomes.

Sources:
- CourtListener API (Free Law Project, free)
- Federal Register API (free, daily executive action updates)
"""
import asyncio
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()

COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v3"
FEDERAL_REGISTER_BASE = "https://www.federalregister.gov/api/v1"

# SCOTUS timing: opinions at 10am ET on non-argument days, bulk by mid-June
SCOTUS_KEYWORDS = [
    "supreme court", "scotus", "certiorari", "oral argument",
    "opinion", "dissent", "concurrence", "amicus",
]

EXECUTIVE_KEYWORDS = [
    "executive order", "presidential memorandum", "proclamation",
    "executive action", "presidential directive",
]

MARKET_RELEVANT_KEYWORDS = {
    "politics": ["election", "voting rights", "redistricting", "gerrymandering", "campaign"],
    "crypto": ["cryptocurrency", "digital asset", "sec", "securities", "commodity"],
    "finance": ["regulation", "antitrust", "merger", "tax", "tariff", "trade"],
    "science": ["climate", "environment", "epa", "fda", "drug", "vaccine", "pandemic"],
    "geopolitical": ["sanctions", "foreign policy", "immigration", "border", "asylum"],
}


class CourtMonitor:
    """
    Monitors court decisions and executive actions for prediction market signals.
    """

    def __init__(self, courtlistener_token: Optional[str] = None):
        import os
        self._cl_token = courtlistener_token or os.getenv("COURTLISTENER_API_TOKEN", "")
        self._client = None
        self._seen_ids: Set[str] = set()
        self._running = False
        self._max_seen = 3000

    @property
    def is_available(self) -> bool:
        # Federal Register API requires no key — always available
        return True

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None

    async def start(self, poll_interval_seconds: int = 3600) -> None:
        """Start polling loop. Default: every 1 hour."""
        self._running = True
        logger.info("CourtMonitor started", interval=poll_interval_seconds)
        while self._running:
            try:
                signals = await self.poll_all()
                if signals:
                    logger.info("Court/exec signals generated", count=len(signals))
            except Exception as e:
                logger.warning("CourtMonitor poll error: %s", e)
            await asyncio.sleep(poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def poll_all(self) -> List[Dict[str, Any]]:
        """Poll all court and executive sources."""
        tasks = [
            self._poll_federal_register(),
        ]
        if self._cl_token:
            tasks.append(self._poll_courtlistener_opinions())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        signals = []
        for result in results:
            if isinstance(result, list):
                signals.extend(result)
            elif isinstance(result, Exception):
                logger.debug("Court poll sub-task failed: %s", result)
        return signals

    # ── CourtListener API ─────────────────────────────────────────────────────

    async def _poll_courtlistener_opinions(self) -> List[Dict[str, Any]]:
        """Fetch recent SCOTUS and federal court opinions."""
        await self._ensure_client()
        signals = []

        try:
            # Recent opinions from SCOTUS
            url = f"{COURTLISTENER_BASE}/opinions/"
            params = {
                "court": "scotus",
                "order_by": "-date_created",
                "page_size": 20,
            }
            headers = {"Authorization": f"Token {self._cl_token}"} if self._cl_token else {}
            resp = await asyncio.wait_for(
                self._client.get(url, params=params, headers=headers), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()

            for opinion in data.get("results", []):
                opinion_id = str(opinion.get("id", ""))
                unique_id = f"cl_opinion:{opinion_id}"
                if unique_id in self._seen_ids:
                    continue
                self._seen_ids.add(unique_id)

                case_name = opinion.get("case_name", "") or ""
                plain_text = (opinion.get("plain_text", "") or "")[:500]
                date_filed = opinion.get("date_filed", "")

                categories = self._match_categories(f"{case_name} {plain_text}")
                if not categories:
                    continue

                signals.append({
                    "source_type": "court",
                    "source_name": f"courtlistener:{opinion_id}",
                    "direction": "NEUTRAL",  # Court opinions require LLM to interpret
                    "confidence": 0.6,
                    "raw_text": f"[SCOTUS Opinion] {case_name} — {plain_text[:200]}",
                    "categories_matched": categories,
                    "date_filed": date_filed,
                    "time_sensitivity": "immediate",
                    "is_breaking": True,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                })

        except asyncio.TimeoutError:
            logger.debug("CourtListener API timeout")
        except Exception as e:
            logger.debug("CourtListener poll failed: %s", e)

        self._evict_seen()
        return signals

    # ── Federal Register API ──────────────────────────────────────────────────

    async def _poll_federal_register(self) -> List[Dict[str, Any]]:
        """Fetch recent executive orders and presidential documents."""
        await self._ensure_client()
        signals = []

        try:
            # Presidential documents from the last 7 days
            since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            url = f"{FEDERAL_REGISTER_BASE}/documents.json"
            params = {
                "conditions[presidential_document_type][]": ["executive_order", "memorandum", "proclamation"],
                "conditions[publication_date][gte]": since,
                "order": "newest",
                "per_page": 20,
            }
            resp = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()

            for doc in data.get("results", []):
                doc_number = doc.get("document_number", "")
                unique_id = f"fr:{doc_number}"
                if unique_id in self._seen_ids:
                    continue
                self._seen_ids.add(unique_id)

                title = doc.get("title", "")
                abstract = doc.get("abstract", "") or ""
                doc_type = doc.get("type", "")
                pub_date = doc.get("publication_date", "")
                html_url = doc.get("html_url", "")

                full_text = f"{title} {abstract}"
                categories = self._match_categories(full_text)
                if not categories:
                    continue

                # Executive orders are high-impact
                confidence = 0.7 if "executive_order" in doc_type.lower() else 0.5

                signals.append({
                    "source_type": "executive",
                    "source_name": f"federal_register:{doc_number}",
                    "direction": "NEUTRAL",
                    "confidence": confidence,
                    "raw_text": f"[{doc_type}] {title} — {abstract[:200]}",
                    "categories_matched": categories,
                    "document_number": doc_number,
                    "publication_date": pub_date,
                    "url": html_url,
                    "time_sensitivity": "hours",
                    "is_breaking": "executive_order" in doc_type.lower(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                })

            # Also check for significant rules/regulations
            url_rules = f"{FEDERAL_REGISTER_BASE}/documents.json"
            params_rules = {
                "conditions[type][]": ["rule"],
                "conditions[significant]": 1,
                "conditions[publication_date][gte]": since,
                "order": "newest",
                "per_page": 10,
            }
            resp2 = await asyncio.wait_for(
                self._client.get(url_rules, params=params_rules), timeout=15.0
            )
            if resp2.status_code == 200:
                for doc in resp2.json().get("results", []):
                    doc_number = doc.get("document_number", "")
                    unique_id = f"fr_rule:{doc_number}"
                    if unique_id in self._seen_ids:
                        continue
                    self._seen_ids.add(unique_id)

                    title = doc.get("title", "")
                    abstract = doc.get("abstract", "") or ""
                    categories = self._match_categories(f"{title} {abstract}")
                    if not categories:
                        continue

                    signals.append({
                        "source_type": "regulatory",
                        "source_name": f"federal_register_rule:{doc_number}",
                        "direction": "NEUTRAL",
                        "confidence": 0.5,
                        "raw_text": f"[Significant Rule] {title} — {abstract[:200]}",
                        "categories_matched": categories,
                        "time_sensitivity": "days",
                        "is_breaking": False,
                        "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                    })

        except asyncio.TimeoutError:
            logger.debug("Federal Register API timeout")
        except Exception as e:
            logger.debug("Federal Register poll failed: %s", e)

        self._evict_seen()
        return signals

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _match_categories(self, text: str) -> List[str]:
        text_lower = text.lower()
        matched = []
        for category, keywords in MARKET_RELEVANT_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                matched.append(category)
        return matched

    def _evict_seen(self) -> None:
        if len(self._seen_ids) > self._max_seen:
            to_remove = list(self._seen_ids)[:len(self._seen_ids) // 2]
            for item in to_remove:
                self._seen_ids.discard(item)

    async def check_once(self) -> List[Dict[str, Any]]:
        return await self.poll_all()
