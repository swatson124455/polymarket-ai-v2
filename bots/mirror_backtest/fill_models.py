"""Dual fill models for the acceptance-gate harness.

PRECISE — walks a real per-level ladder (shadow_fills.book_snapshot) with the
shared `_vwap_from_book` / `_vwap_from_bids` (base_engine.execution.paper_trading,
imported in place per the salvage consumption model — pure module-level
functions, no engine state).

COARSE — orderbook_snapshots rows carry NO ladder (verified 2026-07-02: DDL 070
+ writer orderbook_collector.py:155), only best bid/ask, mid, and depth-within-
1%/5%-of-mid buckets. The coarse model is therefore DELIBERATELY PESSIMISTIC:
it charges the worst price of the smallest bucket that covers the order, so a
rule's coarse edge is a lower bound. Optimistic fills would let a bad rule
through the gate — pessimism is the safe error direction for admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from base_engine.execution.paper_trading import _vwap_from_bids, _vwap_from_book

PRECISE = "precise"
COARSE = "coarse"


@dataclass
class FillResult:
    price: float               # per-share fill price actually charged
    fill_fraction: float       # filled / requested (0..1]
    model: str                 # PRECISE | COARSE
    caveats: list = field(default_factory=list)


def precise_fill(
    book_snapshot: list,
    side: str,                  # BUY | SELL of the priced token
    size_shares: float,
    whale_size_shares: float = 0.0,
) -> Optional[FillResult]:
    """VWAP walk of a real ladder. book_snapshot: [{price, size}, ...] for the
    side being consumed (asks for BUY, bids for SELL). None when no depth."""
    if not book_snapshot or size_shares <= 0:
        return None
    if str(side).upper() == "SELL":
        walked = _vwap_from_bids(book_snapshot, size_shares)
    else:
        walked = _vwap_from_book(book_snapshot, size_shares, whale_size_shares)
    if walked is None:
        return None
    vwap, fill_frac, _slippage = walked
    price = min(0.999, max(0.001, float(vwap)))
    caveats = []
    if fill_frac < 1.0:
        caveats.append("partial_fill")
    return FillResult(price=price, fill_fraction=float(fill_frac),
                      model=PRECISE, caveats=caveats)


# Bucket half-widths of the coarse model: an order that fits inside the 1%/5%
# depth bucket is charged the bucket's WORST price (mid +/- the full width).
_BUCKET_1PCT = 0.01
_BUCKET_5PCT = 0.05


def coarse_fill(
    snapshot_row: dict,
    side: str,                  # BUY | SELL of the priced token
    size_usd: float,
) -> Optional[FillResult]:
    """Pessimistic fill from an orderbook_snapshots aggregate row.

    Row fields used: mid_price, best_ask/best_bid, ask/bid_depth_1pct,
    ask/bid_depth_5pct (depths in USD within 1%/5% of mid; DDL 070).
    Charging rule (BUY; SELL mirrors on the bid side):
      size <= depth_1pct  -> price = max(best_ask, mid*(1+1%))   (worst of bucket)
      size <= depth_5pct  -> price = mid*(1+5%)
      else                -> fill only depth_5pct at mid*(1+5%), rest unfilled
    Returns None when the row lacks a usable mid/side price (uncovered signal).
    """
    if size_usd <= 0 or not isinstance(snapshot_row, dict):
        return None
    try:
        mid = float(snapshot_row.get("mid_price") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (0.0 < mid < 1.0):
        return None
    is_sell = str(side).upper() == "SELL"

    def _f(key: str) -> float:
        try:
            return float(snapshot_row.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    best = _f("best_bid" if is_sell else "best_ask")
    d1 = _f("bid_depth_1pct" if is_sell else "ask_depth_1pct")
    d5 = _f("bid_depth_5pct" if is_sell else "ask_depth_5pct")
    if best <= 0 and d1 <= 0 and d5 <= 0:
        return None

    sign = -1.0 if is_sell else 1.0
    caveats = ["coarse_bucket_model"]
    if d1 >= size_usd > 0:
        edge_of_bucket = mid * (1.0 + sign * _BUCKET_1PCT)
        # worst of (best price, bucket edge) in the adverse direction
        if best > 0:
            price = min(best, edge_of_bucket) if is_sell else max(best, edge_of_bucket)
        else:
            price = edge_of_bucket
        frac = 1.0
    elif d5 >= size_usd:
        price = mid * (1.0 + sign * _BUCKET_5PCT)
        frac = 1.0
    elif d5 > 0:
        price = mid * (1.0 + sign * _BUCKET_5PCT)
        frac = d5 / size_usd
        caveats.append("partial_fill")
    else:
        return None
    return FillResult(price=min(0.999, max(0.001, float(price))),
                      fill_fraction=float(min(1.0, frac)),
                      model=COARSE, caveats=caveats)
