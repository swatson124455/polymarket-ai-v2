"""Unit tests for esports_v2/model/orientation.py — resolve_yes_is_team_a.

Emphasis on the CORRECT-OR-ABSENT contract: the resolver must return a bool ONLY
when unambiguous, and None on any doubt. A wrong bool inverts the sharp edge;
None just skips the market."""
from __future__ import annotations

from esports_v2.model.orientation import normalize_team, resolve_yes_is_team_a


# ── shape 2: team-name outcomes (exact, complete) ────────────────────────────


def test_shape2_yes_outcome_is_team_a():
    assert resolve_yes_is_team_a("Team Liquid", None, "Team Liquid", "Cloud9") is True


def test_shape2_yes_outcome_is_team_b():
    assert resolve_yes_is_team_a("Cloud9", None, "Team Liquid", "Cloud9") is False


def test_shape2_outcome_matches_neither_returns_none():
    assert resolve_yes_is_team_a("Fnatic", None, "Team Liquid", "Cloud9") is None


def test_shape2_punctuation_and_case_insensitive():
    assert resolve_yes_is_team_a("cloud9!", None, "Team Liquid", "Cloud9") is False


# ── shape 1: "Will <team> win?" YES/NO ───────────────────────────────────────


def test_shape1_single_team_subject_is_team_a():
    r = resolve_yes_is_team_a("Yes", "Will Team Liquid win the match?", "Team Liquid", "Cloud9")
    assert r is True


def test_shape1_single_team_subject_is_team_b():
    r = resolve_yes_is_team_a("Yes", "Will Cloud9 win?", "Team Liquid", "Cloud9")
    assert r is False


def test_shape1_both_teams_earliest_is_subject():
    # "Will Cloud9 beat Team Liquid?" -> Cloud9 is the subject -> YES pays Cloud9.
    r = resolve_yes_is_team_a("Yes", "Will Cloud9 beat Team Liquid?", "Team Liquid", "Cloud9")
    assert r is False


def test_shape1_both_teams_earliest_is_team_a():
    r = resolve_yes_is_team_a("Yes", "Will Team Liquid beat Cloud9?", "Team Liquid", "Cloud9")
    assert r is True


def test_shape1_none_yes_outcome_falls_back_to_question():
    r = resolve_yes_is_team_a(None, "Will Cloud9 win?", "Team Liquid", "Cloud9")
    assert r is False


# ── fail-safe: any doubt -> None ─────────────────────────────────────────────


def test_inversion_verb_bails_to_none():
    # "Will X lose to Y?" -> YES pays Y, not the subject -> unsafe blind -> None.
    r = resolve_yes_is_team_a("Yes", "Will Team Liquid lose to Cloud9?", "Team Liquid", "Cloud9")
    assert r is None


def test_neither_team_in_question_returns_none():
    r = resolve_yes_is_team_a("Yes", "Will the underdog win?", "Team Liquid", "Cloud9")
    assert r is None


def test_no_question_for_shape1_returns_none():
    assert resolve_yes_is_team_a("Yes", None, "Team Liquid", "Cloud9") is None


def test_bare_no_label_returns_none():
    assert resolve_yes_is_team_a("No", "Will Cloud9 win?", "Team Liquid", "Cloud9") is None


def test_degenerate_empty_team_returns_none():
    assert resolve_yes_is_team_a("Yes", "Will Cloud9 win?", "", "Cloud9") is None


def test_degenerate_identical_teams_returns_none():
    assert resolve_yes_is_team_a("Cloud9", None, "Cloud9", "Cloud9") is None


def test_substring_team_not_falsely_matched():
    # "G2" must not match inside "G2 Esports" partial words incorrectly; whole-
    # word matching means "G2" matches the token "g2" but not "g2esports".
    r = resolve_yes_is_team_a("Yes", "Will G2 win?", "G2", "MOUZ")
    assert r is True
    # A question mentioning only the OTHER team's superstring shouldn't match G2.
    r2 = resolve_yes_is_team_a("Yes", "Will MOUZ win?", "G2", "MOUZ")
    assert r2 is False


# ── alias injection ──────────────────────────────────────────────────────────


def test_alias_expand_resolves_short_form():
    aliases = {"Team Liquid": ["TL", "Liquid"], "Cloud9": ["C9"]}
    r = resolve_yes_is_team_a(
        "Yes", "Will TL win?", "Team Liquid", "Cloud9",
        alias_expand=lambda t: aliases.get(t, []),
    )
    assert r is True


def test_alias_shape2_short_form_outcome():
    aliases = {"Cloud9": ["C9"]}
    r = resolve_yes_is_team_a(
        "C9", None, "Team Liquid", "Cloud9",
        alias_expand=lambda t: aliases.get(t, []),
    )
    assert r is False


def test_alias_expand_exception_is_swallowed():
    def boom(_t):
        raise RuntimeError("alias source down")

    # Falls back to the bare name; still resolves on the literal outcome.
    r = resolve_yes_is_team_a("Cloud9", None, "Team Liquid", "Cloud9", alias_expand=boom)
    assert r is False


# ── composes with the enrichment seam ────────────────────────────────────────


def test_resolver_output_feeds_enrichment():
    from esports_v2.model.sharp_reference import enrich_with_sharp_prob

    yes_is_team_a = resolve_yes_is_team_a(
        "Yes", "Will Team Liquid win?", "Team Liquid", "Cloud9"
    )
    assert yes_is_team_a is True
    rec = {
        "match_key": "cloud9||team liquid||2026-07-08",
        "market_price": 0.50,
        "yes_is_team_a": yes_is_team_a,
    }
    lookup = {"cloud9||team liquid||2026-07-08": (1.60, 2.50)}  # team_a (Liquid) fav
    (out,) = enrich_with_sharp_prob([rec], lookup, fee=0.0)
    assert out["sharp_side"] == "YES"
    assert out["sharp_should_bet"] is True


def test_normalize_team_basic():
    assert normalize_team("  Team_Liquid!! ") == "team liquid"
