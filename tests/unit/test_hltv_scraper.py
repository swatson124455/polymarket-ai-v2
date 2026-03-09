"""Tests for HLTVScraper — mocked HTTP, no real HLTV requests."""
import asyncio
from unittest.mock import patch, MagicMock

import pytest

from esports.data.hltv_scraper import HLTVScraper, CS2_MAP_POOL, _BoundedCache


class TestBoundedCache:
    def test_set_and_get(self):
        cache = _BoundedCache(max_size=5, default_ttl=60.0)
        cache.set("a", 42)
        assert cache.get("a") == 42

    def test_miss(self):
        cache = _BoundedCache()
        assert cache.get("missing") is None

    def test_max_size_eviction(self):
        cache = _BoundedCache(max_size=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3


class TestHLTVScraper:
    def test_init(self):
        scraper = HLTVScraper()
        assert scraper._cache is not None

    @pytest.mark.asyncio
    async def test_get_cs2_map_pool(self):
        scraper = HLTVScraper()
        pool = await scraper.get_cs2_map_pool()
        assert len(pool) == 7
        assert "inferno" in pool
        assert "dust2" in pool

    @pytest.mark.asyncio
    async def test_get_map_side_rates(self):
        scraper = HLTVScraper()
        rates = await scraper.get_map_side_rates("nuke")
        assert rates["ct"] == 0.57
        assert rates["t"] == 0.43

    @pytest.mark.asyncio
    async def test_get_map_side_rates_unknown(self):
        scraper = HLTVScraper()
        rates = await scraper.get_map_side_rates("de_foo")
        assert rates == {"ct": 0.50, "t": 0.50}

    @pytest.mark.asyncio
    async def test_get_team_rating_cached(self):
        scraper = HLTVScraper()
        scraper._cache.set("rating:cs2:navi", 1.5)
        result = await scraper.get_team_rating("navi", game="cs2")
        assert result == 1.5

    @pytest.mark.asyncio
    async def test_get_map_win_rates_cached(self):
        scraper = HLTVScraper()
        rates = {"inferno": 0.65, "dust2": 0.45}
        scraper._cache.set("maps:navi", rates)
        result = await scraper.get_map_win_rates("navi")
        assert result["inferno"] == 0.65

    @pytest.mark.asyncio
    async def test_get_recent_results_cached(self):
        scraper = HLTVScraper()
        data = [{"opponent": "Vitality", "won": True, "score": "2-0"}]
        scraper._cache.set("results:cs2:navi:20", data)
        result = await scraper.get_recent_results("navi", game="cs2")
        assert len(result) == 1
        assert result[0]["won"] is True

    @pytest.mark.asyncio
    async def test_get_team_rating_scrape_failure(self):
        """When scraping fails, should return None gracefully."""
        scraper = HLTVScraper()
        with patch.object(scraper, "_scrape_hltv_team_rating", return_value=None):
            result = await scraper.get_team_rating("unknownteam999")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_map_win_rates_scrape_failure(self):
        """When scraping fails, should return default rates."""
        scraper = HLTVScraper()
        with patch.object(scraper, "_scrape_hltv_map_stats", return_value=None):
            result = await scraper.get_map_win_rates("unknownteam999")
            assert all(v == 0.50 for v in result.values())

    @pytest.mark.asyncio
    async def test_get_recent_results_empty(self):
        """When scraping fails, should return empty list."""
        scraper = HLTVScraper()
        with patch.object(scraper, "_scrape_hltv_results", return_value=[]):
            result = await scraper.get_recent_results("unknownteam999")
            assert result == []

    @pytest.mark.asyncio
    async def test_get_patch_cached(self):
        scraper = HLTVScraper()
        patch_data = {"version": "CS2 Update", "date": "2026-03-01"}
        scraper._cache.set("patch:cs2", patch_data)
        result = await scraper.get_current_patch_notes("cs2")
        assert result["version"] == "CS2 Update"

    @pytest.mark.asyncio
    async def test_get_patch_non_cs2(self):
        scraper = HLTVScraper()
        result = await scraper.get_current_patch_notes("lol")
        assert result is None

    def test_hltv_get_returns_none_on_failure(self):
        """_hltv_get returns None when request fails."""
        import time
        scraper = HLTVScraper()
        HLTVScraper._last_hltv_request = time.monotonic()  # skip rate limit wait
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("esports.data.hltv_scraper.requests.get", return_value=mock_resp):
            result = scraper._hltv_get("https://www.hltv.org/fake")
            assert result is None

    def test_liquipedia_get_returns_none_on_failure(self):
        import time
        scraper = HLTVScraper()
        HLTVScraper._last_liquipedia_request = time.monotonic()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with patch("esports.data.hltv_scraper.requests.get", return_value=mock_resp):
            result = scraper._liquipedia_get("https://liquipedia.net/fake")
            assert result is None
