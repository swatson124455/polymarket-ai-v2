"""Conservative, correct-or-absent team-name matching primitives.

Extracted verbatim from ``results_join`` (behavior UNCHANGED) so the same trusted
matcher is reused by the Polymarket match-winner index (``pm_market_index``) —
not a looser one. A wrong team match attaches a wrong PM price/orientation (the
S152/B2 loss class), so the rule is deliberately strict:

  - exact normalized equality, OR
  - injected-alias-set overlap, OR
  - a token-subset that shares a NON-GENERIC token ("G2 Esports" <-> "G2",
    "Team Vitality" <-> "Vitality"), never an all-generic overlap
    ("R2 Esports Club" </> "Esports Club"),

subject to a HARD VETO that outranks all three: names whose sibling-roster
qualifiers differ (SIBLING_QUALIFIERS: academy/youth/female/... ) are
DIFFERENT teams of the same org and never match — "T1" </> "T1 Academy",
"Cloud9" </> "Cloud9 White" (fixed 2026-07-10; the subset rule previously
linked them, and the seeded alias table is contaminated with exactly these
cross-roster pairs).

``match_teams`` additionally requires a clean BIJECTION of {home, away} onto the
two candidate teams — a one-sided match is rejected — so callers pair a match to
another only when BOTH teams line up unambiguously.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from esports_v2.model.orientation import normalize_team

# Generic tokens that don't distinguish orgs — a shared generic token is not
# enough to call two names the same team. Mirrors the concept behind
# seed_esports_team_aliases._GENERIC_TOKENS (kept local so this module has no
# script dependency / no dotenv import side effect).
GENERIC_TOKENS = {
    "esports", "esport", "e-sports", "gaming", "team", "club",
    "academy", "youth", "challengers", "challenger", "junior",
}

# Sibling-roster qualifiers: tokens that mark a DIFFERENT team of the same org
# (academy / youth / female / secondary rosters). NOT decorations: "G2
# Esports" == "G2" (decoration), but "T1" != "T1 Esports Academy" — a
# different roster that can play on the SAME day (LCK vs LCK CL), so linking
# them is a live wrong-attach risk (the S152/B2 loss class). Evidence: the
# 2026-07-10 esports_team_aliases dump — every cross-roster group in it
# (T1 / T1 Academy, Weibo Gaming / Youth Team, W7M / W7M Gaming Fe,
# Karmine Corp Blue / Blue Stars) is a distinct-team pair. A DIFFERENCE in
# these tokens vetoes the match — even via injected aliases (the seed table
# is demonstrably contaminated with such groups). Both names carrying the
# SAME qualifier still match ("T1 Academy" == "T1 Esports Academy"). The veto
# can only REMOVE a match, never add one (correct-or-absent-safe direction).
SIBLING_QUALIFIERS = {
    "academy", "youth", "junior", "juniors", "rookies",
    "challenger", "challengers",
    "female", "fe", "women", "womens", "ladies", "gc",
    "blue", "white", "black", "gold", "stars",
}


def sibling_qualifiers(norm_name: str) -> set:
    """The sibling-roster qualifier tokens present in a normalized name."""
    return set(norm_name.split()) & SIBLING_QUALIFIERS


def alias_set(team: str, alias_expand: Optional[Callable[[str], Iterable[str]]]) -> set:
    out = {normalize_team(team)}
    if alias_expand is not None:
        try:
            out |= {normalize_team(a) for a in alias_expand(team) if a}
        except Exception:
            pass
    out.discard("")
    return out


def same_team(
    x: str, y: str, alias_expand: Optional[Callable[[str], Iterable[str]]]
) -> bool:
    """True iff two team names refer to the same org (conservative).

    Match on: exact normalized equality, alias-set overlap, or a token-subset
    that shares a NON-GENERIC token. The subset rule links "G2 Esports" <->
    "G2" and "Team Vitality" <-> "Vitality" without linking "R2 Esports Club"
    <-> "Esports Club" (all-generic overlap) — the same guard the seed script
    trusts.

    HARD VETO before the alias/subset paths: names whose sibling-roster
    qualifiers differ ("T1" vs "T1 Academy") are different rosters and never
    match — not even via injected aliases, because the seeded alias table is
    contaminated with exactly these cross-roster groups (2026-07-10 dump).
    """
    nx, ny = normalize_team(x), normalize_team(y)
    if nx and nx == ny:
        return True
    if not nx or not ny:
        return False
    if sibling_qualifiers(nx) != sibling_qualifiers(ny):
        return False
    ax, ay = alias_set(x, alias_expand), alias_set(y, alias_expand)
    if ax & ay:
        return True
    tx, ty = set(nx.split()), set(ny.split())
    if not tx or not ty:
        return False
    shared_non_generic = (tx & ty) - GENERIC_TOKENS
    if not shared_non_generic:
        return False
    return tx.issubset(ty) or ty.issubset(tx)


def match_teams(
    home: str, away: str, res_a: str, res_b: str,
    alias_expand: Optional[Callable[[str], Iterable[str]]],
) -> Optional[bool]:
    """Bijectively map {home, away} to {res_a, res_b}.

    Returns True if (home==res_a, away==res_b), False if (home==res_b,
    away==res_a), None if the mapping is not an unambiguous bijection (a team
    matches both/neither, or the pairing is inconsistent).
    """
    ha, hb = same_team(home, res_a, alias_expand), same_team(home, res_b, alias_expand)
    aa, ab = same_team(away, res_a, alias_expand), same_team(away, res_b, alias_expand)
    # Orientation 1: home->A, away->B
    orient1 = ha and ab and not hb and not aa
    # Orientation 2: home->B, away->A
    orient2 = hb and aa and not ha and not ab
    if orient1 and not orient2:
        return True
    if orient2 and not orient1:
        return False
    return None  # ambiguous or no clean bijection
