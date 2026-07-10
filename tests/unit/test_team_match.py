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
    assert same_team("Team Vitality", "Vitality", None)
    # NOTE (2026-07-10 behavior change): "DRX Academy" <-> "DRX" was asserted
    # True here — that WAS the sibling-roster bug (different teams, same org).
    # The qualifier veto now rejects it; see the veto tests below.


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


# ── sibling-roster qualifier veto (2026-07-10 fix) ───────────────────────────
# "T1" vs "T1 Academy" are DIFFERENT teams of the same org; the subset rule
# used to link them (and the seeded alias table equates them). A difference in
# SIBLING_QUALIFIERS now vetoes, outranking both aliases and token-subset.


def test_main_roster_does_not_match_academy():
    assert not same_team("T1", "T1 Academy", None)
    assert not same_team("T1", "T1 Esports Academy", None)
    assert not same_team("DRX", "DRX Academy", None)
    assert not same_team("Weibo Gaming", "Weibo Gaming Youth Team", None)
    assert not same_team("W7M", "W7M Gaming Fe", None)
    assert not same_team("Cloud9", "Cloud9 White", None)
    assert not same_team("Karmine Corp", "Karmine Corp Blue", None)
    assert not same_team("Karmine Corp Blue", "Karmine Corp Blue Stars", None)


def test_same_qualifier_both_sides_still_matches():
    assert same_team("T1 Academy", "T1 Esports Academy", None)
    assert same_team("KT Rolster Academy", "KT Academy", None)


def test_decorations_still_match():
    # the intended subset links are untouched
    assert same_team("G2 Esports", "G2", None)
    assert same_team("Team Vitality", "Vitality", None)
    assert same_team("LiT Esports", "LIT", None)


def test_qualifier_veto_outranks_injected_alias():
    # the seeded alias table really contains this contaminated group
    aliases = {"T1": ["T1 Academy", "T1 Esports Academy"],
               "T1 Academy": ["T1"]}
    expand = lambda t: aliases.get(t, [])
    assert not same_team("T1", "T1 Academy", expand)
    assert not same_team("T1 Academy", "T1", expand)


def test_exact_equality_still_wins():
    # identical names trivially share identical qualifier sets
    assert same_team("T1 Academy", "T1 Academy", None)
    assert same_team("KRÜ Esports", "KRU Esports", None)  # diacritic fold


def test_bijection_rejects_academy_fixture():
    # odds row for the main match must not map onto the academy fixture
    assert match_teams("T1", "Gen.G", "T1 Academy", "Gen.G Academy", None) is None


def test_color_roster_qualifiers_veto():
    # Shopify Rebellion Black coexists with main Shopify Rebellion in the
    # 2026-07-10 live index — the subset rule linked them pre-fix.
    assert not same_team("Shopify Rebellion", "Shopify Rebellion Black", None)
    assert not same_team("Gen.G", "Gen.G Gold", None)
    # core-color org names carry the token on BOTH sides -> no veto
    assert same_team("Black Dragons e-Sports", "Black Dragons", None)
