"""
Foresight Learning data generator — creates synthetic prediction questions
from news streams and verifies answers later.

Two data sources:
  1. Resolved questions from the database (prediction_log with known outcomes)
  2. Synthetic questions generated from news articles (verify resolution later)
"""
from __future__ import annotations
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from structlog import get_logger

logger = get_logger()

# Templates for synthetic question generation
TEMPLATES = [
    "Will {entity} {action} by {date}?",
    "Will {metric} be above {threshold} on {date}?",
    "Will {event} happen before {date}?",
]


class ForesightDataGenerator:
    """
    Generates training data for RLVR forecasting models.

    Combines resolved prediction market questions (ground truth) with
    synthetic questions derived from news articles.
    """

    def __init__(self, db=None, news_aggregator=None):
        self._db = db
        self._news = news_aggregator

    async def generate_training_data(
        self,
        num_samples: int = 10000,
        synthetic_ratio: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Generate training dataset of (question, outcome, metadata) tuples.

        Args:
            num_samples: Total number of training samples.
            synthetic_ratio: Fraction that should be synthetic (rest are real resolved).

        Returns:
            List of dicts with keys: question, outcome (0/1), category, resolution_date.
        """
        num_real = int(num_samples * (1.0 - synthetic_ratio))
        num_synthetic = num_samples - num_real

        real_data = await self._load_resolved_questions(limit=num_real)
        synthetic_data = await self._generate_synthetic_questions(limit=num_synthetic)

        combined = real_data + synthetic_data
        random.shuffle(combined)
        return combined

    async def _load_resolved_questions(self, limit: int = 5000) -> List[Dict]:
        """Load resolved prediction market questions from database."""
        if not self._db or not getattr(self._db, "session_factory", None):
            return []
        try:
            from sqlalchemy import text
            async with self._db.get_session() as session:
                r = await session.execute(text("""
                    SELECT m.question, m.resolved_outcome, m.market_category,
                           m.end_date_iso
                    FROM markets m
                    WHERE m.resolved = true
                    AND m.resolved_outcome IS NOT NULL
                    AND m.question IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT :limit
                """), {"limit": limit})
                rows = r.fetchall()
                return [
                    {
                        "question": row[0],
                        "outcome": 1.0 if str(row[1]).lower() in ("yes", "true", "1") else 0.0,
                        "category": row[2] or "",
                        "resolution_date": str(row[3] or ""),
                        "source": "resolved_market",
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning("Failed to load resolved questions: %s", e)
            return []

    async def _generate_synthetic_questions(self, limit: int = 5000) -> List[Dict]:
        """Generate synthetic prediction questions from news articles."""
        if not self._news:
            return []
        questions: List[Dict] = []
        try:
            articles = await self._news.get_recent_articles(limit=min(limit * 2, 1000))
            for article in (articles or []):
                if len(questions) >= limit:
                    break
                title = article.get("title", "")
                if not title or len(title) < 20:
                    continue
                # Create a prediction question from the article
                q = self._article_to_question(title, article)
                if q:
                    questions.append(q)
        except Exception as e:
            logger.debug("Synthetic question generation failed: %s", e)
        return questions

    def _article_to_question(self, title: str, article: Dict) -> Optional[Dict]:
        """Convert a news article title into a prediction question."""
        # Simple heuristic: turn factual statements into "Will X happen?" questions
        title_lower = title.lower().strip()

        # Skip questions that are already questions
        if title_lower.endswith("?"):
            return {
                "question": title,
                "outcome": None,  # Unknown — needs resolution later
                "category": article.get("category", ""),
                "source": "synthetic_news",
            }

        # Convert statement to question
        prefixes = ["will ", "can ", "is ", "are ", "does ", "do "]
        for prefix in prefixes:
            if title_lower.startswith(prefix):
                question = title.rstrip(".!") + "?"
                return {
                    "question": question,
                    "outcome": None,
                    "category": article.get("category", ""),
                    "source": "synthetic_news",
                }

        # Generic conversion
        question = f"Will the following happen: {title.rstrip('.!,')}?"
        return {
            "question": question,
            "outcome": None,
            "category": article.get("category", ""),
            "source": "synthetic_news",
        }
