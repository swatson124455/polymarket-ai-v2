"""Resolve which team the Polymarket YES token pays out on — model-independent.

The sharp-line signal (``sharp_reference.py``) needs ``yes_is_team_a``: does the
YES token resolve on the sharp book's team_a? Today that alignment is entangled
inside the dead ratings model (see ``EB_SHARP_LINE_PLUMBING.md``); this module
lifts it out into a standalone resolver that works for BOTH observed market
shapes:

  - shape 1  "Will <team> win?" YES/NO binaries  — YES pays on the QUESTION'S
             subject team.  Resolved by finding which of {team_a, team_b} is the
             subject of the question.
  - shape 2  team-name-outcome markets            — the YES token's ``outcome``
             label IS a team name.  Resolved by matching that label to a team.

CORRECT-OR-ABSENT contract (matches the rest of the sharp-line core): return a
bool ONLY when the alignment is unambiguous. On any doubt — no match, both teams
equally implicated, an inversion verb ("lose"/"defeat"), degenerate inputs —
return None. ``enrich_with_sharp_prob`` treats None as uncovered and never bets.
A wrong bool would silently invert the edge; None just skips the market.

STATUS: the shape-2 path is exact and complete. The shape-1 subject parse is a
conservative heuristic (earliest-mentioned team is the subject, inversion verbs
bail to None) that must be HARDENED against the real question phrasings the
market-shape probe (``scripts/esports_market_shape_probe.py``) returns before it
is trusted in production. Until then it fails safe: fewer covered markets, never
a wrong side.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

# Affirmative / negative token labels for shape-1 detection.
_YES_LABELS = {"YES", "1", "TRUE"}
_NO_LABELS = {"NO", "0", "FALSE"}

# Verbs that invert "subject == winner" — if present, a blind subject parse is
# unsafe ("Will X lose to Y?" => YES pays on Y). Bail to None until hardened.
_INVERSION_RX = re.compile(r"\b(lose|loses|lost|losing|defeated by|eliminated)\b", re.I)


def normalize_team(name: str) -> str:
    """Lowercase, treat underscores/punctuation as separators, collapse
    whitespace. Keeps Unicode letters (\\w is unicode-aware) so international
    team names survive; only underscore is stripped explicitly since \\w keeps
    it."""
    s = str(name or "").lower().replace("_", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _aliases(team: str, alias_expand: Optional[Callable[[str], Iterable[str]]]) -> set:
    """Normalized alias set for a team: the name itself plus any injected
    aliases (the matcher supplies the real esports_team_aliases map)."""
    out = {normalize_team(team)}
    if alias_expand is not None:
        try:
            out |= {normalize_team(a) for a in alias_expand(team) if a}
        except Exception:
            pass
    out.discard("")
    return out


def _first_position(question_norm: str, alias_set: set) -> Optional[int]:
    """Earliest character index at which any alias of a team appears as a
    whole-word run in the question, or None if absent."""
    best: Optional[int] = None
    for alias in alias_set:
        if not alias:
            continue
        m = re.search(r"\b" + re.escape(alias) + r"\b", question_norm)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def resolve_yes_is_team_a(
    yes_outcome: Optional[str],
    question: Optional[str],
    team_a: str,
    team_b: str,
    *,
    alias_expand: Optional[Callable[[str], Iterable[str]]] = None,
) -> Optional[bool]:
    """True if YES resolves on team_a, False if on team_b, None if unresolvable.

    Args:
        yes_outcome: the YES token's ``outcome`` label. "Yes"/"1"/"True" -> shape
            1 (parse the question subject). Any other non-empty string -> shape 2
            (the label is a team name). None/"" -> fall back to the shape-1 parse.
        question: the market question text (needed for shape 1).
        team_a, team_b: the sharp book's two teams (odds_loader 'A'/'B' order).
        alias_expand: optional team -> aliases callable (inject the matcher's
            alias map so short forms like "G2" resolve).

    Never returns a bool unless the mapping is unambiguous (correct-or-absent).
    """
    na, nb = normalize_team(team_a), normalize_team(team_b)
    if not na or not nb or na == nb:
        return None  # degenerate team inputs — cannot orient

    aliases_a = _aliases(team_a, alias_expand)
    aliases_b = _aliases(team_b, alias_expand)

    label = normalize_team(yes_outcome).upper() if yes_outcome else ""

    # ── shape 2: the YES outcome label is itself a team name ─────────────────
    if label and label not in _YES_LABELS and label not in _NO_LABELS:
        lab_norm = normalize_team(yes_outcome)
        in_a = lab_norm in aliases_a
        in_b = lab_norm in aliases_b
        if in_a and not in_b:
            return True
        if in_b and not in_a:
            return False
        return None  # matches both or neither — ambiguous

    # A bare NO label given where the YES outcome was expected: caller error.
    if label in _NO_LABELS:
        return None

    # ── shape 1: "Will <team> win?" — YES pays on the question's subject ──────
    if not question:
        return None
    qn = normalize_team(question)
    if _INVERSION_RX.search(str(question)):
        return None  # "lose"/"defeat" phrasing — unsafe to parse blind

    pos_a = _first_position(qn, aliases_a)
    pos_b = _first_position(qn, aliases_b)

    if pos_a is not None and pos_b is None:
        return True
    if pos_b is not None and pos_a is None:
        return False
    if pos_a is not None and pos_b is not None:
        if pos_a == pos_b:
            return None  # same start (e.g. one name contains the other) — bail
        return pos_a < pos_b  # earliest-mentioned team is the subject
    return None  # neither team found in the question
