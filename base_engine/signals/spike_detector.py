"""
Spike Detector — Signal 3: Leading Indicators

Z-score engine across pytrends, Wikipedia, and Hacker News data sources.
Detects multi-source correlated spikes for market-relevant topics.

Baselines stored in Redis (30-day rolling mean/stddev per topic per source).
"""
import json
import math
import time
from typing import Dict, List, Optional, Any
from structlog import get_logger
from config.settings import settings

logger = get_logger()


class SpikeDetector:
    """
    Z-score spike detection across multiple data sources.

    Maintains 30-day rolling baselines in Redis. Computes z-scores
    per source and detects multi-source correlated spikes.
    """

    # Source reliability weights (higher = more trusted)
    SOURCE_WEIGHTS = {
        "wikipedia": 0.9,
        "pytrends": 0.7,
        "hackernews": 0.6,
        "gdelt": 0.8,
    }

    def __init__(self, cache=None):
        self._cache = cache
        self._z_notable = settings.SPIKE_Z_SCORE_NOTABLE  # 2.0
        self._z_major = settings.SPIKE_Z_SCORE_MAJOR  # 3.0

    async def check_spike(
        self,
        topic: str,
        sources: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Check if a topic is spiking across data sources.

        Args:
            topic: The topic to check (e.g., "Bitcoin", "Trump")
            sources: {source_name: current_value} dict
                e.g., {"wikipedia": 50000, "pytrends": 85, "hackernews": 42}

        Returns:
            {topic, z_score, is_spike, severity, sources_spiking, confidence}
        """
        z_scores = {}
        sources_spiking = []

        for source, current_value in sources.items():
            baseline = await self._get_baseline(topic, source)
            if baseline is None:
                # No baseline yet — record this value and skip
                await self.update_baseline(topic, source, current_value)
                continue

            mean = baseline.get("mean", 0)
            stddev = baseline.get("stddev", 1)

            if stddev < 0.001:
                stddev = max(abs(mean) * 0.1, 1.0)  # Avoid division by zero

            z = (current_value - mean) / stddev
            z_scores[source] = round(z, 2)

            if z >= self._z_notable:
                sources_spiking.append(source)

            # Always update baseline with new data point
            await self.update_baseline(topic, source, current_value)

        if not z_scores:
            return {
                "topic": topic,
                "z_score": 0.0,
                "is_spike": False,
                "severity": "none",
                "sources_spiking": [],
                "confidence": 0.0,
            }

        # Combined z-score: weighted max across sources
        weighted_z = 0.0
        for source, z in z_scores.items():
            weight = self.SOURCE_WEIGHTS.get(source, 0.5)
            weighted_z = max(weighted_z, z * weight)

        # Severity classification
        if weighted_z >= self._z_major:
            severity = "major"
        elif weighted_z >= self._z_notable:
            severity = "notable"
        else:
            severity = "none"

        # Confidence boost for multi-source correlation
        confidence = min(1.0, weighted_z / 5.0)
        if len(sources_spiking) >= 2:
            confidence = min(1.0, confidence * 1.3)  # 30% boost for correlated spikes
        if len(sources_spiking) >= 3:
            confidence = min(1.0, confidence * 1.2)  # Additional 20% for 3+ sources

        return {
            "topic": topic,
            "z_score": round(weighted_z, 2),
            "z_scores_per_source": z_scores,
            "is_spike": severity != "none",
            "severity": severity,
            "sources_spiking": sources_spiking,
            "confidence": round(confidence, 3),
        }

    async def get_correlated_spikes(
        self,
        topics: List[str],
        sources_data: Dict[str, Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        """
        Check multiple topics and return those with correlated spikes.

        Args:
            topics: List of topics to check
            sources_data: {topic: {source: value}} dict

        Returns:
            List of spike results, filtered to is_spike=True, sorted by z_score desc
        """
        spikes = []
        for topic in topics:
            topic_sources = sources_data.get(topic, {})
            if topic_sources:
                result = await self.check_spike(topic, topic_sources)
                if result["is_spike"]:
                    spikes.append(result)

        spikes.sort(key=lambda x: x["z_score"], reverse=True)
        return spikes

    async def update_baseline(self, topic: str, source: str, value: float):
        """
        Update 30-day rolling baseline for a topic/source pair.

        Stores in Redis as JSON: {values: [last 30], mean, stddev, updated_at}
        """
        if not self._cache:
            return

        key = f"spike:baseline:{topic}:{source}"
        try:
            raw = await self._cache.get(key)
            if raw and isinstance(raw, dict):
                baseline = raw
            else:
                baseline = {"values": [], "mean": 0.0, "stddev": 1.0}

            values = baseline.get("values", [])
            values.append(value)
            # Keep last 30 data points
            if len(values) > 30:
                values = values[-30:]

            mean = sum(values) / len(values) if values else 0.0
            variance = sum((v - mean) ** 2 for v in values) / max(len(values), 1)
            stddev = math.sqrt(variance) if variance > 0 else 1.0

            updated = {
                "values": values,
                "mean": round(mean, 4),
                "stddev": round(stddev, 4),
                "updated_at": time.time(),
            }
            await self._cache.set(key, updated, ttl=86400 * 35)  # 35 days TTL
        except Exception as e:
            logger.debug("Spike baseline update error: %s", e)

    async def _get_baseline(self, topic: str, source: str) -> Optional[Dict]:
        """Load baseline from Redis."""
        if not self._cache:
            return None
        try:
            key = f"spike:baseline:{topic}:{source}"
            raw = await self._cache.get(key)
            if raw and isinstance(raw, dict) and "mean" in raw:
                return raw
            return None
        except Exception:
            return None
