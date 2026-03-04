"""
3-Tier NLP Injury Classifier for sports news text.

Tier 1 (regex, always runs): pattern-match against known injury/status phrases.
  — Returns InjuryEvent with confidence >= 0.70 when matched.
  — If confidence < 0.70, escalates to Tier 2.

Tier 2 (spaCy NER, when T1 conf < 0.70 and INJURY_NLP_TIER != "regex"):
  — Uses en_core_web_sm to extract player entity.
  — If still < 0.70, escalates to Tier 3.

Tier 3 (LLM, when T2 conf < 0.70 and INJURY_NLP_TIER == "llm"):
  — Uses anthropic Claude to classify status + extract player name.
  — Gate: INJURY_LLM_CONFIDENCE_THRESHOLD (default 0.70).
  — Cost: $0.01–0.05/call. Only on genuine ambiguous text.

All tiers return Optional[InjuryEvent] from sports.data.injury_store.
Returns None if no injury/news is detected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from structlog import get_logger

from sports.data.injury_store import InjuryEvent

logger = get_logger()


# ─── Tier 1: Regex Patterns ───────────────────────────────────────────────────

# Maps (regex pattern, detected_status, severity, base_confidence)
# Ordered: highest confidence patterns first
_INJURY_PATTERNS: List[Tuple[re.Pattern, str, str, float]] = [
    # Season-ending
    (re.compile(r"\bseason[- ]ending\b", re.I), "out", "season_ending", 0.95),
    (re.compile(r"\btorn\s+[A-Z]{2,3}\b", re.I), "out", "season_ending", 0.93),
    (re.compile(r"\bplaced\s+on\s+(?:the\s+)?IL\b", re.I), "out", "multi_week", 0.92),
    (re.compile(r"\bplaced\s+on\s+(?:the\s+)?IR\b", re.I), "out", "season_ending", 0.92),
    (re.compile(r"\bon\s+(?:the\s+)?IR\b", re.I), "out", "season_ending", 0.90),
    (re.compile(r"\bon\s+(?:the\s+)?IL\b", re.I), "out", "multi_week", 0.90),

    # Hard outs
    (re.compile(r"\bruled\s+out\b", re.I), "out", "day-to-day", 0.95),
    (re.compile(r"\bwill\s+not\s+play\b", re.I), "out", "day-to-day", 0.93),
    (re.compile(r"\bDNP\b"), "out", "day-to-day", 0.92),
    (re.compile(r"\bscratched\b", re.I), "out", "day-to-day", 0.90),
    (re.compile(r"\blisted\s+(?:as\s+)?out\b", re.I), "out", "day-to-day", 0.90),
    (re.compile(r"\bout\s+(?:for\s+)?(?:tonight|today|this\s+(?:game|week))\b", re.I), "out", "day-to-day", 0.88),
    (re.compile(r"\bsidelined\b", re.I), "out", "multi_week", 0.85),

    # Starting pitcher / goalie specific
    (re.compile(r"\b(?:SP|starter)\s+scratch(?:ed)?\b", re.I), "sp_scratch", "day-to-day", 0.93),
    (re.compile(r"\bgoalie\s+(?:change|swap|scratch(?:ed)?)\b", re.I), "goalie_swap", "day-to-day", 0.93),
    (re.compile(r"\bgoalie\s+pulled\b", re.I), "goalie_swap", "day-to-day", 0.90),
    (re.compile(r"\bstarting\s+goalie\s+(?:is\s+)?(?:out|scratched|changed)\b", re.I), "goalie_swap", "day-to-day", 0.92),

    # Doubtful
    (re.compile(r"\bdoubtful\b", re.I), "doubtful", "day-to-day", 0.88),
    (re.compile(r"\bunlikely\s+to\s+play\b", re.I), "doubtful", "day-to-day", 0.85),
    (re.compile(r"\bnot\s+expected\s+to\s+practice\b", re.I), "doubtful", "day-to-day", 0.83),

    # Questionable
    (re.compile(r"\bquestionable\b", re.I), "questionable", "day-to-day", 0.80),
    (re.compile(r"\bday[- ]to[- ]day\b", re.I), "day-to-day", "day-to-day", 0.78),
    (re.compile(r"\bDTD\b"), "day-to-day", "day-to-day", 0.78),
    (re.compile(r"\blimited\s+(?:in\s+)?practice\b", re.I), "questionable", "day-to-day", 0.72),

    # NFL offseason / free agency
    (re.compile(r"\bagrees?\s+to\s+(?:a\s+)?terms?\b", re.I), "free_agent_move", "offseason_move", 0.90),
    (re.compile(r"\bsigns?\s+with\b", re.I), "free_agent_move", "offseason_move", 0.90),
    (re.compile(r"\breleased\s+by\b", re.I), "free_agent_move", "offseason_move", 0.88),
    (re.compile(r"\bfree\s+agent\b", re.I), "free_agent_move", "offseason_move", 0.82),
    (re.compile(r"\btraded\s+to\b", re.I), "free_agent_move", "offseason_move", 0.88),

    # Tennis withdrawal / retirement
    (re.compile(r"\bretir(?:ed?|ing)\b", re.I), "retirement", "season_ending", 0.92),
    (re.compile(r"\bwithdraw[sn]?\b", re.I), "withdrawal", "day-to-day", 0.92),
    (re.compile(r"\bwithdrawal\b", re.I), "withdrawal", "day-to-day", 0.93),
    (re.compile(r"\bpulled\s+out\b", re.I), "withdrawal", "day-to-day", 0.85),
    (re.compile(r"\bwalkover\b", re.I), "withdrawal", "day-to-day", 0.90),
    (re.compile(r"\bWO\b"), "withdrawal", "day-to-day", 0.80),

    # Generic injury language (low confidence — escalate to T2)
    (re.compile(r"\binjur(?:ed|y)\b", re.I), "questionable", "day-to-day", 0.60),
    (re.compile(r"\bsore\b", re.I), "questionable", "day-to-day", 0.55),
    (re.compile(r"\bpain\b", re.I), "questionable", "day-to-day", 0.50),
]

# Regex to extract player-like names (Title Case words including CamelCase like LeBron/DeAngelo)
_PLAYER_NAME_RE = re.compile(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,3})\b")

# Sports keywords for sport detection fallback
_SPORT_KEYWORDS: Dict[str, List[str]] = {
    "nba": ["nba", "basketball", "lakers", "celtics", "warriors", "heat", "bucks", "knicks", "nets"],
    "nfl": ["nfl", "football", "chiefs", "eagles", "cowboys", "patriots", "packers", "49ers"],
    "mlb": ["mlb", "baseball", "yankees", "dodgers", "astros", "braves", "mets", "cubs"],
    "nhl": ["nhl", "hockey", "rangers", "bruins", "maple leafs", "avalanche", "lightning"],
    "soccer": ["soccer", "football", "epl", "premier league", "la liga", "bundesliga", "champions league", "fc "],
    "tennis": ["tennis", "atp", "wta", "wimbledon", "us open", "french open", "australian open"],
}


# ─── Public Entry Point ───────────────────────────────────────────────────────

async def detect_injury(raw_item: Dict) -> Optional[InjuryEvent]:
    """
    Classify a raw news item and return an InjuryEvent or None.

    Args:
        raw_item: Dict from queue with keys: source, source_id, text, url,
                  and optionally: sport, author_id.

    Returns:
        InjuryEvent if injury/news detected, None otherwise.
    """
    from config.settings import settings

    text   = raw_item.get("text", "")
    source = raw_item.get("source", "unknown")
    url    = raw_item.get("url", "")
    sport  = raw_item.get("sport", "")

    if not text:
        return None

    nlp_tier_setting = getattr(settings, "INJURY_NLP_TIER", "regex").lower()

    # ── Tier 1: Regex ─────────────────────────────────────────────────────────
    t1_result, t1_conf = _tier1_regex(text, sport)

    if t1_result is None:
        return None  # No injury language found at all

    detected_status = t1_result["status"]
    severity        = t1_result["severity"]
    player_raw      = _extract_player_name_regex(text)
    confidence      = t1_conf

    # If T1 confidence is sufficient, return immediately
    if t1_conf >= 0.70 or nlp_tier_setting == "regex":
        if not sport:
            sport = _infer_sport(text)
        if not sport:
            sport = "unknown"
        # I42: Post-filter — "injury report" / "injury updates" spam with no player attached
        if not player_raw and confidence < 0.70:
            return None
        return InjuryEvent(
            player_raw=player_raw or "",
            sport=sport,
            detected_status=detected_status,
            severity=severity,
            confidence=confidence,
            source=source,
            source_url=url,
            raw_text=text,
            nlp_tier="regex",
        )

    # ── Tier 2: spaCy NER ─────────────────────────────────────────────────────
    if nlp_tier_setting in ("spacy", "llm"):
        try:
            t2_player, t2_conf_boost = await _tier2_spacy(text, player_raw)
            if t2_player:
                player_raw = t2_player
                confidence = min(1.0, t1_conf + t2_conf_boost)
        except Exception as exc:
            logger.debug("InjuryDetector: spaCy T2 failed", error=str(exc))

    if confidence >= 0.70 or nlp_tier_setting != "llm":
        if not sport:
            sport = _infer_sport(text)
        if not sport:
            sport = "unknown"
        return InjuryEvent(
            player_raw=player_raw or "",
            sport=sport,
            detected_status=detected_status,
            severity=severity,
            confidence=confidence,
            source=source,
            source_url=url,
            raw_text=text,
            nlp_tier="spacy",
        )

    # ── Tier 3: LLM fallback ──────────────────────────────────────────────────
    if nlp_tier_setting == "llm":
        try:
            llm_result = await _tier3_llm(text, source)
            if llm_result:
                if not sport:
                    sport = _infer_sport(text) or llm_result.get("sport", "unknown")
                return InjuryEvent(
                    player_raw=llm_result.get("player_name", player_raw or ""),
                    sport=sport or llm_result.get("sport", "unknown"),
                    detected_status=llm_result.get("status", detected_status),
                    severity=llm_result.get("severity", severity),
                    confidence=llm_result.get("confidence", confidence),
                    source=source,
                    source_url=url,
                    raw_text=text,
                    nlp_tier="llm",
                )
        except Exception as exc:
            logger.debug("InjuryDetector: LLM T3 failed", error=str(exc))

    # Return T1 result even if low confidence rather than None
    if not sport:
        sport = _infer_sport(text) or "unknown"
    # I42: Final post-filter — drop spam events with no player and low confidence
    if not player_raw and confidence < 0.70:
        return None
    return InjuryEvent(
        player_raw=player_raw or "",
        sport=sport,
        detected_status=detected_status,
        severity=severity,
        confidence=confidence,
        source=source,
        source_url=url,
        raw_text=text,
        nlp_tier="regex",
    )


# ─── Tier 1 Implementation ────────────────────────────────────────────────────

def _tier1_regex(text: str, sport: str = "") -> Tuple[Optional[Dict], float]:
    """
    Match against _INJURY_PATTERNS in order.

    Returns (result_dict, confidence) or (None, 0.0) if no match.
    """
    best_conf = 0.0
    best_result: Optional[Dict] = None

    for pattern, status, severity, base_conf in _INJURY_PATTERNS:
        if pattern.search(text):
            # Sport-specific boosts
            boosted = base_conf
            if sport == "tennis" and status in ("withdrawal", "retirement"):
                boosted = min(1.0, base_conf + 0.05)
            elif sport == "nhl" and status == "goalie_swap":
                boosted = min(1.0, base_conf + 0.03)
            elif sport == "mlb" and status == "sp_scratch":
                boosted = min(1.0, base_conf + 0.03)

            if boosted > best_conf:
                best_conf = boosted
                best_result = {"status": status, "severity": severity}

    return best_result, best_conf


def _extract_player_name_regex(text: str) -> Optional[str]:
    """
    Extract the most likely player name from text via regex.

    Looks for Title Case sequences (2-4 words), skips common false positives.
    """
    _STOP_WORDS = {
        "The", "A", "An", "In", "On", "At", "By", "For", "With", "From",
        "Per", "Up", "No", "Not", "Per", "Due", "Out", "Day", "This",
        "Today", "Tonight", "Week", "Game", "Team", "Coach", "Head", "New",
        "NBA", "NFL", "MLB", "NHL", "ESPN", "AP", "USA", "AFC", "NFC",
    }
    candidates = _PLAYER_NAME_RE.findall(text)
    for candidate in candidates:
        parts = candidate.split()
        if any(p in _STOP_WORDS for p in parts):
            continue
        if len(parts) >= 2:
            return candidate
    return None


# ─── Tier 2 Implementation ────────────────────────────────────────────────────

async def _tier2_spacy(
    text: str, fallback_name: Optional[str]
) -> Tuple[Optional[str], float]:
    """
    Use spaCy en_core_web_sm NER to extract PERSON entity.

    Returns (player_name, confidence_boost) where boost is 0.0–0.15.
    """
    try:
        import spacy

        # Load model lazily (cached by spaCy after first load)
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning("InjuryDetector: en_core_web_sm not found — run: python -m spacy download en_core_web_sm")
            return fallback_name, 0.0

        doc = nlp(text[:512])  # limit to avoid slow processing on long texts
        persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]

        if persons:
            # Use the first PERSON entity found
            player_name = persons[0].strip()
            # Confidence boost: spaCy confirmed there IS a person in the text
            boost = 0.12 if len(persons) == 1 else 0.08  # less certain if multiple
            return player_name, boost

        return fallback_name, 0.0
    except ImportError:
        logger.debug("InjuryDetector: spaCy not installed")
        return fallback_name, 0.0


# ─── Tier 3 Implementation ────────────────────────────────────────────────────

async def _tier3_llm(text: str, source: str) -> Optional[Dict]:
    """
    Use Anthropic Claude to classify injury status and extract player name.

    Returns dict with keys: player_name, sport, status, severity, confidence.
    Returns None on API failure.

    Cost: ~$0.01–0.05/call (haiku model). Gated behind INJURY_NLP_TIER=llm.
    """
    try:
        import anthropic
    except ImportError:
        logger.debug("InjuryDetector: anthropic not installed — T3 unavailable")
        return None

    from config.settings import settings
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        logger.debug("InjuryDetector: no ANTHROPIC_API_KEY — T3 unavailable")
        return None

    prompt = f"""Analyze this sports news text and extract injury/roster information.

Text: "{text[:500]}"

Respond in JSON only with these fields:
- player_name: full name of the player (or "" if not found)
- sport: one of nba/nfl/mlb/nhl/soccer/tennis/unknown
- status: one of out/doubtful/questionable/day-to-day/sp_scratch/goalie_swap/free_agent_move/withdrawal/retirement/unknown
- severity: one of season_ending/multi_week/day-to-day/offseason_move/unknown
- confidence: float 0.0-1.0 (how confident you are this is a real injury/roster move)
- is_injury_news: true/false (is this actually about a player's availability?)

If this is NOT about player availability/injuries/roster moves, set is_injury_news to false and confidence to 0.0.

JSON response only, no other text:"""

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()

        import json
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        if not result.get("is_injury_news", False):
            return None
        if float(result.get("confidence", 0.0)) < 0.50:
            return None

        return {
            "player_name": result.get("player_name", ""),
            "sport": result.get("sport", "unknown"),
            "status": result.get("status", "unknown"),
            "severity": result.get("severity", "unknown"),
            "confidence": float(result.get("confidence", 0.65)),
        }
    except Exception as exc:
        logger.debug("InjuryDetector: LLM T3 API error", error=str(exc))
        return None


# ─── Sport Inference ──────────────────────────────────────────────────────────

def _infer_sport(text: str) -> Optional[str]:
    """Infer sport from text keywords. Returns sport string or None."""
    text_lower = text.lower()
    best_sport = None
    best_count = 0
    for sport, keywords in _SPORT_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > best_count:
            best_count = count
            best_sport = sport
    return best_sport if best_count > 0 else None
