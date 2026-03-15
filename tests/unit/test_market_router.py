"""Tests for SetFit market router (Tier 3B)."""

import pytest

from base_engine.features.market_router import (
    MarketRouter,
    classify_by_keywords,
)


class TestKeywordRouter:
    """Keyword fallback router tests (no torch/setfit dependency)."""

    def test_weather_classification(self):
        cat, conf = classify_by_keywords("Will the temperature in NYC exceed 80F?")
        assert cat == "weather"
        assert conf >= 0.6

    def test_esports_classification(self):
        cat, conf = classify_by_keywords("Will T1 win the League of Legends World Championship?")
        assert cat == "esports"
        assert conf >= 0.6

    def test_politics_classification(self):
        cat, conf = classify_by_keywords("Will Biden win the presidential election?")
        assert cat == "politics"
        assert conf >= 0.6

    def test_crypto_classification(self):
        cat, conf = classify_by_keywords("Will Bitcoin exceed $100,000?")
        assert cat == "crypto"
        assert conf >= 0.6

    def test_sports_classification(self):
        cat, conf = classify_by_keywords("Will the Lakers win the NBA Championship?")
        assert cat == "sports"
        assert conf >= 0.6

    def test_unknown_falls_to_general(self):
        cat, conf = classify_by_keywords("Will the world end tomorrow?")
        assert cat == "general"
        assert conf < 0.5

    def test_multiple_keyword_matches_boost_confidence(self):
        # Multiple weather keywords → higher confidence
        cat, conf = classify_by_keywords(
            "Will rain and snow and precipitation hit Denver this week?"
        )
        assert cat == "weather"
        assert conf >= 0.8

    def test_case_insensitive(self):
        cat, _ = classify_by_keywords("WILL THE TEMPERATURE IN NYC EXCEED 80F?")
        assert cat == "weather"


class TestMarketRouter:
    def test_defaults_to_keyword_router(self):
        router = MarketRouter()
        cat, conf = router.classify("Will it rain in Seattle?")
        assert cat == "weather"
        assert conf >= 0.6

    def test_batch_classify_fallback(self):
        router = MarketRouter()
        results = router.classify_batch([
            "Will it rain?",
            "Will T1 win LoL?",
            "Will Bitcoin hit $100k?",
        ])
        assert len(results) == 3
        assert results[0][0] == "weather"
        assert results[1][0] == "esports"
        assert results[2][0] == "crypto"

    def test_ml_not_available_without_torch(self):
        router = MarketRouter()
        # is_ml_available depends on whether torch is installed
        assert isinstance(router.is_ml_available, bool)
        # Not trained by default
        assert router.is_trained is False

    def test_train_returns_false_without_setfit(self):
        router = MarketRouter()
        if not router.is_ml_available:
            assert router.train() is False
