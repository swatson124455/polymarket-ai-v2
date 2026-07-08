"""EsportsBot sharp-line reference — no-vig sharp price vs the Polymarket price.

Strategy (2026-07 fleet pivot, EB-scoped): the dead ratings model (B12) is
replaced by an EXTERNAL sharp signal. For an esports market EB can match to a
sharp book (Pinnacle via OddsPapi), strip the vig to a fair two-way probability,
align it to the Polymarket YES outcome, and bet the side the sharp line says
Polymarket is underpricing:

    edge(side) = sharp_prob(side) - polymarket_price(side) - fee

This is the EB analog of ``bots/mirror_backtest/sharp_reference.py``, but
MARKET-oriented (compare to the Polymarket quote), not whale-oriented (compare
to a copied whale entry). EB owns this module; it deliberately does NOT import
MB's silo, so MB's file can change without touching EB (bot-session scope).

Split, exactly like MB's:
  - PURE core (this file): no-vig conversion, point-in-time line selection, the
    orientation guard (closes B2), and the two-sided edge rule. Fully testable
    offline — no network, no DB, no settings required.
  - The LIVE odds fetch is NOT here. ``esports_v2/data/odds_loader.py`` already
    fetches OddsPapi/Pinnacle odds into a ``match_key -> (odds_a, odds_b)``
    lookup. Everything here consumes that exact shape, so wiring the paid tier
    is a drop-in at the existing seam — nothing in this file changes.

De-vig method: SIMPLE two-way no-vig (proportional de-overround), matching the
fleet's sharp-line-reference decision (MB's ``no_vig_two_way``). A Shin variant
already exists in ``clv.py:odds_to_implied`` if the operator prefers it — swap
it in at the single ``no_vig_two_way`` call site. (Open decision — flagged.)

Orientation (B2): the sharp odds are for ``(team_a, team_b)`` in the odds_loader
key order. Whether the Polymarket YES token resolves on team_a is NOT inferable
here — the matcher must supply it (``yes_is_team_a``). This module never guesses
the alignment; a record without it is treated as uncovered, exactly like a
record with no odds. That is the by-construction fix for the missing team->YES
guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Defaults: match EB's existing risk controls. MIN_EDGE mirrors
# esports_v2/model/pipeline.py:MIN_EDGE (0.05); FEE is the round-trip taker
# assumption used by MB's sharp rule. Both are overridable per call.
DEFAULT_FEE = 0.02
DEFAULT_MIN_EDGE = 0.05


# ── PURE no-vig core (vendor-independent, fully testable) ────────────────────


def no_vig_two_way(decimal_a: float, decimal_b: float) -> Optional[Tuple[float, float]]:
    """Two-way no-vig implied probabilities from decimal odds.

    ``prob_i = (1/dec_i) / (1/dec_a + 1/dec_b)`` — strips the overround so the
    two sides sum to 1.0. Returns None on non-positive/degenerate odds (both
    must be > 1.0 for a real two-way market). Matches MB's fleet de-vig; see the
    module docstring for the Shin alternative.
    """
    if decimal_a <= 1.0 or decimal_b <= 1.0:
        return None
    ia, ib = 1.0 / decimal_a, 1.0 / decimal_b
    total = ia + ib
    if total <= 0:
        return None
    return ia / total, ib / total


@dataclass
class LineEntry:
    """One sharp-book price observation for an outcome, with its timestamp."""

    price: float          # decimal odds for the outcome at this timestamp
    created_at: datetime  # when the book showed this price


def pick_point_in_time(
    entries: List[LineEntry], at_time: datetime
) -> Optional[LineEntry]:
    """Latest line entry at-or-before ``at_time`` — NO look-ahead.

    Returns None if the book had no entry by then (the event is uncovered at
    that moment). Used on the LIVE intraday line series; harmless for the
    closing-odds backfill (which already carries a single closing price).
    """
    eligible = [e for e in entries if e.created_at <= at_time]
    if not eligible:
        return None
    return max(eligible, key=lambda e: e.created_at)


# ── Orientation guard (closes B2) ────────────────────────────────────────────


def sharp_yes_prob(no_vig_a: float, yes_is_team_a: bool) -> float:
    """No-vig fair probability of the Polymarket YES outcome.

    ``no_vig_a`` is the sharp's fair prob for team_a (the odds_loader 'A' side).
    ``yes_is_team_a`` MUST come from the matcher: True iff the Polymarket YES
    token resolves on team_a winning. This function never infers the alignment —
    that is exactly the missing team->YES guard (B2). Callers that don't know
    the alignment must NOT call this; treat the record as uncovered.
    """
    return no_vig_a if yes_is_team_a else (1.0 - no_vig_a)


# ── Two-sided edge rule ──────────────────────────────────────────────────────


@dataclass
class SharpEdge:
    """The better of the two sides to bet, after de-vig and fees."""

    side: str            # "YES" or "NO" — the side to bet
    sharp_prob: float    # no-vig fair prob of that side
    market_price: float  # Polymarket price of that side
    edge: float          # sharp_prob - market_price - fee (can be negative)
    should_bet: bool     # edge >= min_edge


def evaluate_edge(
    sharp_yes: float,
    market_yes_price: float,
    *,
    fee: float = DEFAULT_FEE,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> Optional[SharpEdge]:
    """Pick the side the sharp line says Polymarket underprices.

    ``sharp_yes`` is the no-vig fair prob of the YES outcome (from
    ``sharp_yes_prob``); ``market_yes_price`` is the Polymarket YES token price
    (NO price = 1 - YES price). Evaluates both sides and returns the higher-edge
    one, flagged ``should_bet`` when it clears ``min_edge``. Returns None on a
    degenerate market/probability (never guesses).

    edge(YES) = sharp_yes - market_yes_price - fee
    edge(NO)  = (1 - sharp_yes) - (1 - market_yes_price) - fee
              = market_yes_price - sharp_yes - fee
    """
    if not (0.0 < market_yes_price < 1.0):
        return None
    if not (0.0 <= sharp_yes <= 1.0):
        return None

    yes_edge = sharp_yes - market_yes_price - fee
    no_edge = market_yes_price - sharp_yes - fee

    if yes_edge >= no_edge:
        side, sp, mp, edge = "YES", sharp_yes, market_yes_price, yes_edge
    else:
        side, sp, mp, edge = "NO", 1.0 - sharp_yes, 1.0 - market_yes_price, no_edge

    return SharpEdge(
        side=side,
        sharp_prob=sp,
        market_price=mp,
        edge=edge,
        should_bet=edge >= min_edge,
    )


# ── Offline enrichment (source-agnostic seam) ────────────────────────────────


def enrich_with_sharp_prob(
    records: List[dict],
    odds_lookup: Dict[str, Tuple[float, float]],
    *,
    fee: float = DEFAULT_FEE,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> List[dict]:
    """Attach a no-vig ``sharp_prob`` + edge to market records, offline.

    Parallel to ``clv.py:enrich_with_clv`` but for the sharp-line-vs-Polymarket
    strategy. ``odds_lookup`` is exactly ``odds_loader.OddsPapiLoader.fetch_odds``
    output — ``match_key -> (decimal_odds_a, decimal_odds_b)`` — so the live
    paid-tier fetch only has to populate this dict; nothing here changes.

    Each record must carry:
      - ``match_key``      : key into ``odds_lookup`` (odds_loader.make_match_key)
      - ``market_price``   : Polymarket YES token price (same field pipeline uses)
      - ``yes_is_team_a``  : bool from the matcher (orientation guard, B2)

    Mutates and returns ``records`` with added fields:
      - ``sharp_prob``  : no-vig fair prob of the YES outcome (None if uncovered)
      - ``sharp_side``  : side to bet, "YES"/"NO" (None if uncovered/degenerate)
      - ``sharp_edge``  : edge on ``sharp_side`` (None if uncovered/degenerate)
      - ``sharp_should_bet`` : bool (False if uncovered/degenerate)

    A record is UNCOVERED (all-None, should_bet False) — never guessed — when it
    lacks odds, lacks the orientation flag, or the odds/market are degenerate.
    """
    for rec in records:
        rec["sharp_prob"] = None
        rec["sharp_side"] = None
        rec["sharp_edge"] = None
        rec["sharp_should_bet"] = False

        match_key = rec.get("match_key")
        odds = odds_lookup.get(match_key) if match_key is not None else None
        yes_is_team_a = rec.get("yes_is_team_a")
        market_price = rec.get("market_price")

        # Uncovered: no odds, or the matcher never resolved orientation (B2).
        if odds is None or not isinstance(yes_is_team_a, bool):
            continue
        if market_price is None:
            continue

        no_vig = no_vig_two_way(float(odds[0]), float(odds[1]))
        if no_vig is None:
            continue

        sharp_yes = sharp_yes_prob(no_vig[0], yes_is_team_a)
        result = evaluate_edge(
            sharp_yes, float(market_price), fee=fee, min_edge=min_edge
        )
        if result is None:
            continue

        rec["sharp_prob"] = sharp_yes
        rec["sharp_side"] = result.side
        rec["sharp_edge"] = result.edge
        rec["sharp_should_bet"] = result.should_bet

    return records
