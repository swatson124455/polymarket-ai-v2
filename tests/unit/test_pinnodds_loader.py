"""Unit tests for PinnOddsLoader — parsing + correct-or-absent filtering.

Fixtures are trimmed from a REAL PinnOdds /kit/v1/markets?sport_id=11 response
captured 2026-07-09 (see esports_v2/data/pinnodds_loader.py docstring).
"""
from esports_v2.data.pinnodds_loader import PinnOddsLoader
from esports_v2.data.odds_loader import make_match_key


# A clean CS2 match-winner event (has num_0.money_line with both sides).
_CS2 = {
    "home": "BIG", "away": "PVISION", "starts": "2026-07-10T08:00:00Z",
    "league_name": "CS2 - XSE Pro League", "is_have_odds": True,
    "periods": {
        "num_0": {"number": 0, "money_line": {"home": 2.3, "away": 1.632, "draw": None}},
        "num_1": {"number": 1, "money_line": {"home": 1.9, "away": 1.9, "draw": None}},
    },
}
# A kills PROP event — num_0.money_line absent (only num_1), team names suffixed.
_KILLS_PROP = {
    "home": "VfB (Kills)", "away": "ROSSMANN Centaurs (Kills)",
    "starts": "2026-07-09T17:00:00Z",
    "league_name": "League of Legends - Prime League 1st Division",
    "is_have_odds": True,
    "periods": {"num_1": {"number": 1, "money_line": {"home": 1.8, "away": 2.0}}},
}
# A series event that has num_0 but a null money_line (match winner not posted).
_NO_LINE = {
    "home": "Virtus.pro", "away": "Yandex", "starts": "2026-07-09T17:15:00Z",
    "league_name": "Dota 2 - Esports World Cup",
    "periods": {"num_0": {"number": 0, "money_line": None}, "num_1": {}},
}


def test_parse_clean_match_winner():
    got = PinnOddsLoader._parse_event(_CS2)
    assert got is not None
    key, odds = got
    assert key == make_match_key("BIG", "PVISION", "2026-07-10T08:00:00Z")
    assert odds == (2.3, 1.632)


def test_kills_prop_is_dropped():
    # Dropped both by the (Kills) name suffix and the missing num_0.money_line.
    assert PinnOddsLoader._parse_event(_KILLS_PROP) is None


def test_event_with_null_money_line_is_dropped():
    assert PinnOddsLoader._parse_event(_NO_LINE) is None


def test_invalid_odds_are_dropped():
    bad = {
        "home": "A", "away": "B", "starts": "2026-07-10",
        "periods": {"num_0": {"money_line": {"home": 1.0, "away": "x", "draw": None}}},
    }
    assert PinnOddsLoader._parse_event(bad) is None


def test_missing_teams_dropped():
    assert PinnOddsLoader._parse_event(
        {"home": "", "away": "B", "periods": {"num_0": {"money_line": {"home": 2.0, "away": 2.0}}}}
    ) is None


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("PINNACLE_ODDS_API_KEY", raising=False)
    try:
        PinnOddsLoader.from_env()
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_fetch_odds_merges_and_dedups(monkeypatch):
    # live feed returns the CS2 match; prematch returns the same match at a
    # different price -> live is passed first, prematch overwrites (documented).
    loader = PinnOddsLoader(api_key="dummy")
    feeds = {
        "live": {"events": [_CS2, _KILLS_PROP]},
        "prematch": {"events": [dict(_CS2, periods={"num_0": {"money_line": {"home": 2.5, "away": 1.55}}})]},
    }
    monkeypatch.setattr(loader, "_get", lambda path, params=None: feeds[params["event_type"]])
    monkeypatch.setattr("esports_v2.data.pinnodds_loader.time.sleep", lambda *_: None)
    out = loader.fetch_odds(event_types=("live", "prematch"))
    key = make_match_key("BIG", "PVISION", "2026-07-10")
    assert list(out.keys()) == [key]      # prop dropped, one match only
    assert out[key] == (2.5, 1.55)        # prematch overwrote live
