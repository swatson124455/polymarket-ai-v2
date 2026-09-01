"""Authoritative YES-team resolution from the Polymarket CLOB (flip-proof Step 3).

The sharp-line edge INVERTS if the Polymarket YES token is aligned to the wrong
team (the S152/B2 loss class). ``EB_SHARP_LINE_PLUMBING.md`` established, from a
live 36-market measurement, that:
  - the DB path stores a ``yes_token_id`` but NO outcome label, so the bot's
    S152 YES-detector silently falls back to token *position*;
  - ``yes_token_id`` is a RELIABLE key (0/36 missing from the live CLOB market);
  - therefore the flip-proof fix is: at orientation time, map the stored
    ``yes_token_id`` -> its CLOB ``outcome`` string (= the authoritative YES team
    NAME), store the NAME (not a bool), and let ``resolve_yes_is_team_a`` align
    that name to the sharp odds' team_a itself.

This module is the CLOB label lookup for that fix. It is read-only (GET
``/markets/{condition_id}``) and correct-or-absent: any fetch error, a shape-1
Yes/No market, or a ``yes_token_id`` not present among the market's tokens yields
None -> the record stays UNCOVERED, never a guessed orientation.

The HTTP fetch is injectable (``fetch`` param) so the resolution logic is fully
unit-testable offline; the default fetch uses ``requests`` against the public
CLOB, exactly like ``scripts/esports_orientation_live_check.py``.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, List, Optional, Tuple

from esports_v2.model.orientation import resolve_yes_is_team_a

logger = logging.getLogger(__name__)

_CLOB_MARKET_URL = "https://clob.polymarket.com/markets/{cid}"

# Labels that mean a shape-1 Yes/No market (NOT team-name outcomes). If every
# token label is one of these, there is no team name to resolve from the label.
_YES_NO_LABELS = {"YES", "NO", "1", "0", "TRUE", "FALSE"}

# Type of the injectable fetch: condition_id -> list[(outcome, token_id)] or None.
ClobFetch = Callable[[str], Optional[List[Tuple[str, str]]]]


def _default_fetch(condition_id: str, timeout: float = 12.0) -> Optional[List[Tuple[str, str]]]:
    """GET the live CLOB market and return [(outcome, token_id)], or None."""
    import requests

    try:
        r = requests.get(_CLOB_MARKET_URL.format(cid=condition_id), timeout=timeout)
        if r.status_code != 200:
            logger.warning(f"clob_market_status cid={condition_id[:12]} code={r.status_code}")
            return None
        d = r.json()
    except Exception as e:  # noqa: BLE001 — correct-or-absent on any transport error
        logger.warning(f"clob_market_error cid={condition_id[:12]} err={type(e).__name__}")
        return None
    toks = d.get("tokens") if isinstance(d, dict) else None
    if not isinstance(toks, list):
        return None
    out: List[Tuple[str, str]] = []
    for t in toks:
        if not isinstance(t, dict):
            continue
        out.append((str(t.get("outcome") or ""), str(t.get("token_id") or "")))
    return out


def resolve_yes_team_from_clob(
    condition_id: str,
    yes_token_id: str,
    *,
    fetch: Optional[ClobFetch] = None,
) -> Optional[str]:
    """Authoritative YES team NAME for a shape-2 market, or None.

    Fetches the CLOB market's ``tokens`` and returns the ``outcome`` string of the
    token whose id == ``yes_token_id``. Returns None (correct-or-absent) when:
    the fetch fails, the market is shape-1 Yes/No (label carries no team name),
    the ``yes_token_id`` is absent, or the resolved label is empty.
    """
    if not condition_id or not yes_token_id:
        return None
    fetch = fetch or _default_fetch
    toks = fetch(condition_id)
    if not toks:
        return None

    labels_upper = {str(o).strip().upper() for o, _ in toks if o}
    if labels_upper and labels_upper <= _YES_NO_LABELS:
        return None  # shape-1 Yes/No market — no team name in the label

    by_id = {tid: out for out, tid in toks}
    label = by_id.get(str(yes_token_id))
    if label is None:
        return None  # stored yes_token_id not among the live tokens — unreliable
    label = str(label).strip()
    if not label or label.upper() in _YES_NO_LABELS:
        return None
    return label


def resolve_yes_is_team_a_via_clob(
    condition_id: str,
    yes_token_id: str,
    team_a: str,
    team_b: str,
    *,
    alias_expand: Optional[Callable[[str], Iterable[str]]] = None,
    fetch: Optional[ClobFetch] = None,
) -> Optional[bool]:
    """Flip-proof ``yes_is_team_a`` for a shape-2 market.

    Composes the authoritative CLOB label lookup with the existing shape-2 path
    of ``resolve_yes_is_team_a``: the YES team NAME is fed as the outcome label
    and aligned to the sharp odds' ``team_a``. Correct-or-absent throughout — any
    unresolved link yields None so the record stays uncovered (never a wrong
    bool, which would invert the edge).
    """
    yes_team = resolve_yes_team_from_clob(
        condition_id, yes_token_id, fetch=fetch
    )
    if yes_team is None:
        return None
    return resolve_yes_is_team_a(
        yes_outcome=yes_team,
        question=None,
        team_a=team_a,
        team_b=team_b,
        alias_expand=alias_expand,
    )
