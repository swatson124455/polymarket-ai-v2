"""Unit tests for the Polymarket match-winner index (GAP B PM price capture).

Fixtures are real live Gamma shapes captured 2026-07-09 (JSON-encoded string
arrays for outcomes/clobTokenIds/outcomePrices).
"""
import json

from esports_v2.data.odds_loader import make_match_key
from esports_v2.data.pm_market_index import (
    PMMarketRef,
    build_pm_index,
    parse_gamma_market,
)


def _mw_market(**over):
    """A real, clean match-winner market (ZennIT vs The Bandits, BO3)."""
    m = {
        "question": "LoL: ZennIT vs The Bandits (BO3) - Road Of Legends Tier 1 Group",
        "conditionId": "0x872ccf45091283c1bb4f0b614f29d181c7af1044f3052b662fc1f12fa37efd04",
        "outcomes": json.dumps(["ZennIT", "The Bandits"]),
        "clobTokenIds": json.dumps(["36408350682438387457574261390236022714495760375683209804952463107329218427825",
                                    "26863304232839016056260116973220480793120213174636761498620284673683799359313"]),
        "outcomePrices": json.dumps(["0.62", "0.38"]),
        "gameStartTime": "2026-01-27 19:00:00+00",
    }
    m.update(over)
    return m


def test_parse_match_winner_ok():
    got = parse_gamma_market(_mw_market())
    assert got is not None
    key, ref = got
    assert key == make_match_key("ZennIT", "The Bandits", "2026-01-27")
    assert isinstance(ref, PMMarketRef)
    assert ref.condition_id.startswith("0x872ccf45")
    assert ref.yes_token_id.startswith("36408350")   # index-0 token
    assert ref.yes_outcome == "ZennIT"                # index-0 outcome
    assert ref.market_price == 0.62                   # index-0 price


def test_key_is_order_invariant():
    """PinnOdds home/away order need not match PM outcome order."""
    key1 = parse_gamma_market(_mw_market())[0]
    swapped = _mw_market(outcomes=json.dumps(["The Bandits", "ZennIT"]))
    key2 = parse_gamma_market(swapped)[0]
    assert key1 == key2


def test_reject_game_winner_prop():
    m = _mw_market(question="LoL: ZennIT vs The Bandits - Game 1 Winner")
    assert parse_gamma_market(m) is None


def test_reject_kill_handicap_prop():
    m = _mw_market(
        question="Series: Anyone's Legend Kill Handicap (-10.5) vs Weibo Gaming (+10.5)",
        outcomes=json.dumps(["Anyone's Legend", "Weibo Gaming"]),
    )
    assert parse_gamma_market(m) is None


def test_reject_map_odd_even_prop():
    m = _mw_market(question="Map 1: Odd/Even Total Kills?",
                   outcomes=json.dumps(["Odd", "Even"]))
    assert parse_gamma_market(m) is None


def test_reject_yes_no_shape1():
    m = _mw_market(question="Will T1 win the LCK 2026 season playoffs?",
                   outcomes=json.dumps(["Yes", "No"]))
    assert parse_gamma_market(m) is None


def test_reject_no_vs_in_title():
    m = _mw_market(question="ZennIT crushes The Bandits tonight")
    assert parse_gamma_market(m) is None


def test_reject_missing_condition_id():
    assert parse_gamma_market(_mw_market(conditionId="")) is None


def test_reject_missing_game_start():
    assert parse_gamma_market(_mw_market(gameStartTime="")) is None


def test_reject_wrong_token_count():
    assert parse_gamma_market(_mw_market(clobTokenIds=json.dumps(["only_one"]))) is None


def test_degenerate_price_becomes_none_but_market_kept():
    """A resolved (0/1) or unparseable price -> market_price None, market still
    indexed for orientation (correct-or-absent on the price field only)."""
    for bad in (json.dumps(["1", "0"]), json.dumps(["0", "1"]),
                json.dumps(["oops", "0.5"]), "not-json"):
        got = parse_gamma_market(_mw_market(outcomePrices=bad))
        assert got is not None
        assert got[1].market_price is None


def test_build_index_from_pages():
    page0 = [_mw_market(),
             _mw_market(question="LoL: ZennIT vs The Bandits - Game 2 Winner")]  # prop
    fetches = {0: page0, 100: []}
    idx = build_pm_index(fetch_page=lambda off, lim: fetches.get(off), max_pages=5)
    assert len(idx) == 1
    key = make_match_key("ZennIT", "The Bandits", "2026-01-27")
    assert key in idx and idx[key].yes_outcome == "ZennIT"


def test_build_index_drops_ambiguous_collision():
    """Two DIFFERENT markets on the same teams+date -> the key is dropped."""
    a = _mw_market(conditionId="0xaaa")
    b = _mw_market(conditionId="0xbbb")  # same teams/date, different market
    idx = build_pm_index(fetch_page=lambda off, lim: (a, b) if off == 0 else [],
                         max_pages=2)
    assert idx == {}


def test_build_index_same_condition_id_not_ambiguous():
    """Paging overlap (same market twice) must NOT be treated as ambiguous."""
    a = _mw_market(conditionId="0xsame")
    idx = build_pm_index(fetch_page=lambda off, lim: (a, a) if off == 0 else [],
                         max_pages=2)
    assert len(idx) == 1


def test_build_index_fetch_failure_returns_empty():
    idx = build_pm_index(fetch_page=lambda off, lim: None, max_pages=3)
    assert idx == {}
