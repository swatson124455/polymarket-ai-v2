"""
Reddit Monitor — polls r/nba, r/nfl, r/baseball, r/hockey, r/soccer for injury intel.

Uses PRAW (Python Reddit API Wrapper) if REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET
are configured. Falls back to the public Reddit JSON API (httpx) if PRAW is not
configured (slower, unauthenticated, rate-limited to ~60 req/min).

Poll interval: SPORTS_REDDIT_POLL_INTERVAL (default 120s).

Puts raw items onto an asyncio.Queue using put_nowait().
Deduplicates by post/comment ID hash.

Gated: if neither REDDIT_CLIENT_ID nor httpx fallback works, silently exits.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import Dict, List
from structlog import get_logger

logger = get_logger()

_SUBREDDITS: Dict[str, str] = {
    "nba": "nba",
    "nfl": "nfl",
    "mlb": "baseball",
    "nhl": "hockey",
    "soccer": "soccer",
}

# Injury keywords to filter posts/comments
_INJURY_KEYWORDS = [
    "injured", "injury", "out", "doubtful", "questionable", "day-to-day",
    "dtd", "dnp", "scratched", "il", "ir", "surgery", "sidelined",
    "ruled out", "will not play", "torn", "fractured", "sprained",
    "released", "signs with", "traded", "free agent",
    "withdrawal", "retired", "scratched",
]

_DEDUP_MAX_SIZE = 5_000


class RedditInjuryMonitor:
    """
    Reddit injury monitor with PRAW + httpx fallback.

    Usage::
        monitor = RedditInjuryMonitor(output_queue)
        asyncio.create_task(monitor.run_forever())
    """

    def __init__(self, output_queue: asyncio.Queue) -> None:
        self._queue = output_queue
        self._running = False
        self._seen: OrderedDict = OrderedDict()

    async def run_forever(self) -> None:
        """Poll Reddit on the configured interval indefinitely."""
        from config.settings import settings

        client_id = getattr(settings, "REDDIT_CLIENT_ID", None)
        client_secret = getattr(settings, "REDDIT_CLIENT_SECRET", None)
        poll_interval = int(getattr(settings, "SPORTS_REDDIT_POLL_INTERVAL", 120))

        if not client_id:
            logger.info(
                "RedditInjuryMonitor: no REDDIT_CLIENT_ID — using public JSON API fallback"
            )

        self._running = True
        logger.info("RedditInjuryMonitor: starting", poll_interval_s=poll_interval)

        while self._running:
            try:
                await asyncio.wait_for(
                    self._poll_all(client_id, client_secret), timeout=90.0
                )
            except asyncio.TimeoutError:
                logger.warning("RedditInjuryMonitor: poll cycle timed out")
            except asyncio.CancelledError:
                logger.info("RedditInjuryMonitor: cancelled")
                break
            except Exception as exc:
                logger.warning("RedditInjuryMonitor: poll error", error=str(exc))
            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _poll_all(
        self, client_id: str | None, client_secret: str | None
    ) -> None:
        """Poll all subreddits."""
        tasks = []
        for sport, subreddit in _SUBREDDITS.items():
            if client_id and client_secret:
                tasks.append(asyncio.create_task(
                    self._poll_praw(subreddit, sport, client_id, client_secret)
                ))
            else:
                tasks.append(asyncio.create_task(
                    self._poll_json_api(subreddit, sport)
                ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.debug("RedditInjuryMonitor: subreddit error", error=str(r))

    async def _poll_praw(
        self,
        subreddit: str,
        sport: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Poll using PRAW (authenticated)."""
        try:
            import praw  # type: ignore
        except ImportError:
            logger.debug("RedditInjuryMonitor: praw not installed — using JSON API")
            await self._poll_json_api(subreddit, sport)
            return

        try:
            def _fetch_posts():
                reddit = praw.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    user_agent="polymarket-sports-monitor/1.0",
                )
                sub = reddit.subreddit(subreddit)
                return [(p.id, p.title, p.selftext[:500], p.permalink) for p in sub.new(limit=25)]

            posts_data = await asyncio.to_thread(_fetch_posts)
            items_queued = 0
            for post_id, post_title, post_selftext, post_permalink in posts_data:
                text = f"{post_title}. {post_selftext}".strip()
                if not self._has_injury_keyword(text):
                    continue
                key = self._dedup_key(post_id)
                if key in self._seen:
                    continue
                self._seen[key] = None
                self._trim_seen()
                item = {
                    "source": "reddit",
                    "source_id": post_id,
                    "sport": sport,
                    "text": text,
                    "url": f"https://reddit.com{post_permalink}",
                }
                try:
                    self._queue.put_nowait(item)
                    items_queued += 1
                except asyncio.QueueFull:
                    pass
            if items_queued:
                logger.info(
                    "RedditInjuryMonitor: PRAW polled",
                    subreddit=subreddit,
                    new_items=items_queued,
                )
        except Exception as exc:
            raise RuntimeError(f"PRAW poll r/{subreddit}: {exc}") from exc

    async def _poll_json_api(self, subreddit: str, sport: str) -> None:
        """Poll using public Reddit JSON API (no auth)."""
        try:
            import httpx
            url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=25"
            headers = {"User-Agent": "polymarket-sports-monitor/1.0"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return
                data = resp.json()

            posts = data.get("data", {}).get("children", [])
            items_queued = 0
            for post_wrapper in posts:
                post = post_wrapper.get("data", {})
                post_id = post.get("id", "")
                title = post.get("title", "")
                selftext = post.get("selftext", "")[:300]
                permalink = post.get("permalink", "")
                text = f"{title}. {selftext}".strip()
                if not self._has_injury_keyword(text):
                    continue
                key = self._dedup_key(post_id)
                if key in self._seen:
                    continue
                self._seen[key] = None
                self._trim_seen()
                item = {
                    "source": "reddit",
                    "source_id": post_id,
                    "sport": sport,
                    "text": text,
                    "url": f"https://reddit.com{permalink}",
                }
                try:
                    self._queue.put_nowait(item)
                    items_queued += 1
                except asyncio.QueueFull:
                    pass
            if items_queued:
                logger.info(
                    "RedditInjuryMonitor: JSON API polled",
                    subreddit=subreddit,
                    new_items=items_queued,
                )
        except Exception as exc:
            raise RuntimeError(f"JSON API poll r/{subreddit}: {exc}") from exc

    @staticmethod
    def _has_injury_keyword(text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in _INJURY_KEYWORDS)

    @staticmethod
    def _dedup_key(post_id: str) -> str:
        return hashlib.md5(post_id.encode()).hexdigest()

    def _trim_seen(self) -> None:
        while len(self._seen) > _DEDUP_MAX_SIZE:
            self._seen.popitem(last=False)  # FIFO eviction
