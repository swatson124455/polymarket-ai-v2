"""Conservative, correct-or-absent team-name matching primitives.

Extracted verbatim from ``results_join`` (behavior UNCHANGED) so the same trusted
matcher is reused by the Polymarket match-winner index (``pm_market_index``) —
not a looser one. A wrong team match attaches a wrong PM price/orientation (the
S152/B2 loss class), so the rule is deliberately strict:

  - exact normalized equality, OR
  - injected-alias-set overlap, OR
  - a token-subset that shares a NON-GENERIC token ("G2 Esports" <-> "G2",
    "Team Vitality" <-> "Vitality"), never an all-generic overlap
    ("R2 Esports Club" </> "Esports Club").

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
    that shares a NON-GENERIC token. The last rule links "G2 Esports" <-> "G2"
    and "DRX Academy" <-> "DRX" without linking "R2 Esports Club" <-> "Esports
    Club" (all-generic overlap) — the same guard the seed script trusts.
    """
    ax, ay = alias_set(x, alias_expand), alias_set(y, alias_expand)
    if ax & ay:
        return True
    nx, ny = normalize_team(x), normalize_team(y)
    if not nx or not ny:
        return False
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
