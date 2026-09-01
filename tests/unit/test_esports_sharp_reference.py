"""Unit tests for esports_v2/model/sharp_reference.py — the EB offline
sharp-line reference core. Pure functions only; no network/DB/settings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from esports_v2.model.sharp_reference import (
    DEFAULT_FEE,
    DEFAULT_MIN_EDGE,
    LineEntry,
    SharpEdge,
    enrich_with_sharp_prob,
    evaluate_edge,
    no_vig_two_way,
    pick_point_in_time,
    sharp_yes_prob,
)


# ── no_vig_two_way ───────────────────────────────────────────────────────────


def test_no_vig_sums_to_one():
    a, b = no_vig_two_way(1.85, 2.05)
    assert a + b == pytest.approx(1.0)


def test_no_vig_strips_overround():
    # Raw implied 1/1.85 + 1/2.05 = 0.5405 + 0.4878 = 1.0283 (2.83% vig).
    a, b = no_vig_two_way(1.85, 2.05)
    raw_a = 1.0 / 1.85
    assert a == pytest.approx(raw_a / (1.0 / 1.85 + 1.0 / 2.05))
    assert a > b  # 1.85 is the favourite


def test_no_vig_even_market():
    a, b = no_vig_two_way(2.0, 2.0)
    assert a == pytest.approx(0.5)
    assert b == pytest.approx(0.5)


@pytest.mark.parametrize("oa,ob", [(1.0, 2.0), (2.0, 1.0), (0.5, 2.0), (-1.0, 2.0)])
def test_no_vig_degenerate_returns_none(oa, ob):
    assert no_vig_two_way(oa, ob) is None


# ── pick_point_in_time (no look-ahead) ───────────────────────────────────────


def _t(mins: int) -> datetime:
    return datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=mins)


def test_pick_point_in_time_latest_before():
    entries = [
        LineEntry(1.90, _t(0)),
        LineEntry(1.85, _t(10)),
        LineEntry(1.80, _t(20)),
    ]
    got = pick_point_in_time(entries, _t(15))
    assert got.price == 1.85  # the 20-min entry is the future — excluded


def test_pick_point_in_time_at_exact_boundary_included():
    entries = [LineEntry(1.90, _t(0)), LineEntry(1.85, _t(10))]
    got = pick_point_in_time(entries, _t(10))
    assert got.price == 1.85


def test_pick_point_in_time_no_entry_yet_returns_none():
    entries = [LineEntry(1.90, _t(30))]
    assert pick_point_in_time(entries, _t(0)) is None


def test_pick_point_in_time_empty_returns_none():
    assert pick_point_in_time([], _t(0)) is None


# ── sharp_yes_prob (orientation guard, B2) ───────────────────────────────────


def test_sharp_yes_prob_team_a_is_yes():
    assert sharp_yes_prob(0.62, yes_is_team_a=True) == pytest.approx(0.62)


def test_sharp_yes_prob_team_a_is_no():
    # If YES resolves on team_b, the YES prob is the complement of team_a's.
    assert sharp_yes_prob(0.62, yes_is_team_a=False) == pytest.approx(0.38)


# ── evaluate_edge (two-sided) ────────────────────────────────────────────────


def test_evaluate_edge_bets_yes_when_underpriced():
    # Sharp says YES worth 0.62, Polymarket YES priced 0.50 -> YES edge 0.10.
    res = evaluate_edge(0.62, 0.50, fee=0.0, min_edge=0.05)
    assert res.side == "YES"
    assert res.edge == pytest.approx(0.12)
    assert res.should_bet is True


def test_evaluate_edge_bets_no_when_yes_overpriced():
    # Sharp says YES worth 0.40, Polymarket YES priced 0.55 -> NO underpriced.
    res = evaluate_edge(0.40, 0.55, fee=0.0, min_edge=0.05)
    assert res.side == "NO"
    # NO sharp = 0.60, NO price = 0.45 -> edge 0.15.
    assert res.sharp_prob == pytest.approx(0.60)
    assert res.market_price == pytest.approx(0.45)
    assert res.edge == pytest.approx(0.15)
    assert res.should_bet is True


def test_evaluate_edge_fee_can_flip_should_bet():
    # 0.04 gross edge, 0.02 fee, 0.05 min -> net 0.02 < min -> no bet.
    res = evaluate_edge(0.54, 0.50, fee=0.02, min_edge=0.05)
    assert res.side == "YES"
    assert res.edge == pytest.approx(0.02)
    assert res.should_bet is False


def test_evaluate_edge_min_edge_boundary_inclusive():
    # FP-safe inclusivity check: an edge exactly equal to min_edge must bet.
    # Feed the function's own computed edge back as the threshold so the test
    # doesn't depend on 0.57-0.50-0.02 landing exactly on 0.05 in float.
    probe = evaluate_edge(0.57, 0.50, fee=0.02, min_edge=0.0)
    res = evaluate_edge(0.57, 0.50, fee=0.02, min_edge=probe.edge)
    assert res.should_bet is True  # edge >= edge -> inclusive


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_evaluate_edge_degenerate_price_returns_none(price):
    assert evaluate_edge(0.6, price) is None


@pytest.mark.parametrize("sy", [-0.01, 1.01])
def test_evaluate_edge_degenerate_prob_returns_none(sy):
    assert evaluate_edge(sy, 0.5) is None


# ── enrich_with_sharp_prob (offline seam) ────────────────────────────────────


def test_enrich_covered_record_attaches_signal():
    recs = [{"match_key": "a||b||2026-07-01", "market_price": 0.50, "yes_is_team_a": True}]
    lookup = {"a||b||2026-07-01": (1.60, 2.50)}  # team_a fav
    out = enrich_with_sharp_prob(recs, lookup, fee=0.0, min_edge=0.05)
    r = out[0]
    # no-vig(1.60, 2.50): 0.625/0.40 -> 0.6098 for team_a=YES.
    assert r["sharp_prob"] == pytest.approx(0.6098, abs=1e-3)
    assert r["sharp_side"] == "YES"
    assert r["sharp_edge"] == pytest.approx(0.1098, abs=1e-3)
    assert r["sharp_should_bet"] is True


def test_enrich_orientation_inversion_flips_side():
    # SAME odds, but YES resolves on team_b -> sharp says YES worth ~0.39,
    # Polymarket YES at 0.50 -> the bet must flip to NO. This is the B2 trap:
    # get yes_is_team_a wrong and the signal inverts.
    lookup = {"a||b||2026-07-01": (1.60, 2.50)}
    rec_a = {"match_key": "a||b||2026-07-01", "market_price": 0.50, "yes_is_team_a": True}
    rec_b = {"match_key": "a||b||2026-07-01", "market_price": 0.50, "yes_is_team_a": False}
    (out_a,) = enrich_with_sharp_prob([rec_a], lookup, fee=0.0)
    (out_b,) = enrich_with_sharp_prob([rec_b], lookup, fee=0.0)
    assert out_a["sharp_side"] == "YES"
    assert out_b["sharp_side"] == "NO"
    assert out_a["sharp_prob"] == pytest.approx(1.0 - out_b["sharp_prob"], abs=1e-9)


def test_enrich_uncovered_when_no_odds():
    recs = [{"match_key": "missing", "market_price": 0.5, "yes_is_team_a": True}]
    (out,) = enrich_with_sharp_prob(recs, {})
    assert out["sharp_prob"] is None
    assert out["sharp_should_bet"] is False


def test_enrich_uncovered_when_orientation_missing():
    # No yes_is_team_a -> the matcher never resolved it -> never guess (B2).
    recs = [{"match_key": "a||b||d", "market_price": 0.5}]
    lookup = {"a||b||d": (1.8, 2.1)}
    (out,) = enrich_with_sharp_prob(recs, lookup)
    assert out["sharp_prob"] is None
    assert out["sharp_side"] is None
    assert out["sharp_should_bet"] is False


def test_enrich_uncovered_when_orientation_not_bool():
    # A truthy-but-non-bool (e.g. a team name string) must NOT be accepted.
    recs = [{"match_key": "a||b||d", "market_price": 0.5, "yes_is_team_a": "team_a"}]
    lookup = {"a||b||d": (1.8, 2.1)}
    (out,) = enrich_with_sharp_prob(recs, lookup)
    assert out["sharp_prob"] is None


def test_enrich_uncovered_when_degenerate_odds():
    recs = [{"match_key": "a||b||d", "market_price": 0.5, "yes_is_team_a": True}]
    lookup = {"a||b||d": (1.0, 2.0)}  # degenerate
    (out,) = enrich_with_sharp_prob(recs, lookup)
    assert out["sharp_prob"] is None


def test_enrich_uncovered_when_no_market_price():
    recs = [{"match_key": "a||b||d", "yes_is_team_a": True}]
    lookup = {"a||b||d": (1.8, 2.1)}
    (out,) = enrich_with_sharp_prob(recs, lookup)
    assert out["sharp_prob"] is None


def test_enrich_returns_same_list_object():
    recs = [{"match_key": "x", "market_price": 0.5, "yes_is_team_a": True}]
    out = enrich_with_sharp_prob(recs, {})
    assert out is recs  # mutates in place, like enrich_with_clv


def test_defaults_match_eb_risk_controls():
    assert DEFAULT_MIN_EDGE == 0.05
    assert DEFAULT_FEE == 0.02
