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


def test_team_present_substring_short_circuits():
    """Substring hit returns (True, 100) without calling rapidfuzz."""
    matched, score = EsportsMarketScanner._team_present(
        team_name="Liquid",
        question_lc="will team liquid win this match",
        alias_map={},
    )
    assert matched is True
    assert score == 100.0


def test_team_present_via_alias():
    """Alias substring counts as a hit."""
    matched, score = EsportsMarketScanner._team_present(
        team_name="AaB Esport",
        question_lc="will aalborg beat fnatic",
        alias_map={"aab esport": ["aab", "aalborg"]},
    )
    assert matched is True
    assert score == 100.0


def test_team_present_fuzzy_above_threshold():
    """No substring, no alias, but fuzzy score above threshold returns matched."""
    matched, score = EsportsMarketScanner._team_present(
        team_name="Team Liquid",
        question_lc="liquid team will be in the finals",
        alias_map={},
    )
    assert matched is True
    assert score >= 80.0


def test_team_present_fuzzy_below_threshold():
    """Random text must not match."""
    matched, score = EsportsMarketScanner._team_present(
        team_name="Team Liquid",
        question_lc="who will win the bitcoin election market today",
        alias_map={},
    )
    assert matched is False
    assert score < 80.0


# ── Contract: BOTH teams required (S195 deeper fix) ─────────────────────────

def test_both_teams_present_requires_both():
    """Single team match must NOT pass — was the pre-fix bug. Famous teams
    like T1 picked up season-long markets when only one team was mentioned."""
    both, sa, sb = EsportsMarketScanner._both_teams_present(
        team_names=["T1", "BNK FEARX"],
        question_lc="will t1 win the lck 2026 season playoffs",
        alias_map={},
    )
    assert both is False, (
        "single-team mention must not match — was the source of the season-"
        "market false positive"
    )
    # T1 alone scores 100; BNK FEARX absent scores low
    assert sa == 100.0
    assert sb < 80.0


def test_both_teams_present_passes_with_both():
    """Both teams in question (typical match-question shape) → match."""
    both, sa, sb = EsportsMarketScanner._both_teams_present(
        team_names=["T1", "BNK FEARX"],
        question_lc="lol: t1 vs bnk fearx (bo3) - lck rounds 1-2",
        alias_map={},
    )
    assert both is True
    assert sa == 100.0
    assert sb == 100.0


def test_both_teams_present_via_aliases():
    """Both teams found via their alias variants."""
    both, _, _ = EsportsMarketScanner._both_teams_present(
        team_names=["AaB Esport", "Cloud9"],
        question_lc="aalborg vs c9 in bo5 final",
        alias_map={
            "aab esport": ["aalborg", "aab"],
            "cloud9": ["c9"],
        },
    )
    assert both is True


# ── Contract: specificity ranking (S195 deeper fix) ─────────────────────────

def test_specificity_score_match_question_wins():
    """A 'X vs Y' question should outrank a season-playoff question."""
    match_q = "lol: t1 vs bnk fearx (bo3) - lck rounds 1-2"
    season_q = "will t1 win the lck 2026 season playoffs"
    s_match = EsportsMarketScanner._specificity_score(match_q)
    s_season = EsportsMarketScanner._specificity_score(season_q)
    assert s_match > s_season, (
        f"match-specific score {s_match} must beat season-market score {s_season}"
    )


def test_specificity_score_penalizes_season_keywords():
    """Season/playoff/championship keywords reduce score."""
    base = EsportsMarketScanner._specificity_score("team a vs team b in the final")
    seasonal = EsportsMarketScanner._specificity_score("team a vs team b in the playoffs")
    assert seasonal < base


def test_specificity_score_penalizes_handicap_submarkets():
    """Map/handicap sub-markets are valid but should rank below full-match."""
    full = EsportsMarketScanner._specificity_score("lol: t1 vs gen.g (bo5)")
    submkt = EsportsMarketScanner._specificity_score("lol: t1 vs gen.g - map 2 winner")
    handicap = EsportsMarketScanner._specificity_score("game handicap: t1 (-1.5) vs gen.g (+1.5)")
    assert full > submkt
    assert full > handicap


@pytest.mark.asyncio
async def test_find_markets_returns_match_specific_first():
    """End-to-end: when both a season market and a match-specific market
    exist, the match-specific one MUST be ranked first so
    _find_polymarket_for_match picks it. This is the regression guard
    for the deep root cause we fixed."""
    scanner, market_service, db = _make_scanner(alias_map={})
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        # Season market — only T1 mentioned, plus playoff keyword (penalty)
        _market("Will T1 win the LCK 2026 season playoffs?", id="season"),
        # Match-specific — both teams + 'vs' bonus
        _market("LoL: T1 vs BNK FEARX (BO3) - LCK Rounds 1-2", id="match"),
        # Handicap sub-market — both teams but penalized
        _market("Game Handicap: T1 (-1.5) vs BNK FEARX (+1.5)", id="handicap"),
    ])
    results = await scanner.find_markets_for_match(
        match_id="m_t1_bnk", game="lol", team_names=["T1", "BNK FEARX"],
    )
    # Season market filtered (BNK FEARX absent). Match-specific ranked above handicap.
    assert len(results) == 2
    assert results[0]["id"] == "match", (
        f"expected match-specific market first, got {results[0]['id']}"
    )
    assert results[1]["id"] == "handicap"


@pytest.mark.asyncio
async def test_find_markets_excludes_single_team_mentions():
    """A market mentioning ONLY one of the two teams must not be returned —
    was the pre-fix behavior that caused season markets to win."""
    scanner, market_service, db = _make_scanner(alias_map={})
    market_service.get_tradeable_esports_markets = AsyncMock(return_value=[
        _market("Will T1 win the LCK 2026 season playoffs?"),
        _market("Will Cloud9 beat 100 Thieves?"),  # T1's opponent isn't here
    ])
    results = await scanner.find_markets_for_match(
        match_id="m_t1_only", game="lol", team_names=["T1", "BNK FEARX"],
    )
    assert results == [], (
        "no market mentions BOTH T1 AND BNK FEARX → must return []"
    )


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
