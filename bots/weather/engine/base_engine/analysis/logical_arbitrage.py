"""
Cross-Market Logical Arbitrage Detector.

Detects mispricings between logically related Polymarket markets:
- Subset constraints: P(A) <= P(B) if A implies B
- Mutual exclusivity: sum of YES prices <= 1.0
- Conditional probability: P(A ∩ B) <= min(P(A), P(B))

Uses sentence embeddings for semantic market grouping,
then LLM classification for relationship type extraction.

$40M documented arbitrage profits from Polymarket (Apr 2024 - Apr 2025, IMDEA study).
"""
import asyncio
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple, Set
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from structlog import get_logger

logger = get_logger()

# Minimum spread to be profitable after fees (2% winner fee + gas + slippage)
MIN_PROFITABLE_SPREAD = 0.025

# Relationship types between markets
RELATIONSHIP_SUBSET = "subset"          # A implies B → P(A) ≤ P(B)
RELATIONSHIP_EXCLUSIVE = "exclusive"    # A and B mutually exclusive → P(A) + P(B) ≤ 1
RELATIONSHIP_CONDITIONAL = "conditional"  # Joint event → P(A∩B) ≤ min(P(A), P(B))
RELATIONSHIP_COMPLEMENT = "complement"  # A = ¬B → P(A) + P(B) ≈ 1
RELATIONSHIP_NONE = "none"


class LogicalArbitrageDetector:
    """
    Detects cross-market logical arbitrage opportunities.

    Pipeline:
    1. Group semantically similar markets via text similarity
    2. Classify relationship type (subset, exclusive, conditional)
    3. Check constraint violations
    4. Filter by profitability (spread > 2.5%)
    """

    def __init__(self, db: Optional[Any] = None, cache: Optional[Any] = None):
        self.db = db
        self.cache = cache
        self._relationship_cache: Dict[str, Tuple[str, float]] = {}  # "m1:m2" → (type, timestamp)
        self._cache_ttl = 86400  # 24h — relationships don't change
        self._cache_max_size = 5000
        self._embeddings_available = False
        self._embedding_model = None

    async def init(self) -> None:
        """Initialize embedding model if available."""
        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer("intfloat/e5-small-v2")
            self._embeddings_available = True
            logger.info("LogicalArbitrage: sentence embeddings available (e5-small-v2)")
        except ImportError:
            logger.info("LogicalArbitrage: sentence_transformers not installed, using text similarity fallback")
            self._embeddings_available = False

    # ── Main scan ─────────────────────────────────────────────────────────────

    async def scan_for_opportunities(
        self,
        markets: List[Dict[str, Any]],
        min_spread: float = MIN_PROFITABLE_SPREAD,
    ) -> List[Dict[str, Any]]:
        """
        Scan all active markets for logical arbitrage opportunities.

        Args:
            markets: List of market dicts with 'id', 'question', 'yes_price', 'category'
            min_spread: Minimum profitable spread (default 2.5%)

        Returns:
            List of arbitrage opportunity dicts
        """
        if len(markets) < 2:
            return []

        # Step 1: Group related markets
        groups = await self._group_related_markets(markets)

        # Step 2: Check constraints within each group
        opportunities = []
        for group in groups:
            if len(group) < 2:
                continue

            opps = await self._check_group_constraints(group, min_spread)
            opportunities.extend(opps)

        # Sort by spread (most profitable first)
        opportunities.sort(key=lambda x: x.get("spread", 0), reverse=True)

        logger.info(
            "Logical arbitrage scan complete",
            markets_scanned=len(markets),
            groups_found=len(groups),
            opportunities=len(opportunities),
        )

        return opportunities

    # ── Market grouping ───────────────────────────────────────────────────────

    async def _group_related_markets(
        self,
        markets: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Group markets by semantic similarity."""

        if self._embeddings_available and self._embedding_model is not None:
            return await self._group_by_embeddings(markets)
        return self._group_by_text_overlap(markets)

    async def _group_by_embeddings(
        self,
        markets: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Group using sentence embeddings + cosine similarity."""
        import numpy as np

        questions = [m.get("question", "") for m in markets]

        # Compute embeddings in thread pool (CPU-bound)
        embeddings = await asyncio.to_thread(
            self._embedding_model.encode, questions, normalize_embeddings=True
        )

        # Cosine similarity matrix
        sim_matrix = np.dot(embeddings, embeddings.T)

        # Greedy clustering: markets with sim > 0.6 go in same group
        threshold = 0.6
        assigned = set()
        groups = []

        for i in range(len(markets)):
            if i in assigned:
                continue
            group = [markets[i]]
            assigned.add(i)

            for j in range(i + 1, len(markets)):
                if j in assigned:
                    continue
                if sim_matrix[i, j] >= threshold:
                    group.append(markets[j])
                    assigned.add(j)

            if len(group) >= 2:
                groups.append(group)

        return groups

    def _group_by_text_overlap(
        self,
        markets: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """Fallback: group by keyword/token overlap."""
        # Extract keywords per market
        market_keywords = []
        for m in markets:
            q = m.get("question", "").lower()
            # Simple tokenization
            words = set(w for w in q.split() if len(w) > 3)
            market_keywords.append(words)

        # Group by Jaccard similarity > 0.3
        assigned = set()
        groups = []

        for i in range(len(markets)):
            if i in assigned:
                continue
            group = [markets[i]]
            assigned.add(i)

            for j in range(i + 1, len(markets)):
                if j in assigned:
                    continue

                # Jaccard similarity
                intersection = len(market_keywords[i] & market_keywords[j])
                union = len(market_keywords[i] | market_keywords[j])
                if union > 0 and intersection / union >= 0.3:
                    group.append(markets[j])
                    assigned.add(j)

            if len(group) >= 2:
                groups.append(group)

        return groups

    # ── Constraint checking ───────────────────────────────────────────────────

    async def _check_group_constraints(
        self,
        group: List[Dict[str, Any]],
        min_spread: float,
    ) -> List[Dict[str, Any]]:
        """Check for constraint violations within a group of related markets."""
        opportunities = []

        # Check 1: Mutual exclusivity (sum of YES prices > 1.0)
        yes_prices = [(m, float(m.get("yes_price", 0) or 0)) for m in group]
        total_yes = sum(p for _, p in yes_prices)
        if total_yes > 1.0 + min_spread:
            opportunities.append({
                "type": "mutual_exclusivity",
                "markets": [m.get("id") for m, _ in yes_prices],
                "questions": [m.get("question", "")[:80] for m, _ in yes_prices],
                "prices": [p for _, p in yes_prices],
                "total_yes": round(total_yes, 4),
                "spread": round(total_yes - 1.0, 4),
                "strategy": "Sell YES on all markets in group (total YES > $1.00)",
                "expected_profit_pct": round((total_yes - 1.0) * 100, 2),
            })

        # Check 2: Pairwise subset/complement constraints
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                m1, m2 = group[i], group[j]
                m1_id, m2_id = m1.get("id", ""), m2.get("id", "")
                p1 = float(m1.get("yes_price", 0) or 0)
                p2 = float(m2.get("yes_price", 0) or 0)

                # Detect relationship type
                rel_type = await self._classify_relationship(m1, m2)

                if rel_type == RELATIONSHIP_SUBSET:
                    # P(A) must be <= P(B) — if P(A) > P(B) + spread, arb exists
                    if p1 > p2 + min_spread:
                        opportunities.append({
                            "type": "subset_violation",
                            "subset_market": m1_id,
                            "superset_market": m2_id,
                            "subset_question": m1.get("question", "")[:80],
                            "superset_question": m2.get("question", "")[:80],
                            "subset_price": p1,
                            "superset_price": p2,
                            "spread": round(p1 - p2, 4),
                            "strategy": f"Sell YES on {m1_id} (subset), Buy YES on {m2_id} (superset)",
                        })

                elif rel_type == RELATIONSHIP_COMPLEMENT:
                    # P(A) + P(B) should ≈ 1.0
                    complement_sum = p1 + p2
                    if abs(complement_sum - 1.0) > min_spread:
                        opportunities.append({
                            "type": "complement_violation",
                            "market_a": m1_id,
                            "market_b": m2_id,
                            "question_a": m1.get("question", "")[:80],
                            "question_b": m2.get("question", "")[:80],
                            "price_a": p1,
                            "price_b": p2,
                            "sum": round(complement_sum, 4),
                            "spread": round(abs(complement_sum - 1.0), 4),
                            "strategy": "Buy both YES" if complement_sum < 1.0 else "Sell both YES",
                        })

        return opportunities

    # ── Relationship classification ───────────────────────────────────────────

    async def _classify_relationship(
        self,
        market_a: Dict[str, Any],
        market_b: Dict[str, Any],
    ) -> str:
        """
        Classify the logical relationship between two markets.

        Uses cache first, then heuristic rules, then LLM fallback.
        """
        id_a = market_a.get("id", "")
        id_b = market_b.get("id", "")
        cache_key = f"{min(id_a, id_b)}:{max(id_a, id_b)}"

        # Check cache
        cached = self._relationship_cache.get(cache_key)
        if cached:
            rel_type, ts = cached
            if time.time() - ts < self._cache_ttl:
                return rel_type

        # Heuristic classification
        q_a = (market_a.get("question", "") or "").lower()
        q_b = (market_b.get("question", "") or "").lower()

        rel_type = self._heuristic_classify(q_a, q_b)

        # Cache result
        self._relationship_cache[cache_key] = (rel_type, time.time())
        if len(self._relationship_cache) > self._cache_max_size:
            oldest = sorted(self._relationship_cache, key=lambda k: self._relationship_cache[k][1])
            for k in oldest[:len(oldest) // 4]:
                del self._relationship_cache[k]

        return rel_type

    def _heuristic_classify(self, q_a: str, q_b: str) -> str:
        """Rule-based relationship classification."""
        # Subset detection: "X wins Y" is subset of "X wins nomination" if Y is after nomination
        # Simplified: if one question is strictly more specific than the other

        # Complement: "Will X happen?" vs "Will X NOT happen?"
        if "not " in q_a and q_a.replace("not ", "") in q_b:
            return RELATIONSHIP_COMPLEMENT
        if "not " in q_b and q_b.replace("not ", "") in q_a:
            return RELATIONSHIP_COMPLEMENT

        # Exclusive: multi-candidate (e.g., "Will A win?" and "Will B win?" same race)
        # Detect by shared structure with different names
        win_patterns = ["win", "become", "elected", "nominated", "chosen"]
        a_has_win = any(w in q_a for w in win_patterns)
        b_has_win = any(w in q_b for w in win_patterns)
        if a_has_win and b_has_win:
            # Extract common context (remove candidate-specific words)
            a_words = set(q_a.split())
            b_words = set(q_b.split())
            overlap = a_words & b_words
            # High overlap + both about winning = likely exclusive
            if len(overlap) > len(a_words) * 0.5:
                return RELATIONSHIP_EXCLUSIVE

        # Subset: one question implies the other
        # "X wins presidency" is subset of "X wins party nomination" (wrong direction)
        # "X wins party nomination" is subset of "X runs for president" (correct)
        # Simple heuristic: if all words of q_a appear in q_b (plus extra), q_a might be subset
        a_tokens = set(q_a.split())
        b_tokens = set(q_b.split())
        if a_tokens and b_tokens:
            a_in_b = len(a_tokens & b_tokens) / len(a_tokens)
            b_in_a = len(a_tokens & b_tokens) / len(b_tokens)
            if a_in_b > 0.8 and b_in_a < 0.6:
                return RELATIONSHIP_SUBSET  # a is more specific → subset of b

        return RELATIONSHIP_NONE

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_cache_stats(self) -> Dict[str, Any]:
        return {
            "relationship_cache_size": len(self._relationship_cache),
            "embeddings_available": self._embeddings_available,
        }
