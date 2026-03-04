"""
Unit tests for MarketParserV2 token ID extraction.

Gamma API returns clobTokenIds as JSON string "[\"id1\", \"id2\"]", not array.
This was causing markets_with_token_ids=0 and only 9k price records instead of millions.
"""
import pytest
from base_engine.data.market_parser_v2 import MarketParserV2


class TestMarketParserV2TokenIds:
    """Test token ID extraction from various API response formats."""

    def test_clob_token_ids_json_string(self):
        """Gamma API returns clobTokenIds as JSON string - must parse before use."""
        raw = {
            "id": "517310",
            "question": "Will Trump deport less than 250,000?",
            "clobTokenIds": '["101676997363687199724245607342877036148401850938023978421879460310389391082353", "4153292802911610701832309484716814274802943278345248636922528170020319407796"]',
        }
        yes_id, no_id = MarketParserV2._extract_token_ids(raw)
        assert yes_id == "101676997363687199724245607342877036148401850938023978421879460310389391082353"
        assert no_id == "4153292802911610701832309484716814274802943278345248636922528170020319407796"

    def test_clob_token_ids_array(self):
        """Also support array format (backward compat)."""
        raw = {
            "id": "m1",
            "clobTokenIds": ["token_yes_123", "token_no_456"],
        }
        yes_id, no_id = MarketParserV2._extract_token_ids(raw)
        assert yes_id == "token_yes_123"
        assert no_id == "token_no_456"

    def test_parse_market_full_gamma_response(self):
        """Parse full market from Gamma API (clobTokenIds as JSON string)."""
        raw = {
            "id": "517310",
            "conditionId": "0xaf9d0e448129a9f657f851d49495ba4742055d80e0ef1166ba0ee81d4d594214",
            "question": "Will Trump deport less than 250,000?",
            "outcomePrices": '["0.0225", "0.9775"]',
            "clobTokenIds": '["101676997363687199724245607342877036148401850938023978421879460310389391082353", "4153292802911610701832309484716814274802943278345248636922528170020319407796"]',
            "liquidity": "14075.32943",
            "volume": "1031044.536383",
        }
        parsed = MarketParserV2.parse_market(raw)
        assert parsed is not None
        assert parsed["yes_token_id"] == "101676997363687199724245607342877036148401850938023978421879460310389391082353"
        assert parsed["no_token_id"] == "4153292802911610701832309484716814274802943278345248636922528170020319407796"
        assert parsed["yes_price"] == 0.0225
        assert parsed["no_price"] == 0.9775
