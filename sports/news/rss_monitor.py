"""
RSS/JSON Feed Monitor — sports injury pipeline.

Polls free sources (Rotowire RSS + ESPN unofficial injury API) every
SPORTS_RSS_POLL_INTERVAL seconds (default 60 s).

feedparser is already in requirements.txt (line 150).

Puts raw items onto an asyncio.Queue using put_nowait() (non-blocking).
Deduplicates by GUID/URL hash — same item never goes on the queue twice.

No API key required.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Dict, List, Optional
from structlog import get_logger

logger = get_logger()

# ─── Feed definitions ─────────────────────────────────────────────────────────

_ROTOWIRE_FEEDS: List[Dict] = [
    {
        "url": "https://www.rotowire.com/basketball/rss-injuries.php",
        "sport": "nba",
        "source": "rotowire_rss",
    },
    {
        "url": "https://www.rotowire.com/football/rss-injuries.php",
        "sport": "nfl",
        "source": "rotowire_rss",
    },
    {
        "url": "https://www.rotowire.com/baseball/rss-injuries.php",
        "sport": "mlb",
        "source": "rotowire_rss",
    },
    {
        "url": "https://www.rotowire.com/hockey/rss-injuries.php",
        "sport": "nhl",
        "source": "rotowire_rss",
    },
]

# ESPN unofficial injury endpoints (no auth required)
_ESPN_ENDPOINTS: List[Dict] = [
    {
        "url": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
        "sport": "nba",
        "source": "espn_api",
    },
    {
        "url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
        "sport": "nfl",
        "source": "espn_api",
    },
    {
        "url": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries",
        "sport": "mlb",
        "source": "espn_api",
    },
    {
        "url": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries",
        "sport": "nhl",
        "source": "espn_api",
    },
    {
        "url": "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/injuries",
        "sport": "soccer",
        "source": "espn_api",
    },
]

_DEDUP_MAX_SIZE = 5_000  # I35: max hashes to keep (using OrderedDict for true FIFO eviction)


class RSSInjuryMonitor:
    """
    Polls Rotowire RSS feeds and ESPN JSON endpoints for injury updates.

    Usage::
        monitor = RSSInjuryMonitor(output_queue)
        asyncio.create_task(monitor.run_forever())

    Output queue items (dict)::
        {
          "source":    "rotowire_rss" | "espn_api",
          "source_id": "<guid hash>",
          "sport":     "nba" | "nfl" | ...,
          "text":      "<combined title + summary>",
          "url":       "<item link>",
        }
    """

    def __init__(self, output_queue: asyncio.Queue) -> None:
        self._queue = output_queue
        self._running = False
        # I35: OrderedDict for true FIFO eviction — set had no order guarantee
        self._seen: OrderedDict = OrderedDict()  # dedup: key → None, evict oldest first

    async def run_forever(self) -> None:
        """Poll all feeds on the configured interval indefinitely."""
        from config.settings import settings

        poll_interval = int(getattr(settings, "SPORTS_RSS_POLL_INTERVAL", 60))
        self._running = True
        logger.info("RSSInjuryMonitor: starting", poll_interval_s=poll_interval)

        while self._running:
            try:
                await asyncio.wait_for(self._poll_all(), timeout=50.0)
            except asyncio.TimeoutError:
                logger.warning("RSSInjuryMonitor: poll cycle timed out (>50s)")
            except asyncio.CancelledError:
                logger.info("RSSInjuryMonitor: cancelled")
                break
            except Exception as exc:
                logger.warning("RSSInjuryMonitor: poll error", error=str(exc))
            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False

    # ─── Internals ────────────────────────────────────────────────────────────

    async def _poll_all(self) -> None:
        """Poll all configured feeds concurrently."""
        tasks = []
        for feed in _ROTOWIRE_FEEDS:
            tasks.append(asyncio.create_task(self._poll_rss(feed)))
        for endpoint in _ESPN_ENDPOINTS:
            tasks.append(asyncio.create_task(self._poll_espn(endpoint)))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.debug("RSSInjuryMonitor: feed error", error=str(r))

    async def _poll_rss(self, feed: Dict) -> None:
        """Parse a Rotowire RSS feed via feedparser."""
        try:
            import feedparser
        except ImportError:
            logger.warning("RSSInjuryMonitor: feedparser not installed")
            return

        try:
            import httpx
            # I34: Explicit per-phase timeouts — 15.0 single value never expires on slow reads
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=3.0)
            ) as client:
                resp = await client.get(feed["url"])
                resp.raise_for_status()
                raw_content = resp.content

            parsed = feedparser.parse(raw_content)
            items_queued = 0
            for entry in parsed.get("entries", []):
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", "")
                guid    = entry.get("id", link)

                text = f"{title}. {summary}".strip(". ").strip()
                if not text:
                    continue

                key = self._dedup_key(guid or text)
                if key in self._seen:
                    continue
                self._seen[key] = None   # I35: OrderedDict insert (maintains insertion order)
                self._trim_seen()

                item = {
                    "source":    feed["source"],
                    "source_id": key,
                    "sport":     feed["sport"],
                    "text":      text,
                    "url":       link,
                }
                try:
                    self._queue.put_nowait(item)
                    items_queued += 1
                except asyncio.QueueFull:
                    logger.debug("RSSInjuryMonitor: queue full", source=feed["source"])

            if items_queued:
                logger.info(
                    "RSSInjuryMonitor: RSS feed polled",
                    sport=feed["sport"],
                    new_items=items_queued,
                )
        except Exception as exc:
            raise RuntimeError(f"RSS poll {feed['url']}: {exc}") from exc

    async def _poll_espn(self, endpoint: Dict) -> None:
        """Poll ESPN unofficial JSON injury endpoint."""
        try:
            import httpx
            # I34: Explicit per-phase timeouts
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=3.0)
            ) as client:
                resp = await client.get(
                    endpoint["url"],
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    return
                data = resp.json()

            items_queued = 0
            # ESPN structure: {"injuries": [{"athlete": {...}, "status": "...", "comment": "..."}, ...]}
            # or {"items": [...]}
            # I33: Log WARNING when both keys are missing — indicates schema change
            if "injuries" not in data and "items" not in data:
                logger.warning(
                    "RSSInjuryMonitor: ESPN response missing 'injuries' and 'items' keys",
                    sport=endpoint["sport"],
                    url=endpoint["url"],
                    keys=list(data.keys())[:10] if isinstance(data, dict) else type(data).__name__,
                )
            injuries = data.get("injuries", data.get("items", []))
            for injury in injuries:
                athlete = injury.get("athlete", {})
                player_name = athlete.get("displayName", athlete.get("fullName", ""))
                status  = injury.get("status", "")
                comment = injury.get("comment", injury.get("description", ""))
                athlete_id = str(athlete.get("id", ""))

                text = f"{player_name} {status}. {comment}".strip()
                if not text or not player_name:
                    continue

                key = self._dedup_key(f"{endpoint['sport']}_{athlete_id}_{status}_{comment[:50]}")
                if key in self._seen:
                    continue
                self._seen[key] = None   # I35: OrderedDict insert
                self._trim_seen()

                item = {
                    "source":    endpoint["source"],
                    "source_id": key,
                    "sport":     endpoint["sport"],
                    "text":      text,
                    "url":       endpoint["url"],
                }
                try:
                    self._queue.put_nowait(item)
                    items_queued += 1
                except asyncio.QueueFull:
                    logger.debug("RSSInjuryMonitor: queue full", source=endpoint["source"])

            if items_queued:
                logger.info(
                    "RSSInjuryMonitor: ESPN feed polled",
                    sport=endpoint["sport"],
                    new_items=items_queued,
                )
        except Exception as exc:
            raise RuntimeError(f"ESPN poll {endpoint['url']}: {exc}") from exc

    @staticmethod
    def _dedup_key(text: str) -> str:
        return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()

    def _trim_seen(self) -> None:
        """I35: Keep the dedup OrderedDict bounded — evicts oldest-inserted entries (true FIFO)."""
        while len(self._seen) > _DEDUP_MAX_SIZE:
            self._seen.popitem(last=False)  # FIFO: remove first-inserted item
