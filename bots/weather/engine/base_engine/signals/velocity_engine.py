"""
Velocity Engine — Signal 5: Social Media Velocity

Sliding window message rate-of-change tracking across all social sources.
Tracks message volume per topic across 5m/15m/1h/4h/24h windows
against 7-day baselines. Detects volume spikes and acceleration.

All data stored in Redis sorted sets (score=timestamp).
"""
import time
from typing import Dict, List, Optional, Any
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()

# Window sizes in seconds
WINDOWS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}


class VelocityEngine:
    """
    Message velocity tracker using Redis sorted sets.

    Records messages with timestamps, computes rate-of-change
    against 7-day rolling baselines, detects spikes.
    """

    def __init__(self, cache=None):
        self._cache = cache
        self._spike_threshold = settings.VELOCITY_SPIKE_THRESHOLD  # 3.0x
        self._major_threshold = settings.VELOCITY_MAJOR_THRESHOLD  # 5.0x

    async def record_message(
        self,
        topic: str,
        source: str,
        timestamp: Optional[float] = None,
        sentiment: float = 0.0,
    ):
        """
        Record a message for velocity tracking.

        Args:
            topic: The topic/keyword (e.g., "Bitcoin", "Trump")
            source: Source name (e.g., "reddit", "telegram", "discord")
            timestamp: Unix timestamp (defaults to now)
            sentiment: Sentiment score for this message (-1 to 1)
        """
        if not self._cache:
            return

        ts = timestamp or time.time()
        key = f"velocity:{topic}:{source}"

        try:
            # Add to sorted set with timestamp as score
            member = f"{ts}:{sentiment:.3f}"
            await self._cache.set(
                f"_velocity_msg:{topic}:{source}:{int(ts*1000)}",
                {"ts": ts, "sentiment": sentiment},
                ttl=86400,  # 24h TTL
            )

            # Increment per-window counters using simple keys
            for window_name, window_secs in WINDOWS.items():
                counter_key = f"velocity:count:{topic}:{source}:{window_name}"
                try:
                    current = await self._cache.get(counter_key)
                    count = (current or 0) + 1 if isinstance(current, (int, float)) else 1
                    await self._cache.set(counter_key, count, ttl=window_secs)
                except Exception:
                    pass

        except Exception as e:
            logger.debug("Velocity record error: %s", e)

    async def get_velocity(
        self,
        topic: str,
        source: str = "all",
    ) -> Dict[str, Any]:
        """
        Get velocity metrics for a topic.

        Args:
            topic: The topic to check
            source: Specific source or "all" for aggregate

        Returns:
            {windows: {5m: count, ...}, velocity, acceleration, is_spike, severity}
        """
        if not self._cache:
            return self._empty_velocity()

        sources = [source] if source != "all" else ["reddit", "telegram", "discord", "fourchan", "hackernews"]
        window_counts = {w: 0 for w in WINDOWS}

        for src in sources:
            for window_name in WINDOWS:
                counter_key = f"velocity:count:{topic}:{src}:{window_name}"
                try:
                    count = await self._cache.get(counter_key)
                    if isinstance(count, (int, float)):
                        window_counts[window_name] += int(count)
                except Exception:
                    pass

        # Load baseline (7-day rolling avg hourly count)
        baseline = await self._get_baseline(topic, source)
        baseline_hourly = baseline.get("avg_hourly", 1.0) if baseline else 1.0
        if baseline_hourly < 0.1:
            baseline_hourly = 0.1  # Floor to avoid div-by-zero

        # Velocity: current 1h count vs baseline
        current_hourly = window_counts.get("1h", 0)
        velocity = current_hourly / baseline_hourly

        # Acceleration: compare current vs 1h ago
        # Use 4h window and subtract 1h to estimate prior hour
        count_4h = window_counts.get("4h", 0)
        count_1h = window_counts.get("1h", 0)
        prior_3h_avg = (count_4h - count_1h) / 3.0 if count_4h > count_1h else baseline_hourly
        if prior_3h_avg < 0.1:
            prior_3h_avg = 0.1
        acceleration = (current_hourly - prior_3h_avg) / prior_3h_avg

        # Spike detection
        if velocity >= self._major_threshold:
            severity = "major"
        elif velocity >= self._spike_threshold:
            severity = "notable"
        else:
            severity = "none"

        return {
            "topic": topic,
            "source": source,
            "windows": window_counts,
            "velocity": round(velocity, 2),
            "acceleration": round(acceleration, 2),
            "baseline_hourly": round(baseline_hourly, 2),
            "is_spike": severity != "none",
            "severity": severity,
        }

    async def update_baseline(self, topic: str, source: str = "all"):
        """
        Update 7-day rolling baseline for a topic.
        Should be called periodically (e.g., daily).
        """
        if not self._cache:
            return

        key = f"velocity:baseline:{topic}:{source}"
        try:
            raw = await self._cache.get(key)
            baseline = raw if isinstance(raw, dict) else {"hourly_counts": [], "avg_hourly": 1.0}

            # Get current 1h count as new data point
            current = await self.get_velocity(topic, source)
            hourly_count = current["windows"].get("1h", 0)

            counts = baseline.get("hourly_counts", [])
            counts.append(hourly_count)
            # Keep last 168 entries (7 days * 24 hours)
            if len(counts) > 168:
                counts = counts[-168:]

            avg = sum(counts) / len(counts) if counts else 1.0

            updated = {
                "hourly_counts": counts,
                "avg_hourly": round(avg, 2),
                "updated_at": time.time(),
            }
            await self._cache.set(key, updated, ttl=86400 * 10)  # 10 day TTL
        except Exception as e:
            logger.debug("Velocity baseline update error: %s", e)

    async def get_top_accelerating(self, topics: Optional[List[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get topics with highest acceleration (fastest growing velocity).

        Args:
            topics: List of topics to check. If None, auto-discovers from Redis keys.
            limit: Max results

        Returns:
            List of velocity results sorted by acceleration desc
        """
        if topics is None:
            # Auto-discover tracked topics from Redis keys
            topics = await self._discover_topics()
        results = []
        for topic in topics:
            v = await self.get_velocity(topic)
            if v["acceleration"] > 0.5:  # Only include accelerating topics
                results.append(v)

        results.sort(key=lambda x: x["acceleration"], reverse=True)
        return results[:limit]

    async def _discover_topics(self) -> List[str]:
        """Discover active topics from Redis velocity keys."""
        if not self._cache or not getattr(self._cache, "redis", None):
            return []
        try:
            keys = []
            async for key in self._cache.redis.scan_iter(match="velocity:*:all", count=100):
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                # velocity:{topic}:all → extract topic
                parts = key.split(":")
                if len(parts) >= 3:
                    keys.append(parts[1])
            return keys[:50]  # Cap at 50 topics
        except Exception:
            return []

    async def _get_baseline(self, topic: str, source: str) -> Optional[Dict]:
        """Load baseline from Redis."""
        if not self._cache:
            return None
        try:
            key = f"velocity:baseline:{topic}:{source}"
            raw = await self._cache.get(key)
            if raw and isinstance(raw, dict):
                return raw
            return None
        except Exception:
            return None

    def _empty_velocity(self) -> Dict[str, Any]:
        return {
            "topic": "",
            "source": "all",
            "windows": {w: 0 for w in WINDOWS},
            "velocity": 0.0,
            "acceleration": 0.0,
            "baseline_hourly": 1.0,
            "is_spike": False,
            "severity": "none",
        }
