"""
Polling Data Client — VoteHub API + FiveThirtyEight archive.

Fetches political polling data and computes aggregated poll averages
for use as features in the prediction engine.

Sources:
- VoteHub API (free REST, comprehensive pollster data)
- FiveThirtyEight GitHub archive (CC-BY-4.0, historical polls + model outputs)
"""
import asyncio
import csv
import io
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from structlog import get_logger

logger = get_logger()

VOTEHUB_BASE = "https://api.votehub.com/v1"
FTE_POLLS_URL = "https://raw.githubusercontent.com/fivethirtyeight/data/master/polls"


class PollingClient:
    """
    Aggregates polling data from VoteHub and FiveThirtyEight.

    Provides weighted poll averages for political prediction markets,
    accounting for recency, sample size, and pollster quality.
    """

    def __init__(self, votehub_api_key: Optional[str] = None):
        import os
        self._votehub_key = votehub_api_key or os.getenv("VOTEHUB_API_KEY", "")
        self._client = None
        self._poll_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 3600  # 1 hour
        self._pollster_ratings: Dict[str, float] = {}
        self._running = False

    @property
    def is_available(self) -> bool:
        return bool(self._votehub_key)

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=20.0)

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── VoteHub API ───────────────────────────────────────────────────────────

    async def fetch_polls_votehub(
        self,
        race_type: str = "president",
        state: Optional[str] = None,
        days_back: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent polls from VoteHub API.

        Args:
            race_type: president, senate, house, governor
            state: Two-letter state code (None = national)
            days_back: How far back to fetch
        """
        if not self._votehub_key:
            return []

        await self._ensure_client()
        polls = []

        try:
            params = {
                "api_key": self._votehub_key,
                "race_type": race_type,
                "days_back": days_back,
                "format": "json",
            }
            if state:
                params["state"] = state

            url = f"{VOTEHUB_BASE}/polls"
            resp = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()

            for poll in data.get("polls", []):
                polls.append({
                    "pollster": poll.get("pollster", "Unknown"),
                    "sample_size": int(poll.get("sample_size", 0) or 0),
                    "population": poll.get("population", "lv"),  # lv, rv, a
                    "start_date": poll.get("start_date", ""),
                    "end_date": poll.get("end_date", ""),
                    "candidates": poll.get("candidates", {}),
                    "margin_of_error": float(poll.get("margin_of_error", 3.0) or 3.0),
                    "partisan": poll.get("partisan", "nonpartisan"),
                    "state": poll.get("state", "national"),
                    "race_type": race_type,
                    "source": "votehub",
                })

        except asyncio.TimeoutError:
            logger.debug("VoteHub API timeout")
        except Exception as e:
            logger.debug("VoteHub poll fetch failed: %s", e)

        return polls

    # ── FiveThirtyEight Archive ───────────────────────────────────────────────

    async def fetch_polls_fte(
        self,
        race_type: str = "president",
        cycle: int = 2026,
    ) -> List[Dict[str, Any]]:
        """
        Fetch polling data from FiveThirtyEight GitHub archive.

        Args:
            race_type: president_polls, senate_polls, house_polls, governor_polls
            cycle: Election cycle year
        """
        await self._ensure_client()
        polls = []

        try:
            filename = f"{race_type}_polls.csv"
            url = f"{FTE_POLLS_URL}/{filename}"
            resp = await asyncio.wait_for(
                self._client.get(url), timeout=30.0
            )
            if resp.status_code != 200:
                logger.debug("FTE archive not available: %s → %d", filename, resp.status_code)
                return []

            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                try:
                    poll_cycle = int(row.get("cycle", 0) or 0)
                    if poll_cycle != cycle:
                        continue

                    polls.append({
                        "pollster": row.get("pollster", "Unknown"),
                        "sample_size": int(row.get("sample_size", 0) or 0),
                        "population": row.get("population", "lv"),
                        "start_date": row.get("start_date", ""),
                        "end_date": row.get("end_date", ""),
                        "candidate_name": row.get("candidate_name", ""),
                        "pct": float(row.get("pct", 0) or 0),
                        "state": row.get("state", ""),
                        "fte_grade": row.get("fte_grade", ""),
                        "partisan": row.get("partisan", ""),
                        "race_type": race_type,
                        "source": "fivethirtyeight",
                    })
                except (ValueError, TypeError):
                    continue

        except asyncio.TimeoutError:
            logger.debug("FTE archive fetch timeout")
        except Exception as e:
            logger.debug("FTE archive fetch failed: %s", e)

        return polls

    # ── Poll Aggregation ──────────────────────────────────────────────────────

    def aggregate_polls(
        self,
        polls: List[Dict[str, Any]],
        candidate: str = "",
        recency_lambda: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Compute weighted poll average using recency, sample size, and quality.

        Implements simplified version of the FiveThirtyEight weighting:
        - Recency: exp(-lambda * days_old)
        - Sample size: sqrt(n), capped at sqrt(1500)
        - Population: LV=1.0, RV=0.85, A=0.7
        - Partisan discount: 0.6× for partisan polls

        Returns:
            weighted_average: float (0-100 scale)
            poll_count: int
            effective_sample: float
            agreement: float (std dev across pollsters — lower = more agreement)
        """
        if not polls:
            return {"weighted_average": 50.0, "poll_count": 0, "effective_sample": 0, "agreement": 0.0}

        now = datetime.now(timezone.utc)
        population_weights = {"lv": 1.0, "rv": 0.85, "a": 0.7}
        max_sample_sqrt = 1500 ** 0.5

        weighted_sum = 0.0
        weight_total = 0.0
        raw_values = []

        for poll in polls:
            # Get candidate percentage
            pct = poll.get("pct", 0)
            if not pct and candidate:
                candidates = poll.get("candidates", {})
                pct = float(candidates.get(candidate, 0) or 0)
            if not pct:
                continue

            pct = float(pct)
            raw_values.append(pct)

            # Recency weight
            end_date_str = poll.get("end_date", "")
            days_old = 30  # default
            if end_date_str:
                try:
                    from dateutil.parser import parse as parse_date
                    end_date = parse_date(end_date_str)
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                    days_old = max(0, (now - end_date).days)
                except Exception:
                    pass

            import math
            recency_w = math.exp(-recency_lambda * days_old)

            # Sample size weight (sqrt, capped)
            sample_size = max(1, poll.get("sample_size", 0) or 1)
            sample_w = min(sample_size ** 0.5, max_sample_sqrt)

            # Population weight
            pop = str(poll.get("population", "a")).lower()
            pop_w = population_weights.get(pop, 0.7)

            # Partisan discount
            partisan = str(poll.get("partisan", "")).lower()
            partisan_w = 0.6 if partisan and partisan != "nonpartisan" else 1.0

            # Composite weight
            weight = recency_w * sample_w * pop_w * partisan_w
            weighted_sum += pct * weight
            weight_total += weight

        if weight_total == 0:
            return {"weighted_average": 50.0, "poll_count": 0, "effective_sample": 0, "agreement": 0.0}

        import numpy as np
        weighted_avg = weighted_sum / weight_total
        agreement = float(np.std(raw_values)) if len(raw_values) > 1 else 0.0

        return {
            "weighted_average": round(weighted_avg, 2),
            "poll_count": len(raw_values),
            "effective_sample": round(weight_total, 1),
            "agreement": round(agreement, 2),
        }

    # ── Market Signal Generation ──────────────────────────────────────────────

    async def get_poll_signal_for_market(
        self,
        market_question: str,
        market_price: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a trading signal based on poll-market divergence.

        If polling aggregate differs significantly from market price,
        that's the core alpha signal.

        Returns signal dict or None if no meaningful divergence.
        """
        cache_key = f"poll:{hash(market_question)}"
        cached = self._poll_cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < self._cache_ttl:
            return cached.get("signal")

        # Extract race info from question (simplified)
        question_lower = market_question.lower()
        race_type = "president"
        if any(w in question_lower for w in ["senate", "senator"]):
            race_type = "senate"
        elif any(w in question_lower for w in ["house", "representative", "congress"]):
            race_type = "house"
        elif any(w in question_lower for w in ["governor"]):
            race_type = "governor"

        # Fetch polls from available sources
        all_polls = []
        if self._votehub_key:
            polls = await self.fetch_polls_votehub(race_type=race_type, days_back=30)
            all_polls.extend(polls)

        fte_polls = await self.fetch_polls_fte(race_type=race_type)
        all_polls.extend(fte_polls)

        if not all_polls:
            return None

        # Aggregate
        agg = self.aggregate_polls(all_polls)
        polling_prob = agg["weighted_average"] / 100.0  # Convert to 0-1

        # Divergence = polling model probability - market price
        divergence = polling_prob - market_price

        signal = None
        # Only generate signal if divergence exceeds threshold
        min_divergence = 0.05  # 5 percentage points
        if abs(divergence) >= min_divergence:
            signal = {
                "source_type": "polling",
                "source_name": "polling_aggregate",
                "direction": "YES" if divergence > 0 else "NO",
                "confidence": min(0.85, abs(divergence) * 2.0),
                "polling_prob": round(polling_prob, 4),
                "market_price": round(market_price, 4),
                "divergence": round(divergence, 4),
                "poll_count": agg["poll_count"],
                "pollster_agreement": agg["agreement"],
                "raw_text": f"Poll avg {polling_prob:.1%} vs market {market_price:.1%} ({divergence:+.1%} divergence, {agg['poll_count']} polls)",
                "time_sensitivity": "hours",
                "is_breaking": False,
            }

        self._poll_cache[cache_key] = {"signal": signal, "ts": time.time()}
        # Cap cache size
        if len(self._poll_cache) > 500:
            oldest = sorted(self._poll_cache, key=lambda k: self._poll_cache[k].get("ts", 0))
            for k in oldest[:100]:
                del self._poll_cache[k]

        return signal
