"""
Signal Ingestion Service - Aggregates signals from multiple external sources.

Sources:
- News (NewsAPI, RSS feeds, GDELT)
- Social media (Twitter/X, Reddit, Discord, Telegram, 4chan)
- Whale tracking (large trades)
- Event calendar (scheduled events)
- Cross-platform arbitrage
- Wikipedia pageviews
- Hacker News
- Spike detection (multi-source z-score)
- Velocity engine (message rate-of-change)
- Sentiment velocity (sentiment shift detection)
"""
import asyncio
import hashlib
import json
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from enum import Enum
from structlog import get_logger
from base_engine.data.database import Database, Signal
from base_engine.data.redis_cache import RedisCache
from base_engine.data.polymarket_client import PolymarketClient
from base_engine.signals.news_sources import NewsAggregator
from base_engine.signals.social_sources import SocialAggregator
from base_engine.signals.event_calendar import EventCalendar
from base_engine.signals.llm_signal_extractor import LLMSignalExtractor
from base_engine.signals.wikipedia_pageviews import WikipediaPageviews
from base_engine.signals.entity_extractor import EntityExtractor
from base_engine.signals.gdelt_client import GDELTClient
from base_engine.signals.hackernews import HackerNewsClient
from base_engine.signals.spike_detector import SpikeDetector
from base_engine.signals.velocity_engine import VelocityEngine
from base_engine.signals.sentiment_velocity import SentimentVelocityTracker
from base_engine.signals.fourchan_poller import FourChanPoller
from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
from config.settings import settings

logger = get_logger()


# Fallback keywords when no markets (Polymarket-relevant: politics, crypto, macro)
DEFAULT_NEWS_KEYWORDS = [
    "Trump", "Bitcoin", "Fed", "election", "crypto",
    "inflation", "SEC", "Congress", "Supreme Court", "rate cut"
]

# Lower threshold to capture more signals for learning (even weak ones)
SIGNAL_CONFIDENCE_MIN = 0.3


class SignalSource(Enum):
    """Signal source types."""
    NEWS = "news"
    SOCIAL = "social"
    WHALE = "whale"
    CALENDAR = "calendar"
    CROSS_PLATFORM = "cross_platform"


class SignalIngestionService:
    """
    Aggregates signals from multiple external sources.
    Feeds all bots with real-time alpha.
    """
    
    def __init__(
        self,
        db: Database,
        cache: RedisCache,
        client: PolymarketClient
    ):
        self.db = db
        self.cache = cache
        self.client = client
        self.running = False
        self.signal_queue = "signals:priority"

        # Source aggregators (existing)
        self.news_aggregator = NewsAggregator()
        self.social_aggregator = SocialAggregator()
        self.event_calendar = EventCalendar(db=db)
        self.llm_extractor = LLMSignalExtractor()
        self.wikipedia = WikipediaPageviews()
        self.entity_extractor = EntityExtractor()

        # New signal sources (Phase 1-3)
        self.gdelt_client = GDELTClient()
        self.hn_client = HackerNewsClient()
        self.spike_detector = SpikeDetector(cache=cache)
        self.velocity_engine = VelocityEngine(cache=cache)
        self.sentiment_velocity = SentimentVelocityTracker(cache=cache)
        self.fourchan_poller = FourChanPoller()
        self.sentiment_analyzer = SentimentAnalyzer()

        # Elite Model Deep Dive: new signal sources (wired by base_engine.py)
        self.legislative_tracker: Optional[Any] = None
        self.polling_client: Optional[Any] = None
        self.court_monitor: Optional[Any] = None
        self.intl_elections: Optional[Any] = None

        # Streaming clients (opt-in, lazy init)
        self._praw_client = None
        self._telegram_client = None
        self._discord_client = None

        self.collection_tasks = []
        self._dedup_window = getattr(settings, "SIGNAL_DEDUP_WINDOW_SECONDS", 1800)
        # Limit concurrent DB writes across all 9 signal loops.
        # Without this, overlapping 60s-cycle loops (news, velocity, 4chan) can spike
        # to 8+ concurrent sessions, exhausting the DB connection pool.
        self._signal_db_sem = asyncio.Semaphore(2)

        # In-memory market cache: shared across all 9 ingestion loops.
        # When Redis is disconnected _get_active_markets() would otherwise call the
        # Polymarket API 9-10 times per minute (once per loop per cycle).
        # This 30s TTL in-memory cache deduplicates those calls to ≤1 per 30s.
        self._local_markets: List[Dict[str, Any]] = []
        self._local_markets_at: float = 0.0
        self._local_markets_ttl: float = 30.0
        self._local_markets_lock = asyncio.Lock()

        # B9: Per-source bounded asyncio.Queue ring buffers (Redpanda alternative).
        # Each ingestion source gets its own queue(maxsize=10000). put_nowait() drops
        # LOW-priority items when full rather than blocking the collection loop.
        # A consumer background task batches and persists to DB at controlled rate.
        self._signal_queues: Dict[str, asyncio.Queue] = {
            "price": asyncio.Queue(maxsize=10000),
            "trade": asyncio.Queue(maxsize=10000),
            "volume": asyncio.Queue(maxsize=5000),
            "sentiment": asyncio.Queue(maxsize=2000),
            "whale": asyncio.Queue(maxsize=1000),
            "orderbook": asyncio.Queue(maxsize=5000),
            "social": asyncio.Queue(maxsize=2000),
            "news": asyncio.Queue(maxsize=1000),
            "onchain": asyncio.Queue(maxsize=500),
        }
        self._queue_drop_counts: Dict[str, int] = {k: 0 for k in self._signal_queues}
    
    def _on_collection_task_done(self, task: asyncio.Task, name: str = "unknown") -> None:
        """Callback for signal collection task — logs and auto-restarts."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical("Signal collection '%s' crashed: %s", name, exc)
            if self.running:
                logger.warning("Auto-restarting signal collection: %s", name)
                _method = getattr(self, name, None)
                if _method and callable(_method):
                    new_task = asyncio.create_task(_method())
                    new_task.add_done_callback(lambda t, n=name: self._on_collection_task_done(t, n))

    async def start(self):
        """Start all signal collection tasks."""
        if self.running:
            return

        self.running = True
        logger.info("Starting signal ingestion service")

        # Core collection loops
        _core_loops = [
            ("_news_collection_loop", self._news_collection_loop),
            ("_social_collection_loop", self._social_collection_loop),
            ("_event_calendar_loop", self._event_calendar_loop),
            ("_wikipedia_collection_loop", self._wikipedia_collection_loop),
            ("_gdelt_collection_loop", self._gdelt_collection_loop),
            ("_hackernews_collection_loop", self._hackernews_collection_loop),
            ("_spike_detection_loop", self._spike_detection_loop),
            ("_velocity_collection_loop", self._velocity_collection_loop),
            ("_fourchan_collection_loop", self._fourchan_collection_loop),
        ]
        self.collection_tasks = []
        for _name, _coro_fn in _core_loops:
            _t = asyncio.create_task(_coro_fn())
            _t.add_done_callback(lambda t, n=_name: self._on_collection_task_done(t, n))
            self.collection_tasks.append(_t)

        # Opt-in streaming tasks
        if getattr(settings, "USE_REDDIT_STREAMING", False):
            _t = asyncio.create_task(self._reddit_stream_task())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_reddit_stream_task"))
            self.collection_tasks.append(_t)
        if getattr(settings, "TELEGRAM_API_ID", None):
            _t = asyncio.create_task(self._telegram_stream_task())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_telegram_stream_task"))
            self.collection_tasks.append(_t)
        if getattr(settings, "DISCORD_BOT_TOKEN", None):
            _t = asyncio.create_task(self._discord_stream_task())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_discord_stream_task"))
            self.collection_tasks.append(_t)
        # Phase 6.1: Kalshi cross-platform lead signals (enabled by default)
        if getattr(settings, "KALSHI_SIGNAL_ENABLED", True):
            _t = asyncio.create_task(self._kalshi_collection_loop())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_kalshi_collection_loop"))
            self.collection_tasks.append(_t)

        # Elite Model Deep Dive: new signal collection loops
        if self.legislative_tracker is not None and self.legislative_tracker.is_available:
            _t = asyncio.create_task(self._legislative_collection_loop())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_legislative_collection_loop"))
            self.collection_tasks.append(_t)
        if self.polling_client is not None and self.polling_client.is_available:
            _t = asyncio.create_task(self._polling_collection_loop())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_polling_collection_loop"))
            self.collection_tasks.append(_t)
        if self.court_monitor is not None and self.court_monitor.is_available:
            _t = asyncio.create_task(self._court_monitor_collection_loop())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_court_monitor_collection_loop"))
            self.collection_tasks.append(_t)
        if self.intl_elections is not None:
            _t = asyncio.create_task(self._intl_elections_collection_loop())
            _t.add_done_callback(lambda t: self._on_collection_task_done(t, "_intl_elections_collection_loop"))
            self.collection_tasks.append(_t)
    
    def enqueue_signal(self, source: str, item: Dict) -> bool:
        """
        B9: Non-blocking enqueue to per-source ring buffer.
        Drops item (returns False) if queue is full — preserves backpressure without blocking.
        Low-priority drop: queue full = source is producing faster than we can consume.
        """
        q = self._signal_queues.get(source)
        if q is None:
            return False
        try:
            q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._queue_drop_counts[source] = self._queue_drop_counts.get(source, 0) + 1
            if self._queue_drop_counts[source] % 100 == 1:  # Log every 100 drops
                logger.warning(
                    "B9 signal queue full: source=%s drops=%d — backpressure active",
                    source, self._queue_drop_counts[source],
                )
            return False

    def get_queue_stats(self) -> Dict[str, Dict]:
        """B9: Return queue depth and drop counts for observability."""
        return {
            source: {
                "depth": q.qsize(),
                "maxsize": q.maxsize,
                "drops": self._queue_drop_counts.get(source, 0),
                "utilization": round(q.qsize() / q.maxsize, 3) if q.maxsize > 0 else 0.0,
            }
            for source, q in self._signal_queues.items()
        }

    # ── Elite Model Deep Dive: New signal collection loops ───────────────

    async def _legislative_collection_loop(self):
        """Collect legislative signals (Congress.gov + ProPublica)."""
        _interval = int(getattr(settings, "LEGISLATIVE_POLL_INTERVAL_SECONDS", 1800))
        while self.running:
            try:
                if self.legislative_tracker:
                    signals = await asyncio.wait_for(
                        self.legislative_tracker.poll_all(), timeout=30.0
                    )
                    for signal in signals:
                        if signal.get("confidence", 0) >= SIGNAL_CONFIDENCE_MIN:
                            await self._publish_signal(signal)
            except asyncio.TimeoutError:
                logger.debug("Legislative poll timed out")
            except Exception as e:
                logger.debug("Legislative collection error: %s", e)
            await asyncio.sleep(_interval)

    async def _polling_collection_loop(self):
        """Collect polling data signals (VoteHub + FiveThirtyEight)."""
        _interval = int(getattr(settings, "POLLING_POLL_INTERVAL_SECONDS", 3600))
        while self.running:
            try:
                if self.polling_client:
                    markets = await self._get_active_markets()
                    for market in markets[:50]:  # Top 50 political markets
                        q = market.get("question", "")
                        price = float(market.get("yes_price", 0) or market.get("outcomePrices", "[0.5]").strip("[]").split(",")[0] or 0.5)
                        signal = await asyncio.wait_for(
                            self.polling_client.get_poll_signal_for_market(q, price),
                            timeout=15.0,
                        )
                        if signal and signal.get("confidence", 0) >= SIGNAL_CONFIDENCE_MIN:
                            signal["market_id"] = market.get("id", "")
                            await self._publish_signal(signal)
            except asyncio.TimeoutError:
                logger.debug("Polling collection timed out")
            except Exception as e:
                logger.debug("Polling collection error: %s", e)
            await asyncio.sleep(_interval)

    async def _court_monitor_collection_loop(self):
        """Collect court and executive action signals."""
        _interval = int(getattr(settings, "COURT_MONITOR_POLL_INTERVAL_SECONDS", 1800))
        while self.running:
            try:
                if self.court_monitor:
                    signals = await asyncio.wait_for(
                        self.court_monitor.poll_all(), timeout=30.0
                    )
                    for signal in signals:
                        if signal.get("confidence", 0) >= SIGNAL_CONFIDENCE_MIN:
                            await self._publish_signal(signal)
            except asyncio.TimeoutError:
                logger.debug("Court monitor poll timed out")
            except Exception as e:
                logger.debug("Court monitor collection error: %s", e)
            await asyncio.sleep(_interval)

    async def _intl_elections_collection_loop(self):
        """Collect international election data."""
        _interval = int(getattr(settings, "INTL_ELECTIONS_POLL_INTERVAL_SECONDS", 43200))
        while self.running:
            try:
                if self.intl_elections:
                    await asyncio.wait_for(
                        self.intl_elections.fetch_elections(), timeout=30.0
                    )
                    # Match elections to active markets
                    markets = await self._get_active_markets()
                    for market in markets[:100]:
                        q = market.get("question", "")
                        elections = self.intl_elections.get_elections_for_market(q)
                        for election in elections:
                            signal = {
                                "source_type": "international_election",
                                "source_name": f"intl_election:{election.get('country', '')}",
                                "direction": "NEUTRAL",
                                "confidence": 0.4,
                                "raw_text": f"[{election.get('country', '')}] {election.get('election_type', '')} election on {election.get('date', '')}",
                                "market_id": market.get("id", ""),
                                "time_sensitivity": "days",
                                "is_breaking": False,
                            }
                            await self._publish_signal(signal)
            except asyncio.TimeoutError:
                logger.debug("Intl elections poll timed out")
            except Exception as e:
                logger.debug("Intl elections collection error: %s", e)
            await asyncio.sleep(_interval)

    async def stop(self):
        """Stop all signal collection tasks."""
        self.running = False
        for task in self.collection_tasks:
            task.cancel()
        await asyncio.gather(*self.collection_tasks, return_exceptions=True)

        # Close aggregators
        await self.news_aggregator.close()
        await self.social_aggregator.close()
        await self.gdelt_client.close()
        await self.hn_client.close()
        await self.fourchan_poller.close()
        # Close new signal sources
        if self.legislative_tracker:
            try:
                await self.legislative_tracker.close()
            except Exception:
                pass
        if self.polling_client:
            try:
                await self.polling_client.close()
            except Exception:
                pass
        if self.court_monitor:
            try:
                await self.court_monitor.close()
            except Exception:
                pass
        if self._praw_client:
            self._praw_client.stop()
        if self._telegram_client:
            try:
                await self._telegram_client.stop()
            except Exception:
                pass
        if self._discord_client:
            try:
                await self._discord_client.stop()
            except Exception:
                pass

        logger.info("Signal ingestion service stopped")
    
    async def _news_collection_loop(self):
        """Collect news every 60 seconds."""
        while self.running:
            try:
                # Placeholder - would integrate with NewsAPI, RSS feeds, etc.
                # 10s timeout prevents hung news API from blocking this loop forever.
                try:
                    news_items = await asyncio.wait_for(self._fetch_news(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.debug("_fetch_news() timed out (10s) — skipping cycle")
                    await asyncio.sleep(60)
                    continue

                for item in news_items:
                    try:
                        signal = await asyncio.wait_for(self._extract_signal_from_news(item), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.debug("_extract_signal_from_news() timed out (10s) — skipping item")
                        continue
                    if signal and signal.get("confidence", 0) >= SIGNAL_CONFIDENCE_MIN:
                        await self._publish_signal(signal)

                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"News collection error: {str(e)}", exc_info=True)
                await asyncio.sleep(60)

    async def _social_collection_loop(self):
        """Collect social media signals."""
        while self.running:
            try:
                # Placeholder - would integrate with Twitter/X API, Reddit, etc.
                # 10s timeout prevents hung social API from blocking this loop forever.
                try:
                    social_items = await asyncio.wait_for(self._fetch_social(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.debug("_fetch_social() timed out (10s) — skipping cycle")
                    await asyncio.sleep(120)
                    continue

                for item in social_items:
                    try:
                        signal = await asyncio.wait_for(self._extract_signal_from_social(item), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.debug("_extract_signal_from_social() timed out (10s) — skipping item")
                        continue
                    if signal and signal.get("confidence", 0) >= SIGNAL_CONFIDENCE_MIN:
                        await self._publish_signal(signal)

                await asyncio.sleep(120)  # Check every 2 minutes
            except Exception as e:
                logger.error(f"Social collection error: {str(e)}", exc_info=True)
                await asyncio.sleep(120)
    
    async def _event_calendar_loop(self):
        """Check for upcoming scheduled events."""
        # Import events from markets on first run
        try:
            await self.event_calendar.import_events_from_markets()
        except Exception as e:
            logger.warning(f"Event import failed: {str(e)}")
        
        while self.running:
            try:
                # Check for events in next 24 hours
                upcoming_events = await self.event_calendar.get_upcoming_events(hours=24)
                
                for event in upcoming_events:
                    if event.get("notified"):
                        continue  # Already notified
                    
                    scheduled_time_str = event.get("scheduled_time")
                    if scheduled_time_str:
                        try:
                            from dateutil.parser import parse
                            scheduled_time = parse(scheduled_time_str)
                            if scheduled_time.tzinfo is None:
                                scheduled_time = scheduled_time.replace(tzinfo=timezone.utc)
                        except Exception:
                            continue
                    else:
                        continue
                    
                    # Create signal for upcoming event
                    signal = {
                        "market_id": event.get("market_id"),
                        "source_type": SignalSource.CALENDAR.value,
                        "source_name": "event_calendar",
                        "direction": "NEUTRAL",  # Events don't have direction
                        "confidence": 0.9,  # High confidence for scheduled events
                        "raw_text": event.get("description", ""),
                        "time_sensitivity": "immediate" if scheduled_time < datetime.now(timezone.utc) + timedelta(hours=1) else "hours",
                        "is_breaking": False,
                        "expires_at": scheduled_time,
                        "priority_score": self._calculate_priority_score({
                            "confidence": 0.9,
                            "time_sensitivity": "immediate" if scheduled_time < datetime.now(timezone.utc) + timedelta(hours=1) else "hours"
                        })
                    }
                    
                    await self._publish_signal(signal)
                    
                    # Mark as notified
                    await self.event_calendar.mark_notified(event["id"])
                
                await asyncio.sleep(3600)  # Check every hour
            except Exception as e:
                logger.error(f"Event calendar error: {str(e)}", exc_info=True)
                await asyncio.sleep(3600)
    
    async def _fetch_news(self) -> List[Dict[str, Any]]:
        """Fetch news from configured sources. Uses market keywords or fallback."""
        try:
            keywords = []
            markets = await self.cache.get("markets:active:all")
            if not markets:
                markets = await self.client.get_markets(active=True, limit=100)

            if markets:
                for market in markets[:20]:
                    question = market.get("question", "")
                    words = [w.lower() for w in question.split() if len(w) > 4]
                    keywords.extend(words[:3])
            if not keywords:
                keywords = list(DEFAULT_NEWS_KEYWORDS)

            articles = await self.news_aggregator.fetch_all_news(
                keywords=list(set(keywords))[:10],
                max_articles=150
            )
            return articles
        except Exception as e:
            logger.error(f"News fetch error: {str(e)}", exc_info=True)
            return []
    
    async def _fetch_social(self) -> List[Dict[str, Any]]:
        """Fetch social media posts. Reddit RSS is in news; this uses API if configured."""
        try:
            keywords = []
            markets = await self.cache.get("markets:active:all")
            if not markets:
                markets = await self.client.get_markets(active=True, limit=50)

            if markets:
                for market in markets[:10]:
                    question = market.get("question", "")
                    words = [w.lower() for w in question.split() if len(w) > 4]
                    keywords.extend(words[:2])
            if not keywords:
                keywords = list(DEFAULT_NEWS_KEYWORDS)[:5]

            posts = await self.social_aggregator.fetch_all_social(
                keywords=list(set(keywords))[:5],
                max_posts=100
            )
            return posts
        except Exception as e:
            logger.error(f"Social fetch error: {str(e)}", exc_info=True)
            return []
    
    async def _extract_signal_from_news(self, news_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract trading signal from news item.
        Uses LLM for intelligent extraction.
        """
        # Get active markets
        markets = await self.cache.get("markets:active:all")
        if not markets:
            markets = await self.client.get_markets(active=True, limit=500)
            if markets:
                await self.cache.set("markets:active:all", markets, ttl=300)
        
        if not markets:
            return None
        
        # Use LLM for extraction
        news_text = f"{news_item.get('title', '')} {news_item.get('text', '')}"
        source = news_item.get("source", "unknown")
        
        signal = await self.llm_extractor.extract_signal(
            text=news_text,
            source=source,
            markets=markets,
            timestamp=news_item.get("published_at")
        )
        
        if signal:
            # Score sentiment before publishing
            sentiment = self.sentiment_analyzer.analyze_text_sentiment(news_text, text_type="news")
            signal["sentiment_score"] = sentiment.get("compound", 0.0)
            signal["sentiment_signal"] = sentiment.get("signal", "neutral")
            if isinstance(signal["sentiment_signal"], Enum):
                signal["sentiment_signal"] = signal["sentiment_signal"].value
            # Boost/reduce confidence based on sentiment alignment
            signal["confidence"] = signal.get("confidence", 0.5) * (1 + sentiment.get("compound", 0) * 0.3)
            signal["confidence"] = max(0.0, min(1.0, signal["confidence"]))
            signal["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=24)
            signal["priority_score"] = self._calculate_priority_score({
                "confidence": signal.get("confidence", 0.5),
                "is_breaking": news_item.get("is_breaking", False),
                "time_sensitivity": signal.get("time_sensitivity", "hours"),
                "sentiment_score": sentiment.get("compound", 0.0),
            })
            return signal

        # Fallback to keyword matching if LLM fails
        return await self._extract_signal_keyword_fallback(news_item, markets)
    
    async def _extract_signal_keyword_fallback(
        self,
        news_item: Dict[str, Any],
        markets: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Fallback keyword matching if LLM extraction fails."""
        news_text = news_item.get("text", "").lower()
        news_title = news_item.get("title", "").lower()
        combined_text = f"{news_title} {news_text}"
        
        matched_markets = []
        for market in markets:
            market_question = market.get("question", "").lower()
            market_keywords = market_question.split()
            
            # Check for keyword overlap
            matches = sum(1 for keyword in market_keywords if keyword in combined_text and len(keyword) > 4)
            if matches >= 2:
                direction = "YES" if any(word in combined_text for word in ["win", "yes", "will", "happen"]) else "NO"
                
                matched_markets.append({
                    "market_id": market.get("id"),
                    "direction": direction,
                    "confidence": min(0.7, matches / 5.0),
                    "reasoning": f"Keyword matches: {matches}"
                })
        
        if matched_markets:
            best_match = max(matched_markets, key=lambda x: x["confidence"])
            return {
                "market_id": best_match["market_id"],
                "source_type": SignalSource.NEWS.value,
                "source_name": news_item.get("source", "unknown"),
                "direction": best_match["direction"],
                "confidence": best_match["confidence"],
                "raw_text": news_item.get("text", ""),
                "time_sensitivity": "immediate" if news_item.get("is_breaking", False) else "hours",
                "is_breaking": news_item.get("is_breaking", False),
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
                "priority_score": self._calculate_priority_score({
                    "confidence": best_match["confidence"],
                    "is_breaking": news_item.get("is_breaking", False)
                })
            }
        
        return None
    
    async def _extract_signal_from_social(self, social_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract signal from social media post."""
        # Get active markets
        markets = await self.cache.get("markets:active:all")
        if not markets:
            markets = await self.client.get_markets(active=True, limit=500)
            if markets:
                await self.cache.set("markets:active:all", markets, ttl=300)
        
        if not markets:
            return None
        
        # Use LLM for extraction
        text = social_item.get("text", "")
        source = social_item.get("source", "unknown")
        
        signal = await self.llm_extractor.extract_signal(
            text=text,
            source=source,
            markets=markets,
            timestamp=social_item.get("created_at")
        )
        
        if signal:
            # Score sentiment before publishing (social text type)
            sentiment = self.sentiment_analyzer.analyze_text_sentiment(text, text_type="social")
            signal["sentiment_score"] = sentiment.get("compound", 0.0)
            signal["sentiment_signal"] = sentiment.get("signal", "neutral")
            if isinstance(signal["sentiment_signal"], Enum):
                signal["sentiment_signal"] = signal["sentiment_signal"].value
            # Social signals typically have lower confidence
            signal["confidence"] = signal.get("confidence", 0.5) * 0.8
            signal["confidence"] = max(0.0, min(1.0, signal["confidence"]))
            signal["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=12)
            signal["priority_score"] = self._calculate_priority_score({
                "confidence": signal.get("confidence", 0.4),
                "time_sensitivity": signal.get("time_sensitivity", "hours"),
                "sentiment_score": sentiment.get("compound", 0.0),
            })
            return signal

        return None
    
    
    def _signal_hash(self, signal: Dict[str, Any]) -> str:
        """Generate dedup hash from signal raw text."""
        import re
        raw = signal.get("raw_text", "") or ""
        normalized = re.sub(r"[^\w\s]", "", raw[:200].lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    async def _is_duplicate_signal(self, signal: Dict[str, Any]) -> bool:
        """Check if signal was already published within dedup window."""
        if not self.cache.redis:
            return False
        sig_hash = self._signal_hash(signal)
        try:
            exists = await self.cache.redis.sismember("signals:seen", sig_hash)
            if exists:
                return True
            # Add hash with TTL via pipeline
            pipe = self.cache.redis.pipeline()
            pipe.sadd("signals:seen", sig_hash)
            pipe.expire("signals:seen", self._dedup_window)
            await pipe.execute()
            return False
        except Exception:
            return False

    async def _publish_signal(self, signal: Dict[str, Any]):
        """Publish signal to database and Redis queue (with dedup)."""
        try:
            # Deduplicate
            if await self._is_duplicate_signal(signal):
                logger.debug("Signal deduped: %s", signal.get("raw_text", "")[:60])
                return

            # Save to database (semaphore limits concurrent writes across all signal loops).
            # Use explicit acquire with 10s timeout so signal loops never deadlock if pool is full.
            if self.db.session_factory:
                try:
                    await asyncio.wait_for(self._signal_db_sem.acquire(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("signal_db_sem timeout — skipping signal write (pool busy)")
                    return
                try:
                    async with self.db.get_session() as session:
                        signal_obj = Signal(
                            market_id=signal["market_id"],
                            source_type=signal["source_type"],
                            source_name=signal["source_name"],
                            direction=signal["direction"],
                            confidence=signal["confidence"],
                            raw_text=signal.get("raw_text"),
                            time_sensitivity=signal.get("time_sensitivity"),
                            is_breaking=signal.get("is_breaking", False),
                            expires_at=signal.get("expires_at"),
                            priority_score=signal.get("priority_score", 0.0)
                        )
                        session.add(signal_obj)
                        await session.commit()
                finally:
                    self._signal_db_sem.release()

            # Add to Redis priority queue
            if self.cache.redis:
                market_id = signal["market_id"]
                priority = signal.get("priority_score", 0.0)
                
                # Store in sorted set (priority queue)
                await self.cache.redis.zadd(
                    f"signals:market:{market_id}",
                    {json.dumps(signal, default=str): priority}
                )
                
                # Also add to global priority queue
                await self.cache.redis.zadd(
                    self.signal_queue,
                    {json.dumps(signal, default=str): priority}
                )
            
            logger.debug(f"Published signal for market {signal['market_id']}", signal=signal)
            
        except Exception as e:
            logger.error(f"Error publishing signal: {str(e)}", exc_info=True)
    
    def _calculate_priority_score(self, item: Dict[str, Any]) -> float:
        """Calculate priority score for signal (0.0 to 1.0)."""
        score = 0.0

        # Confidence contributes 40%
        score += item.get("confidence", 0.0) * 0.4

        # Breaking news gets boost
        if item.get("is_breaking", False):
            score += 0.25

        # Time sensitivity
        time_sens = item.get("time_sensitivity", "days")
        if time_sens == "immediate":
            score += 0.2
        elif time_sens == "hours":
            score += 0.1

        # Sentiment strength contributes 10%
        sentiment = abs(item.get("sentiment_score", 0.0))
        score += sentiment * 0.1

        return min(1.0, score)
    
    async def _wikipedia_collection_loop(self):
        """Collect Wikipedia pageview spikes for market-relevant topics every 10 min."""
        while self.running:
            try:
                markets = await self.cache.get("markets:active:all")
                if not markets:
                    markets = await self.client.get_markets(active=True, limit=100)

                for market in (markets or [])[:30]:
                    question = market.get("question", "")
                    if not question:
                        continue
                    # Use entity extractor to find topics
                    entities = self.entity_extractor.extract(question)
                    topics = entities.get("topics", [])
                    if not topics:
                        # Fallback: use longest capitalized words
                        topics = [w for w in question.split() if len(w) > 4 and w[0].isupper()][:3]

                    for topic in topics[:2]:
                        try:
                            # I18: wrap external fetch — skip topic on timeout
                            signal_data = await asyncio.wait_for(
                                self.wikipedia.get_pageview_signal(topic), timeout=10.0
                            )
                            if signal_data and signal_data.get("spike_ratio", 1.0) > 2.0:
                                signal = {
                                    "market_id": market.get("id"),
                                    "source_type": "wikipedia",
                                    "source_name": f"wikipedia:{topic}",
                                    "direction": "NEUTRAL",
                                    "confidence": min(0.8, signal_data["spike_ratio"] / 10.0),
                                    "raw_text": f"Wikipedia spike: {topic} ({signal_data['spike_ratio']:.1f}x normal views)",
                                    "time_sensitivity": "hours",
                                    "is_breaking": signal_data.get("spike_ratio", 1) > 5.0,
                                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
                                    "priority_score": self._calculate_priority_score({
                                        "confidence": min(0.8, signal_data["spike_ratio"] / 10.0),
                                        "time_sensitivity": "hours",
                                    }),
                                }
                                await self._publish_signal(signal)
                        except Exception:
                            pass
                    await asyncio.sleep(1)  # Rate limit Wikipedia API

                await asyncio.sleep(600)  # Every 10 minutes
            except Exception as e:
                logger.debug("Wikipedia collection error: %s", e)
                await asyncio.sleep(600)

    # ----------------------------------------------------------------
    # NEW COLLECTION LOOPS (Phases 1-3)
    # ----------------------------------------------------------------

    async def _gdelt_collection_loop(self):
        """Collect GDELT news events every 15 minutes."""
        while self.running:
            try:
                markets = await self._get_active_markets(limit=30)
                for market in markets:
                    question = market.get("question", "")
                    if not question:
                        continue
                    entities = self.entity_extractor.extract(question)
                    topics = entities.get("topics", [])
                    if not topics:
                        topics = [w for w in question.split() if len(w) > 4 and w[0].isupper()][:3]
                    for topic in topics[:2]:
                        try:
                            # I18: wrap external fetch — skip topic on timeout
                            articles = await asyncio.wait_for(
                                self.gdelt_client.search_events(
                                    keywords=[topic], timespan="15min", max_records=10
                                ),
                                timeout=10.0,
                            )
                            for article in articles:
                                tone = article.get("tone", 0.0)
                                signal = {
                                    "market_id": market.get("id"),
                                    "source_type": "gdelt",
                                    "source_name": f"gdelt:{article.get('domain', 'unknown')}",
                                    "direction": "YES" if tone > 2.0 else ("NO" if tone < -2.0 else "NEUTRAL"),
                                    "confidence": min(0.7, abs(tone) / 10.0),
                                    "raw_text": article.get("title", "")[:500],
                                    "sentiment_score": tone / 10.0,
                                    "time_sensitivity": "hours",
                                    "is_breaking": False,
                                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
                                    "priority_score": 0.0,
                                }
                                signal["priority_score"] = self._calculate_priority_score(signal)
                                if signal["confidence"] >= SIGNAL_CONFIDENCE_MIN:
                                    await self._publish_signal(signal)
                                    # Feed velocity engine
                                    await self.velocity_engine.record_message(
                                        topic=topic, source="gdelt",
                                        timestamp=time.time(),
                                        sentiment=tone / 10.0,
                                    )
                        except Exception:
                            pass
                    await asyncio.sleep(0.5)
                await asyncio.sleep(900)  # 15 min
            except Exception as e:
                logger.debug("GDELT collection error: %s", e)
                await asyncio.sleep(900)

    async def _hackernews_collection_loop(self):
        """Poll Hacker News Algolia every 15 minutes."""
        while self.running:
            try:
                markets = await self._get_active_markets(limit=20)
                for market in markets:
                    question = market.get("question", "")
                    if not question:
                        continue
                    # I18: wrap external fetch — skip market on timeout
                    try:
                        signal_data = await asyncio.wait_for(
                            self.hn_client.get_market_signal(question), timeout=10.0
                        )
                    except asyncio.TimeoutError:
                        logger.debug("signal_ingestion: HN market signal timed out, skipping market")
                        await asyncio.sleep(1)
                        continue
                    if signal_data and signal_data.get("mention_count", 0) > 0:
                        signal = {
                            "market_id": market.get("id"),
                            "source_type": "hackernews",
                            "source_name": "hackernews",
                            "direction": "NEUTRAL",
                            "confidence": min(0.7, signal_data["mention_count"] / 20.0),
                            "raw_text": f"HN mentions: {signal_data['mention_count']} in 24h for '{question[:80]}'",
                            "time_sensitivity": "hours",
                            "is_breaking": signal_data.get("mention_count", 0) > 10,
                            "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
                            "priority_score": 0.0,
                        }
                        signal["priority_score"] = self._calculate_priority_score(signal)
                        if signal["confidence"] >= SIGNAL_CONFIDENCE_MIN:
                            await self._publish_signal(signal)
                            # Feed spike detector
                            entities = self.entity_extractor.extract(question)
                            for topic in (entities.get("topics", []) or [question.split()[0]])[:1]:
                                await self.spike_detector.update_baseline(
                                    topic, "hackernews", float(signal_data["mention_count"])
                                )
                    await asyncio.sleep(1)
                await asyncio.sleep(900)  # 15 min
            except Exception as e:
                logger.debug("HackerNews collection error: %s", e)
                await asyncio.sleep(900)

    async def _spike_detection_loop(self):
        """Run multi-source spike detection every 5 minutes."""
        while self.running:
            try:
                markets = await self._get_active_markets(limit=30)
                for market in markets:
                    question = market.get("question", "")
                    if not question:
                        continue
                    entities = self.entity_extractor.extract(question)
                    topics = entities.get("topics", [])
                    if not topics:
                        topics = [w for w in question.split() if len(w) > 4 and w[0].isupper()][:2]

                    for topic in topics[:2]:
                        try:
                            # Gather source values
                            sources = {}
                            # Wikipedia — I18: timeout guard
                            try:
                                wiki_signal = await asyncio.wait_for(
                                    self.wikipedia.get_pageview_signal(topic), timeout=10.0
                                )
                                if wiki_signal:
                                    sources["wikipedia"] = wiki_signal.get("spike_ratio", 1.0)
                            except asyncio.TimeoutError:
                                pass
                            # HN mention count — I18: timeout guard
                            try:
                                hn_count = await asyncio.wait_for(
                                    self.hn_client.get_mention_count(topic, hours=24), timeout=10.0
                                )
                            except asyncio.TimeoutError:
                                hn_count = 0
                            if hn_count > 0:
                                sources["hackernews"] = float(hn_count)

                            if sources:
                                spike = await self.spike_detector.check_spike(topic, sources)
                                if spike.get("is_spike"):
                                    signal = {
                                        "market_id": market.get("id"),
                                        "source_type": "spike_detection",
                                        "source_name": f"spike:{topic}",
                                        "direction": "NEUTRAL",
                                        "confidence": spike.get("confidence", 0.5),
                                        "raw_text": f"Spike: {topic} z={spike['z_score']:.1f} ({spike['severity']}) sources={list(spike.get('sources_spiking', []))}",
                                        "time_sensitivity": "immediate" if spike["severity"] == "major" else "hours",
                                        "is_breaking": spike["severity"] == "major",
                                        "expires_at": datetime.now(timezone.utc) + timedelta(hours=6),
                                        "priority_score": 0.0,
                                    }
                                    signal["priority_score"] = self._calculate_priority_score(signal)
                                    await self._publish_signal(signal)
                        except Exception:
                            pass
                    await asyncio.sleep(0.5)
                await asyncio.sleep(300)  # 5 min
            except Exception as e:
                logger.debug("Spike detection error: %s", e)
                await asyncio.sleep(300)

    async def _velocity_collection_loop(self):
        """Report velocity/acceleration metrics every 60 seconds."""
        while self.running:
            try:
                try:
                    top = await asyncio.wait_for(self.velocity_engine.get_top_accelerating(limit=5), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.debug("velocity_engine.get_top_accelerating() timed out (10s) — skipping cycle")
                    await asyncio.sleep(60)
                    continue
                for item in top:
                    if item.get("is_spike"):
                        # Find matching market
                        markets = await self._get_active_markets(limit=100)
                        topic = item.get("topic", "")
                        for market in markets:
                            question = market.get("question", "").lower()
                            if topic.lower() in question:
                                signal = {
                                    "market_id": market.get("id"),
                                    "source_type": "velocity",
                                    "source_name": f"velocity:{topic}",
                                    "direction": "NEUTRAL",
                                    "confidence": min(0.8, item.get("velocity", 1.0) / 10.0),
                                    "raw_text": f"Velocity spike: {topic} {item['velocity']:.1f}x baseline, accel={item.get('acceleration', 0):.1f}",
                                    "time_sensitivity": "immediate",
                                    "is_breaking": item.get("severity") == "major",
                                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=3),
                                    "priority_score": 0.0,
                                }
                                signal["priority_score"] = self._calculate_priority_score(signal)
                                await self._publish_signal(signal)
                                break

                # Check sentiment divergences
                try:
                    markets = await self._get_active_markets(limit=30)
                    topics = set()
                    for m in markets:
                        entities = self.entity_extractor.extract(m.get("question", ""))
                        for t in entities.get("topics", [])[:1]:
                            topics.add(t)
                    if topics:
                        divergences = await self.sentiment_velocity.get_divergences(list(topics)[:10])
                        for div in divergences:
                            if div.get("divergence_type"):
                                for market in markets:
                                    if div["topic"].lower() in market.get("question", "").lower():
                                        direction = "YES" if div["divergence_type"] == "bullish" else "NO"
                                        signal = {
                                            "market_id": market.get("id"),
                                            "source_type": "sentiment_velocity",
                                            "source_name": f"sent_div:{div['topic']}",
                                            "direction": direction,
                                            "confidence": 0.6,
                                            "raw_text": f"Sentiment divergence ({div['divergence_type']}): {div['topic']} shift={div.get('shift', 0):.2f}",
                                            "time_sensitivity": "hours",
                                            "is_breaking": False,
                                            "expires_at": datetime.now(timezone.utc) + timedelta(hours=6),
                                            "priority_score": 0.0,
                                        }
                                        signal["priority_score"] = self._calculate_priority_score(signal)
                                        await self._publish_signal(signal)
                                        break
                except Exception:
                    pass

                await asyncio.sleep(60)
            except Exception as e:
                logger.debug("Velocity collection error: %s", e)
                await asyncio.sleep(60)

    async def _fourchan_collection_loop(self):
        """Poll 4chan /biz/ and /pol/ for prediction market mentions every 60 seconds."""
        while self.running:
            try:
                # Search for Polymarket-related threads
                keywords = ["polymarket", "prediction market", "betting odds", "trump", "bitcoin", "election"]
                try:
                    result = await asyncio.wait_for(self.fourchan_poller.get_market_mentions(keywords), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.debug("fourchan_poller.get_market_mentions() timed out (10s) — skipping cycle")
                    await asyncio.sleep(60)
                    continue
                total = result.get("total_mentions", 0)
                if total > 0:
                    # Feed velocity engine
                    await self.velocity_engine.record_message(
                        topic="polymarket", source="fourchan",
                        timestamp=time.time(),
                    )
                    # High mention count → signal
                    if total >= 5:
                        signal = {
                            "market_id": None,  # No specific market
                            "source_type": "social",
                            "source_name": "fourchan",
                            "direction": "NEUTRAL",
                            "confidence": min(0.6, total / 20.0),
                            "raw_text": f"4chan mentions: {total} across {result.get('per_board', {})}",
                            "time_sensitivity": "hours",
                            "is_breaking": False,
                            "expires_at": datetime.now(timezone.utc) + timedelta(hours=6),
                            "priority_score": 0.0,
                        }
                        signal["priority_score"] = self._calculate_priority_score(signal)
                        # Only publish if we can attach to a market
                        top_threads = result.get("top_threads", [])
                        if top_threads:
                            markets = await self._get_active_markets(limit=100)
                            for thread in top_threads[:3]:
                                thread_text = thread.get("text", "").lower()
                                for market in markets:
                                    q = market.get("question", "").lower()
                                    if any(w in thread_text for w in q.split() if len(w) > 5):
                                        signal["market_id"] = market.get("id")
                                        await self._publish_signal(signal)
                                        break
                                if signal["market_id"]:
                                    break
                await asyncio.sleep(60)
            except Exception as e:
                logger.debug("4chan collection error: %s", e)
                await asyncio.sleep(60)

    # ----------------------------------------------------------------
    # STREAMING TASKS (opt-in, long-lived)
    # ----------------------------------------------------------------

    async def _reddit_stream_task(self):
        """PRAW persistent comment streaming (opt-in)."""
        while self.running:
            try:
                from base_engine.signals.social_sources import PRAWStreamClient
                if not self._praw_client:
                    self._praw_client = PRAWStreamClient()
                if not self._praw_client.is_available:
                    logger.debug("PRAW not available — Reddit streaming disabled")
                    return
                subreddits_str = getattr(settings, "REDDIT_SUBREDDITS",
                    "polymarket,politics,worldnews,cryptocurrency,wallstreetbets")
                subreddits = [s.strip() for s in subreddits_str.split(",") if s.strip()]

                async def _on_comment(item):
                    text = item.get("text", "")
                    topic = item.get("subreddit", "reddit")
                    # Feed velocity + sentiment
                    await self.velocity_engine.record_message(
                        topic=topic, source="reddit_stream",
                        timestamp=time.time(),
                    )
                    sentiment = self.sentiment_analyzer.analyze_text_sentiment(text[:500], text_type="social")
                    await self.sentiment_velocity.record_sentiment(
                        topic=topic,
                        score=sentiment.get("compound", 0.0),
                        timestamp=time.time(),
                    )

                await self._praw_client.start_stream(subreddits, _on_comment)
            except Exception as e:
                logger.debug("Reddit stream error: %s — reconnecting in 30s", e)
                await asyncio.sleep(30)

    async def _telegram_stream_task(self):
        """Telegram event streaming (opt-in)."""
        while self.running:
            try:
                from base_engine.signals.telegram_stream import TelegramStreamClient
                if not self._telegram_client:
                    self._telegram_client = TelegramStreamClient()
                if not self._telegram_client.is_available:
                    return

                async def _on_message(item):
                    text = item.get("text", "")
                    chat = item.get("chat_title", "telegram")
                    await self.velocity_engine.record_message(
                        topic=chat, source="telegram",
                        timestamp=time.time(),
                    )
                    sentiment = self.sentiment_analyzer.analyze_text_sentiment(text[:500], text_type="social")
                    await self.sentiment_velocity.record_sentiment(
                        topic=chat,
                        score=sentiment.get("compound", 0.0),
                        timestamp=time.time(),
                    )

                await self._telegram_client.start(_on_message)
            except Exception as e:
                logger.debug("Telegram stream error: %s — reconnecting in 30s", e)
                await asyncio.sleep(30)

    async def _discord_stream_task(self):
        """Discord event streaming (opt-in)."""
        while self.running:
            try:
                from base_engine.signals.discord_stream import DiscordStreamClient
                if not self._discord_client:
                    self._discord_client = DiscordStreamClient()
                if not self._discord_client.is_available:
                    return

                async def _on_message(item):
                    text = item.get("text", "")
                    channel = item.get("channel_name", "discord")
                    await self.velocity_engine.record_message(
                        topic=channel, source="discord",
                        timestamp=time.time(),
                    )
                    sentiment = self.sentiment_analyzer.analyze_text_sentiment(text[:500], text_type="social")
                    await self.sentiment_velocity.record_sentiment(
                        topic=channel,
                        score=sentiment.get("compound", 0.0),
                        timestamp=time.time(),
                    )

                await self._discord_client.start(_on_message)
            except Exception as e:
                logger.debug("Discord stream error: %s — reconnecting in 30s", e)
                await asyncio.sleep(30)

    # ----------------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------------

    async def _get_active_markets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get active markets from cache or API.

        Priority: Redis cache (300s TTL) → in-memory cache (30s TTL) → API call.
        The in-memory cache ensures that when Redis is disconnected the 9 concurrent
        ingestion loops share one API call per 30s instead of making 9-10 per minute.
        """
        # L1: Redis (shared, 300s TTL)
        markets = await self.cache.get("markets:active:all")
        if markets:
            return markets

        # L2: In-memory instance cache (30s TTL) — protects against Redis being down
        now = time.time()
        if self._local_markets and (now - self._local_markets_at) < self._local_markets_ttl:
            return self._local_markets

        # L3: API call — serialised so only one loop fetches at a time.
        # 5s timeout prevents all 9 loops from blocking indefinitely if API hangs.
        async with self._local_markets_lock:
            # Re-check under lock in case another loop just populated the cache
            if self._local_markets and (time.time() - self._local_markets_at) < self._local_markets_ttl:
                return self._local_markets
            try:
                markets = await asyncio.wait_for(
                    self.client.get_markets(active=True, limit=limit), timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("_get_active_markets() API call timed out (5s) — using stale cache or empty")
                return self._local_markets or []
            if markets:
                await self.cache.set("markets:active:all", markets, ttl=300)
                self._local_markets = markets
                self._local_markets_at = time.time()
        return markets or []

    async def get_signals_for_market(
        self,
        market_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get all active signals for a specific market."""
        signals = []
        
        # Get from Redis priority queue
        if self.cache.redis:
            raw_signals = await self.cache.redis.zrevrange(
                f"signals:market:{market_id}",
                0,
                limit - 1,
                withscores=True
            )
            
            for signal_json, score in raw_signals:
                try:
                    signal = json.loads(signal_json)
                    signal["priority_score"] = score
                    signals.append(signal)
                except Exception:
                    continue
        
        # Also get from database if Redis is empty
        if not signals and self.db.session_factory:
            async with self.db.get_session() as session:
                from sqlalchemy import select, and_
                from base_engine.data.database import Signal
                
                result = await session.execute(
                    select(Signal).where(
                        and_(
                            Signal.market_id == market_id,
                            Signal.acted_on == False,
                            Signal.expires_at > datetime.now(timezone.utc)
                        )
                    ).order_by(Signal.priority_score.desc(), Signal.created_at.desc()).limit(limit)
                )
                db_signals = result.scalars().all()
                
                signals = [
                    {
                        "market_id": s.market_id,
                        "source_type": s.source_type,
                        "source_name": s.source_name,
                        "direction": s.direction,
                        "confidence": s.confidence,
                        "time_sensitivity": s.time_sensitivity,
                        "is_breaking": s.is_breaking,
                        "priority_score": s.priority_score,
                        "created_at": s.created_at.isoformat() if s.created_at else None
                    }
                    for s in db_signals
                ]
        
        return signals

    async def _kalshi_collection_loop(self) -> None:
        """Phase 6.1: Poll Kalshi public REST API every 10s for cross-platform lead signals.

        Compares Kalshi YES price to Polymarket active markets. If >3pp divergence exists
        in either direction, publishes a cross_platform signal with confidence proportional
        to the spread. No auth required (public price reads).
        """
        import aiohttp
        _KALSHI_API = "https://trading-api.kalshi.com/trade-api/v2/markets"
        _poll_interval = int(getattr(settings, "CROSS_PLATFORM_POLL_INTERVAL_S", 10))
        _min_divergence = float(getattr(settings, "CROSS_PLATFORM_SIGNAL_THRESHOLD", 0.03))
        logger.info("Kalshi signal collection started", poll_interval=_poll_interval)

        while self.running:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"Accept": "application/json"},
                ) as session:
                    async with session.get(
                        _KALSHI_API,
                        params={"limit": 100, "status": "open"},
                    ) as resp:
                        if resp.status != 200:
                            await asyncio.sleep(_poll_interval)
                            continue
                        payload = await resp.json()
                        kalshi_markets = payload.get("markets", [])

                # Build a quick lookup by ticker → yes_price
                _kalshi_prices: Dict[str, float] = {}
                for km in kalshi_markets:
                    ticker = km.get("ticker") or ""
                    # Kalshi prices in cents (0-99) → normalize to 0-1
                    _yes_cents = km.get("yes_bid") or km.get("last_price") or km.get("yes_ask")
                    if ticker and _yes_cents is not None:
                        try:
                            _kalshi_prices[ticker.upper()] = float(_yes_cents) / 100.0
                        except (ValueError, TypeError):
                            pass

                # Match against active Polymarket markets (use cached list)
                markets = await self._get_active_markets(limit=50)
                for market in markets:
                    _kalshi_ticker = str(market.get("kalshi_ticker") or "").upper().strip()
                    if not _kalshi_ticker:
                        continue
                    _kalshi_p = _kalshi_prices.get(_kalshi_ticker)
                    if _kalshi_p is None:
                        continue
                    _poly_p = float(market.get("yes_price") or 0.5)
                    _div = _kalshi_p - _poly_p  # positive = Kalshi more bullish on YES
                    if abs(_div) < _min_divergence:
                        continue
                    _conf = min(0.7, abs(_div) * 10)  # 3pp→0.30, 7pp→0.70
                    _dir = "YES" if _div > 0 else "NO"
                    signal = {
                        "market_id": market.get("id"),
                        "source_type": "cross_platform",
                        "source_name": f"kalshi:{_kalshi_ticker}",
                        "signal_source": "kalshi",
                        "direction": _dir,
                        "confidence": _conf,
                        "raw_text": (
                            f"Kalshi {_kalshi_ticker}: {_kalshi_p:.3f} vs "
                            f"Polymarket {_poly_p:.3f} (div={_div:+.3f})"
                        ),
                        "sentiment_score": _div,
                        "time_sensitivity": "minutes",
                        "is_breaking": abs(_div) > 0.05,
                        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
                        "priority_score": 0.0,
                    }
                    signal["priority_score"] = self._calculate_priority_score(signal)
                    await self._publish_signal(signal)
                    logger.debug(
                        "Kalshi cross-platform signal: market=%s ticker=%s div=%+.3f conf=%.2f dir=%s",
                        market.get("id"), _kalshi_ticker, _div, _conf, _dir,
                    )

            except asyncio.CancelledError:
                raise
            except Exception as _e:
                logger.debug("Kalshi signal collection error (non-fatal): %s", _e)

            await asyncio.sleep(_poll_interval)
