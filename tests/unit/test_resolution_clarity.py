"""
Unit tests for Tier 2 #16: LLM Resolution Clarity Scoring.

Covers:
- ResolutionRiskAnalyzer import + instantiation
- Regex-only scoring (_analyze_criteria)
- analyze_llm_clarity: cache hit (no LLM call), LLM fallback on API failure
- Score bounds always 0.0–1.0
- ensemble_bot._get_resolution_clarity: no-rra fast path
- Settings defaults for RESOLUTION_CLARITY_*
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Import guard ────────────────────────────────────────────────────────────

def test_resolution_risk_module_imports():
    from base_engine.analysis.resolution_risk import ResolutionRiskAnalyzer, ResolutionRiskLevel
    assert ResolutionRiskAnalyzer is not None
    assert ResolutionRiskLevel.LOW.value == "low"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_market(question="Will X happen?", description="", resolution_source="", market_id="123"):
    m = MagicMock()
    m.id = market_id
    m.question = question
    m.description = description
    m.resolution_source = resolution_source
    m.end_date_iso = datetime.now(timezone.utc) + timedelta(days=30)
    return m


def _make_rra():
    from base_engine.analysis.resolution_risk import ResolutionRiskAnalyzer
    return ResolutionRiskAnalyzer(db=None)


# ─── Regex scoring ───────────────────────────────────────────────────────────

class TestAnalyzeCriteria:
    def test_empty_description_returns_zero(self):
        rra = _make_rra()
        assert rra._analyze_criteria("") == 0.0

    def test_ambiguous_phrase_lowers_score(self):
        rra = _make_rra()
        clear = rra._analyze_criteria("Will the S&P 500 close above 5000 on 2026-12-31?")
        ambiguous = rra._analyze_criteria("Will the market recover approximately by end of year?")
        assert clear > ambiguous

    def test_score_clamped_0_to_1(self):
        rra = _make_rra()
        # Throw many ambiguous phrases at it — shouldn't go below 0
        score = rra._analyze_criteria(
            "at the discretion of the committee, as determined by reasonable judgment, "
            "approximately around roughly about or similar as deemed"
        )
        assert 0.0 <= score <= 1.0

    def test_numbers_and_date_boost_score(self):
        rra = _make_rra()
        with_date = rra._analyze_criteria("Will GDP exceed 3% before January 1, 2027?")
        without = rra._analyze_criteria("Will GDP grow?")
        assert with_date >= without


# ─── Cache behaviour ─────────────────────────────────────────────────────────

class TestClarityCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm(self):
        rra = _make_rra()
        market = _make_market(market_id="42")

        # Pre-populate cache with a known score
        rra._clarity_cache["42"] = (0.88, datetime.now(timezone.utc))

        called = []

        async def fake_create(**kwargs):
            called.append(True)
            raise AssertionError("LLM should not be called on cache hit")

        rra._anthropic_client = MagicMock()
        rra._anthropic_client.messages.create = fake_create

        score = await rra.analyze_llm_clarity(market)
        assert score == pytest.approx(0.88)
        assert not called  # LLM was not invoked

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_llm(self):
        rra = _make_rra()
        market = _make_market(market_id="99", description="Will X happen by 2027-01-01?")

        # Populate with an old entry (expired)
        rra._clarity_cache["99"] = (0.5, datetime.now(timezone.utc) - timedelta(hours=25))

        # Mock the Anthropic client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"clarity": 0.9}')]
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        rra._anthropic_client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "RESOLUTION_CLARITY_CACHE_TTL_HOURS": "24"}):
            score = await rra.analyze_llm_clarity(market)

        assert 0.0 <= score <= 1.0
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_stored_in_cache_after_llm_call(self):
        rra = _make_rra()
        market = _make_market(market_id="77", description="Will X happen?")

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"clarity": 0.75}')]
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        rra._anthropic_client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            score = await rra.analyze_llm_clarity(market)

        assert "77" in rra._clarity_cache
        cached_score, _ = rra._clarity_cache["77"]
        assert cached_score == pytest.approx(score)


# ─── LLM fallback ────────────────────────────────────────────────────────────

class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_no_api_key_uses_regex(self):
        rra = _make_rra()
        market = _make_market(description="Will the index exceed 5000 by January 1, 2027?")

        with patch.dict("os.environ", {}, clear=False):
            import os
            original = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                score = await rra.analyze_llm_clarity(market)
            finally:
                if original is not None:
                    os.environ["ANTHROPIC_API_KEY"] = original

        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_regex(self):
        rra = _make_rra()
        market = _make_market(description="Will X happen by 2027?")

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("network error"))
        rra._anthropic_client = mock_client

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            score = await rra.analyze_llm_clarity(market)

        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_score_always_between_0_and_1(self):
        from base_engine.analysis.resolution_risk import ResolutionRiskAnalyzer
        rra = ResolutionRiskAnalyzer(db=None)

        # LLM returns out-of-range value
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"clarity": 1.5}')]
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        rra._anthropic_client = mock_client

        market = _make_market(description="Test market with clear binary outcome by 2027-01-01.")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            score = await rra.analyze_llm_clarity(market)

        assert 0.0 <= score <= 1.0


# ─── Cache eviction ──────────────────────────────────────────────────────────

class TestCacheEviction:
    @pytest.mark.asyncio
    async def test_cache_evicts_at_max_size(self):
        from base_engine.analysis import resolution_risk as _rr_mod
        original_max = _rr_mod._CLARITY_CACHE_MAX
        _rr_mod._CLARITY_CACHE_MAX = 3
        try:
            rra = _make_rra()
            now = datetime.now(timezone.utc)
            for i in range(3):
                rra._clarity_cache[str(i)] = (0.5, now)

            # Adding a 4th should evict the oldest (FIFO)
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"clarity": 0.8}')]
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_resp)
            rra._anthropic_client = mock_client

            market = _make_market(market_id="new", description="New market question for 2027.")
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                await rra.analyze_llm_clarity(market)

            assert len(rra._clarity_cache) <= 3
            assert "0" not in rra._clarity_cache  # Oldest was evicted
        finally:
            _rr_mod._CLARITY_CACHE_MAX = original_max


# ─── Singleton client ─────────────────────────────────────────────────────────

class TestSingletonClient:
    @pytest.mark.asyncio
    async def test_client_created_once(self):
        rra = _make_rra()
        assert rra._anthropic_client is None

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="0.7")]
        market = _make_market(description="Will X happen by 2027?", market_id="singleton_test")

        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.messages.create = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_instance

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                await rra.analyze_llm_clarity(market)
                # Remove from cache so second call goes through LLM path
                rra._clarity_cache.pop("singleton_test", None)
                market2 = _make_market(description="Will Y happen by 2027?", market_id="singleton_test2")
                await rra.analyze_llm_clarity(market2)

        # AsyncAnthropic constructor called only once (singleton)
        assert mock_cls.call_count == 1


# ─── Settings ────────────────────────────────────────────────────────────────

def test_settings_resolution_clarity_defaults():
    from config.settings import Settings
    s = Settings()
    assert hasattr(s, "RESOLUTION_CLARITY_ENABLED")
    assert hasattr(s, "RESOLUTION_CLARITY_CACHE_TTL_HOURS")
    assert isinstance(s.RESOLUTION_CLARITY_ENABLED, bool)
    assert s.RESOLUTION_CLARITY_CACHE_TTL_HOURS > 0


# ─── EnsembleBot fast path ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensemble_bot_no_rra_returns_1():
    """_get_resolution_clarity returns 1.0 when no resolution_risk_analyzer is wired."""
    from bots.ensemble_bot import EnsembleBot

    mock_engine = MagicMock()
    mock_engine.resolution_risk_analyzer = None  # Not wired
    mock_engine.db = None

    bot = MagicMock(spec=EnsembleBot)
    bot.base_engine = mock_engine
    bot._get_resolution_clarity = EnsembleBot._get_resolution_clarity.__get__(bot, EnsembleBot)

    result = await bot._get_resolution_clarity({"id": "123"})
    assert result == 1.0


@pytest.mark.asyncio
async def test_ensemble_bot_cache_hit_skips_db():
    """_get_resolution_clarity returns cached score without a DB query."""
    from bots.ensemble_bot import EnsembleBot

    rra = _make_rra()
    rra._clarity_cache["999"] = (0.65, datetime.now(timezone.utc))

    mock_engine = MagicMock()
    mock_engine.resolution_risk_analyzer = rra
    mock_engine.db = MagicMock()
    mock_engine.db.get_session = MagicMock(side_effect=AssertionError("DB should not be queried on cache hit"))

    bot = MagicMock(spec=EnsembleBot)
    bot.base_engine = mock_engine
    bot._get_resolution_clarity = EnsembleBot._get_resolution_clarity.__get__(bot, EnsembleBot)

    with patch.dict("os.environ", {"RESOLUTION_CLARITY_CACHE_TTL_HOURS": "24"}):
        result = await bot._get_resolution_clarity({"id": "999"})

    assert result == pytest.approx(0.65)
