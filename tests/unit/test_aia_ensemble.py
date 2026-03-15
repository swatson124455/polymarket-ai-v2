"""Tests for AIA-style independent CoT ensemble (Tier 3E)."""

import asyncio
import math
import os
from unittest.mock import AsyncMock, patch

import pytest

from base_engine.features.llm_probability import LLMProbabilityEstimator


@pytest.fixture
def estimator():
    """LLM estimator with Anthropic key set."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        est = LLMProbabilityEstimator()
    return est


class TestCoTPromptVariants:
    def test_generates_5_prompts(self, estimator):
        prompts = estimator._build_cot_prompt_variants(
            "Will it rain?", 0.6, "weather", "7 days",
        )
        assert len(prompts) == 5

    def test_prompts_contain_market_context(self, estimator):
        prompts = estimator._build_cot_prompt_variants(
            "Will it snow in Denver?", 0.45, "weather", "3 days",
        )
        for p in prompts:
            assert "Will it snow in Denver?" in p
            assert "0.45" in p

    def test_prompts_are_distinct(self, estimator):
        prompts = estimator._build_cot_prompt_variants(
            "Test?", 0.5, "test", "1 day",
        )
        # All 5 should be unique
        assert len(set(prompts)) == 5


class TestAIAEnsembleAggregation:
    @pytest.mark.asyncio
    async def test_aggregation_with_mocked_providers(self, estimator):
        """5 mocked responses aggregate via extremized geo mean of odds."""
        mock_results = [
            {"probability": 0.6, "reasoning": "base rate"},
            {"probability": 0.65, "reasoning": "contrarian"},
            {"probability": 0.55, "reasoning": "decomposition"},
            {"probability": 0.7, "reasoning": "temporal"},
            {"probability": 0.58, "reasoning": "bayesian"},
        ]
        call_count = 0

        async def mock_call(prompt):
            nonlocal call_count
            result = mock_results[call_count % len(mock_results)]
            call_count += 1
            return result

        estimator._call_anthropic = mock_call

        result = await estimator.estimate_aia_ensemble(
            "Will it rain?", 0.5, "weather", "7 days",
        )

        assert result is not None
        assert result["model"] == "aia_ensemble"
        assert result["n_variants"] == 5
        assert 0.01 <= result["probability"] <= 0.99
        assert "variant_probabilities" in result
        assert len(result["variant_probabilities"]) == 5
        # With d=2.5, the consensus (all >0.5) should push well above 0.5
        assert result["probability"] > 0.6

    @pytest.mark.asyncio
    async def test_extremization_pushes_away_from_half(self, estimator):
        """Extremization with d=2.5 should amplify signals away from 0.5."""
        # All variants agree on 0.7 → extremized should be > 0.7
        async def mock_call(prompt):
            return {"probability": 0.7, "reasoning": "test"}

        estimator._call_anthropic = mock_call

        with patch.dict(os.environ, {"LLM_AIA_EXTREMIZATION": "2.5"}):
            result = await estimator.estimate_aia_ensemble(
                "Test?", 0.5, "test", "1 day",
            )

        assert result is not None
        # geo mean of odds at 0.7: log_odds = log(0.7/0.3) ≈ 0.847
        # extremized: 0.847 * 2.5 = 2.118 → sigmoid ≈ 0.893
        assert result["probability"] > 0.85

    @pytest.mark.asyncio
    async def test_symmetric_at_half(self, estimator):
        """All variants at 0.5 should stay near 0.5 after extremization."""
        async def mock_call(prompt):
            return {"probability": 0.5, "reasoning": "uncertain"}

        estimator._call_anthropic = mock_call

        result = await estimator.estimate_aia_ensemble(
            "Coin flip?", 0.5, "test", "1 day",
        )

        assert result is not None
        assert abs(result["probability"] - 0.5) < 0.01

    @pytest.mark.asyncio
    async def test_returns_none_when_too_few_succeed(self, estimator):
        """<2 successful variants returns None (fallback to single call)."""
        call_count = 0

        async def mock_call(prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"probability": 0.6, "reasoning": "only one"}
            raise Exception("API error")

        estimator._call_anthropic = mock_call

        result = await estimator.estimate_aia_ensemble(
            "Test?", 0.5, "test", "1 day",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_outlier_detection(self, estimator):
        """Variants >0.15 from median flagged as outliers."""
        mock_results = [
            {"probability": 0.6, "reasoning": "a"},
            {"probability": 0.62, "reasoning": "b"},
            {"probability": 0.58, "reasoning": "c"},
            {"probability": 0.61, "reasoning": "d"},
            {"probability": 0.30, "reasoning": "outlier"},  # >0.15 from median ~0.6
        ]
        call_count = 0

        async def mock_call(prompt):
            nonlocal call_count
            result = mock_results[call_count % len(mock_results)]
            call_count += 1
            return result

        estimator._call_anthropic = mock_call

        result = await estimator.estimate_aia_ensemble(
            "Test?", 0.5, "test", "1 day",
        )

        assert result is not None
        assert len(result["outlier_variants"]) >= 1
        assert "bayesian" in result["outlier_variants"]

    @pytest.mark.asyncio
    async def test_disabled_when_no_api_key(self):
        """Returns None when no API keys configured."""
        with patch.dict(os.environ, {}, clear=True):
            est = LLMProbabilityEstimator()
            # Remove any keys
            est._api_key = ""
            est._openai_key = ""
            est._enabled = False

        result = await est.estimate_aia_ensemble(
            "Test?", 0.5, "test", "1 day",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm_calls(self, estimator):
        """Second call with same question returns cached result (no LLM calls)."""
        call_count = 0

        async def mock_call(prompt):
            nonlocal call_count
            call_count += 1
            return {"probability": 0.6, "reasoning": "cached test"}

        estimator._call_anthropic = mock_call

        # First call: should make 5 LLM calls
        result1 = await estimator.estimate_aia_ensemble(
            "Will it rain?", 0.5, "weather", "7 days",
        )
        assert result1 is not None
        first_call_count = call_count

        # Second call: should hit cache, no new LLM calls
        result2 = await estimator.estimate_aia_ensemble(
            "Will it rain?", 0.5, "weather", "7 days",
        )
        assert result2 is not None
        assert result2["probability"] == result1["probability"]
        assert call_count == first_call_count  # no new calls

    @pytest.mark.asyncio
    async def test_spread_and_disagreement(self, estimator):
        """High spread triggers high_disagreement flag."""
        mock_results = [
            {"probability": 0.3, "reasoning": "a"},
            {"probability": 0.8, "reasoning": "b"},
            {"probability": 0.5, "reasoning": "c"},
            {"probability": 0.4, "reasoning": "d"},
            {"probability": 0.7, "reasoning": "e"},
        ]
        call_count = 0

        async def mock_call(prompt):
            nonlocal call_count
            result = mock_results[call_count % len(mock_results)]
            call_count += 1
            return result

        estimator._call_anthropic = mock_call

        result = await estimator.estimate_aia_ensemble(
            "Test?", 0.5, "test", "1 day",
        )

        assert result is not None
        assert result["spread"] == 0.5  # 0.8 - 0.3
        assert result["high_disagreement"] is True
