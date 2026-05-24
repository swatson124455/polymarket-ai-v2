"""
Regulatory monitor — RSS feeds for CFTC, state AGs, and court dockets.

Monitors regulatory bodies for prediction-market-related actions and
emits alerts via EventBus. Keywords: "prediction market", "event contract",
"binary option", "Polymarket", "Kalshi", "ForecastEx".
"""
from __future__ import annotations
import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from structlog import get_logger

logger = get_logger()

# RSS feeds to monitor
REGULATORY_FEEDS = [
    {"name": "CFTC Press", "url": "https://www.cftc.gov/PressRoom/PressReleases/rss.xml"},
    {"name": "CFTC Actions", "url": "https://www.cftc.gov/LawRegulation/CFTCActions/rss.xml"},
    {"name": "SEC Litigation", "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=LIT&dateb=&owner=include&count=40&search_text=&action=getcompany&output=atom"},
]

# Keywords that indicate prediction-market-relevant regulatory action
ALERT_KEYWORDS = [
    "prediction market", "event contract", "binary option",
    "polymarket", "kalshi", "forecastex", "nadex",
    "swaps execution facility", "designated contract market",
    "retail commodity", "binary event",
]


class RegulatoryMonitor:
    """
    Monitor regulatory feeds for prediction market related actions.

    Emits events to EventBus when relevant articles are found.
    """

    def __init__(self, event_bus=None, custom_feeds: Optional[List[Dict]] = None):
        self._event_bus = event_bus
        self._feeds = custom_feeds or REGULATORY_FEEDS
        self._seen_ids: Set[str] = set()
        self._running = False

    async def start(self, poll_interval_seconds: int = 3600):
        """Start monitoring loop. Default: check every hour."""
        self._running = True
        logger.info("RegulatoryMonitor started with %d feeds", len(self._feeds))

        while self._running:
            try:
                alerts = await self._poll_feeds()
                for alert in alerts:
                    logger.warning(
                        "Regulatory alert: %s — %s",
                        alert.get("source"), alert.get("title"),
                    )
                    if self._event_bus:
                        await self._event_bus.emit("regulatory_alert", alert)
            except Exception as e:
                logger.debug("RegulatoryMonitor poll failed: %s", e)

            await asyncio.sleep(poll_interval_seconds)

    def stop(self):
        self._running = False

    async def _poll_feeds(self) -> List[Dict]:
        """Poll all feeds and return matching alerts."""
        alerts = []
        try:
            import feedparser
        except ImportError:
            logger.debug("feedparser not installed — RegulatoryMonitor disabled")
            return []

        for feed_config in self._feeds:
            try:
                feed = await asyncio.to_thread(feedparser.parse, feed_config["url"])
                for entry in feed.get("entries", []):
                    entry_id = entry.get("id") or entry.get("link") or ""
                    if entry_id in self._seen_ids:
                        continue
                    self._seen_ids.add(entry_id)

                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    text = f"{title} {summary}".lower()

                    matches = [kw for kw in ALERT_KEYWORDS if kw in text]
                    if matches:
                        alerts.append({
                            "source": feed_config["name"],
                            "title": title,
                            "summary": summary[:500],
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "keywords_matched": matches,
                        })
            except Exception as e:
                logger.debug("Feed parse failed for %s: %s", feed_config.get("name"), e)

        return alerts

    async def check_once(self) -> List[Dict]:
        """One-shot check (for manual invocation or testing)."""
        return await self._poll_feeds()
