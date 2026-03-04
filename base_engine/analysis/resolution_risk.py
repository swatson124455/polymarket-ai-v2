"""
Resolution Risk Analyzer
========================
Analyzes markets for resolution risk.
Avoids markets with ambiguous resolution criteria.
"""
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from structlog import get_logger
from base_engine.data.database import Database, Market

logger = get_logger()

# Default cache TTL for LLM clarity scores (seconds). Markets rarely change resolution criteria.
_CLARITY_CACHE_TTL = 86400  # 24 hours
_CLARITY_CACHE_MAX = 2000


class ResolutionRiskLevel(Enum):
    """Resolution risk levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResolutionRiskAnalyzer:
    """
    Analyzes risk of resolution disputes or delays.
    Score: 0 (high risk) to 1 (low risk)
    """
    
    # Known problematic patterns
    AMBIGUOUS_PHRASES = [
        r"at the discretion",
        r"as determined by",
        r"may be resolved",
        r"subject to interpretation",
        r"reasonable judgment",
        r"approximately",
        r"around",
        r"roughly",
        r"about",
        r"or similar",
        r"as deemed",
    ]
    
    # Reliable resolution sources
    RELIABLE_SOURCES = {
        "associated press": 0.98,
        "reuters": 0.97,
        "official government": 0.95,
        "sec.gov": 0.99,
        "sec filing": 0.99,
        "sports leagues": 0.99,
        "company announcement": 0.90,
        "court": 0.85,
        "ballotpedia": 0.95,
    }
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        # Bounded in-memory clarity score cache: market_id → (score: float, ts: datetime)
        # FIFO eviction at _CLARITY_CACHE_MAX entries; TTL checked on read.
        self._clarity_cache: "OrderedDict[str, Tuple[float, datetime]]" = OrderedDict()
        # Singleton AsyncAnthropic client (created lazily on first LLM call)
        self._anthropic_client: Optional[Any] = None
    
    async def analyze_resolution_risk(self, market: Market) -> Dict:
        """
        Comprehensive resolution risk analysis.
        
        Args:
            market: Market object from database
        
        Returns:
            Dict with:
                - risk_score: 0.0 (high risk) to 1.0 (low risk)
                - risk_level: "low", "medium", "high"
                - criteria_clarity: 0.0-1.0
                - source_reliability: 0.0-1.0
                - time_certainty: 0.0-1.0
                - red_flags: List of specific issues
                - recommendation: "TRADE", "CAUTION", or "AVOID"
        """
        description = market.description or ""
        resolution_source = market.resolution_source or ""
        
        # 1. Check for ambiguous language
        ambiguity_score = self._analyze_criteria(description)
        
        # 2. Check resolution source reliability
        source_reliability = self._check_source_reliability(resolution_source)
        
        # 3. Check time clarity
        time_clarity = self._check_time_clarity(market)
        
        # 4. Composite risk score
        risk_score = (
            ambiguity_score * 0.35 +
            source_reliability * 0.30 +
            time_clarity * 0.20 +
            0.15  # Base score (assume some risk)
        )
        
        # Normalize to 0-1 (higher = lower risk)
        risk_score = max(0.0, min(1.0, risk_score))
        
        # Invert for risk_score (higher risk_score = higher risk)
        risk_score = 1.0 - risk_score
        
        # Determine risk level
        if risk_score > 0.5:
            risk_level = ResolutionRiskLevel.HIGH
            recommendation = "AVOID"
        elif risk_score > 0.3:
            risk_level = ResolutionRiskLevel.MEDIUM
            recommendation = "CAUTION"
        else:
            risk_level = ResolutionRiskLevel.LOW
            recommendation = "TRADE"
        
        # Get red flags
        red_flags = self._identify_red_flags(market, description, resolution_source)
        
        return {
            "market_id": market.id,
            "risk_score": risk_score,
            "risk_level": risk_level.value,
            "criteria_clarity": ambiguity_score,
            "source_reliability": source_reliability,
            "time_certainty": time_clarity,
            "red_flags": red_flags,
            "recommendation": recommendation,
            "analysis": {
                "description_length": len(description),
                "has_resolution_source": bool(resolution_source),
                "has_end_date": bool(market.end_date_iso),
            }
        }
    
    def _analyze_criteria(self, description: str) -> float:
        """
        Score resolution criteria clarity (0-1).
        Higher score = clearer criteria.
        """
        if not description:
            return 0.0
        
        score = 1.0
        
        # Check for ambiguous language
        description_lower = description.lower()
        for pattern in self.AMBIGUOUS_PHRASES:
            if re.search(pattern, description_lower):
                score -= 0.15
        
        # Check for specific, measurable criteria
        if re.search(r"\d+", description):  # Contains numbers
            score += 0.1
        
        if re.search(r"(before|after|by)\s+\w+\s+\d{1,2},?\s+\d{4}", description):  # Specific date
            score += 0.1
        
        if re.search(r"(yes|no|true|false|win|lose)", description_lower):  # Binary outcome
            score += 0.1
        
        return max(0.0, min(1.0, score))
    
    def _check_source_reliability(self, resolution_source: str) -> float:
        """
        Check resolution source reliability (0-1).
        Higher score = more reliable source.
        """
        if not resolution_source:
            return 0.3  # Unknown source = medium risk
        
        source_lower = resolution_source.lower()
        
        for reliable_source, reliability in self.RELIABLE_SOURCES.items():
            if reliable_source in source_lower:
                return reliability
        
        # Default for unknown sources
        return 0.5
    
    def _check_time_clarity(self, market: Market) -> float:
        """
        Check time-to-resolution certainty (0-1).
        Higher score = more certain timing.
        """
        if not market.end_date_iso:
            return 0.2  # No end date = high uncertainty
        
        # If we have an end date, that's good
        score = 0.8
        
        # Could add more checks here (e.g., is it a specific date vs. "TBD")
        
        return score
    
    def _identify_red_flags(self, market: Market, description: str, resolution_source: str) -> List[str]:
        """Identify specific red flags"""
        flags = []
        
        description_lower = (description or "").lower()
        source_lower = (resolution_source or "").lower()
        
        if "discretion" in description_lower:
            flags.append("DISCRETIONARY_RESOLUTION")
        
        if not resolution_source:
            flags.append("NO_RESOLUTION_SOURCE")
        
        if not market.end_date_iso:
            flags.append("NO_END_DATE")
        
        if len(description) < 100:
            flags.append("VAGUE_DESCRIPTION")
        
        if "approximately" in description_lower or "around" in description_lower:
            flags.append("IMPRECISE_TIMING")
        
        if "may" in description_lower and "resolve" in description_lower:
            flags.append("CONDITIONAL_RESOLUTION")
        
        return flags
    
    async def analyze_llm_clarity(self, market: Market) -> float:
        """Use LLM to score resolution clarity (0=ambiguous, 1=crystal clear).

        Blends 60% LLM / 40% regex scoring. Falls back to regex-only on LLM failure.
        Results are cached for _CLARITY_CACHE_TTL seconds (default 24h) to avoid
        repeated API calls for the same market across scan cycles.
        """
        mid = str(getattr(market, "id", "") or "")
        regex_score = self._analyze_criteria(market.description or "")

        # --- Cache check ---
        cached = self._clarity_cache.get(mid)
        if cached is not None:
            score, ts = cached
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            ttl = float(os.getenv("RESOLUTION_CLARITY_CACHE_TTL_HOURS", "24")) * 3600
            if age < ttl:
                return score
            # Expired — remove so FIFO order stays accurate
            self._clarity_cache.pop(mid, None)

        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                return regex_score

            # Singleton client — avoids creating a new HTTP connection per call
            if self._anthropic_client is None:
                self._anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)

            # Use question (most informative) with description as supplemental context
            question = (market.question or market.description or "")[:400]
            description_snippet = (market.description or "")[:300] if market.description and market.description != market.question else ""
            resolution_source = (market.resolution_source or "none")[:150]

            user_content = (
                f"Question: {question}\n"
                + (f"Context: {description_snippet}\n" if description_snippet else "")
                + f"Resolution source: {resolution_source}\n"
                + "Reply with ONLY a JSON object: {\"clarity\": <0.0-1.0>}"
            )

            resp = await self._anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20,
                system=[
                    {
                        "type": "text",
                        "text": (
                            "You rate the resolution clarity of prediction market questions on a scale "
                            "from 0.0 to 1.0. 0.0 = completely ambiguous (subjective, no objective criteria, "
                            "could be disputed). 0.5 = somewhat clear with edge cases. "
                            "1.0 = crystal clear (objective, measurable, binary outcome). "
                            "Reply ONLY with JSON: {\"clarity\": <float>}"
                        ),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
            text = resp.content[0].text.strip()
            # Parse JSON or bare float
            import json as _json
            try:
                llm_score = float(_json.loads(text).get("clarity", 0.5))
            except Exception:
                llm_score = float(re.search(r"[\d.]+", text).group())  # type: ignore[union-attr]
            llm_score = max(0.0, min(1.0, llm_score))
            blended = 0.6 * llm_score + 0.4 * regex_score

        except Exception as e:
            logger.debug("LLM clarity scoring failed, using regex only: %s", e)
            blended = regex_score

        # --- Cache result (FIFO eviction at max size) ---
        if mid:
            if len(self._clarity_cache) >= _CLARITY_CACHE_MAX:
                self._clarity_cache.popitem(last=False)
            self._clarity_cache[mid] = (blended, datetime.now(timezone.utc))

        return blended

    async def analyze_markets(self, market_ids: Optional[List[str]] = None, limit: int = 100) -> List[Dict]:
        """
        Analyze resolution risk for multiple markets.
        
        Args:
            market_ids: Optional list of specific market IDs. If None, analyzes top markets by liquidity.
            limit: Maximum number of markets to analyze
        
        Returns:
            List of risk analysis dicts, sorted by risk_score (highest risk first)
        """
        if not self.db or not self.db.session_factory:
            return []
        
        async with self.db.get_session() as session:
            from sqlalchemy import select
            
            if market_ids:
                result = await session.execute(
                    select(Market).where(Market.id.in_(market_ids))
                )
            else:
                result = await session.execute(
                    select(Market)
                    .where(Market.active == True)
                    .order_by(Market.liquidity.desc())
                    .limit(limit)
                )
            
            markets = result.scalars().all()
            
            analyses = []
            for market in markets:
                try:
                    analysis = await self.analyze_resolution_risk(market)
                    analyses.append(analysis)
                except Exception as e:
                    logger.warning(f"Failed to analyze resolution risk for {market.id}: {str(e)}")
                    continue
            
            # Sort by risk_score (highest risk first)
            analyses.sort(key=lambda x: x["risk_score"], reverse=True)
            
            return analyses
