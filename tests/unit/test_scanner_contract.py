"""
Contract tests for esports/markets/esports_market_scanner.py.

Pins the scanner's output-dict shape against consumer expectations.
Codifies Protocol 4c (projection lossiness): a projection layer must not
drop fields the upstream provides and downstream consumers read. Tests
that only examine the projection's output against its own declared schema
cannot catch this class of bug — the diff has to cross the projection.

Three known consumer patterns (all landed S182 Phase 1d fix):
  - EsportsBotV2._find_polymarket_for_match reads m.get("yes_price")
  - EsportsBotV2._find_market_info reads m.get("yes_token_id") and m.get("no_token_id")
  - EsportsBotV2._execute_trades reads m.get("id") and m.get("condition_id")
Scanner's upstream `EsportsMarketService.get_tradeable_esports_markets`
emits all six keys; pre-A4 the scanner stripped them in projection.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from esports.markets.esports_market_scanner import EsportsMarketScanner, _CACHE


def _service_market_dict(
    market_id: str = "m-1",
    yes_token: str = "yt-1",
    no_token: str = "nt-1",
    yes_price: float = 0.55,
    no_price: float = 0.45,
    question: str = "Team Alpha vs Team Beta - Match Winner",
) -> dict:
    """Mirror of the dict shape EsportsMarketService.get_tradeable_esports_markets
    emits at esports/markets/esports_market_service.py:236-255. Keep in sync
    if the service's emission schema changes — that drift is the failure mode
    Protocol 4c's contract test exists to catch."""
    return {
        "id": market_id,
        "condition_id": f"cond-{market_id}",
        "question": question,
        "slug": f"slug-{market_id}",
        "category": "esports",
        "liquidity": 1000.0,
        "volume": 0.0,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "yes_price": yes_price,
        "no_price": no_price,
        "resolution_source": "",
        "end_date_iso": None,
        "active": True,
        "resolved": False,
        "resolution": None,
        "tokens": [
            {"tokenId": yes_token, "outcomePrice": yes_price},
            {"tokenId": no_token, "outcomePrice": no_price},
        ],
        "_game": "lol",
    }


def _polymarket_native_dict(
    market_id: str = "poly-1",
    token_id: str = "pt-1",
    price: float = 0.55,
    question: str = "Team Alpha vs Team Beta",
) -> dict:
    """Approximation of Polymarket Gamma API native format (fallback path).
    Crucially lacks yes_token_id/no_token_id/yes_price/no_price as top-level
    keys — the passthrough must emit None for these without regressing."""
    return {
        "id": market_id,
        "question": question,
        "category": "esports",
        "tokens": [{"tokenId": token_id, "outcomePrice": price}],
    }


@pytest.fixture(autouse=True)
def _clear_scanner_cache():
    """Reset the module-level cache between tests — the scanner memoizes
    per match_id+game, which would bleed state across tests otherwise."""
    _CACHE.clear()
    yield
    _CACHE.clear()


class TestFindMarketsForMatchContract:
    """Contract tests for EsportsMarketScanner.find_markets_for_match."""

    @pytest.mark.asyncio
    async def test_emits_paired_keys_from_market_service(self):
        """A4 passthrough: scanner must forward yes_token_id / no_token_id /
        yes_price / no_price / id / condition_id when upstream provides them.
        Regresses the S181 Commit 3 silent-None on `yes_price`."""
        service = MagicMock()
        service.get_tradeable_esports_markets = AsyncMock(
            return_value=[
                _service_market_dict(
                    market_id="m-alpha",
                    yes_token="yt-alpha",
                    no_token="nt-alpha",
                    yes_price=0.72,
                    no_price=0.28,
                    question="Team Alpha vs Team Beta - Match Winner",
                ),
            ]
        )
        scanner = EsportsMarketScanner(market_service=service)

        results = await scanner.find_markets_for_match(
            match_id="match-alpha",
            game="lol",
            team_names=["Team Alpha"],
        )

        assert len(results) == 1
        m = results[0]

        # Pre-A4 canonical keys — stable contract.
        assert m["market_id"] == "m-alpha"
        assert m["token_id"] == "yt-alpha"  # tokens[0] is YES by service convention
        assert m["price"] == 0.72
        assert m["question"] == "Team Alpha vs Team Beta - Match Winner"
        assert m["match_id"] == "match-alpha"
        assert m["game"] == "lol"

        # A4 passthrough keys — the bug was that these did not exist in the
        # output dict despite being present on the input dict. The test
        # asserts existence AND value to pin both the schema and the data path.
        assert m["yes_token_id"] == "yt-alpha"
        assert m["no_token_id"] == "nt-alpha"
        assert m["yes_price"] == 0.72
        assert m["no_price"] == 0.28
        assert m["id"] == "m-alpha"
        assert m["condition_id"] == "cond-m-alpha"

    @pytest.mark.asyncio
    async def test_eb_v2_consumer_pattern_finds_price(self):
        """Integration smoke: the exact read pattern from
        bots/esports_bot_v2.py:547 (_find_polymarket_for_match) and
        bots/esports_bot_v2.py:604 (_find_market_info) must yield non-None.
        Pre-A4 both returned None; post-A4 both find real values. This is
        the concrete regression test for the S181 zero-trading-output bug."""
        service = MagicMock()
        service.get_tradeable_esports_markets = AsyncMock(
            return_value=[
                _service_market_dict(
                    market_id="m-regression",
                    yes_token="yt-reg",
                    no_token="nt-reg",
                    yes_price=0.55,
                    no_price=0.45,
                    question="Team Alpha vs Team Beta - Match Winner",
                ),
            ]
        )
        scanner = EsportsMarketScanner(market_service=service)
        markets = await scanner.find_markets_for_match(
            match_id="match-regression",
            game="lol",
            team_names=["Team Alpha"],
        )
        assert markets, "scanner returned empty — upstream filter or cache bled"
        m = markets[0]

        # Pattern from bots/esports_bot_v2.py:547 _find_polymarket_for_match
        price = m.get("yes_price")
        assert price is not None, (
            "yes_price is None — A4 passthrough regressed, S181 silent-None is back"
        )
        assert 0.03 < price < 0.97
        assert m.get("market_id") == "m-regression"

        # Pattern from bots/esports_bot_v2.py:604 _find_market_info
        assert m.get("yes_token_id"), (
            "yes_token_id is falsy — A4 passthrough regressed, paired-token filter "
            "in _find_market_info will return None"
        )
        assert m.get("no_token_id"), (
            "no_token_id is falsy — same regression path"
        )

    @pytest.mark.asyncio
    async def test_fallback_path_emits_none_for_missing_upstream_keys(self):
        """Polymarket API fallback path lacks yes_token_id/no_token_id/yes_price/
        no_price as top-level keys. Passthrough must emit None for them — matches
        pre-A4 behavior on that path, no regression. Consumers that read those
        keys on fallback-path output continue to get None (same as today)."""
        poly = MagicMock()
        poly.get_markets = AsyncMock(
            return_value=[
                _polymarket_native_dict(
                    market_id="poly-1",
                    token_id="pt-1",
                    price=0.55,
                    question="Team Alpha vs Team Beta",
                ),
            ]
        )
        scanner = EsportsMarketScanner(polymarket_client=poly)
        results = await scanner.find_markets_for_match(
            match_id="match-fallback",
            game="lol",
            team_names=["Team Alpha"],
        )
        assert len(results) == 1
        m = results[0]

        # Pre-A4 canonical keys still populate.
        assert m["market_id"] == "poly-1"
        assert m["token_id"] == "pt-1"
        assert m["price"] == 0.55

        # A4 passthrough: None where upstream does not provide the key.
        assert m["yes_token_id"] is None
        assert m["no_token_id"] is None
        assert m["yes_price"] is None
        assert m["no_price"] is None
        assert m["id"] == "poly-1"  # Polymarket native has "id"
        assert m["condition_id"] is None


class TestFindAllEsportsMarketsContract:
    """Contract tests for the sibling emission site
    EsportsMarketScanner.find_all_esports_markets (line 221-228).
    Same passthrough contract, different call signature."""

    @pytest.mark.asyncio
    async def test_emits_paired_keys_from_market_service(self):
        service = MagicMock()
        service.get_tradeable_esports_markets = AsyncMock(
            return_value=[
                _service_market_dict(
                    market_id="m-all",
                    yes_token="yt-all",
                    no_token="nt-all",
                    yes_price=0.60,
                    no_price=0.40,
                    question="League of Legends: Alpha vs Beta",
                ),
            ]
        )
        scanner = EsportsMarketScanner(market_service=service)

        results = await scanner.find_all_esports_markets(game="lol")
        assert len(results) == 1
        m = results[0]

        assert m["market_id"] == "m-all"
        assert m["yes_token_id"] == "yt-all"
        assert m["no_token_id"] == "nt-all"
        assert m["yes_price"] == 0.60
        assert m["no_price"] == 0.40
        assert m["id"] == "m-all"
        assert m["condition_id"] == "cond-m-all"
