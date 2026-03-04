"""
Social Media Sources - Integrate with Twitter/X, Reddit, Discord, Telegram.

Note: Requires API keys for full functionality.
"""
import asyncio
import httpx
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class TwitterClient:
    """Client for Twitter/X API integration."""
    
    def __init__(self, bearer_token: Optional[str] = None):
        self.bearer_token = bearer_token or getattr(settings, 'TWITTER_BEARER_TOKEN', None)
        self.base_url = "https://api.twitter.com/2"
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
        )
        if not self.bearer_token:
            logger.info("TwitterClient: no TWITTER_BEARER_TOKEN configured — Twitter signals disabled")
    
    async def search_tweets(
        self,
        query: str,
        max_results: int = 100,
        since_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search for tweets.
        
        Args:
            query: Search query
            max_results: Maximum results (max 100)
            since_id: Only return tweets after this ID
        
        Returns:
            List of tweets
        """
        if not self.bearer_token:
            logger.debug("Twitter bearer token not configured - Twitter fetching disabled")
            return []
        
        try:
            url = f"{self.base_url}/tweets/search/recent"
            params = {
                "query": query,
                "max_results": min(max_results, 100),
                "tweet.fields": "created_at,author_id,public_metrics,text"
            }
            
            if since_id:
                params["since_id"] = since_id
            
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            tweets = data.get("data", [])
            
            # Normalize tweet format
            normalized = []
            for tweet in tweets:
                normalized.append({
                    "id": tweet.get("id"),
                    "text": tweet.get("text", ""),
                    "author_id": tweet.get("author_id"),
                    "created_at": tweet.get("created_at"),
                    "metrics": tweet.get("public_metrics", {}),
                    "source": "twitter",
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.debug(f"Fetched {len(normalized)} tweets")
            return normalized
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("Twitter API rate limit exceeded")
            elif e.response.status_code == 401:
                logger.warning("Twitter API authentication failed - check bearer token")
            else:
                logger.error(f"Twitter API HTTP error: {str(e)}", exc_info=True)
            return []
        except Exception as e:
            logger.error(f"Twitter fetch error: {str(e)}", exc_info=True)
            return []
    
    async def stream_tweets(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        Stream tweets matching keywords (requires Twitter API v2 streaming).
        
        Args:
            keywords: Keywords to match
        
        Returns:
            List of tweets
        """
        # Placeholder - would implement streaming API
        # For now, use search
        query = " OR ".join(keywords)
        return await self.search_tweets(query, max_results=100)
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class RedditClient:
    """Client for Reddit API integration."""
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or getattr(settings, 'REDDIT_CLIENT_ID', None)
        self.client_secret = client_secret or getattr(settings, 'REDDIT_CLIENT_SECRET', None)
        self.access_token = None
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def _authenticate(self):
        """Authenticate with Reddit API."""
        if not self.client_id or not self.client_secret:
            return False
        
        try:
            response = await self.client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": "PolymarketBot/1.0"}
            )
            response.raise_for_status()
            data = response.json()
            self.access_token = data.get("access_token")
            return self.access_token is not None
        except Exception as e:
            logger.debug("Reddit authentication failed: %s", e)
            return False
    
    async def search_posts(
        self,
        subreddit: str,
        query: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search Reddit posts.
        
        Args:
            subreddit: Subreddit name
            query: Search query
            limit: Maximum results
        
        Returns:
            List of posts
        """
        if not self.access_token:
            if not await self._authenticate():
                logger.debug("Reddit authentication failed - Reddit fetching disabled")
                return []
        
        try:
            url = f"https://oauth.reddit.com/r/{subreddit}/search"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "PolymarketBot/1.0"
            }
            params = {
                "q": query,
                "limit": min(limit, 100),
                "sort": "new"
            }
            
            response = await self.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            posts = data.get("data", {}).get("children", [])
            
            # Normalize post format
            normalized = []
            for post in posts:
                post_data = post.get("data", {})
                normalized.append({
                    "id": post_data.get("id"),
                    "title": post_data.get("title", ""),
                    "text": f"{post_data.get('title', '')} {post_data.get('selftext', '')}",
                    "subreddit": subreddit,
                    "author": post_data.get("author"),
                    "score": post_data.get("score", 0),
                    "created_utc": post_data.get("created_utc"),
                    "url": post_data.get("url", ""),
                    "source": "reddit",
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.debug(f"Fetched {len(normalized)} Reddit posts")
            return normalized
            
        except Exception as e:
            logger.error(f"Reddit fetch error: {str(e)}", exc_info=True)
            return []
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


class PRAWStreamClient:
    """
    Reddit PRAW streaming client — persistent connection, near-zero rate limit.

    Opt-in via USE_REDDIT_STREAMING=true + REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD.
    Falls back gracefully if praw not installed or credentials missing.
    """

    def __init__(self):
        self._reddit = None
        self._available = False
        self._running = False
        self._init_praw()

    def _init_praw(self):
        try:
            import praw
            client_id = getattr(settings, "REDDIT_CLIENT_ID", None)
            client_secret = getattr(settings, "REDDIT_CLIENT_SECRET", None)
            username = getattr(settings, "REDDIT_USERNAME", None)
            password = getattr(settings, "REDDIT_PASSWORD", None)
            if not all([client_id, client_secret, username, password]):
                logger.debug("PRAW credentials incomplete — streaming disabled")
                return
            self._reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                username=username,
                password=password,
                user_agent="PolymarketBot/2.0 (PRAW streaming)",
            )
            self._available = True
            logger.info("PRAW streaming client initialized")
        except ImportError:
            logger.debug("praw not installed — Reddit streaming disabled")
        except Exception as e:
            logger.debug("PRAW init failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    async def start_stream(self, subreddits: List[str], callback) -> None:
        """
        Stream comments from subreddits via asyncio.to_thread.

        Args:
            subreddits: List of subreddit names (no r/ prefix)
            callback: async callable(item_dict) invoked per comment
        """
        if not self._available or not self._reddit:
            return

        self._running = True
        sub_str = "+".join(subreddits)

        def _stream_sync():
            import praw
            subreddit = self._reddit.subreddit(sub_str)
            for comment in subreddit.stream.comments(skip_existing=True):
                if not self._running:
                    break
                yield {
                    "id": comment.id,
                    "text": comment.body[:1000],
                    "author": str(comment.author) if comment.author else "[deleted]",
                    "subreddit": str(comment.subreddit),
                    "score": comment.score,
                    "created_utc": comment.created_utc,
                    "source": "reddit_stream",
                    "timestamp": datetime.now(timezone.utc),
                }

        try:
            # Run blocking stream in thread, yield items back via queue
            import queue
            q: queue.Queue = queue.Queue(maxsize=500)

            def _producer():
                try:
                    for item in _stream_sync():
                        q.put(item)
                except Exception as e:
                    logger.debug("PRAW stream producer error: %s", e)
                finally:
                    q.put(None)  # sentinel

            import threading
            thread = threading.Thread(target=_producer, daemon=True)
            thread.start()

            while self._running:
                try:
                    item = await asyncio.to_thread(q.get, timeout=5.0)
                    if item is None:
                        break
                    await callback(item)
                except Exception:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.debug("PRAW stream error: %s", e)

    def stop(self):
        """Stop the streaming loop."""
        self._running = False


class BlueSkyClient:
    """
    Bluesky (AT Protocol) public search client — free Twitter alternative.

    Bluesky's AppView API allows searching posts without authentication.
    Endpoint: https://api.bsky.app/xrpc/app.bsky.feed.searchPosts
    No API key required. Rate limit: ~100 req/min unauthenticated.
    Note: public.api.bsky.app returns 403; use api.bsky.app (verified 2026-02-21).
    """

    SEARCH_URL = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)

    async def search_posts(
        self,
        query: str,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Search Bluesky posts for a query string.

        Args:
            query: Search terms (supports boolean: "polymarket OR predictit")
            limit: Max posts to return (max 100)

        Returns:
            List of normalized post dicts compatible with Twitter/social post format.
        """
        try:
            params = {"q": query, "limit": min(limit, 100), "sort": "latest"}
            r = await self.client.get(self.SEARCH_URL, params=params)
            if r.status_code != 200:
                logger.debug("Bluesky search HTTP %d for query '%s'", r.status_code, query[:50])
                return []
            data = r.json()
            posts = data.get("posts", [])
            normalized = []
            for p in posts:
                record = p.get("record", {})
                author = p.get("author", {})
                normalized.append({
                    "id": p.get("uri", ""),
                    "text": record.get("text", ""),
                    "author": author.get("handle", ""),
                    "created_at": record.get("createdAt"),
                    "metrics": {
                        "like_count": p.get("likeCount", 0),
                        "reply_count": p.get("replyCount", 0),
                        "repost_count": p.get("repostCount", 0),
                    },
                    "source": "bluesky",
                    "timestamp": datetime.now(timezone.utc),
                })
            logger.debug("Bluesky: fetched %d posts for '%s'", len(normalized), query[:50])
            return normalized
        except Exception as e:
            logger.debug("Bluesky fetch error: %s", e)
            return []

    async def close(self):
        await self.client.aclose()


class SocialAggregator:
    """
    Aggregate social media posts from multiple sources.
    """

    def __init__(self):
        self.twitter = TwitterClient()
        self.reddit = RedditClient()
        self.bluesky = BlueSkyClient()

    async def fetch_all_social(
        self,
        keywords: List[str],
        max_posts: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Fetch social media posts from all configured sources.

        Args:
            keywords: Keywords to search for
            max_posts: Maximum posts to return

        Returns:
            Aggregated list of social posts
        """
        all_posts = []

        # Fetch from Twitter (requires TWITTER_BEARER_TOKEN — Basic plan $200/mo)
        try:
            query = " OR ".join(keywords[:5])  # Limit keywords
            tweets = await self.twitter.search_tweets(query, max_results=100)
            all_posts.extend(tweets)
        except Exception as e:
            logger.error(f"Twitter fetch failed: {str(e)}", exc_info=True)

        # Fetch from Bluesky (free, no API key needed — public AppView API)
        try:
            query = " OR ".join(keywords[:5])
            bsky_posts = await self.bluesky.search_posts(query, limit=50)
            all_posts.extend(bsky_posts)
        except Exception as e:
            logger.debug("Bluesky fetch failed: %s", e)

        # Fetch from Reddit (r/polymarket, r/wallstreetbets)
        # Note: r/predictit blocks all non-browser clients with 403 regardless of User-Agent
        try:
            for subreddit in ["polymarket", "wallstreetbets"]:
                for keyword in keywords[:3]:  # Limit keywords per subreddit
                    posts = await self.reddit.search_posts(subreddit, keyword, limit=25)
                    all_posts.extend(posts)
        except Exception as e:
            logger.error(f"Reddit fetch failed: {str(e)}", exc_info=True)

        # Sort by timestamp (newest first)
        all_posts.sort(
            key=lambda x: x.get("timestamp", datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True
        )

        return all_posts[:max_posts]

    async def close(self):
        """Close all clients."""
        await self.twitter.close()
        await self.reddit.close()
        await self.bluesky.close()
