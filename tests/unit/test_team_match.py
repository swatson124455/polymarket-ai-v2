"""Unit tests for the shared team-match primitives (extracted from results_join).

These mirror the results_join team-equality tests to lock the behavior in its new
home; results_join re-exports these under its original private names, so its own
suite continues to exercise the same code from the other side.
"""
from esports_v2.model.team_match import match_teams, same_team


def test_same_team_exact_normalized():
    assert same_team("G2 Esports", "g2 esports", None)
    assert same_team("Team Spirit", "team  spirit", None)


def test_same_team_token_subset_shares_non_generic():
    assert same_team("G2 Esports", "G2", None)
    assert same_team("DRX Academy", "DRX", None)
    assert same_team("Team Vitality", "Vitality", None)


def test_same_team_rejects_all_generic_overlap():
    assert not same_team("R2 Esports Club", "Esports Club", None)


def test_same_team_rejects_unrelated():
    assert not same_team("Natus Vincere", "FaZe Clan", None)


def test_same_team_punctuation_is_a_separator():
    # normalize_team splits on punctuation -> "MIBR.LOS" tokens {mibr, los}.
    assert same_team("MIBR.LOS", "MIBR", None)      # shares non-generic 'mibr', subset


def test_same_team_alias_injection():
    aliases = {"NAVI": ["Natus Vincere"]}
    assert same_team("NAVI", "Natus Vincere", lambda t: aliases.get(t, []))


def test_match_teams_bijection_orientations():
    assert match_teams("A", "B", "A", "B", None) is True
    assert match_teams("A", "B", "B", "A", None) is False


def test_match_teams_ambiguous_returns_none():
    # a team that matches neither side -> no clean bijection
    assert match_teams("A", "B", "A", "C", None) is None
    assert match_teams("X", "Y", "A", "B", None) is None


def test_same_team_diacritic_folded():
    # Real 2026-07-10 join miss: PinnOdds 'Cilekler' vs PandaScore 'Çilekler'.
    assert same_team("Cilekler", "Çilekler", None)
    assert same_team("KRÜ Esports", "KRU Esports", None)


def test_diacritic_fold_does_not_conflate_distinct_orgs():
    # Folding must not weaken the non-generic-token guard.
    assert not same_team("KRU Spark", "KRÜ Esports", None)   # academy != main org
