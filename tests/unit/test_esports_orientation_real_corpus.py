"""Regression tests: orientation resolver vs the REAL live market corpus.

Source: scripts/esports_market_shape_probe_public.py run on 2026-07-08 against
2100 live esports-tag Polymarket markets (see EB_MARKET_SHAPE_RESULTS.md). These
lock in the correct-or-absent contract against the ACTUAL phrasings the market
returns — a future edit to orientation.py that reintroduces a sign-flip (the
S152/B2 loss class) or over-covers pollution must fail here.

The whole-corpus sweep at authoring time was: 315/315 shape-1 "Will X win"
correct with ZERO sign-flips, 438/438 pollution questions correctly bailed to
None, 68/68 shape-2 team-name pairs authoritative-correct. The literals below are
a representative slice of that corpus.
"""
from esports_v2.model.orientation import resolve_yes_is_team_a

_DECOY = "Zzz Decoy Squad 9999"  # a team guaranteed absent from every question


# ── shape-1: real "Will <team> win the <event>?" futures/winner questions ──────
# YES pays on the subject team. With subject=team_a and the (absent) opponent as
# team_b, the resolver must return True — and CRITICALLY, never False.
_SHAPE1_SUBJECT_TEAM_A = [
    ("Will BRION win the LCK 2026 season playoffs?", "BRION"),
    ("Will T1 win the LCK 2026 season playoffs?", "T1"),
    ("Will Gen.G Esports win the LCK 2026 season playoffs?", "Gen.G Esports"),
    ("Will Bilibili Gaming win the LPL 2026 season?", "Bilibili Gaming"),
    ("Will Anyone's Legend win the LPL 2026 season?", "Anyone's Legend"),
    ("Will Nongshim RedForce win the LCK 2026 season playoffs?", "Nongshim RedForce"),
]


def test_shape1_real_questions_orient_to_subject_never_flip():
    for q, subj in _SHAPE1_SUBJECT_TEAM_A:
        # subject is team_a
        assert resolve_yes_is_team_a("Yes", q, subj, _DECOY) is True, q
        # subject is team_b (sharp book's A/B order reversed) -> must be False, not True
        assert resolve_yes_is_team_a("Yes", q, _DECOY, subj) is False, q


# ── pollution: real esports-tag Yes/No questions that are NOT winner markets ───
# The tag is dirty. None of these can be oriented -> must all bail to None.
_POLLUTION = [
    "Will MoistCr1TiKaL get a haircut in 2026?",
    "Will Patrick Mahomes be on the cover of Madden NFL 27?",
    "Will Victor Wembanyama be on the cover of NBA 2K27?",
    "Will any FaZe member come out as a furry by July 31?",
    "Will Valve add first CS2 operation by August 31, 2026?",
    "Fnatic merger/acquisition announced by September 1, 2026?",
    "Game 1: Any Player Penta Kill?",
]


def test_pollution_questions_all_bail_to_none():
    for q in _POLLUTION:
        assert resolve_yes_is_team_a("Yes", q, "Some Team", _DECOY) is None, q


# ── shape-2: real team-name-outcome head-to-head markets (authoritative) ───────
_SHAPE2_PAIRS = [
    ("G2 Esports", "100 Thieves"),
    ("DRX", "DetonatioN FocusMe"),
    ("Anyone's Legend", "Weibo Gaming"),
    ("Sentinels", "Cloud9"),
    ("Paper Rex", "T1"),
    ("Nongshim RedForce", "Gen.G Esports"),
]


def test_shape2_real_pairs_resolve_authoritatively():
    for a, b in _SHAPE2_PAIRS:
        # YES outcome label IS a team name -> maps to that team, no question needed
        assert resolve_yes_is_team_a(a, None, a, b) is True, (a, b)
        assert resolve_yes_is_team_a(b, None, a, b) is False, (a, b)


# ── inversion / qualifier phrasings must still bail (defense-in-depth) ─────────
def test_inversion_and_submatch_still_bail_on_real_style_phrasing():
    # inverting predicate -> None even though a team is named
    assert resolve_yes_is_team_a("Yes", "Will T1 be eliminated in the LCK playoffs?", "T1", _DECOY) is None
    # map/game qualifier on a shape-1 phrasing -> None (match-winner odds only)
    assert resolve_yes_is_team_a("Yes", "Will T1 win map 2 vs Gen.G?", "T1", "Gen.G") is None
