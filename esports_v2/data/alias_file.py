"""File-backed alias_expand for the sharp-line pipeline (esports_team_aliases).

The conservative team matcher (``model.team_match``) and every consumer of it
(PM market matching, results join, CLOB orientation) accept an injectable
``alias_expand(team) -> [aliases]`` callable, but on the VPS cron and in the
audit one-shot nothing feeds it — the 1,777-row ``esports_team_aliases`` DB
table is unreachable from those contexts. This module closes that gap with a
plain JSON file:

    {"groups": [["Natus Vincere", "NAVI"], ["FaZe Clan", "FaZe"], ...]}

produced once (re-runnable) by ``deploy/vps/eb_dump_aliases.sh`` from the DB.
Every name in a group is an alias of every other name in that group.

CORRECT-OR-ABSENT: ``load_alias_expand`` returns ``None`` — not a raising or
empty-but-truthy callable — when the file is missing, unreadable, malformed, or
contains no usable groups. Callers pass the result straight through as the
``alias_expand`` argument; ``None`` means matching simply stays exactly as
strict as it is today. A malformed alias file can therefore never make matching
WORSE, only absent. Lookup keys are normalized with the same
``orientation.normalize_team`` the matcher itself uses, so a hit in here is
consistent with ``team_match.alias_set``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from esports_v2.model.orientation import normalize_team

logger = logging.getLogger(__name__)

AliasExpand = Callable[[str], List[str]]


def build_alias_expand(groups) -> Optional[AliasExpand]:
    """Build an ``alias_expand`` from equivalence groups (lists of team names).

    Names appearing in multiple groups get the union of their groups. Returns
    ``None`` when nothing usable survives validation (correct-or-absent).
    """
    if not isinstance(groups, list):
        return None
    lookup: Dict[str, Set[str]] = {}
    for g in groups:
        if not isinstance(g, list):
            continue
        names = [str(n).strip() for n in g if str(n or "").strip()]
        if len(names) < 2:
            continue  # identity-only group adds nothing
        for n in names:
            key = normalize_team(n)
            if key:
                lookup.setdefault(key, set()).update(names)
    if not lookup:
        return None

    def expand(team: str) -> List[str]:
        return sorted(lookup.get(normalize_team(team), ()))

    return expand


def load_alias_expand(path) -> Optional[AliasExpand]:
    """Load ``aliases.json`` into an ``alias_expand`` callable, or ``None``.

    ``None`` on ANY problem (missing file, bad JSON, wrong shape, empty) — the
    caller then matches without aliases, exactly as before.
    """
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — correct-or-absent on any read/parse failure
        logger.warning(f"alias_file_unreadable path={p} err={type(e).__name__}")
        return None
    if not isinstance(data, dict):
        return None
    expand = build_alias_expand(data.get("groups"))
    if expand is None:
        logger.warning(f"alias_file_empty path={p} — matching continues without aliases")
    return expand
