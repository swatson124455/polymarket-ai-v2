"""
Sentiment Velocity Tracker — Signal 5: Social Media Velocity

Tracks how fast average sentiment is CHANGING per topic.
A rapid sentiment shift (e.g., 0.2 → -0.3 in 1 hour) is a stronger
signal than a steady sentiment level. Detects sentiment-price divergences.

All data stored in Redis.
"""
import time
from typing import Dict, List, Optional, Any
from structlog import get_logger

logger = get_logger()


class SentimentVelocityTracker:
    """
    Tracks sentiment change rate per topic using Redis.

    Records sentiment scores with timestamps, computes
    windowed averages and shift rates.
    """

    def __init__(self, cache=None):
        self._cache = cache

    async def record_sentiment(
        self,
        topic: str,
        sentiment_score: float,
        timestamp: Optional[float] = None,
    ):
        """
        Record a sentiment observation for a topic.

        Args:
            topic: The topic (e.g., "Bitcoin", "Trump")
            sentiment_score: Score from -1 to 1
            timestamp: Unix timestamp (defaults to now)
        """
        if not self._cache:
            return

        ts = timestamp or time.time()
        key = f"sent_vel:{topic}"

        try:
            # Store as list of recent observations
            raw = await self._cache.get(key)
            observations = raw if isinstance(raw, list) else []

            observations.append({"ts": ts, "score": sentiment_score})

            # Keep last 500 observations (covers ~24h at moderate volume)
            if len(observations) > 500:
                observations = observations[-500:]

            # Prune observations older than 24h
            cutoff = time.time() - 86400
            observations = [o for o in observations if o["ts"] > cutoff]

            await self._cache.set(key, observations, ttl=86400)
        except Exception as e:
            logger.debug("Sentiment velocity record error: %s", e)

    async def get_sentiment_shift(
        self,
        topic: str,
        window_hours: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Compute sentiment shift rate for a topic.

        Compares average sentiment in [now-window, now] vs [now-2*window, now-window].

        Args:
            topic: The topic to check
            window_hours: Window size in hours

        Returns:
            {current_avg, prior_avg, shift, shift_rate, is_significant, direction}
        """
        if not self._cache:
            return self._empty_shift(topic)

        try:
            key = f"sent_vel:{topic}"
            raw = await self._cache.get(key)
            observations = raw if isinstance(raw, list) else []

            if len(observations) < 3:
                return self._empty_shift(topic)

            now = time.time()
            window_secs = window_hours * 3600

            # Current window: [now - window, now]
            current_obs = [o["score"] for o in observations if o["ts"] > now - window_secs]
            # Prior window: [now - 2*window, now - window]
            prior_obs = [o["score"] for o in observations if now - 2 * window_secs < o["ts"] <= now - window_secs]

            if not current_obs:
                return self._empty_shift(topic)

            current_avg = sum(current_obs) / len(current_obs)
            prior_avg = sum(prior_obs) / len(prior_obs) if prior_obs else current_avg

            shift = current_avg - prior_avg
            shift_rate = shift / window_hours if window_hours > 0 else 0.0

            # Significance: |shift| > 0.2 in the window is notable
            is_significant = abs(shift) > 0.2

            if shift > 0.1:
                direction = "improving"
            elif shift < -0.1:
                direction = "deteriorating"
            else:
                direction = "stable"

            return {
                "topic": topic,
                "current_avg": round(current_avg, 3),
                "prior_avg": round(prior_avg, 3),
                "shift": round(shift, 3),
                "shift_rate": round(shift_rate, 3),
                "is_significant": is_significant,
                "direction": direction,
                "current_obs_count": len(current_obs),
                "prior_obs_count": len(prior_obs),
                "window_hours": window_hours,
            }

        except Exception as e:
            logger.debug("Sentiment shift error: %s", e)
            return self._empty_shift(topic)

    async def get_divergences(
        self,
        topics_with_prices: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """
        Find topics where sentiment is shifting opposite to price movement.

        Args:
            topics_with_prices: {topic: price_change_pct} dict

        Returns:
            List of divergences (sentiment and price moving in opposite directions)
        """
        divergences = []

        for topic, price_change in topics_with_prices.items():
            shift_data = await self.get_sentiment_shift(topic, window_hours=4.0)
            if not shift_data["is_significant"]:
                continue

            sent_shift = shift_data["shift"]

            # Divergence: sentiment improving but price falling, or vice versa
            if sent_shift > 0.15 and price_change < -0.02:
                divergences.append({
                    "topic": topic,
                    "type": "bullish_divergence",
                    "sentiment_shift": sent_shift,
                    "price_change": price_change,
                    "signal": "Sentiment improving while price falling — potential buying opportunity",
                })
            elif sent_shift < -0.15 and price_change > 0.02:
                divergences.append({
                    "topic": topic,
                    "type": "bearish_divergence",
                    "sentiment_shift": sent_shift,
                    "price_change": price_change,
                    "signal": "Sentiment deteriorating while price rising — potential sell signal",
                })

        return divergences

    def _empty_shift(self, topic: str) -> Dict[str, Any]:
        return {
            "topic": topic,
            "current_avg": 0.0,
            "prior_avg": 0.0,
            "shift": 0.0,
            "shift_rate": 0.0,
            "is_significant": False,
            "direction": "stable",
            "current_obs_count": 0,
            "prior_obs_count": 0,
            "window_hours": 1.0,
        }
