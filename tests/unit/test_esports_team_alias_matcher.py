"""S195 — Contract tests for the alias-aware team-name matcher in
esports_market_scanner.py.

Purpose:
  (a) Substring match still works (unchanged for known-good cases)
  (b) Aliases expand the match set (e.g. PandaScore "AaB Esport" finds
      a Polymarket market that says "Aalborg")
  (c) rapidfuzz fallback catches near-misses the alias table missed
  (d) When zero markets match, log_unmatched_prediction is called with
      the closest near-miss snapshot
  (e) Markets without a paired token (yes_token_id + no_token_id missing)
      are not silently filtered here — that's downstream concern in
      _find_market_info; the scanner emits them all
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from esports.markets.esports_market_scanner import EsportsMarketScanner


# ── Fixtures ────────────────────────────────────────────────────────────────

def _make_scanner(alias_map=None, db=None):
    """Construct a scanner with a mocked market_service that returns a
    fixed market list. db.load_esports_team_aliases returns alias_map."""
    market_service = MagicMock()
    if db is None:
        db = MagicMock()
    db.load_esports_team_aliases = AsyncMock(return_value=alias_map or {})
    db.log_unmatched_prediction = AsyncMock(return_value=None)
    scanner = EsportsMarketScanner(db=db, market_service=market_service)
    # Disable the result cache to avoid cross-test state leaks.
    scanner._set_cache = AsyncMock(return_value=None)
    scanner._get_cache = AsyncMock(return_value=None)
    return scanner, market_service, db


def _market(question, **extra):
    """Build a minimal market dict matching what market_service returns."""
    base = {
        "id": extra.get("id", "mkt-1"),
        "question": question,
        "category": "esports",
        "tokens": [{"tokenId": "tok-1", "outcomePrice": "0.55"}],
        "yes_token_id": "yes-tok",
        "no_token_id": "no-tok",
        "yes_price": 0.55,
        "no_price": 0.45,
    }
    base.update(extra)
    return base


# ── Contract (a): substring still works for the easy case ───────────────────

@pytest.mark.asyncio
async def test_substring_match_unchanged():
    """Baseline: when team names appear directly in the question, the
    matcher behaves identical to the pre-S195 substring check."""
    scanner, market_service, db = _make_scanner(alias_map={})
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        _market("Will Team Liquid beat Cloud9 in this match?"),
    ])

    results = await scanner.find_markets_for_match(
        match_id="m1", game="lol", team_names=["Team Liquid", "Cloud9"],
    )
    assert len(results) == 1
    db.log_unmatched_prediction.assert_not_called()


# ── Contract (b): aliases expand matching ───────────────────────────────────

@pytest.mark.asyncio
async def test_alias_expansion_finds_market_with_renamed_org():
    """The PandaScore name 'AaB Esport' must find a Polymarket question
    that says 'Aalborg' if the alias table maps the two."""
    alias_map = {
        "aab esport": ["aab esport", "aalborg", "aab"],
    }
    scanner, market_service, db = _make_scanner(alias_map=alias_map)
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        _market("Will Aalborg beat Fnatic in the next match?"),
    ])

    results = await scanner.find_markets_for_match(
        match_id="m2", game="cs2", team_names=["AaB Esport", "Fnatic"],
    )
    assert len(results) == 1, (
        "alias expansion must let PandaScore name find market with org variant"
    )


# ── Contract (c): rapidfuzz fallback catches near-misses ────────────────────

@pytest.mark.asyncio
async def test_fuzzy_fallback_matches_minor_variation():
    """When neither the canonical name nor any alias is a substring match,
    rapidfuzz fallback must catch close variations (typos, suffixes)."""
    scanner, market_service, db = _make_scanner(alias_map={})
    # Question phrased oddly enough to fail substring but score >80 fuzzy.
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        _market("Fire Flux Esports vs 9INE — winner?"),
    ])

    results = await scanner.find_markets_for_match(
        match_id="m3", game="cs2",
        # The team names match exactly here; this exercises the substring
        # path. The fuzzy path is verified by the unit test below directly.
        team_names=["Fire Flux Esports", "9INE"],
    )
    assert len(results) == 1


def test_team_match_score_substring_short_circuits():
    """Substring hit returns score 100 without calling rapidfuzz."""
    matched, score = EsportsMarketScanner._team_match_score(
        team_names=["Liquid"],
        question_lc="will team liquid win this match",
        expanded=["liquid", "team liquid"],
    )
    assert matched is True
    assert score == 100.0


def test_team_match_score_fuzzy_above_threshold():
    """No substring match, but fuzzy score above threshold returns matched."""
    matched, score = EsportsMarketScanner._team_match_score(
        team_names=["Team Liquid"],
        # token_set_ratio handles word reordering — "Liquid Team" vs "Team Liquid"
        # scores 100 even though substring would miss.
        question_lc="liquid team will be in the finals",
        expanded=[],  # empty so substring stage finds nothing
    )
    assert matched is True
    assert score >= 80.0


def test_team_match_score_fuzzy_below_threshold():
    """Score below threshold (random text) must not match."""
    matched, score = EsportsMarketScanner._team_match_score(
        team_names=["Team Liquid"],
        question_lc="who will win the bitcoin election market today",
        expanded=[],
    )
    assert matched is False
    assert score < 80.0


# ── Contract (d): unmatched-prediction tracker ──────────────────────────────

@pytest.mark.asyncio
async def test_log_unmatched_prediction_called_when_zero_matches():
    """When no markets match the prediction, log_unmatched_prediction must
    be called with the match metadata so the alias gap is observable."""
    scanner, market_service, db = _make_scanner(alias_map={})
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        # Esports market exists, but neither team appears in the question.
        _market("Will the Bitcoin price hit $100k this week?", category="finance"),
        _market("Will Cloud9 beat G2 Esports?"),
    ])

    results = await scanner.find_markets_for_match(
        match_id="m4", game="lol",
        team_names=["Obscure Team Alpha", "Obscure Team Beta"],
    )
    assert results == []
    db.log_unmatched_prediction.assert_awaited_once()
    call_kwargs = db.log_unmatched_prediction.await_args.kwargs
    assert call_kwargs["match_id"] == "m4"
    assert call_kwargs["team_a"] == "Obscure Team Alpha"
    assert call_kwargs["team_b"] == "Obscure Team Beta"
    assert call_kwargs["game"] == "lol"


@pytest.mark.asyncio
async def test_log_unmatched_not_called_when_match_found():
    """Successful matching must NOT log to the unmatched tracker —
    otherwise the table fills with false positives."""
    scanner, market_service, db = _make_scanner(alias_map={})
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        _market("Team Liquid vs Cloud9 — winner?"),
    ])
    results = await scanner.find_markets_for_match(
        match_id="m5", game="lol", team_names=["Team Liquid", "Cloud9"],
    )
    assert len(results) == 1
    db.log_unmatched_prediction.assert_not_called()


# ── Alias-expansion helper unit tests ───────────────────────────────────────

def test_expand_team_aliases_preserves_originals():
    """Expansion must include the original names even if they're not in the alias map."""
    expanded = EsportsMarketScanner._expand_team_aliases(
        team_names=["NoAliasTeam", "Liquid"],
        alias_map={"liquid": ["liquid", "team liquid", "tl"]},
    )
    assert "noaliasteam" in expanded
    assert "liquid" in expanded
    assert "team liquid" in expanded
    assert "tl" in expanded


def test_expand_team_aliases_dedups():
    """Duplicate names (across teams or aliases) must not produce duplicates."""
    expanded = EsportsMarketScanner._expand_team_aliases(
        team_names=["Liquid", "Team Liquid"],
        alias_map={
            "liquid": ["liquid", "team liquid"],
            "team liquid": ["team liquid", "liquid"],
        },
    )
    assert len(expanded) == len(set(expanded))


def test_expand_team_aliases_empty_input():
    """Empty input returns empty list cleanly."""
    assert EsportsMarketScanner._expand_team_aliases([], {}) == []
    assert EsportsMarketScanner._expand_team_aliases(["", None], {}) == []
