"""Polymarket match-winner market index for GAP B (bet-time PM price capture).

Builds a ``match_key -> PMMarketRef`` lookup from live Gamma esports
(``tag_id=64``) shape-2 markets so the forward-collector
(``esports_v2/scripts/collect_pinnodds.py``) can snapshot the matched Polymarket
YES price + ``condition_id``/``yes_token_id`` alongside each PinnOdds sharp line.
That is the data the real ``edge = sharp_prob - PM_price - fee`` backtest
(``sharp_eval.edge_backtest``) needs — the forward-collector otherwise captures
PinnOdds only, so there is no PM price at bet time (handoff GAP B).

The lookup key is ``odds_loader.make_match_key(outcome_a, outcome_b, game_start)``
— the SAME order-invariant key the PinnOdds rows already carry — so a snapshot
row is enriched by a plain dict lookup.

CORRECT-OR-ABSENT (matches the rest of the sharp-line core): only clean,
unambiguous MATCH-WINNER markets are indexed. Any doubt -> the market is skipped
and the snapshot row keeps ``None`` PM fields (never a guessed price / condition):

  - **Yes/No (shape-1) markets are skipped** — they are futures/outright winners,
    a different odds type, not paired with match-winner sharp odds.
  - **Prop markets that carry team-name outcomes are rejected** (Game N / Map N /
    Handicap / Kills / Total / Odd-Even / Over-Under / Rounds / ...). Matching one
    of these to a PinnOdds match-winner line would corrupt the edge (the S152/B2
    loss class).
  - **A title must be a head-to-head** (contains " vs ") to be indexable.
  - **Collisions are dropped as ambiguous.** If two DIFFERENT markets
    (distinct ``condition_id``) resolve to the same ``match_key`` — e.g. a match
    winner and a series prop that both slip the reject filter — the key is
    REMOVED, never guessed. Same bijective principle as ``results_join``.

The reference "YES" token is index 0 of the parallel
``outcomes``/``clobTokenIds``/``outcomePrices`` arrays: ``yes_token_id =
clobTokenIds[0]``, ``market_price = outcomePrices[0]``, ``yes_outcome =
outcomes[0]``. At eval time ``clob_labels`` maps ``yes_token_id`` -> the
authoritative CLOB team NAME and ``resolve_yes_is_team_a`` aligns it to the sharp
odds' ``team_a`` (flip-proof), so the arbitrary index-0 choice cannot invert the
edge.

The HTTP fetch is injectable (``fetch_page`` param) so the index logic is fully
unit-testable offline; the default fetch pages the public Gamma API exactly like
``scripts/esports_market_shape_probe_public.py``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from esports_v2.data.odds_loader import make_match_key

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
# Verified against 948 live shape-2 markets (2026-07-09): this isolates the 12
# real match-winner markets and rejects every prop, with zero false drops.
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


@dataclass
class PMMarketRef:
    """The Polymarket match-winner reference for one match, captured at snapshot
    time. ``market_price`` is the Gamma-reported price of the index-0 (reference
    "YES") token; ``None`` if the price was missing/degenerate but the market is
    otherwise a valid, unambiguous match winner."""

    condition_id: str
    yes_token_id: str
    yes_outcome: str
    market_price: Optional[float]
    question: str
    game_start: str


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


def parse_gamma_market(m: dict) -> Optional[Tuple[str, PMMarketRef]]:
    """Parse one Gamma market into ``(match_key, PMMarketRef)``, or None.

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
    if not game_start:
        return None  # no start -> cannot form the same date key the odds use

    prices = _json_list(m.get("outcomePrices"))
    market_price = _coerce_price(prices[0]) if len(prices) == 2 else None

    key = make_match_key(team_a, team_b, game_start)
    ref = PMMarketRef(
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        yes_outcome=team_a,
        market_price=market_price,
        question=question,
        game_start=game_start,
    )
    return key, ref


def build_pm_index(
    *,
    fetch_page: Optional[FetchPage] = None,
    max_pages: int = 30,
) -> Dict[str, PMMarketRef]:
    """Build a ``match_key -> PMMarketRef`` index of live PM match winners.

    Pages the Gamma esports tag, parses each market correct-or-absent, and DROPS
    any ``match_key`` that two distinct ``condition_id`` markets resolve to
    (ambiguous -> never guessed). Returns {} on any fetch failure at page 0
    (the collector then simply writes odds with null PM fields — no regression).
    """
    fetch_page = fetch_page or _default_fetch_page

    index: Dict[str, PMMarketRef] = {}
    ambiguous: set = set()
    seen_cid: Dict[str, str] = {}  # match_key -> condition_id first indexed
    parsed = 0

    for page in range(max_pages):
        offset = page * _GAMMA_PAGE
        markets = fetch_page(offset, _GAMMA_PAGE)
        if not markets:
            break
        for m in markets:
            got = parse_gamma_market(m)
            if got is None:
                continue
            key, ref = got
            if key in ambiguous:
                continue
            prev_cid = seen_cid.get(key)
            if prev_cid is None:
                index[key] = ref
                seen_cid[key] = ref.condition_id
                parsed += 1
            elif prev_cid != ref.condition_id:
                # Two different markets -> the same teams+date. Cannot tell which
                # is the true match winner from the key alone -> drop, never guess.
                ambiguous.add(key)
                index.pop(key, None)
            # same condition_id seen again (paging overlap) -> keep the first.
        if len(markets) < _GAMMA_PAGE:
            break

    logger.info(
        f"pm_index_built keys={len(index)} parsed={parsed} "
        f"ambiguous_dropped={len(ambiguous)}"
    )
    return index


def _default_fetch_page(offset: int, limit: int) -> Optional[List[dict]]:
    """GET one page of the live esports-tag Gamma feed. None on any failure
    (correct-or-absent — the collector then skips PM enrichment for this tick)."""
    import requests

    url = (
        f"{_GAMMA_URL}?tag_id={_ESPORTS_TAG_ID}&closed=false&active=true"
        f"&archived=false&limit={limit}&offset={offset}"
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
