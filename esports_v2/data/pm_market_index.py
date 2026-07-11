"""Polymarket match-winner market index for GAP B (bet-time PM price capture).

Builds a list of live Gamma esports (``tag_id=64``) shape-2 MATCH-WINNER markets
so the forward-collector (``esports_v2/scripts/collect_pinnodds.py``) can snapshot
the matched Polymarket YES price + ``condition_id``/``yes_token_id`` alongside
each PinnOdds sharp line. That is the data the real
``edge = sharp_prob - PM_price - fee`` backtest (``sharp_eval.edge_backtest``)
needs — the forward-collector otherwise captures PinnOdds only (handoff GAP B).

MATCHING (``match_pm_ref``) reuses the SAME conservative, bijective,
correct-or-absent matcher the results join trusts (``model.team_match``): a
PinnOdds row matches a PM market only when BOTH teams line up (exact-normalized,
injected-alias overlap, or a shared-non-generic token-subset — "G2 Esports" <->
"G2") on the same day (± a small window). This lifts coverage past exact-name
equality (which missed "Team Vitality" vs "Vitality") without a looser rule — a
wrong PM attachment would corrupt both price and orientation (the S152/B2 loss
class). If a row matches TWO distinct PM markets, it is dropped as ambiguous.

CORRECT-OR-ABSENT throughout: only clean, unambiguous match winners are indexed
and matched; any doubt -> the snapshot row keeps ``None`` PM fields:
  - Yes/No (shape-1) futures skipped; team-name PROP markets rejected (Game N /
    Map N / Handicap / Kills / Total / Odd-Even / Over-Under / ...); a title must
    be a head-to-head (contains " vs ").
  - The reference "YES" token is index 0 of the parallel
    ``outcomes``/``clobTokenIds``/``outcomePrices`` arrays. At eval time
    ``clob_labels`` maps ``yes_token_id`` -> the authoritative CLOB team NAME and
    ``resolve_yes_is_team_a`` aligns it to the sharp odds' ``team_a``
    (flip-proof), so the arbitrary index-0 choice cannot invert the edge.

The HTTP fetch is injectable (``fetch_page`` param) so the index logic is fully
unit-testable offline; the default fetch pages the public Gamma API exactly like
``scripts/esports_market_shape_probe_public.py``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from esports_v2.model.team_match import match_teams

logger = logging.getLogger(__name__)

_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
_ESPORTS_TAG_ID = 64  # gamma tag slug "esports" (verified 2026-07-08)
_GAMMA_PAGE = 100     # gamma caps `limit` at 100 regardless of the value asked

# Labels that mean a shape-1 Yes/No market (futures/outright — not a match-winner
# head-to-head). If both outcome labels are in this set the market is skipped.
_YES_NO_LABELS = {"YES", "NO", "1", "0", "TRUE", "FALSE"}

# A head-to-head match/series winner title contains " vs " (positive gate).
_VS_RX = re.compile(r"\bvs\.?\b", re.I)

# Prop markets that ALSO carry team-name outcomes (so they pass the shape-2 gate)
# but are NOT the match winner. Rejecting these is what keeps a PinnOdds
# match-winner line from being priced against a game/map/handicap/total prop.
# Verified against 948 live shape-2 markets (2026-07-09): this isolates the real
# match-winner markets and rejects every prop, with zero false drops.
_PROP_RX = re.compile(
    r"\bgame\s*\d"            # "Game 1 Winner", "- Game 2"
    r"|\bmap\s*\d"           # "Map 1: ..."
    r"|handicap"             # "Kill Handicap", "Game Handicap"
    r"|\bkills?\b"
    r"|\btotal\b"
    r"|odd\s*/\s*even|\bodd\b|\beven\b"
    r"|over\s*/\s*under|\bover\b|\bunder\b"
    r"|\brounds?\b|\btowers?\b|first blood"
    r"|\bspread\b|correct score|most (picked|banned)",
    re.I,
)

# fetch_page(offset, limit) -> list[market dict] | None (None/[] ends paging).
FetchPage = Callable[[int, int], Optional[List[dict]]]
# alias_expand(team) -> [aliases]; injected on the VPS from esports_team_aliases.
AliasExpand = Callable[[str], Iterable[str]]


@dataclass
class PMMarketRef:
    """The Polymarket match-winner reference for one market, captured at snapshot
    time. ``team_a``/``team_b`` are the two outcome team names (``team_a`` ==
    ``yes_outcome`` == index-0 outcome); ``day`` is the ``gameStartTime`` day
    (YYYY-MM-DD). ``market_price`` is the Gamma-reported price of the index-0
    (reference "YES") token, or ``None`` if missing/degenerate."""

    condition_id: str
    yes_token_id: str
    yes_outcome: str
    market_price: Optional[float]
    question: str
    game_start: str
    team_a: str
    team_b: str
    day: str


def _json_list(raw) -> List:
    """Gamma stores ``outcomes``/``clobTokenIds``/``outcomePrices`` as a
    JSON-encoded string (occasionally already a list). Parse to a list, or []
    on anything malformed (correct-or-absent)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _coerce_price(v) -> Optional[float]:
    """A Polymarket price is a probability in the OPEN interval (0, 1). A price at
    0.0 or 1.0 means the market is resolved/degenerate -> None. Unparseable ->
    None. (The market is still indexable for orientation; only the price drops.)"""
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return f if 0.0 < f < 1.0 else None


def _day(value) -> str:
    """The calendar day (YYYY-MM-DD, UTC-ish) of an ISO-ish timestamp, or "".

    Both PinnOdds ``starts`` (``2026-07-10T13:45:00Z``) and Gamma
    ``gameStartTime`` (``2026-07-10 13:45:00+00``) lead with the date, so a
    10-char slice is the same day key on both sides."""
    s = str(value or "").strip()
    return s[:10] if len(s) >= 10 else ""


def parse_gamma_market(m: dict) -> Optional[PMMarketRef]:
    """Parse one Gamma market into a ``PMMarketRef``, or None.

    Correct-or-absent: returns None for anything that is not a clean, two-outcome,
    head-to-head, non-prop match winner with a usable ``condition_id``,
    ``clobTokenIds`` and ``gameStartTime``.
    """
    if not isinstance(m, dict):
        return None

    question = str(m.get("question") or "").strip()
    if not question or not _VS_RX.search(question) or _PROP_RX.search(question):
        return None  # not a head-to-head match winner (or a prop) -> skip

    outcomes = _json_list(m.get("outcomes"))
    if len(outcomes) != 2:
        return None
    labels_upper = {str(o).strip().upper() for o in outcomes if str(o).strip()}
    if not labels_upper or labels_upper <= _YES_NO_LABELS:
        return None  # shape-1 Yes/No (futures) -> not a match-winner target
    team_a = str(outcomes[0]).strip()
    team_b = str(outcomes[1]).strip()
    if not team_a or not team_b:
        return None

    token_ids = _json_list(m.get("clobTokenIds"))
    if len(token_ids) != 2:
        return None
    yes_token_id = str(token_ids[0]).strip()
    if not yes_token_id:
        return None

    condition_id = str(m.get("conditionId") or "").strip()
    if not condition_id:
        return None

    game_start = str(m.get("gameStartTime") or "").strip()
    day = _day(game_start)
    if not day:
        return None  # no start day -> cannot match to a dated odds row

    prices = _json_list(m.get("outcomePrices"))
    market_price = _coerce_price(prices[0]) if len(prices) == 2 else None

    return PMMarketRef(
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        yes_outcome=team_a,
        market_price=market_price,
        question=question,
        game_start=game_start,
        team_a=team_a,
        team_b=team_b,
        day=day,
    )


def build_pm_index(
    *,
    fetch_page: Optional[FetchPage] = None,
    max_pages: int = 30,
    workers: int = 8,
) -> List[PMMarketRef]:
    """Page the Gamma esports tag into a list of PM match-winner refs.

    Parses each market correct-or-absent and de-dupes by ``condition_id`` (paging
    overlap). Returns [] on any fetch failure at page 0 (the collector then simply
    writes odds with null PM fields — no regression). Ambiguity between distinct
    markets is resolved at MATCH time (``match_pm_ref``), not here.

    LATENCY (2026-07-11): page 0 is fetched first (it alone decides the
    fail-to-[] contract), then the remaining pages fetch CONCURRENTLY in
    WAVES of ``workers`` (stdlib threads; the work is pure I/O wait). Wave
    scheduling stops at the first wave containing a short/empty/failed page,
    so only one wave probes past the end/offset-cap instead of blind-fetching
    all ``max_pages``. A failed page with data on a LATER page (a provable
    mid-range hole) gets ONE retry; if it still fails it is logged and
    treated as absent — coverage is >= the old sequential behavior, which
    silently dropped that page AND every page after it. Assembly is in page
    order, so output ordering and dedup semantics are unchanged.
    ``workers=1`` degrades to strictly sequential fetching.
    """
    fetch_page = fetch_page or _default_fetch_page

    first = fetch_page(0, _GAMMA_PAGE)
    if not first:
        logger.info("pm_index_built refs=0 (page 0 empty/failed)")
        return []

    pages: Dict[int, Optional[List[dict]]] = {0: first}
    if len(first) >= _GAMMA_PAGE and max_pages > 1:
        from concurrent.futures import ThreadPoolExecutor

        def _fetch(p: int):
            return p, fetch_page(p * _GAMMA_PAGE, _GAMMA_PAGE)

        next_page = 1
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            while next_page < max_pages:
                wave = list(range(next_page, min(next_page + max(1, workers),
                                                 max_pages)))
                pages.update(dict(pool.map(_fetch, wave)))
                # retry ONLY provable mid-range holes (None with data later)
                holes = [p for p in wave if pages[p] is None
                         and any(pages.get(q) for q in pages if q > p)]
                if holes:
                    pages.update(dict(pool.map(_fetch, holes)))
                    for p in holes:
                        if pages[p] is None:
                            logger.warning(f"pm_index_page_absent page={p} "
                                           f"(hole after retry)")
                if any(pages[p] is None or len(pages[p]) < _GAMMA_PAGE
                       for p in wave):
                    break  # end of data / offset cap reached in this wave
                next_page = wave[-1] + 1

    refs: List[PMMarketRef] = []
    seen_cid: set = set()
    for page in range(max_pages):
        markets = pages.get(page)
        if markets is None:
            if page in pages and any(pages.get(q) for q in range(page + 1, max_pages)):
                logger.warning(f"pm_index_page_absent page={page} (hole after retry)")
                continue  # later pages exist -> mid-range hole, keep going
            break         # deterministic end (cap/end-of-data) or sequential tail
        for m in markets:
            ref = parse_gamma_market(m)
            if ref is None or ref.condition_id in seen_cid:
                continue
            seen_cid.add(ref.condition_id)
            refs.append(ref)
        if len(markets) < _GAMMA_PAGE:
            break

    logger.info(f"pm_index_built refs={len(refs)}")
    return refs


def _days_in_window(day: str, window: int) -> set:
    """{day} widened by +/- ``window`` calendar days (correct-or-absent: the raw
    day back if it can't be parsed)."""
    from datetime import datetime, timedelta

    try:
        base = datetime.strptime(day, "%Y-%m-%d")
    except (ValueError, TypeError):
        return {day}
    return {(base + timedelta(days=off)).strftime("%Y-%m-%d")
            for off in range(-window, window + 1)}


def match_pm_ref(
    home: str,
    away: str,
    starts,
    refs: List[PMMarketRef],
    *,
    alias_expand: Optional[AliasExpand] = None,
    day_window: int = 1,
) -> Optional[PMMarketRef]:
    """Find THE Polymarket match-winner ref for one PinnOdds row, or None.

    A ref is a candidate when its day is within ``day_window`` of the odds row's
    day AND both teams bijectively match (``team_match.match_teams`` — same
    conservative rule the results join uses). Correct-or-absent: 0 candidates ->
    None; candidates spanning >1 distinct ``condition_id`` -> None (ambiguous,
    never guessed); exactly one market -> that ref.
    """
    day = _day(starts)
    if not day:
        return None
    target_days = _days_in_window(day, day_window)

    candidates = [
        r for r in refs
        if r.day in target_days
        and match_teams(home, away, r.team_a, r.team_b, alias_expand) is not None
    ]
    if not candidates:
        return None
    cids = {r.condition_id for r in candidates}
    if len(cids) != 1:
        return None  # matched two different PM markets -> ambiguous, drop
    return candidates[0]


def _default_fetch_page(offset: int, limit: int) -> Optional[List[dict]]:
    """GET one page of the live esports-tag Gamma feed. None on any failure
    (correct-or-absent — the collector then skips PM enrichment for this tick)."""
    import requests

    # order=id&ascending=false (newest market first) is REQUIRED for coverage:
    # Gamma hard-caps offset paging (422 "offset too large" past ~2100) and the
    # esports tag holds ~3600 active markets. Default (oldest-first) order left
    # the newest ~1500 markets unreachable — 93 of 130 live match-winner markets
    # were invisible to the index (measured 2026-07-10). Newest-first puts the
    # upcoming slate in page 0 and pushes the truncation onto stale history.
    url = (
        f"{_GAMMA_URL}?tag_id={_ESPORTS_TAG_ID}&closed=false&active=true"
        f"&archived=false&order=id&ascending=false&limit={limit}&offset={offset}"
    )
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "eb-pm-index/1.0"})
        if r.status_code != 200:
            logger.warning(f"pm_index_gamma_status offset={offset} code={r.status_code}")
            return None
        data = r.json()
    except Exception as e:  # noqa: BLE001 — correct-or-absent on any transport error
        logger.warning(f"pm_index_gamma_error offset={offset} err={type(e).__name__}")
        return None
    return data if isinstance(data, list) else None
