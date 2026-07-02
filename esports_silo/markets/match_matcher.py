#!/usr/bin/env python3
"""esports_silo — market↔match matcher (two-team gate + alias resolution).

Links a Polymarket market (its question text) to a specific silo `matches` row. The core
is PURE (operates on plain data) so it is fully testable without a DB/network; thin async
DB helpers at the bottom load the alias map + candidate matches on the operator's box.

SURGICAL CUT (Cmd 3) from `esports/markets/esports_market_scanner.py`:
  * the TWO-TEAM GATE (`_both_teams_present` / `_team_present`, `:139-196`) — verified-correct
    logic: a market matches only if BOTH teams appear in the question (the pre-S195 bug accepted
    either team, so famous teams like T1 swallowed unrelated markets).
  * the SPECIFICITY heuristic (`_specificity_score`, `:198-229`) — ranks match-specific
    questions above season/handicap/playoff sub-markets.
Copied self-contained; NOT imported from the 15-bot system.

DEVIATIONS (flagged per Cmd 3):
  * Fuzzy fallback: the source uses `rapidfuzz.token_set_ratio`. rapidfuzz is NOT a silo
    dependency (requirements.txt is deliberately minimal), so when it is absent we fall back to
    a stdlib `difflib`-based token-set ratio — a documented APPROXIMATION, same 0-100 scale and
    the same `FUZZY_THRESHOLD` (80). If rapidfuzz IS installed it is used, matching the source.
  * Alias map orientation: the silo `team_aliases` table is `alias -> canonical` (`schema.sql:96`).
    We build `canonical_lc -> [alias_lc, ...]` from it, the shape the gate consumes.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# fuzzy threshold — verbatim from the source (`_FUZZY_THRESHOLD = 80.0`, scanner :40).
FUZZY_THRESHOLD = 80.0

try:  # prefer rapidfuzz if the operator's box has it (matches the source exactly)
    from rapidfuzz import fuzz as _rapidfuzz_fuzz  # type: ignore
    _RAPIDFUZZ = True
except ImportError:  # silo default: stdlib approximation (DEVIATION)
    _rapidfuzz_fuzz = None
    _RAPIDFUZZ = False

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> List[str]:
    return _TOKEN.findall(s.lower())


def fuzzy_ratio(a: str, b: str) -> float:
    """token_set_ratio-style similarity in [0,100].

    rapidfuzz.token_set_ratio when available; otherwise a difflib approximation over the
    sorted token SETS of each string (order-independent, like token_set_ratio). DEVIATION.
    """
    if _RAPIDFUZZ:
        return float(_rapidfuzz_fuzz.token_set_ratio(a, b))
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    # token_set_ratio compares the sorted intersection+remainder; difflib on the sorted
    # token strings is a faithful-enough stand-in for our substring-first use.
    sa = " ".join(sorted(ta))
    sb = " ".join(sorted(tb))
    return 100.0 * difflib.SequenceMatcher(None, sa, sb).ratio()


# ======================================================================================
# alias map
# ======================================================================================
def build_alias_map(rows: List[Dict[str, str]], game: Optional[str] = None
                    ) -> Dict[str, List[str]]:
    """`canonical_lc -> [alias_lc, ...]` from `team_aliases` rows (alias, canonical, game).

    If `game` is given, only rows for that game (or game-agnostic rows with empty game) count.
    """
    out: Dict[str, List[str]] = {}
    for r in rows:
        if game is not None:
            rg = (r.get("game") or "")
            if rg and rg != game:
                continue
        canon = (r.get("canonical") or "").strip().lower()
        alias = (r.get("alias") or "").strip().lower()
        if not canon or not alias:
            continue
        bucket = out.setdefault(canon, [])
        if alias != canon and alias not in bucket:
            bucket.append(alias)
    return out


# ======================================================================================
# the two-team gate (surgical cut)
# ======================================================================================
def team_present(team_name: str, question_lc: str, alias_map: Dict[str, List[str]],
                 threshold: float = FUZZY_THRESHOLD) -> Tuple[bool, float]:
    """Is ONE team mentioned in the question? 3-stage: substring → alias substring → fuzzy.

    Returns (matched, score): 100.0 for a substring/alias hit, the fuzzy value otherwise.
    """
    team_lc = (team_name or "").strip().lower()
    if not team_lc:
        return False, 0.0
    if team_lc in question_lc:                                  # stage 1: direct substring
        return True, 100.0
    for variant in alias_map.get(team_lc, []):                 # stage 2: alias substring
        v = (variant or "").strip().lower()
        if v and v != team_lc and v in question_lc:
            return True, 100.0
    score = fuzzy_ratio(team_lc, question_lc)                  # stage 3: fuzzy fallback
    return (score >= threshold), score


def both_teams_present(team_a: str, team_b: str, question_lc: str,
                       alias_map: Dict[str, List[str]], threshold: float = FUZZY_THRESHOLD
                       ) -> Tuple[bool, float, float]:
    """Require BOTH teams present (the verified-correct gate). Returns (both, score_a, score_b)."""
    a_ok, a_score = team_present(team_a, question_lc, alias_map, threshold)
    b_ok, b_score = team_present(team_b, question_lc, alias_map, threshold)
    return (a_ok and b_ok), a_score, b_score


def specificity_score(question_lc: str) -> float:
    """Higher = more likely ONE specific match (not a season/handicap/sub-market). Source :198."""
    score = 100.0
    if " vs " in question_lc or " vs. " in question_lc:
        score += 30.0
    season_terms = ("season", "playoff", "championship", "tournament", "regular season", "split")
    if any(t in question_lc for t in season_terms):
        score -= 50.0
    sub_terms = ("- map ", "- game ", "map winner", "game winner", "handicap",
                 "total maps", " over ", " under ", "first blood", "first kill")
    if any(t in question_lc for t in sub_terms):
        score -= 20.0
    return score


# ======================================================================================
# linking
# ======================================================================================
@dataclass
class MatchLink:
    match_id: str
    team_a: str
    team_b: str
    score_a: float
    score_b: float
    specificity: float

    @property
    def rank_key(self) -> Tuple[float, float]:
        # specificity first (match-specific > season), then match-quality of the weaker team.
        return (self.specificity, min(self.score_a, self.score_b))


@dataclass
class LinkResult:
    link: Optional[MatchLink]
    near_miss: Optional[Dict[str, Any]] = None  # best single-team partial, for alias triage


def link_market(question: str, candidate_matches: List[Dict[str, Any]],
                alias_map: Dict[str, List[str]], threshold: float = FUZZY_THRESHOLD
                ) -> LinkResult:
    """Best silo match for a market question, via the two-team gate + specificity ranking.

    `candidate_matches`: dicts with match_id, team_a, team_b (pre-filter to the market's game
    and time window on the caller side — the DB helper below does that). Returns the highest-
    ranked match whose BOTH teams appear, or a near-miss (best single-team partial) when none
    pass — the near-miss is triage data ("only team X matched, question said …→ add an alias").
    """
    q = (question or "").lower()
    spec = specificity_score(q)
    passed: List[MatchLink] = []
    best_partial: Optional[Dict[str, Any]] = None
    for mm in candidate_matches:
        ta, tb = str(mm.get("team_a") or ""), str(mm.get("team_b") or "")
        both, sa, sb = both_teams_present(ta, tb, q, alias_map, threshold)
        if both:
            passed.append(MatchLink(str(mm.get("match_id")), ta, tb, sa, sb, spec))
        else:
            partial = max(sa, sb)
            if best_partial is None or partial > best_partial["score"]:
                best_partial = {"match_id": mm.get("match_id"), "team_a": ta, "team_b": tb,
                                "score": partial, "question": question}
    if passed:
        passed.sort(key=lambda x: x.rank_key, reverse=True)
        return LinkResult(link=passed[0])
    return LinkResult(link=None, near_miss=best_partial)


# ======================================================================================
# thin DB layer (needs the operator's box; the logic above is DB-free and tested)
# ======================================================================================
async def load_alias_map(conn, game: Optional[str] = None) -> Dict[str, List[str]]:
    """Load `team_aliases` into a canonical→[alias] map (optionally scoped to a game)."""
    if game:
        rows = await conn.fetch(
            "SELECT alias, canonical, game FROM team_aliases WHERE game = $1 OR game = ''", game)
    else:
        rows = await conn.fetch("SELECT alias, canonical, game FROM team_aliases")
    return build_alias_map([dict(r) for r in rows], game)


async def load_candidate_matches(conn, game: str, start_time, window_hours: int = 48
                                 ) -> List[Dict[str, Any]]:
    """Matches for a game within ±window of a market's time — the linker's candidate set."""
    rows = await conn.fetch(
        """SELECT match_id, team_a, team_b, start_time FROM matches
            WHERE game = $1 AND start_time BETWEEN $2 - ($3 || ' hours')::interval
                                              AND $2 + ($3 || ' hours')::interval""",
        game, start_time, str(window_hours))
    return [dict(r) for r in rows]
