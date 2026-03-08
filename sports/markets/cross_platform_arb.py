"""
Cross-Platform Sports Arbitrage — Polymarket vs Kalshi.

Scans for price discrepancies between Polymarket and Kalshi sports markets
on the same underlying event. When YES(Poly) + NO(Kalshi) < 1.0 (or vice versa),
a risk-free arb exists.

Phase 5: Full implementation.
  - Wraps SportsMarketScanner to find matching markets on both platforms.
  - Applies SPORTS_ARB_MIN_SPREAD gate (default 4%).
  - Returns ArbOpportunity objects for SportsArbBot to execute.

Usage::
    opportunities = await find_sports_arb_opportunities(
        sport="nba", db=db, kalshi_client=kalshi_client
    )
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from structlog import get_logger

from sports.markets.kalshi_client import SportsMarketCandidate
from sports.markets.sports_market_scanner import SportsMarketScanner  # noqa: F401 — kept for test patching
from config.settings import settings  # noqa: F401 � kept at module level for test patching

logger = get_logger()


@dataclass
class ArbOpportunity:
    """A detected cross-platform arb opportunity."""
    sport: str
    event_title: str
    polymarket_id: str
    kalshi_id: str
    poly_yes_price: float       # Polymarket YES price
    kalshi_no_price: float      # Kalshi NO price (equivalent outcome)
    gross_spread: float         # 1.0 - (poly_yes + kalshi_no)  (arb profit margin)
    net_spread: float           # gross_spread minus estimated fees
    leg_a_platform: str         # "polymarket"
    leg_b_platform: str         # "kalshi"
    leg_a_side: str             # "YES"
    leg_b_side: str             # "NO"
    poly_candidate: Optional[SportsMarketCandidate] = None
    kalshi_candidate: Optional[SportsMarketCandidate] = None


async def find_sports_arb_opportunities(
    sport: Optional[str] = None,
    db=None,
    kalshi_client=None,
    min_spread: Optional[float] = None,
) -> List[ArbOpportunity]:
    """
    Find cross-platform arb opportunities between Polymarket and Kalshi.

    Args:
        sport:         Optional sport filter (nba/nfl/etc). If None, scans all sports.
        db:            Database instance.
        kalshi_client: KalshiSportsClient instance.
        min_spread:    Minimum net spread to return. Defaults to SPORTS_ARB_MIN_SPREAD.

    Returns:
        List of ArbOpportunity objects sorted by net_spread descending.
    """
    if min_spread is None:
        min_spread = float(getattr(settings, "SPORTS_ARB_MIN_SPREAD", 0.04))

    sports_to_scan = [sport] if sport else ["nba", "nfl", "mlb", "nhl", "soccer", "tennis"]
    opportunities: List[ArbOpportunity] = []

    for sport_code in sports_to_scan:
        try:
            opps = await asyncio.wait_for(
                _scan_sport_for_arb(sport_code, db, kalshi_client, min_spread),
                timeout=15.0,
            )
            opportunities.extend(opps)
        except asyncio.TimeoutError:
            logger.warning("cross_platform_arb: scan timed out", sport=sport_code)
        except Exception as exc:
            logger.debug("cross_platform_arb: scan error", sport=sport_code, error=str(exc))

    # Sort by net spread descending (best arb first)
    opportunities.sort(key=lambda o: o.net_spread, reverse=True)

    if opportunities:
        logger.info(
            "cross_platform_arb: found opportunities",
            count=len(opportunities),
            best_spread=round(opportunities[0].net_spread, 4) if opportunities else 0,
        )

    return opportunities


async def _scan_sport_for_arb(
    sport: str,
    db,
    kalshi_client,
    min_spread: float,
) -> List[ArbOpportunity]:
    """Scan one sport's markets for arb opportunities."""
    if kalshi_client is None:
        return []

    # Get Kalshi markets for this sport
    try:
        kalshi_markets = await asyncio.wait_for(
            kalshi_client.get_sports_markets(sport=sport),
            timeout=10.0,
        )
    except Exception:
        return []

    if not kalshi_markets:
        return []

    # Get Polymarket markets for this sport
    poly_markets: List[SportsMarketCandidate] = []
    if db:
        try:
            scanner = SportsMarketScanner(db=db)
            poly_markets = await scanner._scan_polymarket(sport, "arb_scan", None, None, db)
        except Exception as exc:
            logger.debug("cross_platform_arb: Polymarket scan error in arb", error=str(exc))

    if not poly_markets:
        return []

    # Match markets by title similarity
    opportunities = []
    for kalshi_m in kalshi_markets:
        kalshi_title_lower = kalshi_m.title.lower()
        for poly_m in poly_markets:
            poly_title_lower = poly_m.title.lower()
            if not _titles_match(poly_title_lower, kalshi_title_lower):
                continue

            # I39: Reject stale prices — any price older than 60s invalidates the arb calc
            _now = time.monotonic()
            _max_age = 60.0
            if poly_m.price_fetched_at is not None and (_now - poly_m.price_fetched_at) > _max_age:
                logger.debug(
                    "cross_platform_arb: skipping stale Polymarket price",
                    market_id=poly_m.market_id,
                    age_s=round(_now - poly_m.price_fetched_at, 1),
                )
                continue
            if kalshi_m.price_fetched_at is not None and (_now - kalshi_m.price_fetched_at) > _max_age:
                logger.debug(
                    "cross_platform_arb: skipping stale Kalshi price",
                    market_id=kalshi_m.market_id,
                    age_s=round(_now - kalshi_m.price_fetched_at, 1),
                )
                continue

            # Check both arb directions:
            if poly_m.current_price and kalshi_m.current_price:
                poly_yes = poly_m.current_price
                kalshi_yes = kalshi_m.current_price

                # Fee estimates per contract (absolute):
                #   Polymarket: 0.10% taker (flat) — post-QCEX US rate
                #   Kalshi:     0.07 × P × (1-P) taker, 0.0175 × P × (1-P) maker
                # Use maker fees for Kalshi (limit orders), taker for Polymarket.
                _poly_fee_rate = 0.001   # 0.10% flat taker
                _kalshi_coeff = 0.0175   # maker coefficient

                # Direction A: Buy YES on Poly + buy NO on Kalshi
                kalshi_no = 1.0 - kalshi_yes
                gross_a = 1.0 - poly_yes - kalshi_no
                poly_fee_a = _poly_fee_rate * poly_yes
                kalshi_fee_a = _kalshi_coeff * kalshi_no * (1.0 - kalshi_no)
                net_a = gross_a - poly_fee_a - kalshi_fee_a

                if net_a >= min_spread:
                    opportunities.append(ArbOpportunity(
                        sport=sport,
                        event_title=poly_m.title or kalshi_m.title,
                        polymarket_id=poly_m.market_id,
                        kalshi_id=kalshi_m.market_id,
                        poly_yes_price=poly_yes,
                        kalshi_no_price=kalshi_no,
                        gross_spread=round(gross_a, 4),
                        net_spread=round(net_a, 4),
                        leg_a_platform="polymarket",
                        leg_b_platform="kalshi",
                        leg_a_side="YES",
                        leg_b_side="NO",
                        poly_candidate=poly_m,
                        kalshi_candidate=kalshi_m,
                    ))

                # Direction B: Buy NO on Poly + buy YES on Kalshi
                poly_no = 1.0 - poly_yes
                gross_b = 1.0 - poly_no - kalshi_yes
                poly_fee_b = _poly_fee_rate * poly_no
                kalshi_fee_b = _kalshi_coeff * kalshi_yes * (1.0 - kalshi_yes)
                net_b = gross_b - poly_fee_b - kalshi_fee_b

                if net_b >= min_spread:
                    opportunities.append(ArbOpportunity(
                        sport=sport,
                        event_title=poly_m.title or kalshi_m.title,
                        polymarket_id=poly_m.market_id,
                        kalshi_id=kalshi_m.market_id,
                        poly_yes_price=poly_no,
                        kalshi_no_price=kalshi_yes,
                        gross_spread=round(gross_b, 4),
                        net_spread=round(net_b, 4),
                        leg_a_platform="polymarket",
                        leg_b_platform="kalshi",
                        leg_a_side="NO",
                        leg_b_side="YES",
                        poly_candidate=poly_m,
                        kalshi_candidate=kalshi_m,
                    ))

    return opportunities


_PUNCT_RE = re.compile(r"[^\w\s]")  # I40: compiled once at module level

_TITLE_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "will", "win", "who",
})


def _titles_match(title_a: str, title_b: str, threshold: float = 0.40) -> bool:
    """
    Check if two market titles describe the same event.

    Primary: Jaccard word-overlap ratio >= threshold (0.40).
    Fallback (I40): When Jaccard < 0.50, apply SequenceMatcher ratio >= 0.70
    on punctuation-stripped titles — handles "Lakers-Warriors" vs "Lakers vs Warriors".
    """
    if not title_a or not title_b:
        return False

    # I40: Strip punctuation before tokenizing — normalise "Lakers-Warriors" → "Lakers Warriors"
    clean_a = _PUNCT_RE.sub(" ", title_a.lower())
    clean_b = _PUNCT_RE.sub(" ", title_b.lower())

    words_a = set(clean_a.split())
    words_b = set(clean_b.split())

    # Remove common stop words
    words_a -= _TITLE_STOP_WORDS
    words_b -= _TITLE_STOP_WORDS

    if not words_a or not words_b:
        return False

    overlap = len(words_a & words_b)
    union = len(words_a | words_b)
    jaccard = overlap / union if union > 0 else 0.0

    if jaccard >= threshold:
        return True

    # I40: SequenceMatcher fallback when Jaccard < 0.50.
    # Uses WORD-LEVEL matching (not character-level) to catch connector/separator variants
    # e.g. "Lakers-Warriors" → ["lakers","warriors"] vs "Lakers vs Warriors" → ["lakers","warriors"].
    # Character-level matching would false-positive on "basketball" ≈ "baseball".
    if jaccard < 0.50:
        words_a_list = sorted(words_a)
        words_b_list = sorted(words_b)
        seq_ratio = SequenceMatcher(None, words_a_list, words_b_list).ratio()
        if seq_ratio >= 0.70:
            return True

    return False
