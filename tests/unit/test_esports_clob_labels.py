"""Unit tests for the CLOB authoritative YES-team resolution (Step 3, flip-proof).

The HTTP fetch is injected, so these exercise the resolution logic offline.
Correct-or-absent is the property under test: any doubt -> None, never a wrong
team / bool.
"""
from esports_v2.data.clob_labels import (
    resolve_yes_is_team_a_via_clob,
    resolve_yes_team_from_clob,
)


def _fetch(tokens):
    """Build an injectable fetch that returns a fixed token list."""
    return lambda cid: tokens


# ── resolve_yes_team_from_clob ───────────────────────────────────────────────


def test_resolves_yes_team_name_from_matching_token():
    tokens = [("G2 Esports", "tokA"), ("100 Thieves", "tokB")]
    name = resolve_yes_team_from_clob("cid", "tokA", fetch=_fetch(tokens))
    assert name == "G2 Esports"
    name_b = resolve_yes_team_from_clob("cid", "tokB", fetch=_fetch(tokens))
    assert name_b == "100 Thieves"


def test_shape1_yes_no_market_returns_none():
    tokens = [("Yes", "tokA"), ("No", "tokB")]
    assert resolve_yes_team_from_clob("cid", "tokA", fetch=_fetch(tokens)) is None


def test_yes_token_not_among_tokens_returns_none():
    tokens = [("G2 Esports", "tokA"), ("100 Thieves", "tokB")]
    assert resolve_yes_team_from_clob("cid", "MISSING", fetch=_fetch(tokens)) is None


def test_fetch_failure_returns_none():
    assert resolve_yes_team_from_clob("cid", "tokA", fetch=lambda cid: None) is None
    assert resolve_yes_team_from_clob("cid", "tokA", fetch=lambda cid: []) is None


def test_empty_inputs_return_none():
    tokens = [("G2 Esports", "tokA")]
    assert resolve_yes_team_from_clob("", "tokA", fetch=_fetch(tokens)) is None
    assert resolve_yes_team_from_clob("cid", "", fetch=_fetch(tokens)) is None


def test_empty_label_returns_none():
    tokens = [("", "tokA"), ("100 Thieves", "tokB")]
    assert resolve_yes_team_from_clob("cid", "tokA", fetch=_fetch(tokens)) is None


# ── resolve_yes_is_team_a_via_clob (composed, flip-proof) ────────────────────


def test_via_clob_yes_is_team_a_true():
    tokens = [("G2 Esports", "tokA"), ("100 Thieves", "tokB")]
    # sharp odds team_a = "G2 Esports"; YES token resolves on G2 -> True
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokA", team_a="G2 Esports", team_b="100 Thieves", fetch=_fetch(tokens)
    )
    assert out is True


def test_via_clob_yes_is_team_a_false():
    tokens = [("G2 Esports", "tokA"), ("100 Thieves", "tokB")]
    # YES token resolves on 100 Thieves, which is team_b -> False
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokB", team_a="G2 Esports", team_b="100 Thieves", fetch=_fetch(tokens)
    )
    assert out is False


def test_via_clob_flip_proof_when_odds_order_reversed():
    # The odds source may order teams the other way. Because we resolve the
    # authoritative NAME and align it to team_a ourselves, the answer follows the
    # name, not token position -> no inversion.
    tokens = [("G2 Esports", "tokA"), ("100 Thieves", "tokB")]
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokA", team_a="100 Thieves", team_b="G2 Esports", fetch=_fetch(tokens)
    )
    assert out is False  # YES=G2, but team_a is 100 Thieves here


def test_via_clob_uses_alias_map():
    tokens = [("Natus Vincere", "tokA"), ("FaZe Clan", "tokB")]
    aliases = {"NAVI": ["Natus Vincere"]}
    exp = lambda t: aliases.get(t, [])
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokA", team_a="NAVI", team_b="FaZe Clan",
        alias_expand=exp, fetch=_fetch(tokens),
    )
    assert out is True


def test_via_clob_ambiguous_label_returns_none():
    # YES team matches neither sharp team -> resolver returns None (uncovered)
    tokens = [("Some Other Team", "tokA"), ("Another", "tokB")]
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokA", team_a="G2 Esports", team_b="100 Thieves", fetch=_fetch(tokens)
    )
    assert out is None


def test_via_clob_shape1_returns_none():
    tokens = [("Yes", "tokA"), ("No", "tokB")]
    out = resolve_yes_is_team_a_via_clob(
        "cid", "tokA", team_a="G2 Esports", team_b="100 Thieves", fetch=_fetch(tokens)
    )
    assert out is None
