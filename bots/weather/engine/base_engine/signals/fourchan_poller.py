"""
4chan Poller — Signal 5: Social Velocity (Contrarian Signal)

Polls /biz/ and /pol/ boards for prediction market mentions.
Rate limited to 1 request/second per 4chan API rules.

API docs: https://github.com/4chan/4chan-API
No authentication required.
"""
import asyncio
import time
import re
import httpx
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class FourChanPoller:
    """
    4chan catalog poller for contrarian sentiment signals.

    Polls board catalogs for keyword mentions. 4chan threads
    often provide early contrarian signals on crypto/political topics.
    """

    BASE_URL = "https://a.4cdn.org"

    def __init__(self, boards: Optional[List[str]] = None):
        self._boards = boards or ["biz", "pol"]
        self._client = httpx.AsyncClient(timeout=10.0)
        self._last_request_time = 0.0
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = 60  # 1 min
        # L4 FIX: Track seen thread_nos so the same thread isn't counted across poll cycles.
        # 4chan catalogs persist threads for days — without dedup, every 60s poll re-counts them.
        self._seen_threads: Dict[str, float] = {}  # "board:thread_no" -> first_seen_monotonic
        self._seen_thread_ttl = 3600.0  # 1 hour: forget threads after they've aged out

    async def _rate_limit(self):
        """Enforce 1 request per 1.5 seconds to respect 4chan rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 1.5:
            await asyncio.sleep(1.5 - elapsed)
        self._last_request_time = time.time()

    async def poll_board(self, board: str) -> List[Dict[str, Any]]:
        """
        Fetch the catalog for a board.

        Args:
            board: Board name without slashes (e.g., "biz", "pol")

        Returns:
            List of threads: {text, board, thread_no, replies, timestamp, subject}
        """
        cache_key = f"4chan:{board}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached["ts"] < self._cache_ttl:
            return cached["data"]

        await self._rate_limit()

        try:
            url = f"{self.BASE_URL}/{board}/catalog.json"
            resp = await self._client.get(url)
            resp.raise_for_status()
            pages = resp.json()

            threads = []
            for page in pages:
                for thread in page.get("threads", []):
                    subject = thread.get("sub", "")
                    comment = thread.get("com", "")
                    # Strip HTML tags from comment
                    clean_comment = re.sub(r"<[^>]+>", " ", comment)[:500]

                    threads.append({
                        "text": f"{subject} {clean_comment}".strip(),
                        "subject": subject,
                        "board": board,
                        "thread_no": thread.get("no", 0),
                        "replies": thread.get("replies", 0),
                        "images": thread.get("images", 0),
                        "timestamp": thread.get("time", 0),
                        "source": "fourchan",
                    })

            self._cache[cache_key] = {"data": threads, "ts": time.time()}
            return threads

        except Exception as e:
            logger.debug("4chan poll error (/%s/): %s", board, e)
            return []

    async def search_keyword(
        self,
        keyword: str,
        board: str = "biz",
    ) -> List[Dict[str, Any]]:
        """
        Search for keyword mentions in a board's catalog.

        Args:
            keyword: Search term (case-insensitive)
            board: Board to search

        Returns:
            Matching threads sorted by replies (engagement) desc
        """
        threads = await self.poll_board(board)
        keyword_lower = keyword.lower()

        matches = [
            t for t in threads
            if keyword_lower in t.get("text", "").lower()
        ]

        matches.sort(key=lambda x: x.get("replies", 0), reverse=True)
        return matches

    async def get_market_mentions(
        self,
        keywords: List[str],
    ) -> Dict[str, Any]:
        """
        Count mentions across all configured boards.

        Args:
            keywords: List of keywords to search

        Returns:
            {total_mentions, per_board, top_threads, source}
        """
        # L4 FIX: Prune stale seen-thread entries before counting
        _now = time.monotonic()
        self._seen_threads = {k: v for k, v in self._seen_threads.items()
                              if _now - v < self._seen_thread_ttl}

        total_mentions = 0
        per_board = {}
        top_threads = []

        for board in self._boards:
            board_mentions = 0
            _board_seen: set = set()  # dedup within this call across keywords
            for keyword in keywords:
                matches = await self.search_keyword(keyword, board)
                for m in matches:
                    _tno = m.get("thread_no")
                    _key = f"{board}:{_tno}"
                    if _tno and _key not in _board_seen and _key not in self._seen_threads:
                        _board_seen.add(_key)
                        self._seen_threads[_key] = _now
                        board_mentions += 1
                        top_threads.append(m)

            per_board[board] = board_mentions
            total_mentions += board_mentions

        # Sort top threads by engagement
        top_threads.sort(key=lambda x: x.get("replies", 0), reverse=True)

        return {
            "total_mentions": total_mentions,
            "per_board": per_board,
            "top_threads": top_threads[:10],
            "source": "fourchan",
        }

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()
