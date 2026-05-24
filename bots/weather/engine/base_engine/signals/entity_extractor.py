"""
spaCy NER Entity Extraction — Tier 3 #33

Extract named entities (people, organizations, locations, dates) from
market questions to improve categorization and knowledge graph linking.

Falls back gracefully when spaCy is not installed.
Usage: EntityExtractor().extract(question) -> {"entities": [...], "topics": [...]}
"""
from typing import Dict, List, Any, Optional
from structlog import get_logger

logger = get_logger()

# Mapping from spaCy labels to our semantic categories
_LABEL_MAP = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",       # Geopolitical entity (countries, cities)
    "LOC": "location",
    "DATE": "date",
    "EVENT": "event",
    "NORP": "group",         # Nationalities, religious, political groups
    "MONEY": "financial",
    "PERCENT": "financial",
    "PRODUCT": "product",
    "LAW": "legal",
    "FAC": "facility",
}


class EntityExtractor:
    """
    Extract named entities from market question text using spaCy.

    Falls back to regex-based extraction when spaCy is unavailable.
    """

    def __init__(self, model_name: str = "en_core_web_sm"):
        self._nlp = None
        self._model_name = model_name
        self._available = False
        self._init_nlp()

    def _init_nlp(self):
        """Load spaCy model if available."""
        try:
            import spacy
            self._nlp = spacy.load(self._model_name)
            self._available = True
            logger.info("EntityExtractor initialized with spaCy model %s", self._model_name)
        except ImportError:
            logger.info("spaCy not installed — EntityExtractor uses regex fallback")
        except OSError:
            logger.info(
                "spaCy model '%s' not found. Run: python -m spacy download %s",
                self._model_name, self._model_name,
            )

    @property
    def is_available(self) -> bool:
        return self._available

    def extract(self, text: str) -> Dict[str, Any]:
        """
        Extract entities from text.

        Returns:
            {
                "entities": [{"text": str, "label": str, "category": str, "start": int, "end": int}],
                "topics": [str],  # unique entity texts for keyword matching
                "categories": [str],  # unique semantic categories found
            }
        """
        if not text:
            return {"entities": [], "topics": [], "categories": []}

        if self._nlp:
            return self._extract_spacy(text)
        return self._extract_regex(text)

    def _extract_spacy(self, text: str) -> Dict[str, Any]:
        """Full spaCy NER extraction."""
        doc = self._nlp(text[:1024])  # Limit input length
        entities = []
        seen_texts = set()

        for ent in doc.ents:
            category = _LABEL_MAP.get(ent.label_, "other")
            ent_text = ent.text.strip()
            if len(ent_text) < 2:
                continue
            entities.append({
                "text": ent_text,
                "label": ent.label_,
                "category": category,
                "start": ent.start_char,
                "end": ent.end_char,
            })
            seen_texts.add(ent_text.lower())

        topics = list(set(e["text"] for e in entities))
        categories = list(set(e["category"] for e in entities))

        return {"entities": entities, "topics": topics, "categories": categories}

    def _extract_regex(self, text: str) -> Dict[str, Any]:
        """Regex fallback: extract capitalized phrases as candidate entities."""
        import re

        entities = []
        # Match capitalized multi-word phrases (likely proper nouns)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
            entities.append({
                "text": match.group(1),
                "label": "PROPN",
                "category": "unknown",
                "start": match.start(),
                "end": match.end(),
            })

        # Match all-caps acronyms (e.g., NATO, WHO, GDP)
        for match in re.finditer(r"\b([A-Z]{2,6})\b", text):
            word = match.group(1)
            if word not in ("YES", "NO", "THE", "AND", "FOR", "BUT", "NOT", "ARE", "WAS", "HAS"):
                entities.append({
                    "text": word,
                    "label": "ACRONYM",
                    "category": "organization",
                    "start": match.start(),
                    "end": match.end(),
                })

        topics = list(set(e["text"] for e in entities))
        return {"entities": entities, "topics": topics, "categories": ["unknown"]}

    def extract_market_entities(self, question: str, description: str = "") -> Dict[str, Any]:
        """
        Extract entities from a market's question + description.
        Combines and deduplicates results from both fields.
        """
        q_result = self.extract(question)
        if not description:
            return q_result

        d_result = self.extract(description[:500])

        # Merge, dedup by text
        seen = set()
        merged = []
        for ent in q_result["entities"] + d_result["entities"]:
            key = ent["text"].lower()
            if key not in seen:
                seen.add(key)
                merged.append(ent)

        topics = list(set(e["text"] for e in merged))
        categories = list(set(e["category"] for e in merged))

        return {"entities": merged, "topics": topics, "categories": categories}
