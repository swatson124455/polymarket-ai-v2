"""Join sharp closing lines to the free match RESULTS we already have on disk.

Step 2 of the sharp-line backtest: given the per-match closing lines from
``closing_line.reduce_to_closing_lines`` (keyed by ``match_key``), attach WHO WON
by matching each line to a finished match in the free results corpus
(``data/esports_matches_bulk.jsonl`` — Oracle's Elixir + GRID; and
``data/cs2/pandascore_cs2.json`` — PandaScore). The join key is the two team
names (via the alias matcher) plus the match day.

CORRECT-OR-ABSENT, because a wrong winner label silently corrupts every metric
downstream (the "impossible numbers" failure class):
  - a line joins only when BOTH its teams map bijectively to the two teams of a
    finished match (each line-team to a distinct result-team);
  - if the same day+teams has MULTIPLE finished matches with DIFFERENT winners
    (e.g. per-game rows of a Bo-series, or two group-stage meetings), the join is
    AMBIGUOUS for a match-winner line -> dropped;
  - team equality is exact-normalized, alias-overlap (inject the real
    ``esports_team_aliases`` map on the VPS), or a conservative token-subset that
    shares a non-generic token ("G2 Esports" <-> "G2"). No fuzzy thresholds.

The alias hook mirrors ``orientation.resolve_yes_is_team_a`` exactly: pass an
``alias_expand(team) -> [aliases]`` callable to inject the matcher's alias map;
offline it works on exact-normalized + token-subset matches.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from esports_v2.model.closing_line import ClosingLine, parse_ts
from esports_v2.model.orientation import normalize_team

# Generic tokens that don't distinguish orgs — a shared generic token is not
# enough to call two names the same team. Mirrors the concept behind
# seed_esports_team_aliases._GENERIC_TOKENS (kept local so this module has no
# script dependency / no dotenv import side effect).
_GENERIC_TOKENS = {
    "esports", "esport", "e-sports", "gaming", "team", "club",
    "academy", "youth", "challengers", "challenger", "junior",
}


@dataclass
class ResultRecord:
    """One finished match from a free results source."""

    match_id: str
    game: str
    team_a: str
    team_b: str
    winner: Optional[str]      # winning team NAME (not 'a'/'b')
    day: str                   # match day, "YYYY-MM-DD" (UTC)
    source: str


@dataclass
class JoinedRecord:
    """A closing line joined to its realized outcome (backtest-ready)."""

    match_key: str
    home: str                  # line 'A' side (odds_a)
    away: str                  # line 'B' side (odds_b)
    starts: str
    day: str
    odds_a: float
    odds_b: float
    open_odds_a: float
    open_odds_b: float
    winner: str                # winning team NAME from the result
    home_won: bool             # True iff the odds_a/home team won
    game: str
    source: str
    result_match_id: str


# ── team-equality primitives (correct-or-absent) ─────────────────────────────


def _alias_set(team: str, alias_expand: Optional[Callable[[str], Iterable[str]]]) -> set:
    out = {normalize_team(team)}
    if alias_expand is not None:
        try:
            out |= {normalize_team(a) for a in alias_expand(team) if a}
        except Exception:
            pass
    out.discard("")
    return out


def _same_team(
    x: str, y: str, alias_expand: Optional[Callable[[str], Iterable[str]]]
) -> bool:
    """True iff two team names refer to the same org (conservative).

    Match on: exact normalized equality, alias-set overlap, or a token-subset
    that shares a NON-GENERIC token. The last rule links "G2 Esports" <-> "G2"
    and "DRX Academy" <-> "DRX" without linking "R2 Esports Club" <-> "Esports
    Club" (all-generic overlap) — the same guard the seed script trusts.
    """
    ax, ay = _alias_set(x, alias_expand), _alias_set(y, alias_expand)
    if ax & ay:
        return True
    nx, ny = normalize_team(x), normalize_team(y)
    if not nx or not ny:
        return False
    tx, ty = set(nx.split()), set(ny.split())
    if not tx or not ty:
        return False
    shared_non_generic = (tx & ty) - _GENERIC_TOKENS
    if not shared_non_generic:
        return False
    return tx.issubset(ty) or ty.issubset(tx)


def _match_teams(
    home: str, away: str, res_a: str, res_b: str,
    alias_expand: Optional[Callable[[str], Iterable[str]]],
) -> Optional[bool]:
    """Bijectively map {home, away} to {res_a, res_b}.

    Returns True if (home==res_a, away==res_b), False if (home==res_b,
    away==res_a), None if the mapping is not an unambiguous bijection (a team
    matches both/neither, or the pairing is inconsistent).
    """
    ha, hb = _same_team(home, res_a, alias_expand), _same_team(home, res_b, alias_expand)
    aa, ab = _same_team(away, res_a, alias_expand), _same_team(away, res_b, alias_expand)
    # Orientation 1: home->A, away->B
    orient1 = ha and ab and not hb and not aa
    # Orientation 2: home->B, away->A
    orient2 = hb and aa and not ha and not ab
    if orient1 and not orient2:
        return True
    if orient2 and not orient1:
        return False
    return None  # ambiguous or no clean bijection


# ── result loaders (free corpora) ────────────────────────────────────────────


def load_bulk_results(path: str | Path) -> List[ResultRecord]:
    """Load ``esports_matches_bulk.jsonl`` (Oracle's Elixir + GRID) rows.

    NOTE: Oracle LoL rows are per-GAME (``..._game_1``/``_game_2``), so a single
    Bo-series shows as multiple rows that the join's ambiguity guard will drop
    when their winners differ — intentional (a game result is not a match-winner
    outcome). PandaScore/GRID CS2 rows are match-level.
    """
    out: List[ResultRecord] = []
    p = Path(path)
    if not p.exists():
        return out
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ta, tb = str(d.get("team_a") or ""), str(d.get("team_b") or "")
            if not ta or not tb:
                continue
            day_dt = parse_ts(d.get("match_date"))
            if day_dt is None:
                continue
            out.append(ResultRecord(
                match_id=str(d.get("match_id") or ""),
                game=str(d.get("game") or ""),
                team_a=ta, team_b=tb,
                winner=(str(d["winner"]) if d.get("winner") else None),
                day=day_dt.strftime("%Y-%m-%d"),
                source=str(d.get("source") or "bulk"),
            ))
    return out


def load_pandascore_cs2(path: str | Path) -> List[ResultRecord]:
    """Load ``data/cs2/pandascore_cs2.json`` (match-level CS2 results)."""
    out: List[ResultRecord] = []
    p = Path(path)
    if not p.exists():
        return out
    try:
        data = json.load(open(p, "r", encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    if not isinstance(data, list):
        return out
    for m in data:
        if not isinstance(m, dict):
            continue
        teams = m.get("teams") or []
        if len(teams) < 2:
            continue
        ta = str((teams[0] or {}).get("name") or "")
        tb = str((teams[1] or {}).get("name") or "")
        if not ta or not tb:
            continue
        day_dt = parse_ts(m.get("startedAt"))
        if day_dt is None:
            continue
        out.append(ResultRecord(
            match_id=str(m.get("id") or ""),
            game="cs2",
            team_a=ta, team_b=tb,
            winner=(str(m["winner"]) if m.get("winner") else None),
            day=day_dt.strftime("%Y-%m-%d"),
            source=str(m.get("source") or "pandascore"),
        ))
    return out


# ── the join ─────────────────────────────────────────────────────────────────


def _index_by_day(results: Iterable[ResultRecord]) -> Dict[str, List[ResultRecord]]:
    idx: Dict[str, List[ResultRecord]] = {}
    for r in results:
        idx.setdefault(r.day, []).append(r)
    return idx


def _days_in_window(day: str, window: int) -> List[str]:
    from datetime import timedelta
    base = parse_ts(day)
    if base is None:
        return [day]
    return [(base + timedelta(days=off)).strftime("%Y-%m-%d")
            for off in range(-window, window + 1)]


@dataclass
class JoinStats:
    """Coverage accounting for a join run (surfaced, never silently dropped)."""

    n_lines: int = 0
    joined: int = 0
    no_result_match: int = 0
    ambiguous_multi_winner: int = 0
    winner_not_a_team: int = 0


def join_closing_lines_to_results(
    closing: Dict[str, ClosingLine],
    results: Iterable[ResultRecord],
    *,
    alias_expand: Optional[Callable[[str], Iterable[str]]] = None,
    day_window: int = 0,
) -> Tuple[List[JoinedRecord], JoinStats]:
    """Join closing lines to finished-match results; return (records, stats).

    ``day_window`` widens the day match to +/- N days (default 0 = same UTC day).
    Correct-or-absent: a line joins only on an unambiguous team bijection to a
    finished match whose winner is one of the two teams; same-day+teams matches
    with conflicting winners are dropped as ambiguous. Every drop is counted in
    ``JoinStats`` so coverage is explicit.
    """
    by_day = _index_by_day(results)
    records: List[JoinedRecord] = []
    stats = JoinStats(n_lines=len(closing))

    for key, cl in closing.items():
        day = ""
        starts_dt = parse_ts(cl.starts)
        if starts_dt is not None:
            day = starts_dt.strftime("%Y-%m-%d")

        # Candidate results across the day window that bijectively match teams.
        candidates: List[Tuple[ResultRecord, bool]] = []
        seen_days = _days_in_window(day, day_window) if day else []
        for d in seen_days:
            for r in by_day.get(d, []):
                orient = _match_teams(cl.home, cl.away, r.team_a, r.team_b, alias_expand)
                if orient is None:
                    continue
                candidates.append((r, orient))

        if not candidates:
            stats.no_result_match += 1
            continue

        # Resolve each candidate's outcome relative to the line's home team.
        outcomes = []  # (result, home_won: Optional[bool])
        for r, orient in candidates:
            if not r.winner:
                continue
            # winner matched to result's a/b, then mapped to line home/away.
            win_is_res_a = _same_team(r.winner, r.team_a, alias_expand)
            win_is_res_b = _same_team(r.winner, r.team_b, alias_expand)
            if win_is_res_a == win_is_res_b:  # winner ambiguous vs the two teams
                continue
            # orient True: home==res_a. home_won iff winner is the team home maps to.
            if orient:
                home_won = win_is_res_a
            else:
                home_won = win_is_res_b
            outcomes.append((r, home_won))

        if not outcomes:
            stats.winner_not_a_team += 1
            continue

        distinct = {ow for _, ow in outcomes}
        if len(distinct) > 1:
            stats.ambiguous_multi_winner += 1
            continue  # conflicting winners for the same match-winner line — drop

        r0, home_won = outcomes[0]
        records.append(JoinedRecord(
            match_key=key, home=cl.home, away=cl.away, starts=cl.starts, day=day,
            odds_a=cl.odds_a, odds_b=cl.odds_b,
            open_odds_a=cl.open_odds_a, open_odds_b=cl.open_odds_b,
            winner=r0.winner, home_won=home_won,
            game=r0.game, source=r0.source, result_match_id=r0.match_id,
        ))
        stats.joined += 1

    return records, stats
