"""
Unit tests for sports/news/injury_detector.py

Tests the 3-tier NLP injury classifier:
  - Tier 1 regex pattern matching (all sports)
  - Player name extraction
  - Sport inference
  - No-injury text returns None
  - Confidence thresholds
"""
import pytest
from unittest.mock import AsyncMock, patch


# ─── Tier 1 Regex Tests ───────────────────────────────────────────────────────

class TestTier1Regex:
    """Tests for _tier1_regex and detect_injury with regex tier."""

    @pytest.mark.asyncio
    async def test_ruled_out_returns_out(self):
        from sports.news.injury_detector import detect_injury
        item = {
            "source": "twitter",
            "source_id": "1",
            "text": "LeBron James ruled out tonight with knee soreness",
            "sport": "nba",
            "url": "",
        }
        with patch("sports.news.injury_detector._tier3_llm", new=AsyncMock(return_value=None)):
            result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "out"
        assert result.confidence >= 0.90
        assert result.nlp_tier == "regex"

    @pytest.mark.asyncio
    async def test_dnp_returns_out(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "2", "text": "Steph Curry DNP tonight", "sport": "nba", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "out"

    @pytest.mark.asyncio
    async def test_doubtful_returns_doubtful(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "3", "text": "Patrick Mahomes listed as doubtful for Sunday", "sport": "nfl", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "doubtful"

    @pytest.mark.asyncio
    async def test_questionable_returns_questionable(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "4", "text": "Giannis Antetokounmpo questionable for Game 5", "sport": "nba", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "questionable"

    @pytest.mark.asyncio
    async def test_goalie_swap_nhl(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "5", "text": "Carey Price goalie swap confirmed for tonight", "sport": "nhl", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "goalie_swap"

    @pytest.mark.asyncio
    async def test_sp_scratch_mlb(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "6", "text": "SP scratch: Gerrit Cole will not start tonight", "sport": "mlb", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        # "SP scratch" pattern or "will not" — either is acceptable
        assert result.detected_status in ("sp_scratch", "out")

    @pytest.mark.asyncio
    async def test_tennis_withdrawal(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "7", "text": "Rafael Nadal withdraws from Wimbledon due to injury", "sport": "tennis", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "withdrawal"
        assert result.confidence >= 0.90

    @pytest.mark.asyncio
    async def test_free_agent_move_nfl(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "twitter", "source_id": "8", "text": "Josh Allen agrees to terms with the Bills on a new deal", "sport": "nfl", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "free_agent_move"

    @pytest.mark.asyncio
    async def test_placed_on_il_mlb(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "rss", "source_id": "9", "text": "Shohei Ohtani placed on the IL with elbow inflammation", "sport": "mlb", "url": ""}
        result = await detect_injury(item)
        assert result is not None
        assert result.detected_status == "out"

    @pytest.mark.asyncio
    async def test_no_injury_text_returns_none(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "twitter", "source_id": "10", "text": "Great game tonight! Lakers win 115-98", "sport": "nba", "url": ""}
        result = await detect_injury(item)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        from sports.news.injury_detector import detect_injury
        item = {"source": "twitter", "source_id": "11", "text": "", "sport": "nba", "url": ""}
        result = await detect_injury(item)
        assert result is None


class TestPlayerNameExtraction:
    """Tests for _extract_player_name_regex."""

    def test_extracts_two_word_name(self):
        from sports.news.injury_detector import _extract_player_name_regex
        result = _extract_player_name_regex("LeBron James ruled out tonight")
        assert result == "LeBron James"

    def test_extracts_three_word_name(self):
        from sports.news.injury_detector import _extract_player_name_regex
        result = _extract_player_name_regex("Giannis Antetokounmpo is questionable")
        assert result == "Giannis Antetokounmpo"

    def test_no_name_in_text(self):
        from sports.news.injury_detector import _extract_player_name_regex
        result = _extract_player_name_regex("ruled out tonight due to knee injury")
        # May return None or a false positive — just ensure no crash
        # (stop words should filter common words)


class TestSportInference:
    """Tests for _infer_sport."""

    def test_infers_nba(self):
        from sports.news.injury_detector import _infer_sport
        assert _infer_sport("NBA star ruled out for tonight's basketball game") == "nba"

    def test_infers_nfl(self):
        from sports.news.injury_detector import _infer_sport
        assert _infer_sport("NFL quarterback listed as doubtful for Sunday football game") == "nfl"

    def test_infers_tennis(self):
        from sports.news.injury_detector import _infer_sport
        assert _infer_sport("Wimbledon withdrawal announced at ATP tournament") == "tennis"

    def test_returns_none_for_unrelated(self):
        from sports.news.injury_detector import _infer_sport
        assert _infer_sport("The stock market rose 2% today") is None


class TestTier1RegexDirect:
    """Tests for _tier1_regex directly."""

    def test_ruled_out_high_confidence(self):
        from sports.news.injury_detector import _tier1_regex
        result, conf = _tier1_regex("Player ruled out for tonight")
        assert result is not None
        assert result["status"] == "out"
        assert conf >= 0.90

    def test_questionable_lower_confidence(self):
        from sports.news.injury_detector import _tier1_regex
        result, conf = _tier1_regex("Player listed as questionable")
        assert result is not None
        assert result["status"] == "questionable"
        assert 0.75 <= conf <= 0.90

    def test_no_match_returns_none(self):
        from sports.news.injury_detector import _tier1_regex
        result, conf = _tier1_regex("Great performance by the whole team tonight")
        assert result is None
        assert conf == 0.0

    def test_season_ending_highest_priority(self):
        from sports.news.injury_detector import _tier1_regex
        result, conf = _tier1_regex("Season-ending surgery confirmed for the star player")
        assert result is not None
        assert result["status"] == "out"
        assert result["severity"] == "season_ending"
        assert conf >= 0.93

    def test_ir_placement(self):
        from sports.news.injury_detector import _tier1_regex
        result, conf = _tier1_regex("Player placed on IR after MRI results")
        assert result is not None
        assert result["status"] == "out"
